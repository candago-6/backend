from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path


@dataclass
class DistilBertConfig:
    model_path: Path
    dataset_path: Path
    max_length: int = 96
    confidence_threshold: float = 0.60
    temperature: float = 1.5

    def __post_init__(self) -> None:
        self.model_path = Path(self.model_path)
        self.dataset_path = Path(self.dataset_path)


class DistilBertPipeline:
    def __init__(self, config: DistilBertConfig):
        self.config = config
        self._tokenizer = None
        self._model = None
        self._device = None
        self._idx_to_intent: dict[int, str] = {}
        self._intent_to_answer: dict[str, str] = {}

    def _load(self) -> None:
        if self._model is not None:
            return

        if not self.config.model_path.exists():
            raise ValueError(
                f"DistilBERT model directory not found: {self.config.model_path}"
            )
        if not self.config.dataset_path.exists():
            raise ValueError(
                f"DistilBERT dataset not found: {self.config.dataset_path}"
            )

        try:
            raw_data = json.loads(
                self.config.dataset_path.read_text(encoding="utf-8")
            )
        except json.JSONDecodeError as exc:
            raise ValueError("Invalid DistilBERT dataset JSON file.") from exc

        if not isinstance(raw_data, list) or not raw_data:
            raise ValueError("DistilBERT dataset must be a non-empty list.")

        try:
            intents = [str(entry["intent"]) for entry in raw_data]
            intent_to_answer = {
                str(entry["intent"]): str(entry["answer"]) for entry in raw_data
            }
        except (KeyError, TypeError) as exc:
            raise ValueError(
                "Each DistilBERT dataset entry must contain intent and answer."
            ) from exc

        import torch
        from transformers import (
            DistilBertForSequenceClassification,
            DistilBertTokenizerFast,
        )

        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        tokenizer = DistilBertTokenizerFast.from_pretrained(self.config.model_path)
        model = DistilBertForSequenceClassification.from_pretrained(
            self.config.model_path
        )

        if model.config.num_labels != len(intents):
            raise ValueError(
                "DistilBERT model label count does not match the configured dataset."
            )

        model.to(device)
        model.eval()

        self._idx_to_intent = dict(enumerate(intents))
        self._intent_to_answer = intent_to_answer
        self._device = device
        self._tokenizer = tokenizer
        self._model = model

    def predict(self, question: str) -> tuple[str, str, float]:
        self._load()

        import torch
        import torch.nn.functional as functional

        encoding = self._tokenizer(
            question,
            return_tensors="pt",
            truncation=True,
            padding="max_length",
            max_length=self.config.max_length,
        )
        input_ids = encoding["input_ids"].to(self._device)
        attention_mask = encoding["attention_mask"].to(self._device)

        with torch.no_grad():
            logits = self._model(
                input_ids=input_ids,
                attention_mask=attention_mask,
            ).logits

        probabilities = functional.softmax(
            logits / self.config.temperature,
            dim=-1,
        ).squeeze()
        confidence = probabilities.max().item()
        intent = self._idx_to_intent[probabilities.argmax().item()]
        answer = self._intent_to_answer[intent]
        return intent, answer, confidence

    def chat(self, question: str) -> tuple[str, bool]:
        _, answer, confidence = self.predict(question)
        if confidence < self.config.confidence_threshold:
            print(f"(confianca={confidence:.2%} < "
                f"threshold={self.config.confidence_threshold:.0%}). ")
            return (
                "Desculpe, não encontrei uma resposta. "
                "Tente reformular sua pergunta.",
                True,
            )
        return answer, False
