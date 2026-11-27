# M10t: fine-tuning the scorer (FT-1..8)

Scripts, settings and commands for a QLoRA fine-tune of Qwen2.5-1.5B-Instruct. The goal (FT-1) is a
cheaper, faster, more consistent scorer that follows our rubric format and evidence rule, not a
smarter one.

> **No data, weights or results exist yet.** The code is tested with a fake knowledge base, fake
> models and stub ML libraries only. The notebook has never run on a GPU. See the owner runbook in
> [`docs/specs/Z9-finetuning.md`](../../docs/specs/Z9-finetuning.md) (C6).

## Pipeline

| Step | Command | Needs | Writes (in `data/`) |
|------|---------|-------|---------------------|
| 1 sample | `build_dataset.py sample` | the real KB (M6) | `candidates.jsonl` |
| 2 draft | `draft_labels.py` | a hosted model | `drafts.jsonl` |
| 3 assemble | `build_dataset.py assemble` | – | `all.jsonl`, `items.jsonl`, `report.json` |
| 4 check | `review.py --name <you>` | a person | `checked.csv` |
| 5 split | `split.py` | `eval/labelled/ideas.jsonl` | `train/val/test.jsonl`, `split-report.json` |
| 6 train | [`train.ipynb`](train.ipynb) + [`train_config.yaml`](train_config.yaml) | free T4 | `outputs/` (git-ignored) |
| 7 export | [`export_gguf.md`](export_gguf.md) | – | release asset, model image |
| 8 compare | `evaluate.py` | two local model servers + the hosted model | `eval/results/finetune-comparison.{json,md}` |

Run from the repo root with the venv active. Every script has `--help`. No ML library is installed
locally.

```bash
# 1. real knowledge base; --ideas adds generated ideas, {"title","text"} or M8's shape per line
python jobs/finetune/build_dataset.py sample --kb-url "$KB_URL" [--analyst-url "$ANALYST_URL"] \
  [--ideas ideas.jsonl] [--max-ideas 100]
# 2. rate-limited and resumable: run again after exit code 2
LLM_BACKEND=direct GEMINI_API_KEY=... python jobs/finetune/draft_labels.py --rpm 10
# 3. fails (and reports counts) below 300 rows or with a missing dimension or score level
python jobs/finetune/build_dataset.py assemble
# 4. in a real terminal; stop with q at any time
python jobs/finetune/review.py --name Zain
# 5. needs >= 60 checked rows, Zain >= 40 and Ahmad >= 20
python jobs/finetune/split.py
# 8. see "Comparison" below
```

To try the scripts without a KB, use `sample --fake-cards eval/fixtures/*cards*.jsonl --out
/tmp/ft/candidates.jsonl`. Anything made from fake input is flagged, and the scripts refuse to write
it into `data/` or `eval/results/`.

## Rules the code enforces

- **Same prompt as serving.** M9's scorers pick the evidence and features, M10's code renders it.
- **No invented evidence.** A teacher reply (or a hand fix) that cites an id which is not in its
  prompt is rejected as a whole.
- **`checked_by` means a person read the row.** `review.py` refuses piped input and has no
  accept-all mode. Do not write `checked.csv` with anything else.
- **No leakage.** Split by idea (near copies together); checked rows are the test set; no idea of
  the labelled set (EVAL-1) in any part. The assertions also run on the committed files
  (`tests/test_committed_data.py`).
- **No weights in git** (`tests/test_packaging.py`).

## Checking labels

Rows come idea by idea, all dimensions together: an idea with a checked row becomes a test idea, so
scattered checks would leave little to train on. Read the evidence before the draft. `a` accepts,
`f` edits the fields (Enter keeps one), `r` removes a bad row for good, `s` skips. Zain and Ahmad
work one after the other on the same `checked.csv` (commit and pull in between).

## Comparison

All three models get each test prompt once. Serve the two local ones with the same llama.cpp image
as the cluster, from a folder holding both GGUF files:

```bash
LLAMA=ghcr.io/ggml-org/llama.cpp:server-v1.1.0
for m in "8081 qwen2.5-1.5b-instruct-q4_k_m" "8082 scorer-lora-v1-q4_k_m"; do
  set -- $m
  docker run -d --rm --name "ft-$1" -p "127.0.0.1:$1:8080" -v "$ggufs:/models:ro" "$LLAMA" \
    --model "/models/$2.gguf" --host 0.0.0.0 --port 8080 --ctx-size 4096
done
LLM_BACKEND=direct GEMINI_API_KEY=... python jobs/finetune/evaluate.py \
  --base-url http://127.0.0.1:8081 --tuned-url http://127.0.0.1:8082
docker stop ft-8081 ft-8082
```

Each server needs about 2 GB of memory. Local decoding is unconstrained, so the valid-JSON rate is
the model's own; `--constrained` repeats the cluster's grammar (write it to another `--out`
folder). Then replace the `TODO` in `eval/results/finetune-comparison.md` with your explanation of
the "did not help" case. Report the bias the file states: the hand checks started from the hosted
teacher's drafts.

## Tests

```bash
pytest jobs/finetune -q
mypy libs services jobs/finetune
```
