"""Publish the DistilBERT FAQ model to the Hugging Face Hub.

The ~517MB weights are excluded from git (see .gitignore), so the trained
model is distributed through the hub instead. Run once per new model version:

    pip install huggingface_hub
    hf auth login                 # or export HF_TOKEN=hf_...
    python services/pln_pipeline/training/publish_model.py candago-6/faq-model-v5

Then point the service at it:

    DISTILBERT_MODEL_PATH=candago-6/faq-model-v5
"""

from __future__ import annotations

import argparse
from pathlib import Path

DEFAULT_MODEL_DIR = Path(__file__).resolve().parents[1] / "app" / "faq_model_v5"
REQUIRED_FILES = (
    "config.json",
    "model.safetensors",
    "tokenizer.json",
    "tokenizer_config.json",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "repo_id",
        help='Target repository on the hub, e.g. "candago-6/faq-model-v5".',
    )
    parser.add_argument(
        "--model-dir",
        type=Path,
        default=DEFAULT_MODEL_DIR,
        help=f"Local model directory to upload (default: {DEFAULT_MODEL_DIR}).",
    )
    parser.add_argument(
        "--private",
        action="store_true",
        help="Create the repository as private.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if not args.model_dir.is_dir():
        raise SystemExit(f"Model directory not found: {args.model_dir}")

    missing = [name for name in REQUIRED_FILES if not (args.model_dir / name).exists()]
    if missing:
        raise SystemExit(
            f"Model directory is incomplete, missing: {', '.join(missing)}"
        )

    from huggingface_hub import HfApi

    api = HfApi()
    api.create_repo(args.repo_id, repo_type="model", private=args.private, exist_ok=True)
    api.upload_folder(
        repo_id=args.repo_id,
        folder_path=str(args.model_dir),
        repo_type="model",
        commit_message=f"Upload {args.model_dir.name}",
    )
    print(f"Uploaded {args.model_dir} to https://huggingface.co/{args.repo_id}")


if __name__ == "__main__":
    main()
