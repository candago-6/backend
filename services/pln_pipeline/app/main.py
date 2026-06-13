import csv
import json
import os
from fastapi import FastAPI, HTTPException, Query
from pathlib import Path

from dotenv import load_dotenv

import numpy as np

from app.models.distilbert import DistilBertConfig, DistilBertPipeline
from app.models.fastText_pipe import FastTextPipeline
from app.models.rag_pipeline import RAGConfig, RAGPipeline
from app.models.rag_remote import RemoteRAGConfig, RemoteRAGPipeline
from app.models.w2vec_pipe import W2VPipeline
from app.models.schemas import (
    ClassResponseOnly,
    ItemSimilarity,
    PreprocessingRequest,
    PreprocessingResponse,
    PreprocessingSummaryResponse,
    RAGRequest,
    RAGResponse,
    RAGSource,
)

app = FastAPI(title="PLN Pipeline Service", version="0.1.0")

load_dotenv()

DEFAULT_TRAINING_CORPUS_PATH = (
    Path(__file__).resolve().parent / "utils" / "duvidas_frequentes_clean.txt"
)
DEFAULT_W2VEC_VECTORIZED_PATH = (
    Path(__file__).resolve().parent / "utils" / "duvidas_frequentes_vetorizado_w2vec.csv"
)
DEFAULT_FASTTEXT_VECTORIZED_PATH = (
    Path(__file__).resolve().parent / "utils" / "duvidas_frequentes_vetorizado_fasttext.csv"
)
DEFAULT_ITEM_RESPONSES_PATH = (
    Path(__file__).resolve().parent / "utils" / "item_responses.json"
)
DEFAULT_FAQ_PDF_PATH = Path(__file__).resolve().parent / "utils" / "faq_fonte.pdf"
DEFAULT_RAG_CACHE_DIR = (
    Path(__file__).resolve().parent / "utils" / "faq_fonte_rag_index"
)
DEFAULT_REMOTE_RAG_CACHE_DIR = (
    Path(__file__).resolve().parent / "utils" / "faq_fonte_rag_remote_index"
)
DEFAULT_DISTILBERT_MODEL_PATH = Path(__file__).resolve().parent / "faq_model"
DEFAULT_DISTILBERT_DATASET_PATH = (
    Path(__file__).resolve().parent / "lm_datasets" / "distilbert_dataset.json"
)

KNN_MIN_TOP_SIMILARITY = 0.18
KNN_MIN_TOP_MARGIN = 0.001
KNN_MIN_CLASS_VOTE_RATIO = 1.0 / 3.0

RAG_PIPELINE: RAGPipeline | None = None
REMOTE_RAG_PIPELINE: RemoteRAGPipeline | None = None
DISTILBERT_PIPELINE: DistilBertPipeline | None = None


