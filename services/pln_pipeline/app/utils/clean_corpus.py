from __future__ import annotations

import re
from pathlib import Path
from typing import List


INPUT = Path(__file__).resolve().parent / "duvidas_frequentes.txt"
OUTPUT = Path(__file__).resolve().parent / "duvidas_frequentes_clean.txt"


QUESTION_RE = re.compile(r"^\s*\d+\s*[-\.)]")
R_PREFIX_RE = re.compile(r"^\s*R\s*:\s*", re.IGNORECASE)


def extract_answers(lines: List[str]) -> List[str]:
    answers: List[str] = []
    collecting = False
    current: List[str] = []

    for i, raw in enumerate(lines):
        line = raw.rstrip("\n")
        if line.strip().startswith("##"):
            # header - finish any current answer and skip
            if current:
                answers.append(_finalize(current))
                current = []
            collecting = False
            continue

        if QUESTION_RE.match(line):
            # start of a question; finish previous answer if present
            if current:
                answers.append(_finalize(current))
                current = []
            collecting = True
            # do not include the question text itself
            continue

        if not collecting:
            # skip lines outside question/answer blocks
            continue

        # line belongs to answer block
        if not current:
            # first line of answer: strip leading R: if present
            cleaned = R_PREFIX_RE.sub("", line).strip()
            if cleaned:
                current.append(cleaned)
        else:
            # subsequent answer lines: keep but strip excess whitespace
            if line.strip():
                current.append(line.strip())

    if current:
        answers.append(_finalize(current))

    return answers


def _finalize(lines: List[str]) -> str:
    # join lines into a single cleaned paragraph, collapse whitespace
    joined = " ".join(lines)
    joined = re.sub(r"\s+", " ", joined).strip()
    return joined


def main() -> int:
    if not INPUT.exists():
        print(f"Input not found: {INPUT}")
        return 1

    raw = INPUT.read_text(encoding="utf-8")
    lines = raw.splitlines()
    answers = extract_answers(lines)

    OUT_DIR = OUTPUT.parent
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text("\n".join(answers), encoding="utf-8")
    print(f"Wrote {len(answers)} cleaned answers to {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
