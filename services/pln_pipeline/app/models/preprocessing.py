from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable

import numpy as np


@dataclass
class PipelineConfig:
	vector_size: int = 100
	window: int = 5
	min_count: int = 1
	workers: int = 1
	epochs: int = 20
	seed: int = 42


class BasePreprocessingPipeline:
	"""Shared text preprocessing and vector post-processing helpers."""

	def __init__(self, config: PipelineConfig | None = None) -> None:
		self.config = config or PipelineConfig()

	def normalize_text(self, text: str) -> str:
		# Remove accents, normalize punctuation and keep alphanumeric tokens.
		text = unicodedata.normalize("NFKD", text)
		text = "".join(ch for ch in text if not unicodedata.combining(ch))
		text = text.lower()
		text = re.sub(r"[^a-z0-9\s]", " ", text)
		text = re.sub(r"\s+", " ", text).strip()
		return text

	def tokenize(self, text: str) -> list[str]:
		normalized = self.normalize_text(text)
		if not normalized:
			return []
		return normalized.split(" ")

	def batch_to_vectors(self, texts: Iterable[str]) -> np.ndarray:
		return np.vstack([self.text_to_vector(text) for text in texts])

	def build_class_reference_vectors(
		self,
		class_samples: dict[str, list[str]],
	) -> dict[str, np.ndarray]:
		"""Create one centroid vector per class based on sample texts."""
		class_vectors: dict[str, np.ndarray] = {}
		for class_name, samples in class_samples.items():
			if not samples:
				class_vectors[class_name] = np.zeros(
					self.config.vector_size,
					dtype=np.float32,
				)
				continue
			vectors = self.batch_to_vectors(samples)
			class_vectors[class_name] = np.mean(vectors, axis=0).astype(np.float32)
		return class_vectors

	def text_to_vector(self, text: str) -> np.ndarray:
		raise NotImplementedError

	@staticmethod
	def cosine_similarity(vector_a: np.ndarray, vector_b: np.ndarray) -> float:
		norm_a = np.linalg.norm(vector_a)
		norm_b = np.linalg.norm(vector_b)
		if norm_a == 0.0 or norm_b == 0.0:
			return 0.0
		return float(np.dot(vector_a, vector_b) / (norm_a * norm_b))
