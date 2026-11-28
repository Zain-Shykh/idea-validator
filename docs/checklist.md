# Checklist — Requirement Progress

> Progress record for every requirement ID in [`requirements.md`](requirements.md). Updated **at the end of every phase** (each member ticks **only own rows**; `Both` rows are ticked when both parts are done).
> Tick = change `[ ]` to `[x]` and fill *Evidence* (test name, PR #, screenshot id, file). Partially done → `[~]` with a note.

## Summary

| Owner | Rows | Done | Partial |
|-------|-----:|-----:|--------:|
| Zain | 89 | 54 | 12 |
| Ahmad | 77 | 0 | 0 |
| Both | 54 | 0 | 25 |
| **Total** | **220** | **54** | **37** |

_Last updated: 2026-11-27 (Z0–Z7 in review; summary recounted from the rows)._

## Phase completion

| Zain phase | Done | Ahmad phase | Done |
|-----------|------|-------------|------|
| Z0 contracts | [ ] | A0 platform bootstrap | [ ] |
| Z1 skeleton | [ ] | A1 skeleton | [ ] |
| Z2 verdict | [ ] | A2 tool layer | [ ] |
| Z3 rubrics+agentops | [ ] | A3 scouts | [ ] |
| Z4 scorer | [ ] | A4 kb+ingestion | [ ] |
| Z5 validators | [ ] | A5 retrieval eval | [ ] |
| Z6 critic | [ ] | A6 niche analyst | [ ] |
| Z7 orchestrator validate | [ ] | A7 profile/cache | [ ] |
| Z8 ideation+discover | [ ] | A8 gateway | [ ] |
| Z9 fine-tuning | [ ] | A9 ui | [ ] |
| Z10 eval+tuning | [ ] | A10 delivery hardening | [ ] |
| Z11 integration | [ ] | A11 report | [ ] |
| Z12 report | [ ] | — |  |

## General rules

| Done | ID | Requirement | Owner | Phase | Evidence |
|:----:|----|-------------|-------|-------|----------|
| [ ] | GEN-1 | Both commit, review each other's PRs, can explain whole system | Both | all | |
| [ ] | GEN-2 | AI-usage log maintained | Both | all | |
| [ ] | GEN-3 | Data only via official APIs / allowed pages; robots.txt; no private data | Ahmad | A2, A3 | |
| [ ] | GEN-4 | No keys/passwords committed; .env.example names only | Both | all | |
| [ ] | GEN-5 | Uses GitHub, Actions, Jenkins, Docker, K8s, RAG, fine-tuning, AgenticOps | Both | all | |
| [~] | GEN-6 | Whole system on Kubernetes, one container per module | Both | Z1, A1, A10 | Z1: Zain's 7 services, one image each, all Ready on minikube (spec Z1 C3); A1, A10 pending |
| [x] | GEN-7 | Schemas defined once (Pydantic), validated in/out, schema files in repo | Zain | Z0 | `libs/common/src/common/contracts/`, `libs/common/schemas/`, `test_contracts.py`, `test_export.py`; each service validates with these from Z1 |
| [~] | GEN-8 | /health and /ready on every service | Both | Z1, A1 | Z0: `common.service.create_app` (`test_service.py`); Z1: Zain's 7 services (`services/*/tests`, compose + cluster smoke); A1 pending |
| [~] | GEN-9 | Settings via env/ConfigMaps; no hard-coded localhost | Both | all | Z0: settings from env, no URL defaults (`test_settings.py`, `test_package_has_no_localhost`) |

## User capabilities

| Done | ID | Requirement | Owner | Phase | Evidence |
|:----:|----|-------------|-------|-------|----------|
| [ ] | U1 | Sign up, log in, save profile; profile changes later recommendations | Both | A7, A8, A9, Z8 | |
| [ ] | U2 | Discover: ranked ideas each with verdict + short reason | Both | Z8, A9 | |
| [~] | U3 | Watch agents live: running agent, tool calls, spend | Both | Z7, A8, A9 | Z7: ordered `RunEvent`s with agent, tool calls and spend (`services/orchestrator/tests/unit/test_validate.py::test_event_stream`); gateway SSE and UI in A8, A9 |
| [~] | U4 | Approve/decline expensive deep search; cost stated, waits | Both | Z7, Z6, A9 | Z7: cost stated, waits, approve/decline recorded (`test_budget.py`); fake scouts; UI A9 |
| [~] | U5 | Validate own idea; only result-changing questions asked | Both | Z7, Z8, A9 | Z7: only flipping questions asked (`test_clarify.py`); real intake agent Z8, UI A9 |
| [~] | U6 | Verdict card: verdict, gates, six scores + evidence links, weights, confidence, stability | Both | Z2, A9 | Z2 card; Z7 stores and publishes it with evidence ids (`test_validate.py::test_happy_path`); UI A9 |
| [ ] | U7 | What would make this GO: concrete changes, apply one, v2 computed by engine | Both | Z2, Z8, A9 | |
| [ ] | U8 | Challenge a claim: critic defends or removes | Both | Z6, Z8, A9 | |
| [ ] | U9 | History, versions, compare two ideas on six dimensions | Ahmad | A7, A9 | |
| [~] | U10 | INSUFFICIENT EVIDENCE card says what was missing, does not guess | Both | Z2, A9 | Z2 `missing`; Z7 carries it through a declined deep search (`test_budget.py`); UI A9 |

## Workflows

| Done | ID | Requirement | Owner | Phase | Evidence |
|:----:|----|-------------|-------|-------|----------|
| [ ] | WF-D1..D9 | Discover workflow (niches, ideator, gap analyst, skeptic, critic, ≤3 rounds, validate survivors, ranked result) | Zain | Z8 | |
| [~] | WF-V1..V9 | Validate workflow (intake/clarify, evidence + scout on demand, 4 parallel scorers, features, verdict, challenge, stability, card) | Zain | Z7 | Z5: WF-V3/V4 in M9; Z7: all steps in M3 (`test_validate.py`, `eval/behaviour/test_orchestrator.py` with real M9, M11, M12, M13); real intake and skeptic Z8, real KB and scouts Ahmad |

## M1 Web UI and API Gateway

| Done | ID | Requirement | Owner | Phase | Evidence |
|:----:|----|-------------|-------|-------|----------|
| [ ] | M1-1 | UI: chat, live progress, verdict card, approval prompt, history (+profile, compare, challenge, what-if, ops page) | Ahmad | A9 | |
| [ ] | M1-2 | API address set at runtime | Ahmad | A9 | |
| [ ] | M1-3 | Login | Ahmad | A8 | |
| [ ] | M1-4 | Rate limits | Ahmad | A8 | |
| [ ] | M1-5 | Input validation | Ahmad | A8 | |
| [ ] | M1-6 | UI component tests | Ahmad | A9 | |
| [ ] | M1-7 | Gateway is the only entrance; browser never calls models/scouts | Ahmad | A8, A10 | |
| [ ] | M1-8 | All public endpoints API-P1..P8 | Ahmad | A8 | |

## M2 Profile, Session and Cache

| Done | ID | Requirement | Owner | Phase | Evidence |
|:----:|----|-------------|-------|-------|----------|
| [ ] | M2-1 | Postgres: users, profiles, ideas, versions, runs | Ahmad | A7 | |
| [ ] | M2-2 | Redis cache for verdicts and retrieval with TTL | Ahmad | A7, A4 | |
| [ ] | M2-3 | Migrations | Ahmad | A7 | |
| [~] | M2-4 | Cache keys include profile + rubric versions | Both | Z0, Z7, A7 | Z0 `verdict_key`; Z7 builds it with all versions (`test_cache.py`); M2 storage A7 |
| [~] | M2-5 | Cache hits visible | Both | Z3, Z7, A9 | Z3 metrics; Z7 `cache_hit` events, trace records and `from_cache` (`test_cache.py`); M2 and UI badge later |
| [ ] | M2-6 | Layered backend (routes, services, repositories) | Ahmad | A7 | |

## M3 Orchestrator

| Done | ID | Requirement | Owner | Phase | Evidence |
|:----:|----|-------------|-------|-------|----------|
| [~] | M3-1 | Discover + Validate as testable state machines | Zain | Z7, Z8 | Z7: Validate (`test_state_machine.py`, `test_resume.py`); Discover Z8 |
| [x] | M3-2 | Parallel agents | Zain | Z7 | Z7: `test_validate.py::test_scorers_run_in_parallel` |
| [x] | M3-3 | Retries and timeouts | Zain | Z7 | Z7: `test_scoring.py`, `test_lifecycle.py` (every call has a time limit) |
| [x] | M3-4 | Stop at budget and ask the user | Zain | Z7 | Z7: `test_budget.py` (fake M2 and M4) |
| [x] | M3-5 | Re-run only what changed | Zain | Z7 | Z7: `test_cache.py::test_profile_change_rescores_feasibility_only`, `test_clarify.py` |
| [x] | M3-6 | Every decision traced | Zain | Z7 | Z7: `test_validate.py::test_trace_records`, step decisions equal the path |

## M4 Scouts

| Done | ID | Requirement | Owner | Phase | Evidence |
|:----:|----|-------------|-------|-------|----------|
| [ ] | M4-1 | ≥ 3 sources | Ahmad | A3 | |
| [ ] | M4-2 | Scheduled (CronJob) and on demand | Ahmad | A3 | |
| [ ] | M4-3 | Per-source rate limits and failure handling | Ahmad | A3 | |
| [ ] | M4-4 | Internet only through M5 | Ahmad | A3, A2 | |

## M5 Tool Layer

| Done | ID | Requirement | Owner | Phase | Evidence |
|:----:|----|-------------|-------|-------|----------|
| [ ] | M5-1 | Connectors | Ahmad | A2 | |
| [ ] | M5-2 | Domain allow-list | Ahmad | A2 | |
| [ ] | M5-3 | Rate limits | Ahmad | A2 | |
| [ ] | M5-4 | robots.txt | Ahmad | A2 | |
| [ ] | M5-5 | Cache | Ahmad | A2 | |
| [ ] | M5-6 | Sanitiser stripping instructions aimed at models | Ahmad | A2 | |
| [ ] | M5-7 | NetworkPolicy: only M5 reaches the internet | Ahmad | A2, A10 | |
| [ ] | M5-8 | POST /fetch | Ahmad | A2 | |

## M6 Knowledge Base / M6i Ingestion

| Done | ID | Requirement | Owner | Phase | Evidence |
|:----:|----|-------------|-------|-------|----------|
| [ ] | M6-1 | Searchable memory; source of every evidence | Ahmad | A4 | |
| [ ] | M6-2 | Hybrid search with reranking | Ahmad | A4 | |
| [ ] | M6-3 | Filters by date, source, niche | Ahmad | A4 | |
| [ ] | M6-4 | Card ids for citations | Ahmad | A4 | |
| [ ] | M6-5 | Freshness and coverage tracking | Ahmad | A4 | |
| [ ] | M6-6 | Retrieval quality on ≥ 20 hand-written queries | Ahmad | A5 | |
| [ ] | M6-7 | POST /search | Ahmad | A4 | |
| [ ] | M6i-1 | Dedupe, chunk, embed, index | Ahmad | A4 | |
| [ ] | M6i-2 | Repeatable without duplicates | Ahmad | A4 | |
| [ ] | M6i-3 | ≥ 1,500 cards from ≥ 3 sources | Ahmad | A4 | |
| [ ] | M6i-4 | ingestion_runs recorded | Ahmad | A4 | |

## M7 Niche and Trend Analyst

| Done | ID | Requirement | Owner | Phase | Evidence |
|:----:|----|-------------|-------|-------|----------|
| [ ] | M7-1 | Named niches (clustering) | Ahmad | A6 | |
| [ ] | M7-2 | Monthly time series | Ahmad | A6 | |
| [ ] | M7-3 | Rising / flat / saturated | Ahmad | A6 | |
| [ ] | M7-4 | Gaps list feeding M8 and M9 | Ahmad | A6 | |

## M8 Idea Generator

| Done | ID | Requirement | Owner | Phase | Evidence |
|:----:|----|-------------|-------|-------|----------|
| [ ] | M8-1 | Ideator, Gap Analyst, Skeptic debate | Zain | Z8 | |
| [ ] | M8-2 | Limited debate rounds | Zain | Z8 | |
| [ ] | M8-3 | Every idea cites gap evidence | Zain | Z8 | |
| [ ] | M8-4 | Ideas defeated with evidence dropped | Zain | Z8 | |
| [ ] | M8-5 | No renamed copies; respects student hours | Zain | Z8 | |

## M9 Validation agents

| Done | ID | Requirement | Owner | Phase | Evidence |
|:----:|----|-------------|-------|-------|----------|
| [x] | M9-1 | Four scorers in parallel (six dimensions) | Zain | Z5, Z7 | Z5: four scorers; Z7: called in parallel (`test_validate.py`); one CPU model serialises scoring (D-010) |
| [x] | M9-2 | Own rubric, tools, output schema each | Zain | Z5 | Z5: own rubrics, KB/analyst queries and feature schema per scorer (`services/validators/src/validators/scorers/`; D-012) |
| [x] | M9-3 | Computed features + model judgment | Zain | Z5 | Z5: `test_features.py`, `test_gate_inputs.py`; model judgment through M10 (fake KB and analyst) |
| [x] | M9-4 | Every score cites evidence ids, else abstain | Zain | Z5 | Z5: `test_scoring.py::test_scores_cite_only_retrieved_ids`, `test_validators.py::test_empty_source_abstains` (fake KB) |

## M10 Scoring Model / M10t Fine-tuning

| Done | ID | Requirement | Owner | Phase | Evidence |
|:----:|----|-------------|-------|-------|----------|
| [~] | M10-1 | Fine-tuned 1B–3B (LoRA/QLoRA) in own container | Zain | Z4, Z9 | Z4: own container serving the base model; fine-tuned weights Z9 |
| [x] | M10-2 | POST /score, /health, /ready | Zain | Z4 | Z4: `services/scorer`, `test_api.py`; probes green on minikube |
| [x] | M10-3 | Schema validation, retry once, hosted fallback logged | Zain | Z4 | Z4: `eval/behaviour/test_scorer_cassettes.py` (live hosted call not verified: no key, M5 not built) |
| [ ] | M10-4 | Comparison base vs fine-tuned vs hosted | Zain | Z9 | |
| [~] | M10-5 | No weights in git; requests/limits measured | Zain | Z9, Z11 | Z4: no weights in git; base model measured (spec Z4 C4); final limits Z11 |
| [ ] | M10t-1 | Data, scripts, documented commands in repo | Zain | Z9 | |

## M11 Verdict Engine

| Done | ID | Requirement | Owner | Phase | Evidence |
|:----:|----|-------------|-------|-------|----------|
| [x] | M11-1 | Fixed rules, plain code, no LLM | Zain | Z2 | `test_purity.py` |
| [x] | M11-2 | Deterministic | Zain | Z2 | `test_properties.py`, `test_edge_cases.py` |
| [x] | M11-3 | Rules in a ConfigMap | Zain | Z2 | `test_api.py::test_changing_the_file_changes_behaviour`; cluster check, spec Z2 C3 |
| [x] | M11-4 | Unit tests: every gate, threshold edge, sensitivity, what-if | Zain | Z2 | `services/verdict/tests/unit/` (100 % coverage) |
| [x] | M11-5 | POST /verdict, /whatif | Zain | Z2 | `test_api.py` |

## M12 Critic and Guardrails

| Done | ID | Requirement | Owner | Phase | Evidence |
|:----:|----|-------------|-------|-------|----------|
| [x] | M12-1 | Claim check against evidence (/check) | Zain | Z6 | Z6: `test_check.py`, `eval/behaviour/test_critic.py` (fake KB, hand-written cassettes) |
| [x] | M12-2 | Injection defence (fetched text is data) | Zain | Z6 | Z6: `eval/behaviour/test_critic_injection.py`, `test_injection.py`; own rules only (D-013) |
| [x] | M12-3 | Input rail | Zain | Z6 | Z6: `services/critic/tests/unit/test_rail.py`, `test_adversarial.py` |
| [x] | M12-4 | Approval above budget | Zain | Z6, Z7 | Z6 rule; Z7: M3 asks and records the decision (`test_budget.py`) |

## M13 AgenticOps

| Done | ID | Requirement | Owner | Phase | Evidence |
|:----:|----|-------------|-------|-------|----------|
| [x] | M13-1 | Tracing per run (/trace, /runs/{id}/trace) | Zain | Z3 | `services/agentops`, `test_trace.py::test_thousand_records_ten_runs`, `test_postgres.py`; client from Z0 |
| [x] | M13-2 | Budgets | Zain | Z3, Z7 | Z3 budget file; Z7 enforcement in M3 (`test_budget.py`); M5 enforcement is A2 (AO-2) |
| [x] | M13-3 | Prompt and rubric registry | Zain | Z3 | `common/registry.py`, `scripts/check_registry.py`, `GET /registry` (`test_check_registry.py`, `test_api.py`); CI wiring proposed for A0 |
| [ ] | M13-4 | Evaluation harness | Zain | Z10 | |
| [ ] | M13-5 | Drift reports | Zain | Z10 | |
| [~] | M13-6 | Dashboard | Both | Z3, A9 | Z3: `GET /summary` (`test_summary.py`); UI page A9 |

## M14 Platform

| Done | ID | Requirement | Owner | Phase | Evidence |
|:----:|----|-------------|-------|-------|----------|
| [ ] | M14-1 | Protected main + PRs | Ahmad | A0 | |
| [ ] | M14-2 | GitHub Actions on every PR | Ahmad | A0 | |
| [ ] | M14-3 | Jenkins for delivery | Ahmad | A0, A10 | |
| [ ] | M14-4 | One Docker image per module | Both | Z1, A1 | |
| [ ] | M14-5 | Full K8s deployment with rollout and rollback | Ahmad | A10 | |

## Data contracts

| Done | ID | Requirement | Owner | Phase | Evidence |
|:----:|----|-------------|-------|-------|----------|
| [x] | DATA-1 | Idea card schema | Zain | Z0 | `test_contracts.py::test_guide_example_validates` |
| [x] | DATA-2 | Student profile schema | Zain | Z0 | `test_contracts.py::test_guide_example_validates` |
| [x] | DATA-3 | Evidence item schema | Zain | Z0 | `test_contracts.py::test_guide_example_validates` |
| [x] | DATA-4 | Dimension score schema | Zain | Z0 | `test_contracts.py::test_guide_example_validates` |
| [x] | DATA-5 | Verdict schema (+missing, gate_details, flips) | Zain | Z0, Z2 | `test_contracts.py::test_guide_example_validates` |
| [x] | DATA-6 | Trace record schema | Zain | Z0 | `test_contracts.py::test_guide_example_validates` |
| [~] | DATA-7 | Fine-tuning JSONL format | Zain | Z9 | Z0: `FineTuneExample` schema; data in Z9 |
| [x] | DATA-8 | Rubric files: anchors 0–5 + example per level | Zain | Z3 | `rubrics/*.yaml`, `test_registry.py::test_repo_rubrics_are_complete` |
| [ ] | DATA-9 | Storage model (app, kb, ops, Redis, files) | Both | A4, A7, Z3 | |

## API

| Done | ID | Requirement | Owner | Phase | Evidence |
|:----:|----|-------------|-------|-------|----------|
| [ ] | API-P1 | POST /auth/register, /auth/login | Ahmad | A8 | |
| [ ] | API-P2 | GET, PUT /profile | Ahmad | A8 | |
| [ ] | API-P3 | POST /discover, /validate | Both | A8, Z7 | |
| [~] | API-P4 | GET /runs/{id}, /runs/{id}/events (SSE) | Both | A8, Z7 | Z7: M3 `GET /runs/{id}` and the event stream; gateway and SSE A8 |
| [~] | API-P5 | POST /runs/{id}/answer, /approve | Both | A8, Z7 | Z7: M3 answer and approve (`test_api.py`); gateway A8 |
| [ ] | API-P6 | POST /ideas/{id}/challenge, /whatif | Both | A8, Z8 | |
| [ ] | API-P7 | GET /ideas, /ideas/{id}/versions, /compare | Ahmad | A7, A8 | |
| [~] | API-P8 | GET /metrics | Both | Z0, A8 | Z0: `/metrics` in `create_app`; gateway in A8 |
| [ ] | API-I1 | M5 POST /fetch | Ahmad | A2 | |
| [ ] | API-I2 | M6 POST /search | Ahmad | A4 | |
| [x] | API-I3 | M10 POST /score | Zain | Z4 | Z4: `POST /score`, spec Z4 C3 |
| [x] | API-I4 | M11 POST /verdict, /whatif | Zain | Z2 | `test_api.py` |
| [x] | API-I5 | M12 POST /check | Zain | Z6 | Z6: `services/critic/tests/unit/test_api.py` |
| [x] | API-I6 | M13 POST /trace, GET /runs/{id}/trace | Zain | Z3 | `test_trace.py`, minikube smoke (spec Z3 C3) |
| [~] | API-I7 | /health, /ready everywhere | Both | Z1, A1 | Z0: `/health`, `/ready` in `create_app`; services in Z1, A1 |

## Verdict rules

| Done | ID | Requirement | Owner | Phase | Evidence |
|:----:|----|-------------|-------|-------|----------|
| [x] | VE-1 | LLM never decides the verdict | Zain | Z2, Z7 | Z2 engine; Z7: `test_verdict_guard.py` |
| [x] | VE-2 | Six dimensions 0–5 + confidence + evidence ids, versioned rubric | Zain | Z3, Z5 | Z3 rubrics; Z5 six scores with confidence and KB evidence ids, `test_api.py::test_five_ideas_give_six_dimensions` (fake KB; real KB at S3) |
| [x] | VE-3 | Dimension definitions in rubrics | Zain | Z3 | `rubrics/*.yaml` (Z3), applied by the four scorers (Z5) |
| [x] | VE-4a | Evidence gate (≥3 sources, ≥2 platforms) → IE | Zain | Z2 | `test_gates.py` |
| [x] | VE-4b | Feasibility gate → NO-GO with hours gap | Zain | Z2 | `test_gates.py` |
| [x] | VE-4c | Risk gate → NO-GO with blocker named | Zain | Z2 | `test_gates.py` |
| [x] | VE-4d | Duplicate gate (unless Differentiation ≥ 3) → NO-GO with product named | Zain | Z2 | `test_gates.py` |
| [x] | VE-5 | Weighted score Σ w·s / 5, weights sum 100 | Zain | Z2 | `test_worked_example.py`, `test_oracle.py` |
| [x] | VE-6 | Goal weight presets | Zain | Z2 | `test_config.py` |
| [x] | VE-7 | GO rule (≥ T_go, no floor breach on first five) | Zain | Z2 | `test_scoring.py` |
| [x] | VE-8 | PIVOT rule naming weakest dimension | Zain | Z2 | `test_scoring.py` |
| [x] | VE-9 | NO-GO below T_no | Zain | Z2 | `test_scoring.py` |
| [ ] | VE-10 | T_go 70, T_no 50, floor 1.5 then tuned | Zain | Z2, Z10 | |
| [x] | VE-11 | Confidence formula, < 0.5 → IE | Zain | Z2 | `test_scoring.py::test_confidence_*` |
| [x] | VE-12 | Challenge penalties (−0.5 each, max −1.0, conf −0.1) | Zain | Z2, Z7 | Z2 penalties (`services/verdict/tests/unit/test_challenge.py`); Z7 passes only critic-supported objections (`test_validate.py`); the challenge endpoint is Z8 (API-P6) |
| [x] | VE-13 | Sensitivity (±20 % weights, ±1 least-confident) → BORDERLINE | Zain | Z2 | `test_sensitivity.py` |
| [ ] | VE-14 | What would make this GO (smallest change, re-run → new version) | Zain | Z2, Z8 | |
| [x] | VE-15 | Decision order per Fig. 4 | Zain | Z2 | `test_gates.py::test_first_failing_gate_decides` |
| [x] | VE-16 | Worked example as unit test | Zain | Z2 | `test_worked_example.py`, `test_whatif.py` |

## Fine-tuning

| Done | ID | Requirement | Owner | Phase | Evidence |
|:----:|----|-------------|-------|-------|----------|
| [ ] | FT-1 | Cheaper/faster/consistent goal stated | Zain | Z9 | |
| [ ] | FT-2 | ≥ 300 examples, all dims, all levels | Zain | Z9 | |
| [ ] | FT-3 | Hosted-drafted labels, ≥ 60 hand-checked and marked | Both | Z9 | |
| [ ] | FT-4 | Split by idea; no labelled-set ideas in training | Zain | Z9 | |
| [ ] | FT-5 | JSONL messages format | Zain | Z9 | |
| [ ] | FT-6 | LoRA/QLoRA 1B–3B on free GPU; settings recorded | Zain | Z9 | |
| [~] | FT-7 | Merge, GGUF, served behind /score; validate/retry/fallback | Zain | Z4, Z9 | Z4: GGUF served behind `/score` with validate/retry/fallback; adapter merge Z9; full image→PVC path with the real model not run (disk) |
| [ ] | FT-8 | Comparison table (MAE, within-1, valid JSON, real ids, latency, cost) + failure case | Zain | Z9 | |

## AgenticOps

| Done | ID | Requirement | Owner | Phase | Evidence |
|:----:|----|-------------|-------|-------|----------|
| [~] | AO-1 | Tracing under one run id + replay command | Zain | Z3, Z7, Z10 | Z0 client; Z3 storage; Z7: M3 traces every step and decision under the run id; replay Z10 |
| [~] | AO-2 | Budgets (tokens, pages, steps, cost) in orchestrator and tool layer | Both | Z7, A2 | Z3 caps; Z7 orchestrator enforcement (`test_budget.py`); tool layer A2 |
| [~] | AO-3 | Prompt and rubric registry; verdict records versions | Zain | Z3 | Z3: registry + version check; versions on verdicts since Z2; "new evaluation" per bump needs Z10 |
| [ ] | AO-4 | Evaluation harness (agreement, confusion matrix, behaviour) as K8s Job + nightly Jenkins | Both | Z10, A10 | |
| [ ] | AO-5 | Drift reports after each ingestion | Zain | Z10 | |
| [~] | AO-6 | Dashboard (cost/verdict, latency/agent, cache hit, fallbacks, approvals, blocked injections) | Both | Z3, A9 | Z3: `/summary` and Prometheus metrics; dashboard page A9 |
| [~] | AO-7 | Behaviour tests: invalid JSON, tool timeout, empty source, poisoned page | Zain | Z10 | Z6: poisoned page (`test_critic_injection.py`); drift-free full suite is Z10 |

## Verdict tuning and evaluation

| Done | ID | Requirement | Owner | Phase | Evidence |
|:----:|----|-------------|-------|-------|----------|
| [ ] | EVAL-1 | Labelled set (all case types), labelled independently by both | Both | Z10 | |
| [ ] | EVAL-2 | Confusion matrix vs human verdicts | Zain | Z10 | |
| [ ] | EVAL-3 | Tuning; false GO on human NO-GO reported separately | Zain | Z10 | |
| [ ] | EVAL-4 | Final values in ConfigMap; before/after matrices; no overfitting | Zain | Z10 | |
| [ ] | EVAL-5 | Retrieval metrics on ≥ 20 queries + baseline gate | Ahmad | A5 | |

## Data collection and ethics

| Done | ID | Requirement | Owner | Phase | Evidence |
|:----:|----|-------------|-------|-------|----------|
| [ ] | ETH-1 | Official APIs first; terms + robots.txt read | Ahmad | A2, A3 | |
| [ ] | ETH-2 | Per-domain rate limits, clear User-Agent, caching, back-off | Ahmad | A2 | |
| [ ] | ETH-3 | No logins, no private data | Ahmad | A3 | |
| [ ] | ETH-4 | Every card keeps source + URL; verdict card links them | Both | A4, A9 | |
| [~] | ETH-5 | Fetched text is data, never obeyed | Both | A2, Z6 | Z6: M12 scan, M9 filter and evidence-as-data judge prompt; M5 sanitiser is A2 |
| [ ] | ETH-6 | Card data snapshot | Ahmad | A4 | |

## Platform

| Done | ID | Requirement | Owner | Phase | Evidence |
|:----:|----|-------------|-------|-------|----------|
| [ ] | GIT-1 | Protected main: PR only, required checks, 1 review | Ahmad | A0 | |
| [ ] | GIT-2 | One issue per task, linked to PR | Both | all | |
| [ ] | GIT-3 | Conventional commits | Both | all | |
| [ ] | GIT-4 | Deliberate merge conflict resolved in a PR | Both | A10, Z11 | |
| [ ] | GIT-5 | Repo layout per Guide §4.1 | Both | baseline | |
| [ ] | LOC-1 | Local K8s enforcing NetworkPolicy (minikube + Calico) | Ahmad | A0 | |
| [ ] | LOC-2 | docker-compose.yml for dev | Ahmad | A0 | |
| [ ] | LOC-3 | .env.example | Both | baseline | |
| [ ] | LOC-4 | README starts the system with a short command list | Both | A10, Z12 | |
| [~] | DOCK-1 | One image per module, ≥ 6 images | Both | Z1, A1 | Z1: 7 images (`services/*/Dockerfile`); A1 pending |
| [~] | DOCK-2 | Multi-stage, non-root, pinned, .dockerignore | Both | Z1, A1 | Z1: Zain's 7 images, uid 10001, base pinned by digest (`test_packaging.py`); A1 pending |
| [ ] | DOCK-3 | Tagged by commit SHA | Ahmad | A0, A10 | |
| [ ] | DOCK-4 | Scanned in CI | Ahmad | A0 | |
| [ ] | CI-1 | Lint + type checks | Ahmad | A0 | |
| [ ] | CI-2 | Unit tests incl. verdict engine | Ahmad | A0 | |
| [ ] | CI-3 | UI component tests | Ahmad | A9 | |
| [ ] | CI-4 | Retrieval quality gate | Ahmad | A5 | |
| [ ] | CI-5 | Docker build + image scan | Ahmad | A0 | |
| [ ] | CI-6 | K8s manifest validation | Ahmad | A0 | |
| [ ] | JEN-1 | Jenkinsfile: checkout, build+tag SHA, push, deploy, smoke, nightly eval vs baseline | Ahmad | A10 | |
| [ ] | JEN-2 | Failure stops pipeline, nothing deployed | Ahmad | A10 | |
| [ ] | JEN-3 | Jenkins in container; kubeconfig as credential | Ahmad | A0 | |
| [ ] | JEN-4 | SCM polling or tunnel | Ahmad | A0 | |
| [~] | K8S-1 | Deployments + Services for all modules | Both | Z1, A1 | Z1: `k8s/base/<svc>.yaml` for Zain's 7; A1 pending |
| [ ] | K8S-2 | StatefulSet/PVC for Postgres, Redis, vector store | Ahmad | A0, A4 | |
| [ ] | K8S-3 | CronJobs (scouts, ingestion) + Jobs (eval, model import) | Both | A3, A4, Z9, Z10 | |
| [~] | K8S-4 | ConfigMaps (weights, thresholds, budgets, rubrics) + Secrets | Both | Z2, Z3, A0 | Z2 verdict-config; Z3 budgets, rubrics, prompts ConfigMaps; Secrets and the rest A0 |
| [~] | K8S-5 | Probes everywhere; requests/limits from measurements | Both | Z11, A10 | Z1: probes and initial requests/limits on Zain's 7; measured values in Z11, A10 |
| [ ] | K8S-6 | HPA on gateway | Ahmad | A8, A10 | |
| [ ] | K8S-7 | NetworkPolicy default-deny; only M5 egress; ui → api only | Ahmad | A0, A2 | |
| [ ] | K8S-8 | RollingUpdate maxUnavailable 0; rollback demonstrated | Ahmad | A10 | |
| [ ] | K8S-9 | Ingress single entry | Ahmad | A0 | |
| [ ] | K8S-10 | NetworkPolicy test (fail from non-M5, works from M5) | Ahmad | A2, A10 | |
| [~] | K8S-11 | Non-root, pinned image on every workload | Both | all | Z1: Zain's 7 (`test_packaging.py::test_manifest_workload_standards`); other workloads pending |
| [~] | K8S-12 | Traces to stdout + trace volume; metrics; dashboard | Zain | Z3 | Z0 stdout JSON lines; Z3 PVC `traces`, metrics, `/summary`; dashboard page A9 |

## Submission

| Done | ID | Requirement | Owner | Phase | Evidence |
|:----:|----|-------------|-------|-------|----------|
| [ ] | SUB-1 | Repo link with code, Dockerfiles, workflows, Jenkinsfile, k8s/, FT data/scripts, rubrics, eval sets, README | Both | Z12, A11 | |
| [ ] | SUB-2 | Report PDF ≤ 10 pages with sections a–h | Both | Z12, A11 | |
| [ ] | SUB-3 | Screenshots SS-1..18 with captions | Both | Z11, A10 | |
| [ ] | SUB-4 | AI-usage log + ≥ 3 ADRs | Both | all | |
| [ ] | SUB-5 | Signed contribution table matching git history | Both | Z12, A11 | |

## Anti-requirements (verify none happen)

| Done | ID | Requirement | Owner | Phase | Evidence |
|:----:|----|-------------|-------|-------|----------|
| [~] | AVOID-1 | No unbounded prompt/retries/rounds/spend | Zain | Z7, Z8 | Z7: bounded retries, time limits, one clarify round, one deep search, budgets (`test_scoring.py`, `test_budget.py`); debate rounds Z8 |
| [x] | AVOID-2 | Verdict cache key includes profile + rubric version | Zain | Z0, Z7 | Z0 key; Z7 use and no caching of degraded results (`test_cache.py`, `test_edge_cases.py`) |
| [x] | AVOID-3 | Model never decides/adjusts verdict | Zain | Z2, Z7 | Z2; Z7: `test_verdict_guard.py` |
| [~] | AVOID-4 | No score/claim without evidence id; competitors from KB only | Zain | Z5 | Z5 (M9): `test_validators.py::test_invented_competitor_name_never_leaves_m9`; claims in Z6, ideation in Z8 |
| [ ] | AVOID-5 | No model/scout calls from browser; no localhost in images | Both | A9, all | |
| [ ] | AVOID-6 | Retrieval tested by metrics; chunks keep card id | Ahmad | A4, A5 | |
| [ ] | AVOID-7 | No renamed copies; ideas respect hours | Zain | Z8 | |
| [~] | AVOID-8 | Thin end-to-end on K8s early (walking skeleton) | Both | Z1, A0, A1 | Z1: thin version of Zain's services on K8s with a stub run end to end (spec Z1 C3); A0, A1 pending |

## Screenshots (SUB-3)

Tracked in [`evidence/screenshots/captions.md`](evidence/screenshots/captions.md).
