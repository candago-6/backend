from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable

import numpy as np


PORTUGUESE_STOPWORDS = {
	"a",
	"ao",
	"aos",
	"as",
	"com",
	"como",
	"da",
	"das",
	"de",
	"dela",
	"dele",
	"deles",
	"do",
	"dos",
	"e",
	"ela",
	"ele",
	"eles",
	"em",
	"entre",
	"era",
	"essa",
	"esse",
	"esta",
	"este",
	"eu",
	"foi",
	"ha",
	"isso",
	"isto",
	"ja",
	"la",
	"lhe",
	"mais",
	"mas",
	"me",
	"meu",
	"minha",
	"na",
	"nas",
	"nem",
	"no",
	"nos",
	"o",
	"os",
	"ou",
	"para",
	"pela",
	"pelo",
	"por",
	"que",
	"se",
	"sem",
	"ser",
	"seu",
	"sua",
	"tambem",
	"te",
	"tem",
	"tenho",
	"ter",
	"um",
	"uma",
	"voce",
	"voces",
}


@dataclass
class PipelineConfig:
	vector_size: int = 200
	window: int = 3
	min_count: int = 2
	workers: int = 1
	epochs: int = 8
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

	def remove_stopwords(self, tokens: Iterable[str]) -> list[str]:
		return [token for token in tokens if token and token not in PORTUGUESE_STOPWORDS]

	def preprocess_tokens(self, text: str) -> list[str]:
		tokens = self.tokenize(text)
		return self.remove_stopwords(tokens)

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

	@staticmethod
	def knn(
		user_vector: np.ndarray,
		item_reference_vectors: Iterable[tuple[int, str, np.ndarray]],
		k: int = 3,
	) -> tuple[str | None, list[tuple[int, str, float]]]:
		if k < 1:
			raise ValueError("k must be >= 1.")

		scored: list[tuple[int, str, float]] = []
		for item_id, class_name, item_vector in item_reference_vectors:
			similarity = BasePreprocessingPipeline.cosine_similarity(
				user_vector,
				item_vector,
			)
			scored.append((item_id, class_name, similarity))

		scored.sort(key=lambda entry: entry[2], reverse=True)
		if not scored:
			return None, []

		neighbors = scored[: min(k, len(scored))]
		class_stats: dict[str, dict[str, float | int]] = {}
		for _, class_name, similarity in neighbors:
			stats = class_stats.setdefault(
				class_name,
				{"count": 0, "sum": 0.0, "best": -1.0},
			)
			stats["count"] += 1
			stats["sum"] += similarity
			if similarity > stats["best"]:
				stats["best"] = similarity

		predicted_class = max(
			class_stats.items(),
			key=lambda entry: (entry[1]["count"], entry[1]["sum"], entry[1]["best"]),
		)[0]
		return predicted_class, neighbors
