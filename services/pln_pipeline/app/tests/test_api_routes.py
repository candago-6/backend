"""Testes das rotas HTTP do pln-pipeline.

Roda dentro do container, sem GPU e sem chamar a API do Gemini:

    docker compose exec pln-pipeline python -m unittest app.tests.test_api_routes -v

Os modelos pesados (DistilBERT e RAG) são substituídos por dublês. As rotas de
vetorização (fasttext/w2vec) são exercitadas de verdade porque treinam o corpus
em poucos segundos — e é exatamente esse retreino por requisição que o relatório
aponta como custo escondido.
"""

import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app import main
from app.main import (
    app,
    is_knn_match_eligible,
    load_item_responses,
    normalize_knn_similarities,
    resolve_knn_item_response,
    resolve_top_item_response,
    save_retraining_record,
    truncate_snippet,
)
from app.models.distilbert import is_hub_repo_id
from app.models.rag_remote import (
    RAGChunk,
    RAGHit,
    clean_answer,
    extract_faq_entries,
    fallback_answer_from_hits,
    format_context,
    minimal_cleanup,
    normalize_text,
    split_text,
)
from app.models.schemas import ItemSimilarity, RetrainingDatasetRequest

client = TestClient(app)


class DistilBertFalso:
    def __init__(self, resposta="resposta modelo", fallback=False, erro=None):
        self.resposta, self.fallback, self.erro = resposta, fallback, erro
        self.perguntas = []

    def chat(self, pergunta):
        if self.erro:
            raise self.erro
        self.perguntas.append(pergunta)
        return self.resposta, self.fallback


class RagFalso:
    def __init__(self, resposta="resposta rag", hits=None, erro=None):
        self.resposta, self.hits, self.erro = resposta, hits or [], erro

    def ask(self, pergunta, top_k=None):
        if self.erro:
            raise self.erro
        return self.resposta, self.hits


class TestHealth(unittest.TestCase):
    def test_health_responde_ok(self):
        resposta = client.get("/api/health")

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json(), {"status": "ok", "service": "pln-pipeline"})


class TestRotaDistilBert(unittest.TestCase):
    """É a rota que o whatsapp-bot chama em produção (PLN_URL do compose)."""

    def setUp(self):
        self._original = main.DISTILBERT_PIPELINE

    def tearDown(self):
        main.DISTILBERT_PIPELINE = self._original

    def test_resposta_com_confianca_alta(self):
        main.DISTILBERT_PIPELINE = DistilBertFalso("Você tem 30 dias.", fallback=False)

        resposta = client.post("/api/distilbert", json={"raw_text": "qual o prazo?"})

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(
            resposta.json(), {"class_response": "Você tem 30 dias.", "is_fallback": False}
        )

    def test_fallback_sinalizado_para_o_bot_escalar(self):
        """`is_fallback=True` é o que faz o bot contar a falha e escalar aos 3."""
        main.DISTILBERT_PIPELINE = DistilBertFalso(
            "Desculpe, não encontrei uma resposta.", fallback=True
        )

        corpo = client.post("/api/distilbert", json={"raw_text": "xyz"}).json()

        self.assertTrue(corpo["is_fallback"])

    def test_texto_vazio_devolve_422(self):
        self.assertEqual(client.post("/api/distilbert", json={"raw_text": ""}).status_code, 422)

    def test_texto_so_com_espaco_devolve_400(self):
        resposta = client.post("/api/distilbert", json={"raw_text": "   "})

        self.assertEqual(resposta.status_code, 400)
        self.assertEqual(resposta.json(), {"detail": "Text is empty."})

    def test_campo_ausente_devolve_422(self):
        self.assertEqual(client.post("/api/distilbert", json={}).status_code, 422)

    def test_falha_do_modelo_vira_500(self):
        """O bot trata como instabilidade e responde a mensagem de contingência."""
        main.DISTILBERT_PIPELINE = DistilBertFalso(erro=RuntimeError("modelo fora do ar"))

        resposta = client.post("/api/distilbert", json={"raw_text": "oi"})

        self.assertEqual(resposta.status_code, 500)


