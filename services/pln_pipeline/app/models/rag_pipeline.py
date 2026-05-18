from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
import hashlib
import json
import re
import unicodedata

import numpy as np


@dataclass
class RAGConfig:
    pdf_path: Path
    cache_dir: Path
    chunk_size: int = 500
    chunk_overlap: int = 80
    emb_model_name: str = "sentence-transformers/all-MiniLM-L6-v2"
    llm_model_name: str = "unicamp-dl/ptt5-large-portuguese-vocab"
    top_k: int = 4
    max_input_tokens: int = 512
    reserved_for_prompt_tokens: int = 180
    max_new_tokens: int = 256
    do_sample: bool = False

    def __post_init__(self) -> None:
        self.pdf_path = Path(self.pdf_path)
        self.cache_dir = Path(self.cache_dir)


@dataclass
class RAGChunk:
    text: str
    source: str
    page: int


@dataclass
class RAGHit:
    chunk: RAGChunk
    score: float


def normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    normalized = normalized.lower()
    return normalized


def minimal_cleanup(text: str) -> str:
    text = text.replace("\r", "\n")
    text = text.replace("\t", " ")
    text = re.sub(r"\s*\n\s*", " ", text)
    text = re.sub(r"[ \u00A0]+", " ", text)
    return text.strip()


