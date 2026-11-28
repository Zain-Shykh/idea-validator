# Z9 — M10t Fine-tuning pipeline + model comparison

| Field | Value |
|-------|-------|
| Phase | `Z9` from `docs/implementation-plan-zain.md` |
| Owner | Zain |
| Author | Claude Code headless session |
| Branch | `feat/z9-finetuning` (parent `feat/z8-ideation-discover`, PR #19) |
| Status | Blocked (code in review; data, training and comparison are owner steps, C6) |
| Requirement IDs | M10t-1, M10t-2, FT-1..8, M10-1, M10-4, M10-5, DATA-7, SUB-2e, SS-18 |
| Architecture refs | §12.2, §14.1, §14.2, ADR-005 |
| Depends on | Z5 (M9 features and evidence bounds), Z4 (prompt builder, scorer). **S3 (real KB) is not available**: `FakeKB` in tests only |
| Issue / PR | #20 / #21 (draft) |

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
| `checked.csv` names an unknown item or invalid label | `split` fails (a rejected row may be gone) |
| `checked.csv` has a wrong number of cells or an unended last line | every script stops; nothing appended |
| `test.jsonl` differs from the hash in `split-report.json` | `evaluate` refuses |
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
| 9 | Testing subagent, fixes | tests | full suite, ruff, mypy | [x] |
| 10 | Part C, runbook, tracking docs, draft PR | docs | — | [x] |

### B2. Commit plan
`docs(spec)` → `docs(plan)` → one `feat(finetune)` per task 1–7 (tests included) → `docs(finetune)` →
`fix(finetune)` (test report) → `docs(Z9)`. Push after every task. Decision rows go with their change.

### B3. Definition of Done (phase)
- [x] A4 criteria 1–11 pass; O1–O7 stay unticked with their reasons.
- [x] ruff and mypy clean (`mypy libs services jobs/finetune`); coverage target met.
- [x] No heavy ML dependency added to `.venv`, `requirements-dev.txt` or any service image.
- [x] No data, labels, checks, logs or results committed.
- [x] Tracking docs updated; plan status **Blocked** with reasons; **draft** PR against `feat/z8-ideation-discover`.

---

## Part C — Implementation and test report

**Outcome: Blocked.** All code that needs no real KB, GPU, Gemini key or person is built and tested.
**No dataset, label, hand check, training log, metric or comparison result exists.**
`jobs/finetune/data/` holds only its README; `eval/results/` and `eval/labelled/` are empty.

### C1. What was built
| File | What |
|------|------|
| `jobs/finetune/dataset.py` | File formats, item ids, near-copy grouping, labelled-set reader, grounded-label rule, safe file reading and writing, fake-output guard |
| `build_dataset.py` | `sample` (records M9's `ScoreRequest`s, renders with M10's prompt code, hides the idea's own card) and `assemble` (stratified, reports counts, writes nothing incomplete) |
| `draft_labels.py` | Rate-limited, bounded, resumable teacher drafts; ungrounded replies are rejected, never repaired |
| `review.py` | Terminal-only hand check, idea by idea; accept / fix / reject / skip |
| `split.py` | train / val / test by idea group, leakage assertions on the rows written, check quotas |
| `train_config.yaml`, `train.ipynb` | Unsloth QLoRA settings and notebook with pinned versions and a training log |
| `evaluate.py` | Three models, one attempt per row, six metrics, JSON and markdown, rule-picked "did not help" row |
| `export_gguf.md`, `model-image/README.md` | Merge → GGUF Q4_K_M → release asset → `scorer-model:v1` → cluster |
| `k8s-lora/{component,dev}` | Switch to `scorer-lora@v1` on the scorer **and** the orchestrator |
| `README.md`, `data/README.md`, `tests/` | Commands and tests; every fixture value is marked FAKE |

Also: `pyproject.toml` (test and mypy paths), a `model_id` argument on the scorer test rig,
`docs/setup.md` §11, `.env.example`, `README.md`. **No service source, contract or image changed**,
and no dependency was added.

The first session stopped on a usage limit after task 3 and left the split step as a `wip` commit.
It was run before anything was built on it: its tests passed, with one lint error and one guard that
missed subfolders of the data folder (`b258dde`).

### C2. Deviations from spec / architecture
| What | Why | Logged as |
|------|-----|-----------|
| §14.1 splits into train and test; here there is also `val.jsonl`, plus `items.jsonl` and report files | Validation loss must not use the test set; DATA-7 lines allow no extra field | D-021 |
| The cluster switch is a Kustomize component, not an edit of `k8s/base`; it also sets the orchestrator's `SCORER_MODEL_ID` | No tuned model exists; verdict versions and cache keys must name the right model | D-021 |
| Spec: `split` accepted every `checked.csv` row; now rejected rows are replaced by `assemble` and may be absent from `all.jsonl`; `evaluate` also requires `split-report.json` | Test report | Z9-17, Z9-19 |

### C3. Test report

**Final run (after the fixes, 2026-11-28):**
| Command | Result |
|---------|--------|
| `pytest -q` (whole repo) | 1941 passed, 2 skipped (no data), 6 deselected (integration and cluster) |
| `pytest jobs/finetune -q --cov=jobs/finetune` | 140 passed, 2 skipped; the six scripts: 985 of 1008 statements (98 %), each ≥ 97 % |
| `ruff check .`, `ruff format --check .` | clean (includes the notebook) |
| `mypy libs services jobs/finetune` | clean, 124 files |
| `python scripts/check_registry.py` | ok, 17 entries |
| `kubectl kustomize` + `kubeconform -strict`, dev and `k8s-lora/dev` | 27/27 valid, both |
| `docker build` of the model image with a 1 MiB random stand-in (tag `…standin-model:fake`) | built; runs as uid 10001; `sha256sum -c` OK; image removed, build cache pruned |

**Testing subagent** (`agent-skills:test-engineer`, test files only): added `tests/test_adversarial.py`
and Job equality checks. Its run: 215 passed, 6 failed (reproductions), 2 skipped.

| # | Bug found | Fix (`75f32a4`) |
|---|-----------|-----------------|
| 1 | A `checked.csv` line cut while writing read as valid; the next check was glued to it and lost without a message | A file with an unended last line is refused, also before appending |
| 2 | An unquoted comma in a hand edit silently cut the reason, which would become test truth | A row with the wrong number of cells is refused |
| 3 | Ideas were compared HTML-escaped (`&amp;` adds the word `amp`), so short near copies could land in two parts, unseen by the assertion | `idea_block` unescapes |
| 4 | Hand-rejected rows counted towards the 300 minimum | `assemble` replaces them; `split` warns about leftovers; the committed-data test counts without them |
| 5 | `--max-ideas -1` sampled all ideas but one | refused |

Also fixed from its notes: `evaluate` accepted a hand-pruned `test.jsonl` (cherry-picking);
`--min-rows` could be lowered on the real data folder; `--rpm nan/inf` passed; a missing input file
printed a traceback. A draft file without a final newline is now continued safely.

Not fixed: a formula-like reason is stored as is in `checked.csv` (harmless unless opened in a
spreadsheet: do not); the fake guard relies on flags, so a fake server given as `--kb-url` or
`--base-url` is not detected.

**What the tests do not show.**
- The notebook ran only against **stub** ML libraries. Its arguments were also compared with the
  source of the pinned wheels (all 17 `SFTConfig` arguments, model loading, LoRA, merge method,
  response-only helper). It has **never run on a GPU**; the pins have not been installed together.
- `export_gguf.md`, the comparison commands in the README and the cluster switch have **not been
  run** (no tuned model). The component was only rendered and schema-checked.
- All KB, teacher and model behaviour in tests is fake. Nothing here says anything about data
  quality or model quality.
- The committed-data tests skip until data exists (they were run once against a temporary fake split).

### C4. Measurements
None. There is no model, data or training run to measure.

### Acceptance criteria
A4 rows 1–11 (code) pass. **O1–O7 are not met:**
| # | Blocked by |
|---|-----------|
| O1 | No real KB (sync point S3) and no Gemini key |
| O2 | No drafts yet; hand checks need Zain and Ahmad |
| O3 | No real data; `eval/labelled/ideas.jsonl` not written |
| O4 | No data; no Colab/Kaggle GPU in a headless session |
| O5 | No trained model |
| O6 | No test set, tuned model or Gemini key |
| O7 | No model image (cluster not started) |

### C5. Follow-ups
- Count the rows left out as longer than 2048 tokens in the first real run; M10 allows prompts up to
  about 4096 tokens, so raise `max_seq_length` if many are dropped.
- Report MAE per dimension if the overall number hides a weak one.
- Add the component to the dev overlay once `scorer-model:v1` exists; Ahmad owns the base and
  overlays after A0.
- CI (A0) should run `pytest`, which includes the no-weights check.

### C6. Owner runbook (remaining manual steps)

Run from the repo root with the venv active, on a new branch from this one (or `main` after the
merges). Commit each result as it is made. **Never edit data files or results by hand**, except the
one explanation in step 13.

1. **Wait for S3:** the real KB (Ahmad, ≥ 1,500 cards) running and reachable. In the cluster:
   `kubectl -n idea-validator port-forward svc/kb 8006:8000`. Put `GEMINI_API_KEY` in `.env` (never
   commit it) and export it.
2. **Write the EVAL-1 ideas** (about 40) to `eval/labelled/ideas.jsonl`, one
   `{"title": …, "text": …}` per line. Verdict labels can wait for Z10. Add no idea to this list
   after step 9 without splitting again.
3. *(Recommended)* Write generated ideas to a file (M8 output or by hand), including very strong,
   very weak and thin-evidence ones: scores 0 and 5 are likely to be rare.
4. **Sample** (add `--analyst-url` if M7 runs):
   `python jobs/finetune/build_dataset.py sample --kb-url http://127.0.0.1:8006 --ideas <file> --max-ideas 100`.
   Aim for at least 450 candidates; add `--topics` or raise `--max-ideas` if there are fewer.
5. **Draft:** `LLM_BACKEND=direct python jobs/finetune/draft_labels.py --rpm 10`. Check your free
   quota first and lower `--rpm` or `--max-calls` to fit. Exit code 2 means run it again.
6. **Assemble:** `python jobs/finetune/build_dataset.py assemble`. If it lists problems, add ideas
   for the missing level or dimension: `sample --force` writes a new candidates file, so keep a copy
   of the old one. Existing drafts stay valid only for unchanged prompts.
7. **Check ≥ 40 rows (Zain):** `python jobs/finetune/review.py --name Zain`. Do a few extra, because
   rejected rows do not count. Commit and push `data/`.
8. **Check ≥ 20 rows (Ahmad):** pull, `review.py --name Ahmad`, commit, push. One at a time on the
   same file. If anyone rejected a row, run `assemble` again, then check more if the counts need it.
9. **Split:** `python jobs/finetune/split.py`, read the warnings, then `pytest jobs/finetune -q`:
   the two committed-data tests must now **run and pass**, not skip. Commit `data/`.
10. **Train** on a free T4: upload `train.ipynb`, follow its first cell (`FT_DIR`, no `%cd`), run
    all. Check the "longer than 2048 tokens" counts and that validation loss does not rise. Download
    the log, check `"fake_data": false`, save it as `eval/results/finetune-train-log.json` and
    commit it. If you change a setting, change `train_config.yaml`, rerun and commit both.
11. **Export and publish:** [`export_gguf.md`](../../jobs/finetune/export_gguf.md) §1–2. Put the
    sha256 and llama.cpp commit in the release notes.
12. **Serve in the cluster:** check `df -h ~` (≥ 7 GB plus the 5 GB margin), then §3–4: build and load
    `scorer-model:v1`, `kubectl apply -k jobs/finetune/k8s-lora/dev`, run `scripts/scorer_check.py`,
    and do the fallback check (unverified: if `kill 1` does not restart the model server, make it
    unavailable another way). Check that a new verdict records `scorer-lora@v1`.
13. **Compare:** start the two llama.cpp servers and run `evaluate.py` as in the README ("Comparison").
    In `eval/results/finetune-comparison.md`, replace the `TODO` with your own explanation. If the
    tuned model is not better, report that as it is. Commit both files; take screenshot SS-18.
14. **Close the phase:** record times, memory and counts in C4, tick the plan's criteria and the
    checklist rows with evidence, set Z9 to "In review" and mark the PR ready.