class TestRotaRag(unittest.TestCase):
    def setUp(self):
        self._original = main.REMOTE_RAG_PIPELINE

    def tearDown(self):
        main.REMOTE_RAG_PIPELINE = self._original

    def test_resposta_com_fontes(self):
        hit = RAGHit(
            chunk=RAGChunk(text="Pergunta: X Resposta: Y", source="faq_fonte.pdf", page=3),
            score=0.81,
        )
        main.REMOTE_RAG_PIPELINE = RagFalso("O prazo é de 7 dias.", [hit])

        corpo = client.post("/api/rag_remote", json={"question": "prazo?"}).json()

        self.assertEqual(corpo["answer"], "O prazo é de 7 dias.")
        self.assertEqual(len(corpo["sources"]), 1)
        self.assertEqual(corpo["sources"][0]["page"], 3)
        self.assertEqual(corpo["sources"][0]["source"], "faq_fonte.pdf")

    def test_resposta_de_nao_encontrado_e_a_que_o_bot_reconhece(self):
        """O bot cancela a contingência com `semAcento(...).includes('nao encontrei')`.

        As duas formas de "não encontrei" que o pipeline pode devolver precisam
        passar nesse teste, senão resposta vazia vira resposta boa (index.js:96).
        """
        for texto in (
            "Nao encontrei essa informacao no documento.",
            "Não encontrei essa informação em minha base dados. Redirecionando para atendimento humano...",
        ):
            with self.subTest(texto=texto):
                normalizado = (
                    texto.encode("ascii", "ignore").decode().lower()
                    if texto.isascii()
                    else normalize_text(texto)
                )
                self.assertIn("nao encontrei", normalizado)

    def test_pergunta_vazia_devolve_422(self):
        self.assertEqual(client.post("/api/rag_remote", json={"question": ""}).status_code, 422)

    def test_pergunta_so_com_espaco_devolve_400(self):
        resposta = client.post("/api/rag_remote", json={"question": "   "})

        self.assertEqual(resposta.status_code, 400)
        self.assertEqual(resposta.json(), {"detail": "Question is empty."})

    def test_top_k_fora_do_intervalo_devolve_422(self):
        for k in (0, 11, -1):
            with self.subTest(k=k):
                resposta = client.post("/api/rag_remote", json={"question": "oi", "top_k": k})
                self.assertEqual(resposta.status_code, 422)

    def test_falha_do_pipeline_vira_500(self):
        main.REMOTE_RAG_PIPELINE = RagFalso(erro=RuntimeError("gemini fora"))

        self.assertEqual(
            client.post("/api/rag_remote", json={"question": "oi"}).status_code, 500
        )

    def test_erro_de_configuracao_vira_400(self):
        main.REMOTE_RAG_PIPELINE = RagFalso(erro=ValueError("GEMINI_API_KEY is not set."))

        resposta = client.post("/api/rag_remote", json={"question": "oi"})

        self.assertEqual(resposta.status_code, 400)
        self.assertIn("GEMINI_API_KEY", resposta.json()["detail"])


