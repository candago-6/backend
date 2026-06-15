from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from pathlib import Path
from urllib import error, request


DEFAULT_DATASET_PATH = (
    Path(__file__).resolve().parents[1]
    / "lm_datasets"
    / "faq_dataset_v4.json"
)
DISTILBERT_ROUTE = "/api/distilbert"
DEFAULT_KNN_ROUTE = "/api/fasttext/knn"


@dataclass(frozen=True)
class LabeledQuestion:
    intent: str
    question: str
    expected_answer: str


@dataclass
class RouteMetrics:
    route: str
    matches: int = 0
    fallbacks: int = 0
    errors: int = 0
    latency_sum: float = 0.0


def normalize_answer(answer: object) -> str:
    return " ".join(str(answer or "").split())


def load_labeled_questions(
    dataset_path: Path,
    questions_per_intent: int,
) -> list[LabeledQuestion]:
    if not dataset_path.exists():
        raise FileNotFoundError(f"Dataset not found: {dataset_path}")

    data = json.loads(dataset_path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("Dataset must be a JSON list.")

    labeled_questions: list[LabeledQuestion] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue

        intent = str(entry.get("intent") or "").strip()
        expected_answer = str(entry.get("answer") or "").strip()
        questions = entry.get("questions")
        if not intent or not expected_answer or not isinstance(questions, list):
            continue

        selected = questions[:questions_per_intent] if questions_per_intent > 0 else questions
        labeled_questions.extend(
            LabeledQuestion(
                intent=intent,
                question=str(question).strip(),
                expected_answer=expected_answer,
            )
            for question in selected
            if str(question).strip()
        )

    if not labeled_questions:
        raise ValueError("No labeled questions found in dataset.")
    return labeled_questions


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


def percentage(count: int, total: int) -> float:
    return (count / total) * 100.0 if total else 0.0


def print_route_metrics(metrics: RouteMetrics, total: int) -> None:
    completed = total - metrics.errors
    average_latency = metrics.latency_sum / completed if completed else 0.0
    print(f"\n{metrics.route}")
    print(f"  expected_answer_matches: {metrics.matches}/{total}")
    print(f"  match_percent: {percentage(metrics.matches, total):.1f}")
    print(f"  fallbacks: {metrics.fallbacks}/{total}")
    print(f"  errors: {metrics.errors}/{total}")
    print(f"  average_latency_s: {average_latency:.3f}")


def run_comparison(
    base_url: str,
    knn_route: str,
    questions: list[LabeledQuestion],
    timeout: int,
    verbose: bool,
) -> None:
    distilbert = RouteMetrics(route=DISTILBERT_ROUTE)
    knn = RouteMetrics(route=knn_route)
    agreement_count = 0
    total_start = time.perf_counter()

    for index, labeled in enumerate(questions, start=1):
        responses: dict[str, dict] = {}
        for metrics in (distilbert, knn):
            request_start = time.perf_counter()
            try:
                response = call_api(base_url, metrics.route, labeled.question, timeout)
            except Exception as exc:
                metrics.errors += 1
                if verbose:
                    print(f"[{index}] {metrics.route} error: {exc}")
                continue

            metrics.latency_sum += time.perf_counter() - request_start
            responses[metrics.route] = response
            actual_answer = normalize_answer(response.get("class_response"))
            if actual_answer == normalize_answer(labeled.expected_answer):
                metrics.matches += 1
            if response.get("is_fallback") or actual_answer.startswith(
                "Desculpe, nao encontrei uma resposta"
            ):
                metrics.fallbacks += 1

        distilbert_answer = normalize_answer(
            responses.get(DISTILBERT_ROUTE, {}).get("class_response")
        )
        knn_answer = normalize_answer(
            responses.get(knn_route, {}).get("class_response")
        )
        agreed = bool(distilbert_answer and distilbert_answer == knn_answer)
        agreement_count += int(agreed)

        if verbose:
            expected = normalize_answer(labeled.expected_answer)
            print(
                f"[{index}/{len(questions)}] intent={labeled.intent} "
                f"distilbert_match={distilbert_answer == expected} "
                f"knn_match={knn_answer == expected} agreement={agreed}"
            )

    total = len(questions)
    print("\n=== DistilBERT vs KNN route comparison ===")
    print(f"questions_tested: {total}")
    print_route_metrics(distilbert, total)
    print_route_metrics(knn, total)
    print("\nroute_agreement")
    print(f"  matching_answers: {agreement_count}/{total}")
    print(f"  match_percent: {percentage(agreement_count, total):.1f}")

    delta = percentage(distilbert.matches - knn.matches, total)
    if delta > 0:
        result = f"DistilBERT by {delta:.1f} percentage points"
    elif delta < 0:
        result = f"KNN by {abs(delta):.1f} percentage points"
    else:
        result = "tie"
    print(f"\nresult: {result}")
    print(f"total_elapsed_s: {time.perf_counter() - total_start:.3f}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare DistilBERT and KNN internal routes against labeled FAQ answers."
        )
    )
    parser.add_argument(
        "--base-url",
        default="http://localhost:8001",
        help="Base URL of the running PLN service.",
    )
    parser.add_argument(
        "--knn-route",
        choices=["/api/w2vec/knn", "/api/fasttext/knn"],
        default=DEFAULT_KNN_ROUTE,
        help="KNN route to compare with DistilBERT.",
    )
    parser.add_argument(
        "--dataset-path",
        type=Path,
        default=DEFAULT_DATASET_PATH,
        help="Labeled FAQ dataset containing intent, answer, and questions.",
    )
    parser.add_argument(
        "--questions-per-intent",
        type=int,
        default=1,
        help="Questions sampled per intent (0 means all questions).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Maximum total questions after sampling (0 means no limit).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Timeout in seconds for each route request.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print the comparison result for every question.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    questions = load_labeled_questions(args.dataset_path, args.questions_per_intent)
    if args.limit > 0:
        questions = questions[: args.limit]

    print(f"Loaded {len(questions)} labeled questions from {args.dataset_path}")
    run_comparison(
        base_url=args.base_url,
        knn_route=args.knn_route,
        questions=questions,
        timeout=args.timeout,
        verbose=args.verbose,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