def split_text(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    if chunk_size <= 0:
        return []
    if chunk_overlap < 0:
        chunk_overlap = 0
    if chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap must be smaller than chunk_size")

    text = text.strip()
    if not text:
        return []

    chunks: list[str] = []
    start = 0
    text_len = len(text)

    while start < text_len:
        end = min(text_len, start + chunk_size)
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end == text_len:
            break
        start = max(0, end - chunk_overlap)

    return chunks


FAQ_ENTRY_PATTERN = re.compile(
    r"(?:^|\s)\d+\s*-\s*(?P<question>.+?)\s*R\s*:\s*(?P<answer>.+?)(?=\s*\d+\s*-\s*|$)",
    re.IGNORECASE,
)


def extract_faq_entries(text: str) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for match in FAQ_ENTRY_PATTERN.finditer(text):
        question = match.group("question").strip()
        answer = match.group("answer").strip()
        if question and answer:
            entries.append((question, answer))
    return entries


def extract_pages_with_metadata(pdf_path: Path) -> list[tuple[str, int]]:
    from pypdf import PdfReader

    reader = PdfReader(str(pdf_path))
    pages: list[tuple[str, int]] = []

    for page_number, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        pages.append((minimal_cleanup(text), page_number))

    return pages


def build_chunks_from_pages(
    pages: Iterable[tuple[str, int]],
    chunk_size: int,
    chunk_overlap: int,
    source_name: str,
) -> list[RAGChunk]:
    chunks: list[RAGChunk] = []
    for page_text, page_number in pages:
        faq_entries = extract_faq_entries(page_text)
        if faq_entries:
            for question, answer in faq_entries:
                qa_text = f"Pergunta: {question} Resposta: {answer}"
                chunks.append(
                    RAGChunk(text=qa_text, source=source_name, page=page_number)
                )
            continue

        for chunk_text in split_text(page_text, chunk_size, chunk_overlap):
            chunks.append(
                RAGChunk(text=chunk_text, source=source_name, page=page_number)
            )
    return chunks


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(chunk_size)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def make_fingerprint(pdf_path: Path, config: RAGConfig) -> dict:
    return {
        "pdf_path": str(pdf_path),
        "pdf_sha256": sha256_file(pdf_path),
        "emb_model_name": config.emb_model_name,
        "chunk_size": config.chunk_size,
        "chunk_overlap": config.chunk_overlap,
        "parser_version": "faq-v1",
    }


def cache_paths(cache_dir: Path) -> dict[str, Path]:
    return {
        "index": cache_dir / "index.faiss",
        "chunks": cache_dir / "chunks.json",
        "config": cache_dir / "config.json",
    }


def load_cached_index(cache_dir: Path, fingerprint: dict) -> tuple[object, list[RAGChunk]] | None:
    paths = cache_paths(cache_dir)
    if not paths["index"].exists() or not paths["chunks"].exists() or not paths["config"].exists():
        return None

    try:
        config_data = json.loads(paths["config"].read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None

    if config_data.get("fingerprint") != fingerprint:
        return None

    try:
        import faiss

        index = faiss.read_index(str(paths["index"]))
        chunk_data = json.loads(paths["chunks"].read_text(encoding="utf-8"))
        chunks = [
            RAGChunk(
                text=item.get("text", ""),
                source=item.get("source", ""),
                page=int(item.get("page", 0) or 0),
            )
            for item in chunk_data
        ]
        return index, chunks
    except Exception:
        return None


def save_cached_index(
    cache_dir: Path,
    index: object,
    chunks: list[RAGChunk],
    fingerprint: dict,
) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    paths = cache_paths(cache_dir)

    import faiss

    faiss.write_index(index, str(paths["index"]))
    paths["chunks"].write_text(
        json.dumps(
            [
                {"text": chunk.text, "source": chunk.source, "page": chunk.page}
                for chunk in chunks
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    paths["config"].write_text(
        json.dumps({"fingerprint": fingerprint}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def format_context_from_hits(
    hits: list[RAGHit],
    tokenizer,
    max_input_tokens: int,
    reserved_for_prompt_tokens: int,
) -> str:
    budget = max(1, max_input_tokens - reserved_for_prompt_tokens)
    parts: list[str] = []

    for hit in hits:
        chunk = hit.chunk
        header = f"[Fonte: {chunk.source} | Pagina: {chunk.page}]"
        parts.append(header + "\n" + chunk.text.strip())

    full_context = "\n\n".join(parts)
    enc = tokenizer(
        full_context,
        add_special_tokens=False,
        truncation=True,
        max_length=budget,
        return_tensors=None,
    )
    return tokenizer.decode(enc["input_ids"], skip_special_tokens=True)


def clean_answer(text: str) -> str:
    cleaned = text.strip()
    cleaned = re.sub(r"^resposta[^:]*:\s*", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def fallback_answer_from_hits(hits: list[RAGHit]) -> str | None:
    for hit in hits:
        text = hit.chunk.text
        match = re.search(r"resposta\s*:\s*(.+)", text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
        match = re.search(r"\bR\s*:\s*(.+)", text)
        if match:
            return match.group(1).strip()
    return None


class RAGPipeline:
    def __init__(self, config: RAGConfig) -> None:
        self.config = config
        self.embedding_model = None
        self.llm_tokenizer = None
        self.llm_model = None
        self.index = None
        self.chunks: list[RAGChunk] = []
        self._prepared = False

    def prepare(self) -> None:
        if self._prepared:
            return

        if not self.config.pdf_path.exists():
            raise ValueError(f"PDF not found: {self.config.pdf_path}")

        self.config.cache_dir.mkdir(parents=True, exist_ok=True)
        self.embedding_model = self._build_embedding_model()
        self.llm_tokenizer, self.llm_model = self._build_llm()

        fingerprint = make_fingerprint(self.config.pdf_path, self.config)
        cached = load_cached_index(self.config.cache_dir, fingerprint)
        if cached is not None:
            self.index, self.chunks = cached
            self._prepared = True
            return

        pages = extract_pages_with_metadata(self.config.pdf_path)
        self.chunks = build_chunks_from_pages(
            pages=pages,
            chunk_size=self.config.chunk_size,
            chunk_overlap=self.config.chunk_overlap,
            source_name=self.config.pdf_path.name,
        )

        embeddings = self._encode_chunks(self.chunks)
        self.index = self._build_faiss_index(embeddings)
        save_cached_index(self.config.cache_dir, self.index, self.chunks, fingerprint)
        self._prepared = True

    def ask(self, question: str, top_k: int | None = None) -> tuple[str, list[RAGHit]]:
        if not question.strip():
            raise ValueError("Question is empty.")

        self.prepare()
        if not self.chunks:
            return "Nao encontrei essa informacao no documento.", []

        k = top_k or self.config.top_k
        k = max(1, min(k, len(self.chunks)))

        query_vector = self._encode_query(question)
        scores, indices = self.index.search(query_vector, k)

        hits: list[RAGHit] = []
        seen_text: set[str] = set()
        for idx, score in zip(indices[0], scores[0]):
            if idx < 0:
                continue
            chunk = self.chunks[idx]
            key = chunk.text
            if key in seen_text:
                continue
            seen_text.add(key)
            hits.append(RAGHit(chunk=chunk, score=float(score)))

        if not hits:
            return "Nao encontrei essa informacao no documento.", []

        context = format_context_from_hits(
            hits,
            tokenizer=self.llm_tokenizer,
            max_input_tokens=self.config.max_input_tokens,
            reserved_for_prompt_tokens=self.config.reserved_for_prompt_tokens,
        )

        prompt = (
            "Use apenas o contexto para responder em portugues. "
            'Se nao houver informacao suficiente, responda: "Nao encontrei essa informacao no documento."\n\n'
            f"CONTEXTO:\n{context}\n\n"
            f"PERGUNTA:\n{question}\n\n"
            "Resposta:"
        )

        inputs = self.llm_tokenizer(
            prompt,
            truncation=True,
            max_length=self.config.max_input_tokens,
            return_tensors="pt",
        )

        import torch

        self.llm_model.eval()
        with torch.no_grad():
            gen_ids = self.llm_model.generate(
                **inputs,
                max_new_tokens=self.config.max_new_tokens,
                do_sample=self.config.do_sample,
            )

        answer = self.llm_tokenizer.decode(gen_ids[0], skip_special_tokens=True)
        answer = clean_answer(answer)

        if not answer:
            fallback = fallback_answer_from_hits(hits)
            if fallback:
                return fallback, hits

        return answer, hits

    def _build_embedding_model(self):
        from sentence_transformers import SentenceTransformer

        return SentenceTransformer(self.config.emb_model_name, device="cpu")

    def _build_llm(self):
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(self.config.llm_model_name)
        model = AutoModelForSeq2SeqLM.from_pretrained(self.config.llm_model_name)
        return tokenizer, model

    def _encode_chunks(self, chunks: list[RAGChunk]) -> np.ndarray:
        if not chunks:
            dim = self.embedding_model.get_sentence_embedding_dimension()
            return np.zeros((0, dim), dtype=np.float32)

        texts = [normalize_text(chunk.text) for chunk in chunks]
        embeddings = self.embedding_model.encode(
            texts,
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        return embeddings.astype(np.float32)

    def _encode_query(self, question: str) -> np.ndarray:
        text = normalize_text(question)
        embeddings = self.embedding_model.encode(
            [text],
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        return embeddings.astype(np.float32)

    def _build_faiss_index(self, embeddings: np.ndarray):
        import faiss

        if embeddings.size:
            dim = int(embeddings.shape[1])
        else:
            dim = int(self.embedding_model.get_sentence_embedding_dimension())

        index = faiss.IndexFlatIP(dim)
        if embeddings.size:
            index.add(embeddings)
        return index
