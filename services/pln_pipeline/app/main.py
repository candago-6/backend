import csv
import json
from fastapi import FastAPI, HTTPException
from pathlib import Path

import numpy as np

from app.models.fastText_pipe import FastTextPipeline
from app.models.w2vec_pipe import W2VPipeline
from app.models.schemas import ClassResponseOnly, ItemSimilarity, PreprocessingRequest

app = FastAPI(title="PLN Pipeline Service", version="0.1.0")

DEFAULT_TRAINING_CORPUS_PATH = (
    Path(__file__).resolve().parent / "utils" / "duvidas_frequentes.txt"
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


@app.get("/api/health")
def health_check() -> dict[str, str]:
    return {"status": "ok", "service": "pln-pipeline"}


@app.post("/api/w2vec", response_model=ClassResponseOnly)
def preprocessing_w2vec(payload: PreprocessingRequest) -> ClassResponseOnly:
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
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return ClassResponseOnly(
        class_response=class_response,
    )


@app.post("/api/fasttext", response_model=ClassResponseOnly)
def preprocessing_fasttext(payload: PreprocessingRequest) -> ClassResponseOnly:
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
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return ClassResponseOnly(
        class_response=class_response,
    )
