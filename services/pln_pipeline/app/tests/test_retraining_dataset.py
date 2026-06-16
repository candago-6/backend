from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import main


class RetrainingDatasetEndpointTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dataset_path = Path(self.temp_dir.name) / "retraining_dataset.json"
        self.client = TestClient(main.app)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def post_sample(self, payload: dict[str, str]):
        with patch.object(main, "RETRAINING_DATASET_PATH", self.dataset_path):
            return self.client.post("/api/retraining-dataset", json=payload)

    def read_dataset(self) -> list[dict[str, str]]:
        return json.loads(self.dataset_path.read_text(encoding="utf-8"))

    def test_frontend_can_send_question_and_answer_to_populate_retraining_dataset(self) -> None:
        payload = {
            "question": "Como cancelar uma cobrança indevida no cartão?",
            "answer": "Procure o fornecedor e registre reclamação com os comprovantes.",
        }

        response = self.post_sample(payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "message": "Registro salvo no dataset de re-treinamento.",
                "total_records": 1,
                "record": payload,
            },
        )
        self.assertEqual(self.read_dataset(), [payload])

    def test_retraining_dataset_appends_new_records_without_losing_existing_data(self) -> None:
        existing_record = {
            "question": "Pergunta já revisada",
            "answer": "Resposta já revisada",
        }
        self.dataset_path.write_text(
            json.dumps([existing_record], ensure_ascii=False),
            encoding="utf-8",
        )
        new_record = {
            "question": "Produto chegou com defeito, o que faço?",
            "answer": "Guarde a nota fiscal e acione a garantia junto ao fornecedor.",
        }

        response = self.post_sample(new_record)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total_records"], 2)
        self.assertEqual(self.read_dataset(), [existing_record, new_record])

    def test_retraining_dataset_rejects_blank_question_or_answer(self) -> None:
        blank_question = self.post_sample({"question": "   ", "answer": "Resposta válida"})
        blank_answer = self.post_sample({"question": "Pergunta válida", "answer": "   "})

        self.assertEqual(blank_question.status_code, 422)
        self.assertEqual(blank_answer.status_code, 422)
        self.assertFalse(self.dataset_path.exists())

    def test_retraining_dataset_reports_invalid_existing_json(self) -> None:
        self.dataset_path.write_text("{invalid-json", encoding="utf-8")

        response = self.post_sample(
            {
                "question": "Pergunta válida",
                "answer": "Resposta válida",
            }
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"detail": "Retraining dataset JSON is invalid."})


if __name__ == "__main__":
    unittest.main()
