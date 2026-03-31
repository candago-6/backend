from __future__ import annotations
from typing import Iterable

import numpy as np
from gensim.models import Word2Vec

from app.models.preprocessing import BasePreprocessingPipeline, PipelineConfig


class W2VPipeline(BasePreprocessingPipeline):
    """Word2Vec pipeline over shared preprocessing behavior."""

    def __init__(self, config: PipelineConfig | None = None) -> None:
        super().__init__(config)
        self.model: Word2Vec | None = None

    def fit(self, texts: Iterable[str]) -> None:
        tokenized_corpus = [self.preprocess_tokens(text) for text in texts]
        tokenized_corpus = [tokens for tokens in tokenized_corpus if tokens]
        if not tokenized_corpus:
            raise ValueError("Training corpus is empty after preprocessing.")

        self.model = Word2Vec(
            sentences=tokenized_corpus,
            vector_size=self.config.vector_size,
            window=self.config.window,
            min_count=self.config.min_count,
            workers=self.config.workers,
            epochs=self.config.epochs,
            seed=self.config.seed,
        )

    def _require_model(self) -> Word2Vec:
        if self.model is None:
            raise RuntimeError("Word2Vec model not trained. Call fit() first.")
        return self.model

    def text_to_vector(self, text: str) -> np.ndarray:
        model = self._require_model()
        tokens = self.preprocess_tokens(text)

        vectors = [model.wv[token] for token in tokens if token in model.wv]
        if not vectors:
            return np.zeros(self.config.vector_size, dtype=np.float32)

        return np.mean(vectors, axis=0).astype(np.float32)
