from fastapi import FastAPI, HTTPException
from pathlib import Path

from app.models.fastText_pipe import FastTextPipeline
from app.models.w2vec_pipe import W2VPipeline
from app.models.schemas import PreprocessingRequest, PreprocessingResponse

app = FastAPI(title="PLN Pipeline Service", version="0.1.0")

DEFAULT_TRAINING_CORPUS_PATH = (
    Path(__file__).resolve().parent / "utils" / "duvidas_frequentes.txt"
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


@app.get("/api/health")
def health_check() -> dict[str, str]:
    return {"status": "ok", "service": "pln-pipeline"}


@app.post("/api/w2vec", response_model=PreprocessingResponse)
def preprocessing_w2vec(payload: PreprocessingRequest) -> PreprocessingResponse:
    pipeline = W2VPipeline()

    try:
        pipeline.fit(load_default_training_corpus())
        vector = pipeline.text_to_vector(payload.raw_text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return PreprocessingResponse(
        normalized_text=pipeline.normalize_text(payload.raw_text),
        tokens=pipeline.tokenize(payload.raw_text),
        vector_size=len(vector),
        vector=vector.tolist(),
    )


@app.post("/api/fasttext", response_model=PreprocessingResponse)
def preprocessing_fasttext(payload: PreprocessingRequest) -> PreprocessingResponse:
    pipeline = FastTextPipeline()

    try:
        pipeline.fit(load_default_training_corpus())
        vector = pipeline.text_to_vector(payload.raw_text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return PreprocessingResponse(
        normalized_text=pipeline.normalize_text(payload.raw_text),
        tokens=pipeline.tokenize(payload.raw_text),
        vector_size=len(vector),
        vector=vector.tolist(),
    )