class TestRotaRetreinamento(unittest.TestCase):
    """Par pergunta/resposta revisado no painel, para alimentar o re-treino."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._original = main.RETRAINING_DATASET_PATH
        main.RETRAINING_DATASET_PATH = Path(self._tmp.name) / "retraining_dataset.json"

    def tearDown(self):
        main.RETRAINING_DATASET_PATH = self._original
        self._tmp.cleanup()

    def _registros(self):
        return json.loads(main.RETRAINING_DATASET_PATH.read_text(encoding="utf-8"))

    def test_primeiro_registro_cria_o_arquivo(self):
        resposta = client.post(
            "/api/retraining-dataset",
            json={"question": "Como cancelar cobrança indevida?", "answer": "Registre reclamação."},
        )

        self.assertEqual(resposta.status_code, 200)
        corpo = resposta.json()
        self.assertEqual(corpo["total_records"], 1)
        self.assertEqual(corpo["record"]["question"], "Como cancelar cobrança indevida?")
        self.assertEqual(len(self._registros()), 1)

    def test_registros_seguintes_sao_acumulados(self):
        for i in range(3):
            client.post("/api/retraining-dataset", json={"question": f"p{i}", "answer": f"r{i}"})

        self.assertEqual(len(self._registros()), 3)

    def test_espacos_nas_pontas_sao_removidos(self):
        corpo = client.post(
            "/api/retraining-dataset", json={"question": "  pergunta  ", "answer": "  resposta  "}
        ).json()

        self.assertEqual(corpo["record"], {"question": "pergunta", "answer": "resposta"})

    def test_campos_em_branco_sao_recusados(self):
        for payload in (
            {"question": "", "answer": "r"},
            {"question": "p", "answer": ""},
            {"question": "   ", "answer": "r"},
            {"question": "p"},
            {},
        ):
            with self.subTest(payload=payload):
                self.assertEqual(
                    client.post("/api/retraining-dataset", json=payload).status_code, 422
                )

    def test_arquivo_corrompido_devolve_400(self):
        main.RETRAINING_DATASET_PATH.parent.mkdir(parents=True, exist_ok=True)
        main.RETRAINING_DATASET_PATH.write_text("{isso não é json", encoding="utf-8")

        resposta = client.post("/api/retraining-dataset", json={"question": "p", "answer": "r"})

        self.assertEqual(resposta.status_code, 400)

    def test_gravacao_direta_pela_funcao(self):
        destino = Path(self._tmp.name) / "outro.json"

        registro, total = save_retraining_record(
            RetrainingDatasetRequest(question="p", answer="r"), destino
        )

        self.assertEqual(total, 1)
        self.assertEqual(registro, {"question": "p", "answer": "r"})
        self.assertTrue(destino.exists())


class TestRotasDeVetorizacao(unittest.TestCase):
    """Smoke real das 4 rotas de similaridade — sem dublê, com gensim de verdade."""

    ROTAS = ["/api/fasttext", "/api/fasttext/knn", "/api/w2vec", "/api/w2vec/knn"]

    def test_todas_as_rotas_respondem_com_o_contrato_esperado(self):
        for rota in self.ROTAS:
            with self.subTest(rota=rota):
                resposta = client.post(
                    rota, json={"raw_text": "produto com defeito troca garantia"}
                )

                self.assertEqual(resposta.status_code, 200)
                corpo = resposta.json()
                self.assertIn("class_response", corpo)
                self.assertIn("is_fallback", corpo)
                self.assertIn("predicted_class", corpo)
                self.assertIsInstance(corpo["class_response"], str)
                self.assertTrue(corpo["class_response"])

    def test_texto_vazio_devolve_422_em_todas(self):
        for rota in self.ROTAS:
            with self.subTest(rota=rota):
                self.assertEqual(client.post(rota, json={"raw_text": ""}).status_code, 422)

    def test_k_fora_do_intervalo_devolve_422(self):
        for rota in ("/api/fasttext/knn", "/api/w2vec/knn"):
            for k in (0, 21):
                with self.subTest(rota=rota, k=k):
                    resposta = client.post(
                        f"{rota}?k={k}", json={"raw_text": "produto com defeito"}
                    )
                    self.assertEqual(resposta.status_code, 422)

    def test_texto_sem_relacao_cai_no_fallback(self):
        corpo = client.post(
            "/api/fasttext/knn", json={"raw_text": "zzzz qqqq xxxx wwww"}
        ).json()

        self.assertIn("is_fallback", corpo)


class TestRegrasDoKnn(unittest.TestCase):
    """Limiares que decidem entre responder e escalar para o humano."""

    def _vizinhos(self, *trios):
        return list(trios)

    def test_similaridade_alta_e_votos_suficientes_sao_elegiveis(self):
        vizinhos = self._vizinhos((1, "A", 0.9), (2, "A", 0.5), (3, "B", 0.4))
        normalizados = normalize_knn_similarities(vizinhos)

        self.assertTrue(is_knn_match_eligible(vizinhos, "A", normalizados))

    def test_similaridade_abaixo_do_minimo_nao_e_elegivel(self):
        vizinhos = self._vizinhos((1, "A", 0.10), (2, "A", 0.05))

        self.assertFalse(
            is_knn_match_eligible(vizinhos, "A", normalize_knn_similarities(vizinhos))
        )

    def test_empate_entre_primeiro_e_segundo_nao_e_elegivel(self):
        vizinhos = self._vizinhos((1, "A", 0.80), (2, "B", 0.80))

        self.assertFalse(
            is_knn_match_eligible(vizinhos, "A", normalize_knn_similarities(vizinhos))
        )

    def test_classe_minoritaria_nao_e_elegivel(self):
        vizinhos = self._vizinhos((1, "A", 0.9), (2, "B", 0.5), (3, "B", 0.4), (4, "B", 0.3))

        self.assertFalse(
            is_knn_match_eligible(vizinhos, "A", normalize_knn_similarities(vizinhos))
        )

    def test_sem_vizinhos_nao_e_elegivel(self):
        self.assertFalse(is_knn_match_eligible([], None, []))
        self.assertFalse(is_knn_match_eligible([(1, "A", 0.9)], None, []))

    def test_normalizacao_soma_cem_por_cento(self):
        normalizados = normalize_knn_similarities([(1, "A", 0.6), (2, "B", 0.4)])

        self.assertAlmostEqual(sum(n.similarity for n in normalizados), 100.0, places=4)
        self.assertEqual([n.rank for n in normalizados], [1, 2])

    def test_normalizacao_com_similaridades_negativas_distribui_igual(self):
        normalizados = normalize_knn_similarities([(1, "A", -0.5), (2, "B", -0.9)])

        self.assertEqual([round(n.similarity, 4) for n in normalizados], [50.0, 50.0])

    def test_normalizacao_de_lista_vazia(self):
        self.assertEqual(normalize_knn_similarities([]), [])


class TestRespostasPorItem(unittest.TestCase):
    RESPOSTAS = {"1": "resposta um", "2": "resposta dois", "default": "resposta padrão"}

    def test_item_mais_similar_define_a_resposta(self):
        similaridades = [
            ItemSimilarity(item=2, classe="B", similarity=0.9),
            ItemSimilarity(item=1, classe="A", similarity=0.1),
        ]

        self.assertEqual(resolve_top_item_response(similaridades, self.RESPOSTAS), "resposta dois")

    def test_sem_similaridade_usa_o_default(self):
        self.assertEqual(resolve_top_item_response([], self.RESPOSTAS), "resposta padrão")

    def test_knn_usa_o_primeiro_vizinho_da_classe_prevista(self):
        vizinhos = [(9, "B", 0.9), (1, "A", 0.8), (2, "A", 0.7)]

        self.assertEqual(resolve_knn_item_response(vizinhos, "A", self.RESPOSTAS), "resposta um")

    def test_knn_sem_classe_prevista_usa_o_default(self):
        self.assertEqual(
            resolve_knn_item_response([(1, "A", 0.9)], None, self.RESPOSTAS), "resposta padrão"
        )

    def test_item_sem_resposta_cadastrada_usa_o_default(self):
        self.assertEqual(
            resolve_top_item_response(
                [ItemSimilarity(item=999, classe="Z", similarity=0.9)], self.RESPOSTAS
            ),
            "resposta padrão",
        )

    def test_arquivo_real_de_respostas_carrega_e_tem_default(self):
        respostas = load_item_responses()

        self.assertIsInstance(respostas, dict)
        self.assertTrue(respostas)
        self.assertIn("default", respostas)


class TestAuxiliaresDoRag(unittest.TestCase):
    def test_normalize_text_tira_acento_e_caixa(self):
        self.assertEqual(normalize_text("Não Encontrei ÇÃO"), "nao encontrei cao")

    def test_minimal_cleanup_junta_quebras_de_linha(self):
        self.assertEqual(minimal_cleanup("linha um\n  linha dois\t x"), "linha um linha dois x")

    def test_extract_faq_entries_le_o_formato_do_pdf(self):
        texto = "1 - Qual o prazo? R: São 30 dias. 2 - E para durável? R: São 90 dias."

        entradas = extract_faq_entries(texto)

        self.assertEqual(len(entradas), 2)
        self.assertEqual(entradas[0], ("Qual o prazo?", "São 30 dias."))
        self.assertEqual(entradas[1][1], "São 90 dias.")

    def test_extract_faq_entries_sem_padrao_devolve_vazio(self):
        self.assertEqual(extract_faq_entries("texto corrido sem perguntas"), [])

    def test_split_text_respeita_tamanho_e_sobreposicao(self):
        pedacos = split_text("a" * 250, chunk_size=100, chunk_overlap=20)

        self.assertTrue(all(len(p) <= 100 for p in pedacos))
        self.assertGreater(len(pedacos), 1)

    def test_split_text_com_sobreposicao_maior_que_o_bloco_levanta_erro(self):
        with self.assertRaises(ValueError):
            split_text("texto", chunk_size=10, chunk_overlap=10)

    def test_split_text_com_texto_vazio(self):
        self.assertEqual(split_text("   ", 100, 10), [])

    def test_clean_answer_remove_prefixo_resposta(self):
        self.assertEqual(clean_answer("RESPOSTA: são 30 dias"), "são 30 dias")
        self.assertEqual(clean_answer("  texto normal  "), "texto normal")

    def test_fallback_extrai_resposta_do_trecho(self):
        hits = [RAGHit(chunk=RAGChunk("Pergunta: X Resposta: valor Y", "f.pdf", 1), score=0.5)]

        self.assertEqual(fallback_answer_from_hits(hits), "valor Y")

    def test_fallback_sem_padrao_devolve_none(self):
        hits = [RAGHit(chunk=RAGChunk("texto qualquer", "f.pdf", 1), score=0.5)]

        self.assertIsNone(fallback_answer_from_hits(hits))

    def test_format_context_respeita_o_limite_de_caracteres(self):
        hits = [
            RAGHit(chunk=RAGChunk("x" * 500, "f.pdf", i), score=0.5) for i in range(1, 6)
        ]

        contexto = format_context(hits, max_context_chars=1200)

        self.assertLessEqual(len(contexto), 1600)
        self.assertIn("[Fonte 1 | p.1 | f.pdf]", contexto)


class TestAuxiliaresDiversos(unittest.TestCase):
    def test_truncate_snippet_corta_na_palavra(self):
        recortado = truncate_snippet("palavra " * 100, max_chars=50)

        self.assertTrue(recortado.endswith("..."))
        self.assertLessEqual(len(recortado), 53)

    def test_truncate_snippet_mantem_texto_curto(self):
        self.assertEqual(truncate_snippet("texto curto"), "texto curto")

    def test_identificacao_de_repo_do_hugging_face(self):
        self.assertTrue(is_hub_repo_id("caiquefrd/faq-model-v5"))
        self.assertFalse(is_hub_repo_id("/app/app/faq_model_v5"))
        self.assertFalse(is_hub_repo_id("./faq_model_v5"))
        self.assertFalse(is_hub_repo_id("org/repo/extra"))


if __name__ == "__main__":
    unittest.main()
