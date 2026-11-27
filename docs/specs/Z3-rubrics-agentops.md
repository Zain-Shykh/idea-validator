# Z3 — Rubrics, prompts registry, M13 AgenticOps core

| Field | Value |
|-------|-------|
| Phase | `Z3` from `docs/implementation-plan-zain.md` |
| Owner | Zain |
| Author | Claude Code headless session (`scripts/run-phase.sh`) |
| Branch | `feat/z3-rubrics-agentops` (parent: `feat/z2-verdict-engine`, PR #7) |
| Status | In review |
| Requirement IDs | DATA-8, VE-2, AO-1 (store + read), AO-2 (budgets config), AO-3, AO-6 (summary API), M13-1..3, M13-6, M2-5 (cache-hit aggregation), API-I6, K8S-12 |
| Architecture refs | `architecture.md` §7.2, §8.3, §8.5, §12.2, §12.4, §15, §17; D-002, D-003 |
| Depends on | Z0 (contracts, `common.trace`), Z1 (agentops stub), Z2 (`config/verdict.yaml`) |
| Issue / PR | — |

---

## Part A — Specification

### A1. Objective
Make the scoring rubrics, prompts and budget rules versioned artefacts that cannot change silently, and turn the AgenticOps stub into the service that stores every trace record and answers the dashboard questions.

### A2. Context
- `common.trace.TraceClient` already writes stdout lines and POSTs batches to `AGENTOPS_URL`, at least once (Z0-19). The M13 stub throws batches away.
- `common.contracts.agents.Rubric` checks anchors 0–5; per-level examples are not checked yet (Z0-6).
- `config/verdict.yaml` has `version` and `weights_version`, but nothing forces a bump (Z2-14).
- No real producer of traces exists yet (the orchestrator stub does not trace). Z3 therefore also fixes **how** run facts are written into trace records (A5), for Z4–Z7 and M5.
- No Postgres runs in the cluster yet (Z1-10; A0 owns it).

### A3. Scope
**In scope:** six rubrics + manifest; seven prompts; `common.registry` loader; `scripts/check_registry.py` + `config/registry.lock.json` (also covers `verdict.yaml` and `budgets.yaml`); `config/budgets.yaml` with a strict model; `services/agentops` trace store, index, trace read, registry, summary, metrics, Alembic migration; ConfigMaps, PVC, `db-migrate` init container.

**Out of scope:** budget enforcement (Z7 M3, A2 M5); rendering prompts and calling models (Z4–Z8); replay, evaluation, drift, `eval_runs` table (Z10); CI wiring of the registry check (A0, proposed in the PR); real Postgres in the cluster (A0); dashboard page (A9).

### A4. Requirements and acceptance criteria

Tests under `services/agentops/tests/` are written `A/…`, those under `libs/common/tests/` `L/…`.

| # | Req ID | Acceptance criterion | Verified by |
|---|--------|----------------------|-------------|
| 1 | DATA-8, VE-2, M13-3 | Each of the six rubrics has anchors 0–5 and at least one worked example per level; each example is a valid scorer output that cites ids present in its input | `L/test_registry.py::test_repo_rubrics_are_complete` |
| 2 | AO-3, M13-3 | On a temp copy, changing a rubric, prompt, `verdict.yaml` or `budgets.yaml` without a version bump fails the check, and `--update` refuses to record it; with a bump, `--update` succeeds and the check passes. A weight change also needs a new `weights_version`; a rubric bump also needs a new rubric set id | `L/test_check_registry.py` |
| 3 | M13-1, API-I6, AO-1 | 1,000 records for 10 runs posted in batches → `GET /runs/{id}/trace` returns each run's records ordered by `(ts, step_id)` with `parent_step` intact; JSONL files exist; `traces_index` totals (cost, tokens, steps, cache hits, fallbacks) are exact, also after batches are re-sent | `A/unit/test_trace.py::test_thousand_records_ten_runs` and `A/integration/test_postgres.py` |
| 4 | AO-6, M13-6, M2-5 | From seeded traces with known numbers, `GET /summary` returns cost per verdict, latency per agent (p50, p95), cache hit rate, fallbacks, approvals and blocked injections, plus runs by status and budget stops | `A/unit/test_summary.py::test_summary_known_numbers` |
| 5 | API-I6, GEN-7 | An invalid record (schema, unsafe `run_id`, absurd number, not JSON) → 422 with the error envelope, nothing stored, `ideaval_trace_rejected_total` grows, the service keeps answering | `A/unit/test_trace.py::test_invalid_batches_are_rejected_and_counted` |
| 6 | AO-1 | The Alembic migration goes up, down and up again against Postgres, and creates the `ops` database when missing | `A/integration/test_postgres.py::test_migration_up_down` |
| 7 | K8S-12, K8S-1 | On minikube the agentops pod is Ready with the `traces` PVC Bound and probes green; a posted record can be read back | manual (C4) + `eval/behaviour/test_packaging.py` |
| 8 | AO-2 | `config/budgets.yaml` loads into a strict model with all four caps for both workflows and per-action caps | `L/test_registry.py::test_repo_budgets` |
| 9 | AO-3, M13-3 | `GET /registry` lists every rubric, the manifest, every prompt and both config files with name, version and sha256 equal to the committed lock | `A/unit/test_api.py::test_registry_matches_lock` |

### A5. Interfaces and contracts

**Endpoints** (§7.2, unchanged paths)

| Endpoint | Behaviour |
|---|---|
| `POST /trace` | JSON array of `TraceRecord`, ≤ 1000 records, ≤ 5 MB → `202`. `422` invalid (whole batch), `413` too large, `503 STORAGE_UNAVAILABLE` (client retries; resending is safe) |
| `GET /runs/{id}/trace` | `TraceRecord[]` ordered by `(ts, step_id)`, one per `step_id`; unknown run → `[]` |
| `GET /summary` | `OpsSummary`; `503` when the database is down |
| `GET /registry` | `RegistryResponse` |
| `GET /ready` | database table reachable, trace directory writable, registry valid |
| `GET /drift/latest`, `GET /eval/latest` | unchanged stubs (Z10) |

**Contracts (`contracts/ops.py`, additive):** `OpsSummary.runs_by_status` and `AgentLatency.p50_ms` (both defaulted); `OpsEvent` names and one payload model per event.

**Run facts in traces.** A fact the dashboard needs is a `TraceRecord` with `kind="decision"`, `tool=<OpsEvent>` and `output=<payload JSON>`:

| `tool` | Payload | Emitted by |
|---|---|---|
| `run_started` | `{workflow, user_id}` | M3 |
| `run_status` | `{status: RunState}` | M3 |
| `verdict` | `{idea_id, version, verdict, versions}` | M3 |
| `approval` | `{approval_id, decision: pending\|approved\|declined}` | M3 |
| `injection_blocked` | `{stage, count}` | M5, M12 |
| `scorer_fallback` | `{dimension}` | M10 |

A decision record with another `tool` is stored and ignored by the numbers. A known event with a bad payload is stored, ignored and counted (`ideaval_trace_bad_events_total`). Cost and tokens are summed over all records, so each model call must carry its cost on **one** record only. A verdict-cache lookup is a `tool_call` with `tool="verdict_cache"` and `cache_hit`.

**Metric definitions**
- *cost per verdict* = total cost of all runs / number of verdict events (0 without verdicts).
- *latency per agent* = over `llm_call` and `tool_call` records, by `agent`: calls, mean, nearest-rank p50 and p95.
- *cache hit rate* = `cache_hit` records / all `llm_call` and `tool_call` records.
- *fallbacks* = `scorer_fallback` events. *blocked injections* = sum of `count`.
- *approvals* = latest decision per `(run, approval_id)`.
- *runs by status* = latest `run_status` per run (`RUNNING` if none); *budget stops* = runs in `BUDGET_STOPPED`.

**Env vars:** `AGENTOPS_TRACES_DIR`, `AGENTOPS_RUBRICS_DIR`, `AGENTOPS_PROMPTS_DIR`, `AGENTOPS_CONFIG_DIR`, `AGENTOPS_DATABASE_URL` (optional override), else `POSTGRES_HOST/PORT/USER/PASSWORD` + `OPS_DB`.

**Prometheus:** `ideaval_runs_total`, `ideaval_verdict_cost_usd`, `ideaval_agent_latency_seconds`, `ideaval_llm_tokens_total`, `ideaval_cache_requests_total`, `ideaval_scorer_fallbacks_total`, `ideaval_approvals_total`, `ideaval_injections_blocked_total`, `ideaval_budget_stops_total` (§15.1), plus `ideaval_trace_rejected_total`, `ideaval_trace_bad_events_total`. Only newly stored records are counted, so re-sent batches do not inflate them.

### A6. Design

| File | Responsibility |
|---|---|
| `rubrics/<dimension>.yaml`, `rubrics/manifest.yaml` | DATA-8; the manifest names the set `r1` and each rubric version |
| `prompts/<name>@v1.md` | front matter `name`, `version`; body with `{{placeholders}}`; evidence rule of §12.2 wherever outside text appears |
| `config/budgets.yaml` | `version`, `workflows.{discover,validate}`, `actions` |
| `common/registry.py` | load + validate rubrics, prompts, budgets; list entries with sha256. Needs PyYAML (extra `registry`) |
| `scripts/check_registry.py`, `config/registry.lock.json` | lock = `{kind/name: {version, sha256}}` (+ weights hash for the verdict rules). Check: files must equal the lock. `--update`: rewrites the lock only if every changed file has a higher version |
| `agentops/services/aggregate.py` | pure: records → run index row; rows → `OpsSummary` |
| `agentops/services/ingest.py` | validate batch, append JSONL, re-index touched runs, count metrics |
| `agentops/repositories/{files,index}.py` | JSONL on disk; SQLAlchemy table |
| `agentops/migrations/`, `agentops/migrate.py` | create database if missing, `alembic upgrade head` |

**Data model** `traces_index` (§8.3): `run_id` PK, `workflow`, `user_id`, `status`, `steps`, `tokens_in`, `tokens_out`, `cost_usd`, `latency_ms` (first start to last end), `cache_hits`, `fallbacks`, `started_at`, `finished_at`, `versions` JSON, plus `metrics` JSON (cacheable count, verdict labels, approvals, blocked injections, per-agent latencies) so `/summary` never reads trace files.

**Ingest algorithm**
```
lock
for each run in the batch:
    known = step_ids already in the run's files
    append the lines of unknown step_ids (first one wins; changed after review, C2) to traces/<UTC date of record.ts>/<run_id>.jsonl
    records = all lines of the run, one per step_id (first wins)
    upsert index(records)            # recomputed, never incremented: re-sends are harmless
    count Prometheus metrics for step_ids not in known (after the upsert succeeded)
```

### A7. Edge cases and failure handling
| Case | Expected behaviour |
|------|--------------------|
| Same batch sent twice | totals and returned trace unchanged |
| Database down during ingest | 503; lines stay on disk; the retry indexes them |
| Half-written last line (crash, full disk) | next append starts on a new line; unreadable lines are skipped and logged |
| Disk full / directory not writable | 503; `/ready` 503 |
| `run_id` with `/` or other unsafe characters | 422; nothing touches the filesystem |
| Huge integers or cost | 422 (they would overflow the database) |
| Run crossing midnight | two files, read together |
| Invalid rubric / prompt / budgets mounted | app starts, `/ready` 503, `/registry` 503 |
| Registry file duplicated (`intake@v1` and `intake@v2`) | loader error |

### A8. Test plan
| Level | What | Command |
|-------|------|---------|
| Unit | registry loader, check script (temp copies), aggregation, ingest with SQLite | `pytest libs/common services/agentops -q` |
| Contract | every response validates against `common.contracts` | same |
| Integration | migration and ingest against Postgres | `POSTGRES_HOST_PORT=5433 docker compose up -d postgres` then `AGENTOPS_TEST_DATABASE_URL=… pytest -m integration services/agentops` |
| Static | manifests, ConfigMap file lists, Dockerfile | `pytest eval/behaviour`; kubeconform |
| Cluster smoke | pod Ready, PVC Bound, post + read | `kubectl …` |

Coverage target: ≥ 80 % `agentops`, ≥ 85 % `libs/common`.

### A9. Assumptions and open questions
- A rubric level 0 is an **evidence-backed** lowest score. "No evidence at all" is an abstention, not a 0 (M9-4); the anchors say so.
- Rubric and prompt texts are first versions; Z9/Z10 may change them with a version bump.
- Budget values are starting guesses; Z7 and Z11 tune them.
- The check compares with a committed lock file, so it works without git history. Editing the lock by hand would bypass it; that is visible in review.
- Trace files are never deleted (volume 1 GiB); retention is a follow-up.
- The dev overlay may carry a temporary Postgres until A0 provides the real one.

### A10. Risks
| Risk | Mitigation |
|------|-----------|
| Producers do not follow the event convention | typed payload models in contracts; bad events are counted |
| Re-reading a run's file on every batch is O(n²) per run | runs are bounded (budgets); marked in code, incremental index if it matters |
| Little disk | build only the agentops image; remove it afterwards |

---

## Part B — Plan

### B1. Task breakdown
| # | Task | Files | Verification | Done |
|---|------|-------|--------------|------|
| 1 | Rubrics, manifest, prompts, budgets, registry loader | `rubrics/**`, `prompts/**`, `config/budgets.yaml`, `libs/common/src/common/registry.py`, `libs/common/pyproject.toml` | `pytest libs/common/tests/test_registry.py` | [x] |
| 2 | Registry check and lock (incl. the Z2 follow-up) | `scripts/check_registry.py`, `config/registry.lock.json` | `pytest libs/common/tests/test_check_registry.py` | [x] |
| 3 | Ops contracts: events, two summary fields, schemas, fixture | `contracts/ops.py`, `libs/common/schemas/`, `eval/fixtures/ops_summary.json` | `python -m common.contracts.export --check` | [x] |
| 4 | AgentOps: files, index, ingest, aggregation, routes, metrics, migration | `services/agentops/**`, `pyproject.toml` | `pytest services/agentops`, mypy | [x] |
| 5 | Packaging: requirements, Dockerfile, compose, ConfigMaps, PVC, init container, temporary Postgres in dev | `services/agentops/{Dockerfile,compose.yaml,requirements.txt}`, `k8s/**`, `{rubrics,prompts,config}/kustomization.yaml`, `eval/behaviour/test_packaging.py`, `.env.example` | packaging tests, kubeconform | [x] |
| 6 | Postgres integration tests | `services/agentops/tests/integration/` | `pytest -m integration services/agentops` | [x] |
| 7 | Testing subagent, fixes | — | full suite | [x] |
| 8 | Image build, minikube smoke | `k8s/overlays/dev` | pod Ready, PVC Bound | [x] |
| 9 | Docs and PR | `docs/**` | — | [x] |

### B2. Commit plan
`docs(spec)` → `docs(plan)` → `feat(registry): rubrics, prompts, budgets, loader` → `feat(registry): version lock check` → `feat(contracts): ops events` → `feat(agentops): trace store, summary, registry` → `chore(k8s): agentops config, PVC, migrate` → `test`/`fix` → `docs(Z3)`. Decision rows go into the commit they belong to.

### B3. Definition of Done
- [x] All A4 criteria pass.
- [x] `ruff check .`, `ruff format --check .`, `mypy libs services` clean; coverage targets met.
- [x] agentops image builds, non-root; manifests validate.
- [x] Checklist, plan, deviations, decisions, AI-usage log, setup updated.
- [x] PR against `feat/z2-verdict-engine`.

---

## Part C — Implementation and test report

### C1. What was built
| File | What |
|---|---|
| `rubrics/*.yaml`, `rubrics/manifest.yaml` | six rubrics v1 (anchors 0–5, one worked example per level, all fictional), set `r1` |
| `prompts/*@v1.md` | seven prompts with front matter and the evidence-as-data rule; none asks for a verdict about an idea |
| `config/budgets.yaml` | caps v1 per workflow and per action |
| `libs/common/src/common/registry.py` | loaders and validation (extra `common[registry]`) |
| `scripts/check_registry.py`, `config/registry.lock.json` | version lock for 16 files, incl. `verdict.yaml` and `weights_version` (Z2 follow-up) |
| `libs/common/src/common/contracts/ops.py` (+ schemas, fixture) | ops events; `runs_by_status`, `p50_ms` |
| `services/agentops/**` | trace ingest and read, JSONL files, `traces_index`, migration, summary, registry, metrics |
| `k8s/base/agentops.yaml`, `{config,rubrics,prompts}/kustomization.yaml`, `k8s/overlays/dev` | ConfigMaps, PVC `traces`, `db-migrate` init, temporary Postgres, agentops tag `9d8410a` |
| `services/orchestrator/tests/unit/test_run.py` | its contract test now runs against the real AgentOps app on SQLite |

### C2. Deviations from spec / architecture
| What | Why | Logged in deviations.md as |
|------|-----|---------------------------|
| Run facts are decision records with a typed payload; `OpsSummary` has two more fields | DATA-6 has no fields for the AO-6 facts | D-007 |
| Only `traces_index` exists in `ops` (plus a `metrics` column); `/registry` reads the mounted files | no reader for the other tables yet | D-008 |
| Spec said the last record wins for a repeated `step_id`; the first one wins | a later write could rewrite the audit trail | Z3-14 (spec updated) |

An automated security review ran during the session. (1) Last-wins: fixed. My first fix only changed which line won on read, so a re-sent record with an older date still replaced the original through folder order; it was also committed with its new test failing, because a pipe to `tail` hid the exit code (`ec168ac`). The review and the test both showed it; `6ef6df3` decides it at ingest. (2) No authentication on `/trace` and `/runs/{id}/trace`: not changed. This is the known limit in architecture §16; the routes are not exposed through the gateway and NetworkPolicy is A0's.

### C3. Test report
Testing subagent (`agent-skills:test-engineer`; added `test_edge_cases.py`, `test_registry_edges.py`, two Postgres tests and more lock and packaging tests; changed no production code):
- Before its tests: 664 passed. After: 784 passed, 7 failed (one bug); integration 5 passed; ruff, format, mypy clean; kubeconform 25/25; coverage 99 % over `common` + `agentops`.
- No flaky tests (three repeated runs).

| Finding | Result (`b181715`) |
|---|---|
| Bug: an impossible YAML date raised a plain `ValueError`; agentops crashed at import instead of staying not ready | fixed in the shared loader |
| A deleted prompt could be recorded and re-added changed under the same version | `--update` refuses removals; test added |
| `run_id` `.`/`..` accepted but unreadable over HTTP; `parent_step` unbounded | rejected with 422 |
| Dev overlay still tagged the stub image | updated after the build |
| A deleted lock can be re-created with `--update` | not changed (visible in the diff) |
| A two-day batch failing on the second file undercounts Prometheus | not changed (index totals stay right) |
| `TraceClient` drops a batch on 413 (needs > 250 kB per record) | not changed, follow-up |

Final run: `ruff check .`, `ruff format --check .`, `mypy libs services scripts/check_registry.py` clean; `pytest -q` → 794 passed, 8 deselected; coverage `agentops` + `common.registry` 98 % in their own tests (`migrate.py` 76 % there, its Postgres branch runs in the integration tests); `pytest -m integration services/agentops` → 5 passed; `python scripts/check_registry.py` and `python -m common.contracts.export --check` ok. The 3 Redis integration tests of the orchestrator were not run.

Manual checks (image `ghcr.io/zain-shykh/idea-validator-agentops:9d8410a`):
- Compose (Postgres on 5433): `agentops-migrate` exit 0, agentops healthy, uid 10001; posted the seeded runs → 202, 10 records for `run_a`, summary 2 runs / 3 verdicts / $0.003 per verdict / hit rate 0.4 / 1 fallback / approvals 1-1-1 / 4 injections; 16 registry entries.
- minikube `idea-validator`: `kubectl apply -k k8s/overlays/dev` with a hand-made Secret `db`; PVC `traces` Bound (1 Gi); new agentops pod Ready, 0 restarts, `db-migrate` "database is up to date"; from the orchestrator pod `/ready` 200, post 202, unsafe `run_id` 422, trace with parent link, 16 registry entries; after `rollout restart` the same trace and summary were returned. Profile stopped afterwards; host images removed (the copy in the minikube node stays).

### C4. Measurements
| What | Value |
|---|---|
| agentops memory on compose after the smoke test | 68 MiB (limit 384 MiB) |
| agentops image on disk | 272 MB |
| 1,000 records, 10 runs, re-sent in part (test, SQLite, host) | about 3 s |

In-cluster memory was not measured (`kubectl top` had no data yet).

### C5. Follow-ups
- **A0 (Ahmad):** run `python scripts/check_registry.py` in CI; real Postgres Service `postgres` and Secret `db` (`POSTGRES_PASSWORD`) in `create_secrets.sh`, replacing the temporary one; NetworkPolicy limiting who reaches agentops.
- **A2/Z6/Z7/Z4 (producers):** emit the ops events of `contracts/ops.py`; put each model call's cost on one record only (the caller or M5, not both).
- **A9:** the Ops page can show `runs_by_status` and `p50_ms`.
- Z4–Z8: render prompts and escape evidence text so it cannot close its `<evidence>` block.
- Z7: enforce `budgets.yaml` through `common.registry.load_budgets`.
- Z10: `eval_runs` migration, drift and evaluation reports, replay; require an evaluation run after a version bump.
- Later: trace retention (1 GiB volume), SQL aggregation for `/summary`, per-run incremental index, duplicate YAML keys.
