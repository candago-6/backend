from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
import hashlib
import json
import os
import re
import unicodedata

import numpy as np


@dataclass
class RemoteRAGConfig:
    pdf_path: Path
    cache_dir: Path
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    generation_model: str = "gemini-2.5-flash"
    generation_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    generation_api_key_env: str = "GEMINI_API_KEY"
    top_k: int = 8
    min_score: float = 0.2
    max_context_chars: int = 8000

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
    return normalized.lower()


def minimal_cleanup(text: str) -> str:
    text = text.replace("\r", "\n")
    text = text.replace("\t", " ")
    text = re.sub(r"\s*\n\s*", " ", text)
    text = re.sub(r"[ \u00A0]+", " ", text)
    return text.strip()


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
    source_name: str,
    chunk_size: int = 800,
    chunk_overlap: int = 120,
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


def make_fingerprint(pdf_path: Path, config: RemoteRAGConfig) -> dict:
    return {
        "pdf_path": str(pdf_path),
        "pdf_sha256": sha256_file(pdf_path),
        "embedding_model": config.embedding_model,
        "parser_version": "faq-remote-v1",
    }


def cache_paths(cache_dir: Path) -> dict[str, Path]:
    return {
        "index": cache_dir / "index.faiss",
        "chunks": cache_dir / "chunks.json",
        "config": cache_dir / "config.json",
    }


def load_cached_index(
    cache_dir: Path,
    fingerprint: dict,
) -> tuple[object, list[RAGChunk]] | None:
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


def format_context(
    hits: list[RAGHit],
    max_context_chars: int,
) -> str:
    blocks: list[str] = []
    total = 0
    for idx, hit in enumerate(hits, start=1):
        page_str = f"p.{hit.chunk.page}" if hit.chunk.page else "p.?"
        header = f"[Fonte {idx} | {page_str} | {hit.chunk.source}]"
        block = header + "\n" + hit.chunk.text
        if total + len(block) > max_context_chars:
            break
        blocks.append(block)
        total += len(block)
    return "\n\n".join(blocks)


class RemoteRAGPipeline:
    def __init__(self, config: RemoteRAGConfig) -> None:
        self.config = config
        self.index = None
        self.chunks: list[RAGChunk] = []
        self._prepared = False
        self._generation_client = None
        self._embedding_model = None

    def prepare(self) -> None:
        if self._prepared:
            return

        if not self.config.pdf_path.exists():
            raise ValueError(f"PDF not found: {self.config.pdf_path}")

        self.config.cache_dir.mkdir(parents=True, exist_ok=True)
        self._generation_client = self._build_generation_client()
        self._embedding_model = self._build_embedding_model()

        fingerprint = make_fingerprint(self.config.pdf_path, self.config)
        cached = load_cached_index(self.config.cache_dir, fingerprint)
        if cached is not None:
            self.index, self.chunks = cached
            self._prepared = True
            return

        pages = extract_pages_with_metadata(self.config.pdf_path)
        self.chunks = build_chunks_from_pages(
            pages=pages,
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
            score = float(score)
            if score < self.config.min_score:
                continue
            chunk = self.chunks[idx]
            if chunk.text in seen_text:
                continue
            seen_text.add(chunk.text)
            hits.append(RAGHit(chunk=chunk, score=score))

        if not hits:
            return "Nao encontrei essa informacao no documento.", []

        context = format_context(hits, max_context_chars=self.config.max_context_chars)
        instructions = (
            "Voce e um assistente de perguntas e respostas. "
            "Responda em portugues de forma objetiva. "
            "Use apenas o contexto fornecido. "
            "Se nao houver informacao suficiente, responda: "
            '"Nao encontrei essa informacao no documento."'
        )
        user_input = (
            f"CONTEXTO:\n{context}\n\n"
            f"PERGUNTA:\n{question}\n\n"
            "RESPOSTA:"
        )

        resp = self._generation_client.chat.completions.create(
            model=self.config.generation_model,
            messages=[
                {"role": "system", "content": instructions},
                {"role": "user", "content": user_input},
            ],
            temperature=0.0,
        )

        answer = clean_answer(resp.choices[0].message.content or "")
        if not answer:
            fallback = fallback_answer_from_hits(hits)
            if fallback:
                return fallback, hits

        return answer, hits

    def _build_generation_client(self):
        from openai import OpenAI

        api_key = os.getenv(self.config.generation_api_key_env)
        if not api_key:
            raise ValueError(
                f"{self.config.generation_api_key_env} is not set. "
                "Configure it in the .env file."
            )
        return OpenAI(
            api_key=api_key,
            base_url=self.config.generation_base_url,
        )

    def _build_embedding_model(self):
        from sentence_transformers import SentenceTransformer

        return SentenceTransformer(self.config.embedding_model, device="cpu")

    def _encode_chunks(self, chunks: list[RAGChunk]) -> np.ndarray:
        if not chunks:
            dim = self._embedding_model.get_sentence_embedding_dimension()
            return np.zeros((0, dim), dtype=np.float32)

        texts = [normalize_text(chunk.text) for chunk in chunks]
        return self._embed_texts(texts)

    def _encode_query(self, question: str) -> np.ndarray:
        text = normalize_text(question)
        return self._embed_texts([text])

    def _embed_texts(self, texts: list[str]) -> np.ndarray:
        embeddings = self._embedding_model.encode(
            texts,
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        return embeddings.astype(np.float32)

    def _build_faiss_index(self, embeddings: np.ndarray):
        import faiss

        dim = (
            int(embeddings.shape[1])
            if embeddings.size
            else int(self._embedding_model.get_sentence_embedding_dimension())
        )
        index = faiss.IndexFlatIP(dim)
        if embeddings.size:
            index.add(embeddings)
        return index
