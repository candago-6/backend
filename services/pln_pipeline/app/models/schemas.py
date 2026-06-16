from pydantic import BaseModel, Field, field_validator


class PreprocessingRequest(BaseModel):
    raw_text: str = Field(..., min_length=1, description="Text to vectorize")


class ItemSimilarity(BaseModel):
    item: int
    classe: str
    similarity: float
    rank: int | None = None


class PreprocessingResponse(BaseModel):
    normalized_text: str
    tokens: list[str]
    vector_size: int
    vector: list[float]
    item_similarities: list[ItemSimilarity]
    predicted_class: str | None
    class_response: str
    is_fallback: bool = False


class PreprocessingSummaryResponse(BaseModel):
    normalized_text: str
    item_similarities: list[ItemSimilarity]
    predicted_class: str | None
    class_response: str
    is_fallback: bool = False


class ClassResponseOnly(BaseModel):
    class_response: str
    is_fallback: bool = False


class RAGRequest(BaseModel):
    question: str = Field(..., min_length=1, description="User question")
    top_k: int | None = Field(
        None,
        ge=1,
        le=10,
        description="Number of chunks to retrieve",
    )


class RAGSource(BaseModel):
    source: str
    page: int
    score: float
    snippet: str


class RAGResponse(BaseModel):
    answer: str
    sources: list[RAGSource]


class RetrainingDatasetRequest(BaseModel):
    question: str = Field(..., min_length=1, description="Question sent by the frontend")
    answer: str = Field(..., min_length=1, description="Expected answer for retraining")

    @field_validator("question", "answer")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Field cannot be blank")
        return value


class RetrainingDatasetRecord(BaseModel):
    question: str
    answer: str


class RetrainingDatasetResponse(BaseModel):
    message: str
    total_records: int
    record: RetrainingDatasetRecord
