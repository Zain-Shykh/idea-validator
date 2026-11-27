# Z2 — M11 Verdict Engine

| Field | Value |
|-------|-------|
| Phase | `Z2` from `docs/implementation-plan-zain.md` |
| Owner | Zain |
| Author | Claude Code headless session |
| Branch | `feat/z2-verdict-engine` (parent `feat/z1-walking-skeleton`, PR #5) |
| Status | In review |
| Requirement IDs | M11-1..5, VE-1, VE-4a..d, VE-5..16, U6, U7 (engine side), U10, API-I4, K8S-4 (`verdict-config`), CI-2 (engine tests) |
| Architecture refs | `architecture.md` §13 (incl. 13.1, 13.2), §7.2, §8.5, ADR-001 |
| Depends on | Z0 (contracts), Z1 (service skeleton). No fakes: M11 has no dependencies |
| Issue / PR | — |

---

## Part A — Specification

### A1. Objective
Replace the Z1 stub with the deterministic rules engine that turns six evidence-backed scores and gate inputs into GO / PIVOT / NO-GO / INSUFFICIENT_EVIDENCE, with gates, confidence, challenge penalties, stability and what-would-make-GO. It is the only component that produces a verdict.

### A2. Context
Callers: M3 (`/verdict`, `/whatif`). Callees: none. Contracts (`core.py`, `verdict.py`) exist from Z0 and are not changed. The orchestrator is still a stub that sends six abstained scores with zero sources, so with the real engine it still gets INSUFFICIENT_EVIDENCE (evidence gate).

### A3. Scope
**In scope:** `engine.py`, `config.py`, `config/verdict.yaml`, the three endpoints, readiness, `verdict-config` ConfigMap mounted in the Deployment, compose mount, tests, the shared VE-16 run-event fixture.

**Out of scope:** computing gate inputs (Z5), accepting objections (Z6), concretizing candidates and creating idea versions (Z7/Z8), tuning thresholds (Z10), trace records of verdict calls (the caller M3 traces them, Z7), rendering the card (A9).

### A4. Requirements and acceptance criteria
Tests are in `services/verdict/tests/unit/` unless a path is given.

| # | Req ID | Acceptance criterion | Verified by |
|---|--------|----------------------|-------------|
| 1 | M11-4, VE-4a | 2 sources/2 platforms and 3 sources/1 platform → IE with `missing` naming the shortfall; 3/2 passes | `test_gates.py::test_evidence_gate_*` |
| 2 | VE-4b | effort = 0.8 × hours passes; +1 h → NO-GO stating the 1 h gap | `test_gates.py::test_feasibility_gate_*` |
| 3 | VE-4c | a legal or safety blocker → NO-GO naming it; a low risk score without a blocker passes the gate | `test_gates.py::test_risk_gate_*` |
| 4 | VE-4d | same-core same-user product with differentiation 2.9 → NO-GO naming it; 3.0 passes | `test_gates.py::test_duplicate_gate_*` |
| 5 | VE-15 | gates run in order evidence → feasibility → risk → duplicate; the first failure decides, later gates are `not_checked` | `test_gates.py::test_first_failing_gate_decides` |
| 6 | VE-11 | confidence = 0.7·mean + 0.3·min(1, sources/5); 0.4999 → IE, 0.5 continues; sources cap at 5 | `test_scoring.py::test_confidence_*` |
| 7 | VE-5..10 | 70.0 GO, 69.99 PIVOT, 50.0 PIVOT, 49.99 NO-GO; weakest dimension named | `test_scoring.py::test_threshold_edges` |
| 8 | VE-7, VE-8 | a floor dimension at 1.49 with score ≥ 70 → PIVOT naming it; learning 0 does not breach | `test_scoring.py::test_floor_*` |
| 9 | VE-6, M11-3 | each preset sums to 100; bad sums and other invalid config are rejected | `test_config.py` |
| 10 | VE-12 | 1 objection −0.5; 3 objections capped at −1.0; confidence −0.1 each; unsupported ignored | `test_challenge.py` |
| 11 | VE-13 | exactly 14 runs for score-decided verdicts, 0 for gate/IE; redistributed weights sum to 100 ± 1e-9 | `test_sensitivity.py` |
| 12 | VE-16 | 70.5 GO; feasibility −20 % → 69.87 → PIVOT; result GO + BORDERLINE with `feasibility weight -20% -> PIVOT (69.9)` listed | `test_worked_example.py::test_worked_example_verdict` |
| 13 | VE-14, VE-16 | candidates include competition 2.0 → 3.0 → 73.5 GO, stable (73.07 under the flip run); no candidate → empty list | `test_whatif.py` |
| 14 | M11-4 | stable case: no flips → STABLE | `test_sensitivity.py::test_stable_case` |
| 15 | M11-2 | same input → identical JSON, independent of input list order; score and verdict monotonic in each dimension | `test_properties.py` (hypothesis) |
| 16 | M11-1, VE-1 | `engine.py` imports only the standard library, `common.contracts` and `verdict.config`: no LLM, HTTP, clock or randomness | `test_purity.py` |
| 17 | M11-3 | changing the YAML (e.g. `T_go: 75`) changes the verdict without code change; invalid or missing config → `/ready` 503 and `/verdict` 503, `/health` 200 | `test_api.py` |
| 18 | M11-5, API-I4 | `POST /verdict`, `POST /whatif`, `GET /config` validate requests and responses against `common.contracts`; apply mode on an abstained dimension → 422 | `test_api.py` |
| 19 | K8S-4 | `verdict-config` is generated from `config/verdict.yaml` and mounted read-only in the 2-replica Deployment; compose mounts the same file | `eval/behaviour/test_packaging.py`; `kubectl kustomize k8s/overlays/dev \| kubeconform` |
| 20 | U6, U10 | verdict carries gates, six scores with evidence ids, weights, confidence, stability; IE says what is missing and has no score | `test_worked_example.py`, `test_gates.py` |
| 21 | CI-2, quality | `services/verdict` coverage ≥ 95 %; ruff + mypy clean | `pytest services/verdict --cov=verdict`; `ruff check .`; `mypy libs services` |

### A5. Interfaces and contracts
| Method, path | Request | Response | Errors |
|---|---|---|---|
| `POST /verdict` | `VerdictRequest` | `Verdict` | 422 invalid request, 503 no valid config |
| `POST /whatif` | `WhatIfRequest` | `WhatIfResponse`: `search` → `candidates`; `apply` → `verdict` | 422 (also: change to an abstained or repeated dimension), 503 |
| `GET /config` | — | `VerdictConfigResponse` (`versions.rubrics` = `per-request`) | 503 |
| `GET /ready` | — | 200 only if the config loaded and validated | 503 |

No contract changes. Env: `VERDICT_CONFIG_PATH` (default `config/verdict.yaml`; the image sets `/etc/verdict/verdict.yaml`, which is not baked in). `versions.engine` = `m11@<version>`, `profile_weights` = `weights_version`.

### A6. Design
| File | Responsibility |
|---|---|
| `verdict/config.py` | Pydantic model of `verdict.yaml` (unknown keys are errors) and `load_config(path)` |
| `verdict/engine.py` | pure functions: `apply_challenge`, `check_gates`, `weighted_score`, `confidence`, `classify`, `redistribute`, `sensitivity`, `what_would_make_go`, `apply_changes`, `decide` |
| `verdict/api/routes.py` | HTTP only |
| `verdict/main.py` | `build_app(config_path)`: loads the config once; a ConfigMap change renames the generated ConfigMap and rolls the pods |
| `config/kustomization.yaml` | generates `verdict-config`; `k8s/base` includes `../../config` (Kustomize does not read files outside its root, `kubectl apply -k` cannot relax that) |

Algorithm:
```
effective = apply_challenge(scores)               # in Dimension order; objections de-duplicated by id
conf      = w_mean*mean(effective.conf) + w_sources*min(1, n_sources/sources_norm)
gates in order; first fail -> IE (evidence) or NO-GO, score null, sensitivity 0, stop
feasibility effort unknown -> not_checked, go on; if no later gate fails -> IE
conf < min -> IE, score null, sensitivity 0
score     = sum(w*s)/5        (abstained s = 0)
label     = GO if score >= T_go and no floor breach; elif score >= T_no -> PIVOT; else NO-GO
flips     = 12 weight runs (+20 %, -20 % per dimension, rest redistributed in proportion)
          + 2 runs: least-confident dimension +/- 1, clamped to [0, 5]
stability = BORDERLINE if any run's label differs else STABLE
what-if   = unless (GO and STABLE): every (scored dimension, +0.5..+2.0 capped at 5) plus, if the
            duplicate gate failed, differentiation -> override; keep those whose label is GO;
            sort (stable first, delta asc, weight desc, dimension order)
```
Comparisons use values rounded to 9 decimals (removes float noise such as 69.999999999 for an exact 70); output values are rounded to 2.

**Note on the worked example.** Hand calculation shows that three weight runs flip it: competition +20 % (69.42), feasibility −20 % (69.87) and learning −20 % (69.53). §13/VE-16 name the feasibility one; the engine lists all (§13.1: "`flips` lists each flipping run"). Competition → 3.0 stays GO in all 12 weight runs; whether it is *stable* also depends on the least-confident dimension (−1 on feasibility would give 68.5), so the test fixes `risk` as least confident (→ 71.5). By the §13.1 sort rule, smaller stable changes (e.g. feasibility +0.5 → 73.0) come before competition +1.0; the list contains both.

### A7. Edge cases and failure handling
| Case | Expected behaviour |
|------|--------------------|
| Abstained dimension | score 0 in the sum, confidence 0, listed in `missing`; breaches the floor if a floor dimension; never a what-if candidate |
| All six abstained | evidence gate or confidence → IE |
| Effort estimate missing | feasibility `not_checked`; NO-GO if a later gate fails, else IE naming the estimate |
| `available_hours` 0 | passes only with effort 0 |
| Duplicate with different user or features | gate passes |
| Duplicate and differentiation abstained | gate fails |
| Objection without evidence | rejected by the contract (422); the engine also ignores it |
| Same objection id twice on one dimension | counted once (on two dimensions: two objections) |
| Several blockers | all named, in sorted order |
| Penalty below 0 | score and confidence clamp at 0 |
| Tie for weakest / least confident | higher weight, then fixed dimension order |
| Invalid, unreadable or missing config | `/ready` 503, `/verdict` `/whatif` `/config` 503, `/health` 200, error logged |
| NaN/inf in a typed field, score out of range, unknown field | 422 (contracts); free-form `features` values are passed through (NaN serialises as null) |

### A8. Test plan
| Level | What | Command |
|-------|------|---------|
| Unit | every row of §13.2, edges of A7 | `pytest services/verdict -q --cov=verdict` |
| Property | determinism, monotonicity, redistribution sums | same (hypothesis) |
| Contract | endpoints against `common.contracts`; orchestrator tests run against the real verdict app | `pytest -q` |
| Packaging | ConfigMap, mount, compose, pins | `pytest eval/behaviour -q` |
| Manual | verdict image build, non-root, mounted config, `/ready` with and without it; kubeconform | recorded in C3 |
Coverage target: `services/verdict` ≥ 95 %.

### A9. Assumptions and open questions
1. A missing effort estimate cannot pass the feasibility gate; the result is IE, not a guess (U10).
2. `score` is null for every gate-decided and INSUFFICIENT_EVIDENCE verdict, so the card never shows a number for an undecidable idea. `confidence` is always reported.
3. `Verdict.scores` are the scores the engine used (after challenge penalties); the originals are in `features.challenge`.
4. What-if `from`/`to` and apply-mode changes refer to those effective scores; penalties are not applied twice.
5. What-if is computed for every score-decided verdict except GO + STABLE (a BORDERLINE GO has candidates, as in VE-16) and for a failed duplicate gate. All qualifying steps are listed, not only the smallest per dimension.
6. `weakest_dimension` is set for every score-decided verdict. The floor breach and "score too low" are explained in `gate_details` (`floor`, `score`).
7. M11 trusts `gate_inputs` and evidence ids: it has no I/O; existence is checked by M9/M12.

### A10. Risks
| Risk | Mitigation |
|------|-----------|
| Config edited without a version bump → stale cached verdicts | comment in the YAML; Z3's registry check can cover it (follow-up) |
| Card shows a rounded value on the other side of a threshold (69.996 → 70.0, PIVOT) | rounding only at output is required by §13.1; accepted |

---

## Part B — Plan

### B1. Task breakdown
| # | Task | Files | Verification | Done |
|---|------|-------|--------------|------|
| 1 | Rules file and its loader | `config/verdict.yaml`, `services/verdict/src/verdict/config.py`, `requirements.txt` (pyyaml), `tests/unit/test_config.py` | pytest | [x] |
| 2 | Pure engine with the §13.2 matrix | `verdict/engine.py`, `tests/unit/{conftest,test_gates,test_scoring,test_challenge,test_sensitivity,test_worked_example,test_whatif,test_properties,test_purity}.py` | pytest, coverage ≥ 95 % | [x] |
| 3 | API and readiness | `verdict/{main,settings}.py`, `api/routes.py`, `tests/unit/test_api.py`; orchestrator tests still green | `pytest -q`, mypy | [x] |
| 4 | Shared VE-16 fixture = engine output | `eval/fixtures/run_events_validate.jsonl` + the orchestrator copy, a test that keeps it equal | pytest | [x] |
| 5 | ConfigMap, mount, compose, image env, `.env.example` | `config/kustomization.yaml`, `k8s/base/{kustomization,verdict}.yaml`, `services/verdict/{Dockerfile,compose.yaml}`, `eval/behaviour/test_packaging.py` | pytest, kubeconform | [x] |
| 6 | Testing subagent, fixes | tests | all green | [x] |
| 7 | Build the verdict image only, checks, dev overlay tag | `k8s/overlays/dev/kustomization.yaml` (own image tag) | docker run | [x] |
| 8 | Docs, PR | docs | — | [x] |

### B2. Commit plan
1. `docs(spec): …`, 2. `docs(plan): …`, 3. `feat(verdict): deterministic rules engine and verdict.yaml`, 4. `feat(verdict): serve the engine`, 5. `chore(k8s): verdict-config ConfigMap`, 6. `test/fix(verdict): …`, 7. `docs(Z2): …`

### B3. Definition of Done (phase)
- [x] All A4 criteria pass; ruff + mypy clean; `services/verdict` coverage ≥ 95 %.
- [x] Verdict image builds, non-root; manifests pass kubeconform.
- [x] Checklist, plan, deviations, AI-usage log, decisions updated.
- [x] PR against `feat/z1-walking-skeleton`.

---

## Part C — Implementation and test report

### C1. What was built
| File | What |
|---|---|
| `config/verdict.yaml`, `config/kustomization.yaml` | rules v1 / w1; generates `verdict-config` |
| `services/verdict/src/verdict/engine.py` | pure rules engine (§13) |
| `verdict/{config,settings,main}.py`, `api/routes.py` | strict config, readiness, `/verdict`, `/whatif`, `/config` |
| `services/verdict/{Dockerfile,compose.yaml,requirements.txt}` | config path, mount, pyyaml |
| `k8s/base/{verdict,kustomization}.yaml`, `k8s/overlays/dev` | mount (2 replicas), include `config/`, verdict tag `bedf5d8` |
| `services/verdict/tests/unit/` | §13.2 matrix, properties, oracle, edge cases, API, config |
| `eval/fixtures/run_events_validate.jsonl` (+ orchestrator copy) | VE-16 card = engine output |
| `eval/behaviour/test_packaging.py`, `pyproject.toml`, `.env.example` | packaging check, test path, `VERDICT_CONFIG_PATH` |

The orchestrator was not changed: its stub sends zero sources, so the real engine still answers INSUFFICIENT_EVIDENCE.

### C2. Deviations from spec / architecture
| What | Why | Logged in deviations.md as |
|------|-----|---------------------------|
| Feasibility gate can be `not_checked` (unknown effort) | must not guess | D-006 |

Process note: the Z2 decision rows were appended together with this report, not one by one with each commit.

**Worked example (VE-16).** All stated numbers hold: 70.5 GO; feasibility −20 % → 69.87 PIVOT; competition → 3.0 = 73.5 GO, 73.07 in that run. In addition: competition +20 % (69.42) and learning −20 % (69.53) also flip the base case, as does −1 on `risk` when it is least confident (68.5). The first candidate by the §13.1 sort order is feasibility 4.0 → 4.5 (73.0), not competition → 3.0; both are stable and listed. Worth stating in the report.

### C3. Test report
Testing subagent (`agent-skills:test-engineer`; added `test_oracle.py`, an independent exact-arithmetic implementation compared through hypothesis, and `test_edge_cases.py`; changed no production code):
- Before its tests: ruff, format, mypy clean; 535 passed; verdict coverage 100 %.
- After: 584 passed, 3 failed (the bugs below). The oracle found no disagreement. kubeconform 19/19 valid. Image: uid 10001; `/ready` 503 without rules, 200 with the read-only mount.

| Bug | Fix (`bedf5d8`) |
|---|---|
| Config with `weight_delta: 0` and one weight 100 loaded, then every request got 500 (ZeroDivisionError) | config rejects a weight of 100 |
| Same objection id on two dimensions: result depended on list order | de-duplicate by (dimension, id) |
| Blocker order changed `gate_details.risk` | blockers sorted |
| "gap 0 h" for a 0.001 h gap; "0.5 is below 0.5" | more precise numbers |
| Possible zero-step candidate ("from 5 to 5"; found by reading) | targets must exceed the shown value |

Not fixed: duplicate YAML keys and lax type coercion in the rules (Z2-14); NaN inside free-form `features` serialises as null (shared contract, spec A7 narrowed). No flaky tests seen.

Final run after the fixes (`bedf5d8`): `ruff check .`, `ruff format --check .`, `mypy libs services` clean; `pytest -q` → 587 passed, 3 deselected; `pytest services/verdict --cov=verdict` → 230 passed, 100 %. The 3 deselected Redis integration tests were not run in this phase. `docker compose config` valid; compose was not started.

Manual checks:
- Image `ghcr.io/zain-shykh/idea-validator-verdict:bedf5d8` (read-only root filesystem): `id -u` 10001; without rules `/health` 200, `/ready` 503; mounted: `/ready` 200, worked example → GO BORDERLINE 70.5, four flips.
- minikube `idea-validator`, `kubectl apply -k k8s/overlays/dev`: `verdict-config-th47gbd46c` created, both new verdict pods Ready, mount read-only; from the orchestrator pod `/verdict` → GO BORDERLINE 70.5 (`m11@v1`), and a stub run's stream ended with verdict INSUFFICIENT_EVIDENCE and `run_finished`. The `validators` pod was still restarting after the cluster start when pods were listed (untouched in this phase). Profile stopped afterwards; the host image was removed (the copy loaded into the minikube node stays).

### C4. Measurements
| What | Value |
|---|---|
| `decide()` on the worked example (incl. what-if search, host) | 8.2 ms mean of 200 calls |
| Verdict container memory after one request | 43 MiB (limit 384 MiB) |
| Image size | 212 MB on disk, 50.9 MB content |

### C5. Follow-ups
- Z3: registry check should fail when `verdict.yaml` changes without a `version` bump; optionally reject duplicate YAML keys.
- Z5: always send `est_effort_hours`. Z7: keep original scores for replay (Verdict.scores are post-challenge); call what-if apply on effective scores.
- Z7/Z8: applying a candidate must create idea version 2 and re-run scorers and engine (U7); M11 returns the same version number for a hypothetical.
- A9: the card can have many candidates (17 for VE-16); show the first few. A value like 69.996 displays as 70.0 next to PIVOT.
- The orchestrator image (`11276f4`) was not rebuilt; only its copy of the fixture's replaced verdict event changed.
- A0: take over `../../config` in the base kustomization; CI must run `services/verdict` tests (CI-2).
