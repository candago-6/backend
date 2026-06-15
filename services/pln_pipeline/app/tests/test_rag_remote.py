from __future__ import annotations

import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np

from app.models.rag_remote import RAGChunk, RAGHit, RemoteRAGConfig, RemoteRAGPipeline


class RemoteRAGPipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = RemoteRAGConfig(
            pdf_path=Path("faq.pdf"),
            cache_dir=Path("cache"),
        )
        self.pipeline = RemoteRAGPipeline(self.config)

    def test_defaults_use_local_embeddings_and_gemini_generation(self) -> None:
        self.assertTrue(self.config.embedding_model.startswith("sentence-transformers/"))
        self.assertEqual(self.config.generation_model, "gemini-2.5-flash")
        self.assertEqual(self.config.generation_api_key_env, "GEMINI_API_KEY")

    def test_embedding_uses_local_sentence_transformer(self) -> None:
        embedding_model = MagicMock()
        embedding_model.encode.return_value = np.array([[0.1, 0.2]], dtype=np.float32)
        self.pipeline._embedding_model = embedding_model

        result = self.pipeline._embed_texts(["duvida"])

        embedding_model.encode.assert_called_once_with(
            ["duvida"],
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        np.testing.assert_array_equal(result, np.array([[0.1, 0.2]], dtype=np.float32))

    @patch("openai.OpenAI")
    def test_generation_client_uses_gemini_configuration(
        self,
        openai_client: MagicMock,
    ) -> None:
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
            self.pipeline._build_generation_client()

        openai_client.assert_called_once_with(
            api_key="test-key",
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        )

    def test_ask_uses_chat_completions(self) -> None:
        hit = RAGHit(RAGChunk("Pergunta: X Resposta: Y", "faq.pdf", 1), 0.9)
        self.pipeline._prepared = True
        self.pipeline.chunks = [hit.chunk]
        self.pipeline.index = MagicMock()
        self.pipeline.index.search.return_value = (
            np.array([[0.9]], dtype=np.float32),
            np.array([[0]], dtype=np.int64),
        )
        self.pipeline._encode_query = MagicMock(
            return_value=np.array([[0.1, 0.2]], dtype=np.float32)
        )
        self.pipeline._generation_client = MagicMock()
        self.pipeline._generation_client.chat.completions.create.return_value = (
            SimpleNamespace(
                choices=[
                    SimpleNamespace(message=SimpleNamespace(content="Resposta: Y"))
                ]
            )
        )

        answer, hits = self.pipeline.ask("X")

        self.assertEqual(answer, "Y")
        self.assertEqual(hits, [hit])
        self.pipeline._generation_client.chat.completions.create.assert_called_once()


if __name__ == "__main__":
    unittest.main()
