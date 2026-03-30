from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any
from urllib import error, request


DEFAULT_INPUT = Path(__file__).resolve().parent / "duvidas_frequentes_rotulado.csv"
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent


def call_vector_api(base_url: str, route: str, raw_text: str, timeout: int) -> dict[str, Any]:
    payload = json.dumps({"raw_text": raw_text}).encode("utf-8")
    url = f"{base_url.rstrip('/')}{route}"
    req = request.Request(
        url=url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with request.urlopen(req, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} calling {url}: {detail}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Failed calling {url}: {exc.reason}") from exc

    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON response from {url}: {body[:200]}") from exc

    if "vector" not in data:
        raise RuntimeError(f"Response from {url} has no 'vector' field: {data}")

    return data


def vectorize_csv(
    input_csv: Path,
    output_csv: Path,
    base_url: str,
    route: str,
    timeout: int,
) -> None:
    with input_csv.open("r", encoding="utf-8", newline="") as src:
        reader = csv.DictReader(src, delimiter=";")
        required_columns = {"item", "classe", "texto"}
        if not required_columns.issubset(set(reader.fieldnames or [])):
            raise RuntimeError(
                "Input CSV must contain columns: item;classe;texto"
            )

        rows: list[dict[str, str]] = []
        for index, row in enumerate(reader, start=1):
            text = (row.get("texto") or "").strip()
            if not text:
                raise RuntimeError(f"Empty texto at row {index}.")

            response = call_vector_api(base_url, route, text, timeout)
            rows.append(
                {
                    "item": row.get("item", ""),
                    "classe": row.get("classe", ""),
                    "vetor": json.dumps(response.get("vector", []), ensure_ascii=False),
                }
            )

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", encoding="utf-8", newline="") as dst:
        writer = csv.DictWriter(
            dst,
            fieldnames=[
                "item",
                "classe",
                "vetor",
            ],
            delimiter=";",
        )
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Vectoriza a coluna 'texto' do CSV de duvidas usando as rotas "
            "/api/w2vec e /api/fasttext."
        )
    )
    parser.add_argument(
        "--base-url",
        default="http://localhost:8001",
        help="Base URL do servico PLN (padrao: http://localhost:8001)",
    )
    parser.add_argument(
        "--input-csv",
        type=Path,
        default=DEFAULT_INPUT,
        help="Arquivo CSV de entrada (padrao: duvidas_frequentes_rotulado.csv)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Pasta de saida para os CSVs vetorizados",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Timeout em segundos para cada requisicao HTTP",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    input_csv: Path = args.input_csv
    output_dir: Path = args.output_dir
    base_url: str = args.base_url
    timeout: int = args.timeout

    if not input_csv.exists():
        print(f"Arquivo de entrada nao encontrado: {input_csv}", file=sys.stderr)
        return 1

    w2vec_output = output_dir / "duvidas_frequentes_vetorizado_w2vec.csv"
    fasttext_output = output_dir / "duvidas_frequentes_vetorizado_fasttext.csv"

    try:
        vectorize_csv(input_csv, w2vec_output, base_url, "/api/w2vec", timeout)
        print(f"W2Vec concluido: {w2vec_output}")

        vectorize_csv(input_csv, fasttext_output, base_url, "/api/fasttext", timeout)
        print(f"FastText concluido: {fasttext_output}")
    except Exception as exc:  # noqa: BLE001
        print(f"Erro: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
