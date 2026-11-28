# Fine-tuning data

**Empty on purpose.** Nothing has been generated yet: the real knowledge base, the teacher model and
the hand checks were not available when the pipeline was written. Files appear here only from the
steps in [`../README.md`](../README.md), run on real data. The scripts refuse to write output of a
fake knowledge base or fake model into this folder.

| File | Written by |
|------|-----------|
| `candidates.jsonl`, `drafts.jsonl` | `build_dataset.py sample`, `draft_labels.py` |
| `all.jsonl` (DATA-7), `items.jsonl`, `report.json` | `build_dataset.py assemble` |
| `checked.csv` | `review.py` (a person at a terminal) |
| `train.jsonl`, `val.jsonl`, `test.jsonl`, `split-report.json` | `split.py` |
