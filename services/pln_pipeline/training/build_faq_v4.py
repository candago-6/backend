"""Merge faq_dataset_v3.json with new_questions_v4.json -> faq_dataset_v4.json.

Keeps each intent's answer, combines existing + new questions (existing first),
de-duplicates case-insensitively (preserving the first occurrence), and reports counts.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
V3 = HERE / "faq_dataset_v3.json"
NEW = HERE / "new_questions_v4.json"
OUT = HERE / "faq_dataset_v4.json"

v3 = json.loads(V3.read_text(encoding="utf-8"))
new = json.loads(NEW.read_text(encoding="utf-8"))

missing = [e["intent"] for e in v3 if e["intent"] not in new]
extra = [k for k in new if k not in {e["intent"] for e in v3}]
if missing:
    sys.exit(f"ERRO: intents sem novas perguntas: {missing}")
if extra:
    sys.exit(f"ERRO: chaves em new que nao existem no v3: {extra}")

out = []
total = 0
counts = []
for entry in v3:
    seen = set()
    merged = []
    for q in list(entry["questions"]) + list(new[entry["intent"]]):
        key = " ".join(q.lower().split())
        if key in seen:
            continue
        seen.add(key)
        merged.append(q)
    out.append({"intent": entry["intent"], "answer": entry["answer"], "questions": merged})
    counts.append((entry["intent"], len(merged)))
    total += len(merged)

OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

mn = min(c for _, c in counts)
mx = max(c for _, c in counts)
print(f"OK -> {OUT.name}")
print(f"intents={len(out)} | total questions={total} | mean={total/len(out):.1f} | min={mn} max={mx}")
low = [(i, c) for i, c in counts if c < 35]
if low:
    print("intents abaixo de 35:")
    for i, c in low:
        print(f"  {c:3d}  {i}")