def load_default_training_corpus() -> list[str]:
    if not DEFAULT_TRAINING_CORPUS_PATH.exists():
        raise ValueError("Default training corpus file not found.")

    lines = [
        line.strip()
        for line in DEFAULT_TRAINING_CORPUS_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not lines:
        raise ValueError("Default training corpus is empty.")
    return lines


def load_item_reference_vectors(
    vectorized_csv_path: Path,
    expected_vector_size: int,
) -> list[tuple[int, str, np.ndarray]]:
    if not vectorized_csv_path.exists():
        raise ValueError(f"Vectorized class file not found: {vectorized_csv_path.name}")

    item_vectors: list[tuple[int, str, np.ndarray]] = []
    with vectorized_csv_path.open("r", encoding="utf-8", newline="") as src:
        reader = csv.DictReader(src, delimiter=";")
        for row in reader:
            item_raw = (row.get("item") or "").strip()
            class_name = (row.get("classe") or "").strip()
            vector_raw = (row.get("vetor") or "").strip()
            if not item_raw or not class_name or not vector_raw:
                continue

            try:
                item_id = int(item_raw)
                vector_data = json.loads(vector_raw)
                vector_np = np.array(vector_data, dtype=np.float32)
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                raise ValueError(
                    f"Invalid vector row in {vectorized_csv_path.name} for item '{item_raw}'."
                ) from exc

            if vector_np.shape != (expected_vector_size,):
                continue

            item_vectors.append((item_id, class_name, vector_np))

    if not item_vectors:
        return []

    return item_vectors


def load_item_responses() -> dict[str, str]:
    if not DEFAULT_ITEM_RESPONSES_PATH.exists():
        raise ValueError("Item responses file not found.")

    try:
        data = json.loads(DEFAULT_ITEM_RESPONSES_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid item responses JSON file.") from exc

    if not isinstance(data, dict):
        raise ValueError("Item responses JSON must be an object.")

    return {str(key): str(value) for key, value in data.items()}


def calculate_class_similarities(
    pipeline: W2VPipeline | FastTextPipeline,
    user_vector: np.ndarray,
    item_reference_vectors: list[tuple[int, str, np.ndarray]],
) -> list[ItemSimilarity]:
    similarities = [
        ItemSimilarity(
            item=item_id,
            classe=class_name,
            similarity=pipeline.cosine_similarity(user_vector, item_vector),
        )
        for item_id, class_name, item_vector in item_reference_vectors
    ]
    similarities.sort(key=lambda result: result.similarity, reverse=True)
    return similarities


def resolve_top_item_response(
    item_similarities: list[ItemSimilarity],
    item_responses: dict[str, str],
) -> str:
    if not item_similarities:
        return item_responses.get(
            "default",
            "Nao foi possivel identificar um item com confianca.",
        )

    top_item = str(item_similarities[0].item)
    return item_responses.get(top_item) or item_responses.get(
        "default",
        "Nao foi possivel identificar um item com confianca.",
    )


def resolve_knn_item_response(
    neighbors: list[tuple[int, str, float]],
    predicted_class: str | None,
    item_responses: dict[str, str],
) -> str:
    if not neighbors or predicted_class is None:
        return item_responses.get(
            "default",
            "Nao foi possivel identificar um item com confianca.",
        )

    for item_id, class_name, _ in neighbors:
        if class_name == predicted_class:
            return item_responses.get(str(item_id)) or item_responses.get(
                "default",
                "Nao foi possivel identificar um item com confianca.",
            )

    return item_responses.get(
        "default",
        "Nao foi possivel identificar um item com confianca.",
    )


def normalize_knn_similarities(
    neighbors: list[tuple[int, str, float]],
) -> list[ItemSimilarity]:
    if not neighbors:
        return []

    weights = [max(similarity, 0.0) for _, _, similarity in neighbors]
    total_weight = sum(weights)
    if total_weight <= 0.0:
        normalized_weight = 100.0 / len(neighbors)
        return [
            ItemSimilarity(
                item=item_id,
                classe=class_name,
                similarity=normalized_weight,
                rank=rank,
            )
            for rank, (item_id, class_name, _) in enumerate(neighbors, start=1)
        ]

    return [
        ItemSimilarity(
            item=item_id,
            classe=class_name,
            similarity=(weight / total_weight) * 100.0,
            rank=rank,
        )
        for rank, ((item_id, class_name, _), weight) in enumerate(
            zip(neighbors, weights),
            start=1,
        )
    ]


def is_knn_match_eligible(
    neighbors: list[tuple[int, str, float]],
    predicted_class: str | None,
    normalized_similarities: list[ItemSimilarity],
) -> bool:
    if not neighbors or not normalized_similarities or predicted_class is None:
        return False

    top_similarity = neighbors[0][2]
    second_similarity = neighbors[1][2] if len(neighbors) > 1 else -1.0
    predicted_votes = sum(
        1 for _, class_name, _ in neighbors if class_name == predicted_class
    )
    vote_ratio = predicted_votes / len(neighbors)

    return (
        top_similarity >= KNN_MIN_TOP_SIMILARITY
        and (top_similarity - second_similarity) >= KNN_MIN_TOP_MARGIN
        and vote_ratio >= KNN_MIN_CLASS_VOTE_RATIO
    )


def truncate_snippet(text: str, max_chars: int = 300) -> str:
    snippet = " ".join(text.split())
    if len(snippet) <= max_chars:
        return snippet
    trimmed = snippet[:max_chars].rsplit(" ", 1)[0]
    if not trimmed:
        trimmed = snippet[:max_chars]
    return trimmed + "..."


def get_rag_pipeline() -> RAGPipeline:
    global RAG_PIPELINE
    if RAG_PIPELINE is None:
        config = RAGConfig(
            pdf_path=DEFAULT_FAQ_PDF_PATH,
            cache_dir=DEFAULT_RAG_CACHE_DIR,
        )
        RAG_PIPELINE = RAGPipeline(config)
    return RAG_PIPELINE


def get_rag_remote_pipeline() -> RemoteRAGPipeline:
    global REMOTE_RAG_PIPELINE
    if REMOTE_RAG_PIPELINE is None:
        config = RemoteRAGConfig(
            pdf_path=DEFAULT_FAQ_PDF_PATH,
            cache_dir=DEFAULT_REMOTE_RAG_CACHE_DIR,
        )
        REMOTE_RAG_PIPELINE = RemoteRAGPipeline(config)
    return REMOTE_RAG_PIPELINE


def get_distilbert_pipeline() -> DistilBertPipeline:
    global DISTILBERT_PIPELINE
    if DISTILBERT_PIPELINE is None:
        model_path = Path(
            os.getenv("DISTILBERT_MODEL_PATH", str(DEFAULT_DISTILBERT_MODEL_PATH))
        )
        config = DistilBertConfig(
            model_path=model_path,
            dataset_path=DEFAULT_DISTILBERT_DATASET_PATH,
        )
        DISTILBERT_PIPELINE = DistilBertPipeline(config)
    return DISTILBERT_PIPELINE


@app.get("/api/health")
def health_check() -> dict[str, str]:
    return {"status": "ok", "service": "pln-pipeline"}


@app.post("/api/w2vec", response_model=PreprocessingResponse)
def preprocessing_w2vec(payload: PreprocessingRequest) -> PreprocessingResponse:
    pipeline = W2VPipeline()

    try:
        pipeline.fit(load_default_training_corpus())
        vector = pipeline.text_to_vector(payload.raw_text)
        if DEFAULT_W2VEC_VECTORIZED_PATH.exists():
            item_reference_vectors = load_item_reference_vectors(
                DEFAULT_W2VEC_VECTORIZED_PATH,
                pipeline.config.vector_size,
            )
        else:
            item_reference_vectors = []
        item_similarities = calculate_class_similarities(
            pipeline,
            vector,
            item_reference_vectors,
        )
        item_responses = load_item_responses()
        class_response = resolve_top_item_response(
            item_similarities,
            item_responses,
        )
        predicted_class = item_similarities[0].classe if item_similarities else None
        is_fallback = predicted_class is None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return PreprocessingResponse(
        normalized_text=pipeline.normalize_text(payload.raw_text),
        tokens=pipeline.preprocess_tokens(payload.raw_text),
        vector_size=pipeline.config.vector_size,
        vector=vector.tolist(),
        item_similarities=item_similarities,
        predicted_class=predicted_class,
        class_response=class_response,
        is_fallback=is_fallback,
    )


@app.post("/api/w2vec/knn", response_model=PreprocessingSummaryResponse)
def preprocessing_w2vec_knn(
    payload: PreprocessingRequest,
    k: int = Query(3, ge=1, le=20),
) -> PreprocessingSummaryResponse:
    pipeline = W2VPipeline()

    try:
        pipeline.fit(load_default_training_corpus())
        vector = pipeline.text_to_vector(payload.raw_text)
        if DEFAULT_W2VEC_VECTORIZED_PATH.exists():
            item_reference_vectors = load_item_reference_vectors(
                DEFAULT_W2VEC_VECTORIZED_PATH,
                pipeline.config.vector_size,
            )
        else:
            item_reference_vectors = []
        predicted_class, neighbors = pipeline.knn(
            vector,
            item_reference_vectors,
            k=k,
        )
        item_similarities = normalize_knn_similarities(neighbors)
        item_responses = load_item_responses()
        if not is_knn_match_eligible(neighbors, predicted_class, item_similarities):
            predicted_class = None
            item_similarities = []
            class_response = item_responses.get(
                "default",
                "Nao foi possivel identificar um item com confianca.",
            )
        else:
            class_response = resolve_knn_item_response(
                neighbors,
                predicted_class,
                item_responses,
            )
        is_fallback = predicted_class is None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return PreprocessingSummaryResponse(
        normalized_text=pipeline.normalize_text(payload.raw_text),
        item_similarities=item_similarities,
        predicted_class=predicted_class,
        class_response=class_response,
        is_fallback=is_fallback,
    )


@app.post("/api/fasttext", response_model=PreprocessingResponse)
def preprocessing_fasttext(payload: PreprocessingRequest) -> PreprocessingResponse:
    pipeline = FastTextPipeline()

    try:
        pipeline.fit(load_default_training_corpus())
        vector = pipeline.text_to_vector(payload.raw_text)
        if DEFAULT_FASTTEXT_VECTORIZED_PATH.exists():
            item_reference_vectors = load_item_reference_vectors(
                DEFAULT_FASTTEXT_VECTORIZED_PATH,
                pipeline.config.vector_size,
            )
        else:
            item_reference_vectors = []
        item_similarities = calculate_class_similarities(
            pipeline,
            vector,
            item_reference_vectors,
        )
        item_responses = load_item_responses()
        class_response = resolve_top_item_response(
            item_similarities,
            item_responses,
        )
        predicted_class = item_similarities[0].classe if item_similarities else None
        is_fallback = predicted_class is None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return PreprocessingResponse(
        normalized_text=pipeline.normalize_text(payload.raw_text),
        tokens=pipeline.preprocess_tokens(payload.raw_text),
        vector_size=pipeline.config.vector_size,
        vector=vector.tolist(),
        item_similarities=item_similarities,
        predicted_class=predicted_class,
        class_response=class_response,
        is_fallback=is_fallback,
    )


@app.post("/api/fasttext/knn", response_model=PreprocessingSummaryResponse)
def preprocessing_fasttext_knn(
    payload: PreprocessingRequest,
    k: int = Query(3, ge=1, le=20),
) -> PreprocessingSummaryResponse:
    pipeline = FastTextPipeline()

    try:
        pipeline.fit(load_default_training_corpus())
        vector = pipeline.text_to_vector(payload.raw_text)
        if DEFAULT_FASTTEXT_VECTORIZED_PATH.exists():
            item_reference_vectors = load_item_reference_vectors(
                DEFAULT_FASTTEXT_VECTORIZED_PATH,
                pipeline.config.vector_size,
            )
        else:
            item_reference_vectors = []
        predicted_class, neighbors = pipeline.knn(
            vector,
            item_reference_vectors,
            k=k,
        )
        item_similarities = normalize_knn_similarities(neighbors)
        item_responses = load_item_responses()
        if not is_knn_match_eligible(neighbors, predicted_class, item_similarities):
            predicted_class = None
            item_similarities = []
            class_response = item_responses.get(
                "default",
                "Nao foi possivel identificar um item com confianca.",
            )
        else:
            class_response = resolve_knn_item_response(
                neighbors,
                predicted_class,
                item_responses,
            )
        is_fallback = predicted_class is None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return PreprocessingSummaryResponse(
        normalized_text=pipeline.normalize_text(payload.raw_text),
        item_similarities=item_similarities,
        predicted_class=predicted_class,
        class_response=class_response,
        is_fallback=is_fallback,
    )


@app.post("/api/rag", response_model=RAGResponse)
def rag_answer(payload: RAGRequest) -> RAGResponse:
    if not payload.question.strip():
        raise HTTPException(status_code=400, detail="Question is empty.")

    try:
        pipeline = get_rag_pipeline()
        answer, hits = pipeline.ask(payload.question, top_k=payload.top_k)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    sources = [
        RAGSource(
            source=hit.chunk.source,
            page=hit.chunk.page,
            score=hit.score,
            snippet=truncate_snippet(hit.chunk.text),
        )
        for hit in hits
    ]

    return RAGResponse(answer=answer, sources=sources)


@app.post("/api/rag_remote", response_model=RAGResponse)
def rag_remote_answer(payload: RAGRequest) -> RAGResponse:
    if not payload.question.strip():
        raise HTTPException(status_code=400, detail="Question is empty.")

    try:
        pipeline = get_rag_remote_pipeline()
        answer, hits = pipeline.ask(payload.question, top_k=payload.top_k)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    sources = [
        RAGSource(
            source=hit.chunk.source,
            page=hit.chunk.page,
            score=hit.score,
            snippet=truncate_snippet(hit.chunk.text),
        )
        for hit in hits
    ]

    return RAGResponse(answer=answer, sources=sources)


@app.post("/api/distilbert", response_model=ClassResponseOnly)
def distilbert_answer(payload: PreprocessingRequest) -> ClassResponseOnly:
    if not payload.raw_text.strip():
        raise HTTPException(status_code=400, detail="Text is empty.")

    try:
        pipeline = get_distilbert_pipeline()
        class_response = pipeline.chat(payload.raw_text)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return ClassResponseOnly(class_response=class_response)
