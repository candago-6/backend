// Predicados puros do bot, isolados aqui para terem teste automatizado sem
// precisar subir o Chromium/WhatsApp Web. O index.js os consome via require.
//
//     npm test
//

// O RAG sinaliza "sem resposta" de duas formas: a determinística
// "Nao encontrei essa informacao no documento." (rag_remote.py:311,334) e a que o
// modelo gera, "Não encontrei essa informação em minha base dados..."
// (rag_remote.py:342). O teste anterior — includes("não encontrei") — não pegava
// nenhuma das duas: a primeira não tem acento, a segunda começa com N maiúsculo.
// Por isso toda resposta vazia do RAG virava resposta boa e cancelava o handoff.
const semAcento = (s) => String(s ?? '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
const ragSemResposta = (answer) => semAcento(answer).includes('nao encontrei');

// Com \b para nao casar "assim"/"simples" com sim, nem "naopode" com nao.
const dizSim = (t) => /\bsim\b/.test(semAcento(t));
const dizNao = (t) => /\bnao\b/.test(semAcento(t));

// Vai junto de toda resposta dos modelos. Sem esta pergunta a conversa fica em
// 'open' esperando o Vigilante, e a mensagem de despedida do proprio usuario
// ("Obrigado") era tratada como pergunta nova e ia parar nos modelos.
// Texto puro em vez de Buttons: os botoes do whatsapp-web.js nao renderizam
// de forma confiavel nos clientes atuais.
const PERGUNTA_VERIFICACAO = '\n\n---\n_Resolvi o seu problema?_ Responda *Sim* ou *Não*.';

// Grupos, canais e status não são atendimento 1:1 e nunca devem entrar no fluxo.
const isIgnorableChat = (id) =>
    typeof id !== 'string' ||
    id.endsWith('@g.us') ||
    id.endsWith('@newsletter') ||
    id.endsWith('@broadcast');

const isValidCpf = (v) => v.replace(/\D/g, '').length === 11;

module.exports = {
    semAcento,
    ragSemResposta,
    dizSim,
    dizNao,
    PERGUNTA_VERIFICACAO,
    isIgnorableChat,
    isValidCpf,
};
