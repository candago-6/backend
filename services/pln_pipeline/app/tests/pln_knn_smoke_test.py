from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from urllib import error, request


DEFAULT_CORPUS_PATH = (
    Path(__file__).resolve().parents[1]
    / "utils"
    / "duvidas_frequentes.txt"
)

QUESTION_RE = re.compile(r"^\s*(\d+)\s*[-\.)]\s*(.+)$")


def extract_numbered_questions(corpus_path: Path) -> list[tuple[int, str]]:
    if not corpus_path.exists():
        raise FileNotFoundError(f"Corpus not found: {corpus_path}")

    questions: list[tuple[int, str]] = []
    for raw_line in corpus_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        match = QUESTION_RE.match(line)
        if not match:
            continue

        item_id = int(match.group(1))
        question_text = match.group(2).strip()
        if question_text:
            questions.append((item_id, question_text))

    return questions


def call_api(base_url: str, route: str, question: str, timeout: int) -> dict:
    payload = json.dumps({"raw_text": question}).encode("utf-8")
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

    return json.loads(body)


def run_smoke_test(base_url: str, route: str, questions: list[tuple[int, str]], limit: int) -> None:
    selected_questions = questions[:limit] if limit > 0 else questions
    if not selected_questions:
        print("No numbered questions found in corpus.")
        return

    start_time = time.perf_counter()
    total_requests = 0
    success_count = 0
    rejection_count = 0
    latency_sum = 0.0

    for item_id, question in selected_questions:
        request_start = time.perf_counter()
        print(f"\n[item {item_id}] {question}")
        try:
            response = call_api(base_url, route, question, timeout=30)
        except Exception as exc:
            total_requests += 1
            latency_sum += time.perf_counter() - request_start
            print("error:", exc)
            continue

        total_requests += 1
        request_latency = time.perf_counter() - request_start
        latency_sum += request_latency

        predicted_class = response.get("predicted_class")
        class_response = response.get("class_response")
        if predicted_class is None:
            rejection_count += 1
        else:
            success_count += 1

        print("predicted_class:", predicted_class)
        print("class_response:", class_response)
        print(f"request_latency_s: {request_latency:.3f}")

        similarities = response.get("item_similarities") or []
        if similarities:
            print("top_neighbors:")
            for neighbor in similarities[:3]:
                rank = neighbor.get("rank")
                classe = neighbor.get("classe")
                similarity = neighbor.get("similarity")
                print(f"  - rank={rank} class={classe} similarity={similarity}")
        else:
            print("top_neighbors: []")

    total_elapsed = time.perf_counter() - start_time
    average_latency = latency_sum / total_requests if total_requests else 0.0
    success_rate = (success_count / total_requests) * 100.0 if total_requests else 0.0
    rejection_rate = (rejection_count / total_requests) * 100.0 if total_requests else 0.0

    print("\n=== Route performance ===")
    print("route:", route)
    print("questions_tested:", total_requests)
    print("answered:", success_count)
    print("rejected:", rejection_count)
    print(f"success_rate_percent: {success_rate:.1f}")
    print(f"rejection_rate_percent: {rejection_rate:.1f}")
    print(f"average_latency_s: {average_latency:.3f}")
    print(f"total_elapsed_s: {total_elapsed:.3f}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Smoke test for PLN KNN routes using numbered questions from duvidas_frequentes.txt."
    )
    parser.add_argument(
        "--base-url",
        default="http://localhost:8001",
        help="Base URL of the PLN service.",
    )
    parser.add_argument(
        "--route",
        choices=["/api/w2vec/knn", "/api/fasttext/knn"],
        default="/api/fasttext/knn",
        help="KNN route to exercise.",
    )
    parser.add_argument(
        "--corpus-path",
        type=Path,
        default=DEFAULT_CORPUS_PATH,
        help="Path to duvidas_frequentes.txt.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Number of questions to test (0 means all numbered questions).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    questions = extract_numbered_questions(args.corpus_path)
    print(f"Loaded {len(questions)} numbered questions from {args.corpus_path}")
    run_smoke_test(args.base_url, args.route, questions, args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
