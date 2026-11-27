# Z0 — Shared contracts, `libs/common`, repo tooling

| Field | Value |
|-------|-------|
| Phase | `Z0` from `docs/implementation-plan-zain.md` |
| Owner | Zain |
| Author | Claude Code headless session (`scripts/run-phase.sh`) |
| Branch | `feat/z0-contracts-common` (parent: `chore/headless-decision-log`, PR #1) |
| Status | Plan ready |
| Requirement IDs | GEN-7, GEN-8, GEN-9, DATA-1..8 (schemas), API-I7, API-P8 (metrics helper), M13-1 (client side), AO-1 (propagation), AVOID-2 (cache keys) |
| Architecture refs | §7.2, §7.3, §8.4, §8.5, §12.1, §12.2, §15, §19–§21 |
| Depends on | nothing |
| Issue / PR | filled in Part C |

---

## Part A — Specification

### A1. Objective
One installable package, `libs/common`, holding every shared JSON schema (Pydantic v2, exported to JSON Schema) and the runtime helpers every service needs (app bootstrap, service-to-service HTTP, tracing, LLM access, cache keys, settings). Both tracks then code against frozen contracts instead of each other (sync S1).

### A2. Context
- `libs/common` is the only code both members share (work-distribution §1). Every service of M1–M14 imports it.
- Nothing exists yet: no services, no fakes. M5 (`/llm/chat`) and M13 (`/trace`) do not exist, so `LLM_BACKEND=direct|fake` and a stdout-only trace sink must work without them.

### A3. Scope
**In scope**
- `common.contracts`: `core.py`, Zain's `orchestrator.py`, `agents.py`, `verdict.py`, `ops.py`; placeholders for Ahmad's `kb.py`, `analyst.py`, `tools.py`, `scouts.py`, `profile.py`, `gateway.py` (request/response models from §7.1/§7.2); `export.py` with `--check`.
- `common.service`, `common.context`, `common.http`, `common.trace`, `common.llm`, `common.cache_keys`, `common.settings`.
- Root `pyproject.toml` (ruff, mypy, pytest) and root `.dockerignore`.
- Fixtures: `eval/fixtures/cards_small.jsonl`, `run_events_validate.jsonl`, `ops_summary.json`.

**Out of scope**
- Any service logic, Dockerfiles, manifests (Z1+).
- Fake services (`FakeKB`, `FakeStore`, …): written by the first consumer phase.
- Rubric files and the prompt registry (Z3); fine-tuning data (Z9); real LLM cassettes (Z4+).
- CI workflow (Ahmad, A0). `export --check` is the command A0 calls.

### A4. Requirements and acceptance criteria
Tests live in `libs/common/tests/`.

| # | Req ID | Acceptance criterion | Verified by |
|---|--------|----------------------|-------------|
| 1 | GEN-7 | `pip install -e libs/common` works in `.venv`; `import common.contracts` succeeds | command: `pip install -e libs/common && python -c "import common.contracts"` |
| 2 | DATA-1..7 | The Guide example JSONs, copied from requirements §5 into the tests, validate (DATA-8 rubric too, with the anchors it requires) | `test_contracts.py::test_guide_example_validates` |
| 3 | GEN-7, DATA-3/4, M9-4, AVOID-4 | A score without evidence ids, score 5.5, confidence 1.2, an unknown dimension, a malformed evidence id and NaN are rejected | `test_contracts.py::test_invalid_variant_rejected` |
| 4 | GEN-7 | `python -m common.contracts.export --check` passes on committed schemas and fails on a stale, missing or extra file | `test_export.py` |
| 5 | GEN-8, API-I7, API-P8 | `create_app`: `/health` 200; `/ready` 503 when the check fails, raises or hangs and 200 when it passes; `/metrics` returns Prometheus text | `test_service.py::test_health`, `::test_ready_*`, `::test_metrics_prometheus_text` |
| 6 | AO-1 | Request id and `X-Run-Id` are accepted or generated, returned, logged as JSON, and errors use the §21.3 envelope without leaking input values or stack traces | `test_service.py::test_request_ids_propagate`, `::test_error_envelope_*`, `::test_json_log_has_run_id` |
| 7 | AO-1 | `ServiceClient` retries on 503, 429 and timeout, not on 400; keeps the same `Idempotency-Key`; propagates `X-Run-Id`; validates the response model | `test_http.py` (respx) |
| 8 | M13-1, DATA-6 | `TraceClient` writes one valid `TraceRecord` JSON line per record and POSTs in batches; a failing sink never raises into the caller; `step()` measures latency and records errors | `test_trace.py` |
| 9 | M10-3 groundwork, AO-7 groundwork | `LLMClient` fake backend replays a cassette; invalid JSON causes exactly one repair retry, a second invalid reply raises `LLMOutputError` | `test_llm.py::test_fake_replays_cassette`, `::test_invalid_json_one_repair_retry`, `::test_second_invalid_raises` |
| 10 | M2-4, AVOID-2, AO-3 | `verdict_key` changes when the profile version, rubric set, engine version, weights version or kb version changes (also idea text, user, prompt versions, scorer model) | `test_cache_keys.py::test_verdict_key_changes_with_each_input` |
| 11 | GEN-9 | Settings come from env vars; no `localhost` default in the package | `test_settings.py`, `grep -rn localhost libs/common/src` is empty |
| 12 | GEN-7 | Fixtures validate against the contracts: ≥ 60 cards from ≥ 3 sources including one poisoned card; run events; ops summary | `test_fixtures.py` |
| 13 | — | ruff + mypy clean; coverage of `libs/common` ≥ 85 % | `ruff check . && ruff format --check . && mypy libs services`; `pytest libs/common --cov=common` |
| 14 | GEN-1 | Ahmad reviews `core.py` (S1) | manual, in the PR; cannot pass in this session |

### A5. Interfaces and contracts
- **Endpoints added by `create_app`:** `GET /health` → `{"status":"ok"}`; `GET /ready` → 200 `{"status":"ready"}` or 503 error envelope `NOT_READY`; `GET /metrics` → Prometheus text.
- **Headers:** `X-Request-Id`, `X-Run-Id`, `X-Parent-Step`, `X-User-Id` (§21.2). Values not matching `[A-Za-z0-9._:-]{1,128}` are ignored.
- **Errors:** `{"error": {code, message, details, request_id}}` (§21.3).
- **Metrics:** `ideaval_http_requests_total{service,route,code}`.
- **Contract files:** all new. `core.py` holds the types of §7.3 and DATA-1..6.
- **Env vars:** `LOG_LEVEL`, `AGENTOPS_URL`, `TOOLS_URL`, `LLM_BACKEND`, `LLM_CASSETTE_PATH` (new), `LLM_MODEL_AGENT_DEFAULT|AGENT_CHEAP|SCORER_HOSTED|LOCAL_DEV` (new, `direct` backend only), `OLLAMA_BASE_URL`.

### A6. Design

```text
libs/common/
  pyproject.toml            # setuptools; extra [direct] = litellm
  schemas/*.json            # one JSON Schema per model
  src/common/
    contracts/{base,core,orchestrator,agents,verdict,ops,kb,analyst,tools,scouts,profile,gateway,export}.py
    context.py              # contextvars: request_id, run_id, parent_step, user_id
    service.py              # create_app, ApiError, JSON logging
    http.py                 # ServiceClient, ServiceError
    trace.py                # TraceClient, step()
    llm.py                  # LLMClient: gateway | direct | fake
    cache_keys.py
    settings.py
```

- **`ContractModel` base:** `extra="forbid"`, `allow_inf_nan=False`, so typos and NaN scores fail loudly (GEN-7).
- **Invariants in the models** (cheap, and every producer gets them): scored dimension needs evidence ids; abstained dimension (`score: null`) has confidence 0; evidence id starts with its card id; weights cover six dimensions and sum to 100; `T_no ≤ T_go`; `Verdict.scores` and `VerdictRequest.scores` hold each of the six dimensions once; `INSUFFICIENT_EVIDENCE` names what is missing (U10); accepted objections, blockers, gaps and generated ideas cite ≥ 1 evidence id; `VerdictRequest.goal` equals `profile.goal`.
- **`ServiceClient`:** tenacity, 1 try + 2 retries, exponential back-off with jitter, only on 429/5xx/`TimeoutException`. Every POST/PUT/PATCH carries an `Idempotency-Key` that stays the same across its retries (§21.4).
- **`TraceClient`:** `emit()` is synchronous and never raises: stdout JSON line, then a bounded buffer; a batch is POSTed to `{AGENTOPS_URL}/trace` when full, on `flush()` and `aclose()`. A failed POST keeps the records (oldest dropped past the bound). No `AGENTOPS_URL` → stdout only.
- **`LLMClient.chat(alias, messages, schema, run_id, agent, ...)`:** call backend → parse (code fences stripped) → validate with `schema`; on failure add the reply plus the validation error and call once more; then raise `LLMOutputError` (the agent abstains or fails the step, §12.2). Every backend call is traced as `llm_call`.
- **Cassette:** JSONL, one `{"content": …, "agent"?: …, "alias"?: …, "model"?, "tokens_in"?, "tokens_out"?, "cost_usd"?}` per line. Each call consumes the first unused line whose `agent`/`alias` (when present) match; none left → `LLMCassetteMissError`.
- **`cache_keys`:** sha256 over canonical JSON (sorted keys; idea text whitespace-normalised). All version arguments are required keyword arguments.

### A7. Edge cases and failure handling
| Case | Expected behaviour |
|------|--------------------|
| `ready_check` raises or exceeds its timeout | `/ready` 503, no stack trace in the response |
| Unhandled exception in a route | 500 envelope `INTERNAL_ERROR`; traceback only in logs |
| Request validation error | 422 envelope; submitted values not echoed (passwords) |
| Hostile or oversized id header | ignored; a new request id is generated |
| Downstream 503 three times | `ServiceError` with status 503 after 3 attempts |
| Downstream 200 with a wrong shape | `ServiceError` code `INVALID_RESPONSE`, no retry |
| Connection refused | `ServiceError` code `UNAVAILABLE`, no retry (§21.4 lists only 429/5xx/timeout) |
| M13 down | trace POST fails, warning logged, caller unaffected |
| Model returns prose / fenced JSON / wrong schema | fenced JSON accepted; otherwise one repair retry, then `LLMOutputError` |
| Backend timeout or error | `LLMUnavailableError`, traced with `error` |
| Cassette exhausted or file missing | `LLMCassetteMissError` |
| Score without evidence ids | validation error (M9-4) |

### A8. Test plan
| Level | What | Command |
|-------|------|---------|
| Unit | contracts, export, cache keys, settings, trace, llm | `pytest libs/common -q` |
| Contract | Guide examples, fixtures against models and exported JSON Schema | same |
| HTTP fakes | `ServiceClient`, gateway LLM backend (respx); `create_app` (FastAPI TestClient) | same |
| Integration / cluster | none in Z0 (no service, no store) | — |

No test calls a real model. Coverage target: ≥ 85 % of `libs/common`.

### A9. Assumptions and open questions
All are recorded in `docs/decisions.md` (Z0-*).
- The plan lists `RunStatus` both as an enum and as the §7.3 model: the model keeps the name (public API), the enum is `RunState`.
- `StudentProfile` gets `version` (default 1), as §7.1 returns it and cache keys need it.
- `verdict_key` also takes `user_id`, `prompt_versions` and `scorer_model`. §8.4 omits `user_id`, but `profile_version` is per user, so two students would share a cached verdict (deviation).
- `VerdictRequest` carries `rubric_set`, `kb_version`, `prompt_versions`, because M11 must record them (§8.5) and has no I/O to look them up.
- DATA-7 and DATA-8 get schema models now; the data files are Z9 and Z3. "At least one example per level" is enforced by the Z3 rubric checks, because the Guide example has a single example.
- `step_id` is time-ordered (milliseconds × 4096 + random) unless the caller passes one: unique across pods without a shared counter, still below 2^53 for the UI.
- `direct` backend model ids default to Gemini `*-latest` aliases and `ollama/llama3.2`; **not verified against the live API** in this phase (no real model calls). Overridable by env.
- Ahmad's contract files are first drafts from §7; he owns them after the merge.

### A10. Risks
| Risk | Mitigation |
|------|-----------|
| Ahmad needs different shapes | placeholders are small; changes are cheap before anyone depends on them |
| Strict `extra="forbid"` breaks a consumer on an additive change | one repo, all images build the same `libs/common` |
| Schema export differs between Pydantic versions | Pydantic pinned; `--check` catches it |

---

## Part B — Plan

### B1. Task breakdown
| # | Task | Files | Verification | Done |
|---|------|-------|--------------|------|
| 1 | Package and repo tooling | `libs/common/pyproject.toml`, `src/common/__init__.py`, `py.typed`, root `pyproject.toml`, `.dockerignore` | `pip install -e libs/common`; `ruff check .` | [ ] |
| 2 | Core and Zain's module contracts | `contracts/{base,core,orchestrator,agents,verdict,ops}.py`, `tests/test_contracts.py` | pytest: Guide examples, invalid variants | [ ] |
| 3 | Ahmad's placeholders and schema export | `contracts/{kb,analyst,tools,scouts,profile,gateway,export}.py`, `schemas/*.json`, `tests/test_export.py` | `export --check` | [ ] |
| 4 | Settings, context, cache keys | `settings.py`, `context.py`, `cache_keys.py` + tests | pytest | [ ] |
| 5 | App bootstrap and HTTP client | `service.py`, `http.py` + tests | pytest (TestClient, respx) | [ ] |
| 6 | Trace and LLM clients | `trace.py`, `llm.py` + tests | pytest | [ ] |
| 7 | Fixtures | `eval/fixtures/*`, `tests/test_fixtures.py` | pytest | [ ] |
| 8 | Independent test pass, fixes | tests, fixes | full suite, ruff, mypy, coverage | [ ] |
| 9 | Docs and PR | checklist, plan, deviations, architecture §8.4, AI log, README, setup, `.env.example` | review of the diff | [ ] |

No Dockerfile or manifest in this phase, so no image build or kubeconform.

### B2. Commit plan
1. `docs(spec): add Z0 spec`
2. `docs(plan): add Z0 plan`
3. `chore(common): package skeleton and repo tooling`
4. `feat(contracts): shared schemas and JSON Schema export`
5. `feat(common): settings, cache keys, service bootstrap, http client`
6. `feat(common): trace and llm clients`
7. `test(eval): contract fixtures`
8. `test(common)` / `fix(common)` from the test report
9. `docs(Z0): update spec report, checklist, plan, logs`

### B3. Definition of Done (phase)
- [ ] A4 criteria 1–13 pass (14 is a human review).
- [ ] ruff + mypy clean; coverage ≥ 85 %.
- [ ] Checklist, plan, deviations, AI-usage log, decision log updated.
- [ ] PR opened against `chore/headless-decision-log`.
