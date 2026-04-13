from pydantic import BaseModel, Field


class PreprocessingRequest(BaseModel):
    raw_text: str = Field(..., min_length=1, description="Text to vectorize")


class ItemSimilarity(BaseModel):
    item: int
    classe: str
    similarity: float


class PreprocessingResponse(BaseModel):
    normalized_text: str
    tokens: list[str]
    vector_size: int
    vector: list[float]
    item_similarities: list[ItemSimilarity]
    predicted_class: str | None
    class_response: str


class ClassResponseOnly(BaseModel):
    class_response: str
