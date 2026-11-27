# Z9 — M10t Fine-tuning pipeline + model comparison

| Field | Value |
|-------|-------|
| Phase | `Z9` from `docs/implementation-plan-zain.md` |
| Owner | Zain |
| Author | Claude Code headless session |
| Branch | `feat/z9-finetuning` (parent `feat/z8-ideation-discover`, PR #19) |
| Status | Implementing |
| Requirement IDs | M10t-1, M10t-2, FT-1..8, M10-1, M10-4, M10-5, DATA-7, SUB-2e, SS-18 |
| Architecture refs | §12.2, §14.1, §14.2, ADR-005 |
| Depends on | Z5 (M9 features and evidence bounds), Z4 (prompt builder, scorer). **S3 (real KB) is not available**: `FakeKB` in tests only |
| Issue / PR | see Part C |

---

## Part A — Specification

### A1. Objective
Reproducible data, scripts, settings and an honest three-way comparison for a QLoRA-tuned Qwen2.5-1.5B
scorer. FT-1 goal: a **cheaper, faster, more consistent** model that follows our rubric format and
evidence rule, not a smarter one.

**Partly blocked.** No real KB (S3), no Colab/Kaggle GPU, no Gemini key and no human label checking
are available. This phase delivers every piece of code, tested against fixtures and fakes. It produces
**no dataset, labels, checks, training log, metrics or comparison**. The owner produces those with the
runbook in C6.

### A2. Context
- M10 (Z4) builds its prompt with `scorer.services.prompt.build_messages`. M9 (Z5) decides which
  evidence and features reach it. Training text must be exactly that text.
- M6 is Ahmad's and not built. `common.fakes.FakeKB` stands in, in tests only.
- `eval/labelled/` (the EVAL-1 set, Z10) is empty.
- `jobs/finetune/model-image/` and the `model-import` Job (Z4) already pack and load a GGUF.

### A3. Scope
**In scope:** `jobs/finetune/` (`dataset.py`, `build_dataset.py`, `draft_labels.py`, `review.py`,
`split.py`, `train.ipynb`, `train_config.yaml`, `export_gguf.md`, `evaluate.py`, `README.md`, tests);
the Kustomize component that switches the scorer to `scorer-lora@v1`; a no-weights-in-git check;
documentation.

**Out of scope:** producing real data, labels, weights or results (owner, C6); the labelled set and its
human verdicts (Z10); measured requests/limits of the tuned model (Z11); any change to M10, M9 or
contracts; switching `k8s/base` to the tuned model before the image exists.

### A4. Requirements and acceptance criteria
`T/` is `jobs/finetune/tests/`. "Code" criteria can pass in this session; "Owner" criteria need real
data, a GPU or a person and **stay unticked**.

| # | Req ID | Acceptance criterion | Verified by |
|---|--------|----------------------|-------------|
| 1 | FT-5, DATA-7 | Every exported row is a valid `FineTuneExample`; its system and user messages equal what M10 builds for the `ScoreRequest` M9 sends; its label cites only ids in the prompt | `T/test_build_dataset.py::test_prompt_equals_what_the_scorer_serves`, `T/test_draft_labels.py::test_invented_evidence_id_is_not_kept` |
| 2 | FT-2 | A card reframed as an idea never appears in its own evidence; labelled-set ideas are never sampled; selection is stratified by (dimension, level); fewer than 300 rows or a missing dimension or level fails and is reported with counts | `T/test_build_dataset.py::test_source_card_is_not_its_own_evidence`, `::test_labelled_idea_is_not_sampled`, `::test_assemble_is_stratified`, `::test_assemble_fails_on_missing_level` |
| 3 | FT-3 | Drafting is JSON only, rate-limited, resumable and bounded; invalid or ungrounded drafts are recorded as rejected, never repaired into a label | `T/test_draft_labels.py` |
| 4 | FT-3 | Review writes `checked_by`, `checked_at` and fixed fields only for a person at a terminal; a fix must pass the same contract and evidence rule; it resumes and walks idea by idea | `T/test_review.py` |
| 5 | FT-4 | Split is by idea (near-duplicates share a group), checked rows form the test set with the checked label, and it fails on any idea in two parts, on any labelled-set idea, on a missing labelled set, on < 60 checked rows or on checker quotas (Zain 40, Ahmad 20) | `T/test_split.py` |
| 6 | FT-4 | When `data/train.jsonl` exists, the committed files themselves pass the leakage assertions | `T/test_committed_data.py` (skips today: no data) |
| 7 | FT-6 | `train_config.yaml` records base model, rank, alpha, dropout, LR, epochs, batch, accumulation, max sequence length and seed; the notebook runs top to bottom against **stub** ML libraries and writes settings, data hashes and loss history to the log path | `T/test_notebook.py` |
| 8 | FT-7, M10-5 | No weight files and no file over 5 MB in git | `T/test_packaging.py::test_no_weights_in_git` |
| 9 | FT-7, M10-1 | The component renders `scorer-model:v1`, a new GGUF file name, a new Job name and `SCORER_MODEL_ID=scorer-lora@v1`; the model image builds from a small stand-in file and its checksum verifies; fallback works with the tuned model id | `T/test_packaging.py::test_lora_component`, `::test_fallback_with_lora_model_id`; manual docker build (C3) |
| 10 | FT-8, M10-4 | `evaluate.py` computes the six metrics for three models from one attempt per row, refuses a test set that is not hand-checked, writes JSON and a markdown table, and picks a "did not help" case with the explanation left for a person | `T/test_evaluate.py` |
| 11 | (rule) | Output from a fake KB or fake model cannot be written under `jobs/finetune/data/` or `eval/results/` | `T/test_guards.py` |
| O1 | FT-2, DATA-7 | Owner: `all.jsonl` ≥ 300 from the real KB, six dimensions, levels 0–5 | runbook |
| O2 | FT-3 | Owner: ≥ 60 hand-checked rows by both members; they are the test set | runbook |
| O3 | FT-4 | Owner: split of the real data passes the assertions | runbook |
| O4 | FT-6 | Owner: notebook runs on a free T4; real loss curves committed | runbook |
| O5 | FT-7, M10-5 | Owner: GGUF release asset and model image | runbook |
| O6 | FT-8, M10-4, SS-18 | Owner: real comparison table and one explained "did not help" case | runbook |
| O7 | M10-1 | Owner: the cluster scorer serves the tuned model; fallback still works | runbook |

### A5. Interfaces and contracts
- No endpoint or contract change. Reused: `ScoreRequest`, `ScorerRequest`, `ScorerOutput`,
  `FineTuneExample`, `IdeaCard`, `StudentProfile`, M6 `/search`, `/cards/{id}`, `/evidence`.
- CLIs (all `python jobs/finetune/<name>.py --help`):
  `build_dataset.py sample|assemble`, `draft_labels.py`, `review.py --name`, `split.py`, `evaluate.py`.
- Env: the existing `LLM_BACKEND`, `GEMINI_API_KEY`, `TOOLS_URL`, `AGENTOPS_URL`; new only for the
  notebook: `FT_DIR`, `FT_LOG_PATH`.
- Files in `jobs/finetune/data/`: `candidates.jsonl` (prompt + provenance), `drafts.jsonl` (teacher
  replies), `all.jsonl` (DATA-7), `items.jsonl` (one metadata row per `all.jsonl` row), `report.json`,
  `checked.csv`, `train.jsonl`, `val.jsonl`, `test.jsonl`, `split-report.json`.
- `eval/labelled/ideas.jsonl` (read only): one `{"title", "text"}` object per line, extra keys ignored
  (also accepted nested under `"idea"`).

### A6. Design
```
KB (M6) --search/cards--> build_dataset sample --candidates--> draft_labels (agent-default)
   --drafts--> build_dataset assemble (stratified) --all+items--> review (person) --checked.csv-->
   split (leakage asserts) --train/val/test--> train.ipynb (T4) --> merged GGUF
   --> model image / release --> scorer-lora@v1 ; evaluate.py(base, tuned, hosted, test.jsonl)
```
- `dataset.py`: file formats, item id (hash of system + user), idea similarity and grouping, labelled
  set reader, the guard for fake output.
- `sample`: topic queries → card ids from `/search` → cards → reframed ideas, plus `--ideas` file
  (generated ideas). For each idea and a rotating fixed student profile it runs M9's four scorers with a
  scorer stand-in that records every `ScoreRequest`, then builds the messages with M10's code. The
  idea's own card is hidden from the KB for that idea.
- `assemble`: usable drafts only; round-robin over (dimension, floor(score)) cells, hash order, until
  `--target` rows (default 360).
- `review`: ideas in hash order, all dimensions of one idea together, so about 60 checks use about
  20 % of ideas. Commands: accept, fix, reject (bad row, left out of every part), skip, quit.
- `split`: group ideas; groups with a checked row are test (only their checked rows are kept); more
  groups are held out as validation up to 20 % of ideas (at least one in ten of the rest); the others
  train. Validation is separate so the test set never guides training.
- `evaluate`: one attempt per row, no repair, no fallback. MAE and within-1 over valid replies, also
  MAE over the rows all three scored; valid-JSON rate = replies valid as the scorer's reply schema /
  replies received; real-id rate = cited ids present in the prompt / cited ids; latency p50 and mean;
  cost and tokens per score. Local models are unconstrained by default (`--constrained` = as served).

### A7. Edge cases and failure handling
| Case | Expected behaviour |
|------|--------------------|
| KB down or no cards | `sample` fails with a message; nothing written |
| M9 sends no request (no evidence, unconfirmed blocker) | no candidate for that dimension (no evidence, no claim) |
| Teacher unavailable or rate limited | wait and retry up to 3 times, then stop; rerun resumes |
| Teacher reply invalid, or cites no id of the prompt | recorded as rejected, not retried, never labelled |
| Fewer than target usable drafts, missing level | `assemble` reports counts, exits 1 |
| review without a terminal | refuses to run |
| Control characters in evidence | removed before printing (no terminal escapes) |
| Broken or duplicate row in a data file | the script stops and names file and line |
| `checked.csv` names an unknown item or invalid label | `split` fails |
| Labelled set missing or empty | `split` fails; `sample` warns |
| Notebook example longer than max sequence length | left out and counted in the log |
| Model call fails in `evaluate` | counted as an error, not as invalid JSON; no reply at all → no report |
| Fake KB or model with a real output path | refused |

### A8. Test plan
| Level | What | Command |
|-------|------|---------|
| Unit | every script's logic with `FakeKB`, cassettes, injected sleep and input | `pytest jobs/finetune -q --cov=jobs/finetune` |
| Pipeline | sample → draft → assemble → review → split → evaluate in a temp folder | same |
| Notebook | cells executed with stub `unsloth`, `trl`, `datasets`, `torch` | same |
| Packaging | git size check, Kustomize render, model image with a stand-in file | same + manual docker build |
Coverage target: ≥ 80 % of the `jobs/finetune` Python files. Stub runs prove plumbing only; they say
nothing about training or model quality.

### A9. Assumptions and open questions
- The EVAL-1 ideas must be written to `eval/labelled/ideas.jsonl` **before** the split, although their
  human verdicts belong to Z10.
- The real M6 follows `contracts/kb.py`; it has no list endpoint, so cards are found through search.
- Generated ideas come from a file (M8 output or hand-written); M7 is not built, so M8 cannot run now.
- Effort estimates in the dataset use M9's band midpoint (no model call); `trend_growth` is null unless
  `--analyst-url` is given.
- Known bias: hand checks start from the hosted teacher's draft, which favours the hosted model in the
  comparison. The report must say so.
- Stub-library tests cannot show that the notebook runs on a real T4; that stays the owner's check.

### A10. Risks
| Risk | Mitigation |
|------|-----------|
| Fake output mistaken for results | path guard, `fake` flag on rows, `FAKE` in fixture names |
| Leakage through near-duplicate ideas | grouping by word similarity, assertions on the written files |
| Rare levels (0, 5) missing in real data | oversample, thin-evidence generated ideas, clear failure report |
| Notebook library drift | pinned `unsloth` version; the log records resolved versions |

---

## Part B — Plan

### B1. Task breakdown
| # | Task | Files | Verification | Done |
|---|------|-------|--------------|------|
| 1 | Shared formats, ids, grouping, labelled set, fake guard; sampling and assembly | `dataset.py`, `build_dataset.py`, tests, `pyproject.toml` paths | `pytest jobs/finetune` | [x] |
| 2 | Teacher drafts | `draft_labels.py`, tests | same | [x] |
| 3 | Terminal review | `review.py`, tests | same | [x] |
| 4 | Split with leakage assertions | `split.py`, tests incl. committed-data test | same | [x] |
| 5 | Training config and notebook | `train_config.yaml`, `train.ipynb`, stub-library test | same | [x] |
| 6 | Comparison | `evaluate.py`, tests, pipeline test | same | [x] |
| 7 | Export and packaging: GGUF steps, model image `v1`, scorer component, weights check | `export_gguf.md`, `model-image/README.md`, `k8s-lora/`, tests | `kubectl kustomize`, `kubeconform`, docker build with a stand-in | [x] |
| 8 | README, setup, `data/README.md` | docs | read-through | [x] |
| 9 | Testing subagent, fixes | tests | full suite, ruff, mypy | [ ] |
| 10 | Part C, runbook, tracking docs, draft PR | docs | — | [ ] |

### B2. Commit plan
`docs(spec)` → `docs(plan)` → one `feat(finetune)` per task 1–7 (tests included) → `docs(finetune)` →
`fix(finetune)` (test report) → `docs(Z9)`. Push after every task. Decision rows go with their change.

### B3. Definition of Done (phase)
- [ ] A4 criteria 1–11 pass; O1–O7 stay unticked with their reasons.
- [ ] ruff and mypy clean (`mypy libs services jobs/finetune`); coverage target met.
- [ ] No heavy ML dependency added to `.venv`, `requirements-dev.txt` or any service image.
- [ ] No data, labels, checks, logs or results committed.
- [ ] Tracking docs updated; plan status **Blocked** with reasons; **draft** PR against `feat/z8-ideation-discover`.

---

## Part C — Implementation and test report

*(filled during and after implementation)*
