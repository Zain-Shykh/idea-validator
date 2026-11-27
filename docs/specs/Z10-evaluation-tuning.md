# Z10 — Evaluation harness, behaviour tests, replay, drift, verdict tuning

| Field | Value |
|-------|-------|
| Phase | `Z10` from `docs/implementation-plan-zain.md` |
| Owner | Zain |
| Author | Claude Code headless session |
| Branch | `feat/z10-evaluation-tuning` (from `feat/z9-finetuning`, draft PR #21) |
| Status | Plan ready |
| Requirement IDs | AO-1 (replay), AO-4, AO-5, AO-7, M13-4, M13-5, EVAL-1..4, VE-10, JEN-1 (nightly eval stage content), SUB-2c, SUB-2f, SS-14..16 |
| Architecture refs | `architecture.md` §8.3, §15, §17.3, §17.4, §18, §21.5 |
| Depends on | Z7, Z8, Z9. Missing: real M2, M6, M4, M5, M7 (A-phases), Gemini key, fine-tuned model, cluster deployment of Ahmad's services, human labels |
| Issue / PR | set in Part C |

---

## Part A — Specification

### A1. Objective
Measure and improve the system like an operations team: run every labelled idea through the orchestrator and score the verdicts against human ones, tune the engine's thresholds and weights offline without overfitting, replay a stored run from its trace, prove agent behaviour under failure and attack in tests that need no model or network, and report drift after ingestion.

### A2. Context
- Exists: M3 (Validate/Discover over HTTP), M8, M9, M10, M11, M12, M13 (traces, summary, registry; `/drift/latest` and `/eval/latest` are stubs), cassettes and a real-services-in-process test stack (`eval/behaviour/behaviour_stack.py`), the M10t split that needs `eval/labelled/ideas.jsonl`.
- Missing (the phase is **partly blocked**): M2 (stores ideas; the harness creates them there), the real knowledge base, a hosted model key, a deployed cluster stack, and **human labels**. So no confusion matrix, κ, baseline, tuned value or cluster run can be real in this phase.
- Today M3's `tool_call` records keep only a summary of each reply, and the run's input is not traced. A stored run cannot be replayed from its trace; this phase changes that.

### A3. Scope
**In scope:** about 40 AI-drafted candidate ideas with profiles; label file formats, validation and inter-rater agreement; `run_eval.py` (run, score, behaviour checks, report, `--compare`); `tune.py`; replay; four behaviour tests; `drift.py`; `eval_runs` and `/drift/latest`, `/eval/latest`; eval image, Job, drift CronJob, `scripts/check_eval.sh`.

**Out of scope:** any label, resolved verdict, `baseline.json`, tuned value, matrix or metric presented as real (owner, C6 runbook); changing `config/verdict.yaml`; the dashboard page (Ahmad, A9); the Jenkinsfile (A10); replaying a whole Discover run (its child Validate runs can be replayed).

### A4. Requirements and acceptance criteria

| # | Req ID | Acceptance criterion | Verified by |
|---|--------|----------------------|-------------|
| 1 | EVAL-1 | ≥ 40 candidate ideas with valid profiles, ≥ 8 of each intended case type, marked AI-drafted and pending review; the intended type is kept apart from what labellers read | `jobs/evaluation/tests/test_labelled.py` |
| 2 | EVAL-1 | **(blocked: people)** both members' independent labels and the resolution are committed. Code: label files are validated (unknown, duplicate, missing id; bad verdict), disagreements and κ are listed, nothing writes a label file | `test_labelled.py` (format); runbook C6 |
| 3 | AO-4, M13-4, EVAL-2 | Given a stack behind the orchestrator API, `run_eval` creates each idea, runs it, answers questions neutrally, declines approvals, and records the verdict, the M11 input and the behaviour checks; with labels it reports agreement, the 4×4 matrix, κ and **false GO on human NO-GO** separately; without labels those are absent, not zero | `test_run_eval.py`, `test_eval_metrics.py` |
| 4 | AO-4 | Behaviour checks from the trace: expected tools used, cited evidence was retrieved in the run, budget kept, flagged evidence never cited | `test_eval_metrics.py` |
| 5 | AO-4, JEN-1 | `--compare` exits non-zero when a metric is worse than the baseline past its tolerance, when a metric disappears, when the labelled set differs, or when fake and real are mixed; `scripts/check_eval.sh` passes that exit code on | `test_check_eval.py` (fake fixtures under `tests/`) |
| 6 | EVAL-3, EVAL-4, VE-10 | `tune.py` replays stored M11 inputs through the real engine over a grid of T_go, T_no, floor and weight moves with stratified k-fold CV; reports before/after matrices, false GO on NO-GO, fold variance and the train/test gap; recommends a change only if held-out results improve without more false GOs; never writes `config/verdict.yaml` | `test_tune.py` |
| 7 | EVAL-4 | **(blocked: labels)** real before/after matrices and tuned values with justification in `config/verdict.yaml` | runbook C6 |
| 8 | AO-7 | Four behaviour tests pass with network connections blocked and no model: invalid JSON from a scorer (retry, then fallback or abstain), tool timeout (bounded retries, abstain), empty source (INSUFFICIENT_EVIDENCE with `missing`), poisoned page (withheld, counted, never cited, no instruction followed) | `eval/behaviour/test_failures.py` |
| 9 | AO-1 | Replaying a recorded Validate run (also with clarify and approval) reproduces the same verdict with no service, Redis or model; a changed rule or tampered record gives a diff and exit code 1 | `services/agentops/tests/unit/test_replay.py` |
| 10 | AO-1 | Full replies are in the M13 record and never on stdout | `libs/common/tests/test_trace.py` |
| 11 | AO-5, M13-5 | `drift.py` reports freshness, niche coverage (and change since the previous report) and PSI of the verdict mix (eval users excluded; "not enough data" instead of a number for small windows), stores it in `eval_runs` and a JSON file; `GET /drift/latest` returns it, 404 before the first | `test_drift.py`, `test_api.py`, `test_postgres.py` |
| 12 | AO-5 | **(blocked: real KB, UI)** a report after a real ingestion, shown on the dashboard | runbook C6 |
| 13 | AO-4 | Eval Job and drift CronJob manifests are valid and hardened; the eval image builds and runs its scripts as non-root | `eval/behaviour/test_packaging.py`, kubeconform, docker build |
| 14 | AO-4 | **(blocked: cluster stack)** the eval Job runs in the cluster | runbook C6 |

### A5. Interfaces and contracts
- `core.TraceRecord` gains optional `reply: str | None` (additive): the full reply of a tool call, for replay. Redacted on stdout.
- New M3 trace records (agent `orchestrator`, kind `tool_call`): `run_input` (the initial checkpoint, in `input`), `student_answer`, `student_approval` (the request, in `input`). Memo hits carry the reused value in `reply`.
- M13: `GET /drift/latest`, `GET /eval/latest` → `Report` from `eval_runs`; 404 `NOT_FOUND` when there is none (was a stub with `git_sha="stub"`). Migration `0002`.
- `run_eval.py` is a client of: M2 `POST /internal/ideas`; M3 `POST /runs`, `GET /runs/{id}`, `/answer`, `/approve`; M13 `GET /runs/{id}/trace`.
- `drift.py` reads M6 `GET /stats` (`KbStats`) and the `ops` database.
- Env: `ORCHESTRATOR_URL`, `PROFILE_URL`, `AGENTOPS_URL`, `KB_URL`, `POSTGRES_*`, `OPS_DB`, `GIT_SHA`. No new names in `.env.example`.
- Label files (written by people only): `eval/labelled/labels_<name>.csv` = `idea_id,verdict,note`; `resolved.csv` = `idea_id,verdict,labelled_by,resolution`.

### A6. Design
| File | Responsibility |
|------|----------------|
| `eval/labelled/ideas.jsonl`, `case_types.csv`, `README.md` | candidates; intended case type (not shown to labellers); labelling rules |
| `jobs/evaluation/labelled.py` | load and validate ideas and labels; CLI: disagreements and inter-rater κ |
| `jobs/evaluation/eval_metrics.py` | matrix, agreement, κ, false GO, behaviour checks, baseline comparison |
| `jobs/evaluation/reports.py` | git sha, writing reports, the fake guard, storing in `eval_runs` |
| `jobs/evaluation/run_eval.py` | drive the orchestrator, build the report, `--compare` |
| `jobs/evaluation/tune.py` | grid + k-fold CV over stored M11 inputs |
| `jobs/evaluation/drift.py` | the drift report |
| `services/agentops/src/agentops/replay.py` | `python -m agentops.replay` |
| `jobs/evaluation/Dockerfile`, `k8s/base/eval-job.yaml`, `k8s/base/drift-cronjob.yaml`, `scripts/check_eval.sh` | delivery |

**Eval run.** One new user id per idea and per eval run (`u_eval_<tag>_<n>`), so the verdict cache cannot answer and hide a regression. The M11 input is the `input` of the run's last `final_verdict` record: original scores, before challenge penalties (Z2-6). A run that fails, stops or times out counts as a disagreement and in `errors`.

**Tuning.** Candidates = T_go {60,65,70,75,80} × T_no {40,45,50,55,60} × floor {1.0,1.5,2.0} × weights {unchanged, move 5 points from one dimension to another in every preset}; each is validated by `VerdictConfig`. A candidate is ranked on a set of ideas by (false GO on human NO-GO, errors in agreement, distance from the current rules), in that order, so a tie keeps the current values. Folds are stratified by human verdict and fixed by a hash of the idea id. For each fold the best candidate of the other folds is scored on the held-out one. Recommend `adopt` only if the held-out agreement is higher than the current rules' and the held-out false GOs are not more; else `keep`.

**Replay.** The `run_input` record gives the starting checkpoint. M3's real state machine runs with: peers that answer from the records (matched by tool, then input hash, then order; recorded failures raise again), a memo that serves recorded memo hits, an in-memory stand-in for M2, no Redis. M11 is **not** replayed from the record: the real engine decides again (in process, no network), so a changed rule shows up. The command diffs requests, decisions and the final verdict; exit 0 same verdict, 1 different, 2 cannot replay (no input record, cache-hit run, Discover parent).

**Drift.** PSI over the four labels between the last 7 days and the 28 days before, with 0.5 added to every count; below 10 verdicts in either window the value is `null`.

### A7. Edge cases and failure handling
| Case | Expected behaviour |
|------|--------------------|
| Orchestrator or M2 down | the idea is an `error`; if every idea fails, exit 2 and write no report |
| Run never finishes | per-idea time limit, counted as error |
| More questions or approvals than a run can have | after 4 interactions: error |
| Labels cover only some ideas | with `resolved.csv` present every idea run must have a label, else stop |
| Baseline or report unreadable | exit 2 (never "no regression") |
| Fake input | output may not be written under `eval/results/`, nor stored in `eval_runs` |
| Tuning with too few ideas for the folds, or no labels | stop with a message, no output |
| Trace with a missing or extra call | replay reports it and exits 1 or 2 |
| M6 down during drift | exit 2, no report |

### A8. Test plan
| Level | What | Command |
|-------|------|---------|
| Unit | metrics, labels, tuning, drift, replay, trace | `pytest jobs/evaluation services/agentops libs/common -q` |
| Harness end to end | `run_eval` against the real M3 and M11 on the fake stack | `pytest jobs/evaluation -q` |
| Behaviour | four failure cases, sockets blocked | `pytest eval/behaviour/test_failures.py -q` |
| Integration | `eval_runs` on real Postgres | `pytest -m integration services/agentops` |
| Packaging | manifests, image | `pytest eval/behaviour/test_packaging.py`; kubeconform; `docker build -f jobs/evaluation/Dockerfile .` |

Coverage target: ≥ 90 % for `jobs/evaluation` and `agentops.replay`.

### A9. Assumptions and open questions
- The eval run is not interactive: each intake question gets the fixed answer "No further information.", every approval is declined. Measured verdicts are therefore those of a student who adds nothing and spends nothing extra.
- The recorded cassettes are hand-written: no hosted key exists to record from (as in Z4–Z8).
- "Injection resisted" in a live run means: no evidence the scan flagged is cited by any score. With nothing flagged it is not measured.
- Regression tolerances: agreement and κ may drop by 0.05, tool use by 0.05; false GOs, errors, evidence ratio, budget and injection may not get worse at all.
- `/drift/latest` answers 404 before the first report. The UI must treat that as "no report yet" (proposal for A9).
- The case type a candidate was drafted for is not its human verdict.

### A10. Risks
| Risk | Mitigation |
|------|-----------|
| AI-drafted ideas bias the evaluation | marked as drafts; the owner edits or replaces them before labelling; intended types kept apart |
| Test numbers mistaken for results | fake guard on every output; fixtures under `tests/` with `FAKE` in name and content |
| 40 ideas are too few to tune on | CV variance reported; "keep" is the default |

---

## Part B — Plan

### B1. Task breakdown

| # | Task | Files | Verification | Done |
|---|------|-------|--------------|------|
| 1 | Candidate ideas, label formats and validation | `eval/labelled/**`, `jobs/evaluation/labelled.py`, `pyproject.toml` | `test_labelled.py` | [ ] |
| 2 | Trace what replay needs | `contracts/core.py`, `common/trace.py`, M3 `run.py`, `runner.py`, `validate.py`, schemas | `test_trace.py`, M3 tests | [ ] |
| 3 | Replay | `agentops/replay.py` | `test_replay.py` | [ ] |
| 4 | Metrics, behaviour checks, comparison | `eval_metrics.py`, `reports.py` | `test_eval_metrics.py` | [ ] |
| 5 | `run_eval.py` and `check_eval.sh` | + `scripts/check_eval.sh`, fake fixtures | `test_run_eval.py`, `test_check_eval.py` | [ ] |
| 6 | `tune.py` | + `verdict.engine` public label function | `test_tune.py` | [ ] |
| 7 | `eval_runs`, report endpoints, `drift.py` | agentops migration 0002, repository, routes; `drift.py` | `test_api.py`, `test_drift.py`, integration | [ ] |
| 8 | Four behaviour tests | `eval/behaviour/test_failures.py`, cassettes | pytest | [ ] |
| 9 | Image, Job, CronJob | `jobs/evaluation/Dockerfile*`, `k8s/base/*.yaml`, overlay tag | packaging tests, kubeconform, docker build | [ ] |
| 10 | Testing subagent, fixes, docs, draft PR | tests, docs | full suite | [ ] |

### B2. Commit plan
`docs(spec)`, `docs(plan)`, `feat(eval): candidate ideas and label checks`, `feat(trace): record what replay needs`, `feat(agentops): replay`, `feat(eval): metrics, run_eval, check_eval`, `feat(eval): tuning`, `feat(agentops): eval_runs and drift`, `test(behaviour): four failure cases`, `build(eval): image and manifests`, `fix(...)`, `docs(Z10)`.

### B3. Definition of Done (phase)
As the template. Criteria 2, 7, 12 and 14 (and the real part of 11) stay open: they need people, the real KB, the UI and the cluster.

---
