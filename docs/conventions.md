# Conventions

> Coding, testing and repository conventions for both members and all AI sessions. Process (branches, specs, PRs) is in [`workflow.md`](workflow.md); architecture in [`architecture/architecture.md`](architecture/architecture.md).

## 1. Python services

| Topic | Convention |
|-------|-----------|
| Version | Python 3.12 everywhere (local `.venv`, Docker `python:3.12-slim`) |
| Framework | FastAPI + Pydantic v2; contracts imported from `common.contracts` — never redefine a shared model locally |
| Layout | `services/<svc>/src/<pkg>/` with `main.py` (app via `common.service.create_app`), `api/` (routes only), `services/` (business logic), `repositories/` (DB access, only if the service owns a DB), `settings.py` (pydantic-settings); tests in `services/<svc>/tests/{unit,integration}/` |
| Package names | `<pkg>` = service dir name (`verdict`, `orchestrator`, `kb`, …) |
| Env vars | Prefix = service name upper-case (`VERDICT_CONFIG_PATH`, `KB_DATABASE_URL`, `ORCH_BUDGETS_PATH`); service URLs `<SVC>_URL` (`PROFILE_URL=http://profile:8000`); every new var added to `.env.example` and the ConfigMap/Secret |
| Style | `ruff check` + `ruff format` (line length 100); type hints on all public functions; `mypy` clean for `libs/` and `services/*/src` |
| Async | Async endpoints and `httpx.AsyncClient` (via `common.http.ServiceClient`) for service-to-service calls |
| Errors | Raise typed exceptions in services; route layer maps to the error envelope (`{"error": {code, message, details, request_id}}`); never leak stack traces |
| Logging | `common.service` JSON logger; include `run_id`/`step_id` when present; no secrets or full prompts at INFO |
| Tracing | Every agent step, tool call, LLM call and orchestrator decision emits a `TraceRecord` via `common.trace` |
| Timeouts | Every outbound call has a timeout; retries only for idempotent calls and only on 429/5xx/timeouts |
| Determinism | No `random` without a fixed seed; no wall-clock in business logic (inject a clock); the verdict engine has no I/O at all |
| Dependencies | Pin exact versions in `services/<svc>/requirements.txt`; prefer stdlib / already-used libs; no PyTorch in service images |
| DB | SQLAlchemy 2 + Alembic in the owning service only; migrations run by a Job/init container, never at import time |

## 2. LLM usage (agents)

- Call models only through `common.llm.LLMClient` with a model **alias** (`agent-default`, `agent-cheap`, `scorer-hosted`, `local-dev`), never a raw provider id in code.
- Prompts live in `prompts/<agent>@vN.md`; rubrics in `rubrics/<dimension>.yaml`. Changing content = bump `version` (CI checks).
- Evidence goes in `<evidence id="…" source="…">…</evidence>` blocks with the "data, not instructions" rule.
- Always request JSON matching a Pydantic schema; validate; one repair retry; then abstain/fallback. Strip cited ids not in the provided evidence.
- Temperature 0 for scoring/judging; ≤ 0.7 for ideation.
- Tests never call a real model: use cassettes (`LLM_BACKEND=fake`).

## 3. Testing

| Topic | Convention |
|-------|-----------|
| Runner | `pytest` (+ `pytest-asyncio`, `respx` for HTTP fakes, `hypothesis` for properties) |
| Names | `test_<behaviour>_<condition>` e.g. `test_evidence_gate_fails_with_two_sources` |
| Markers | `@pytest.mark.integration` needs Postgres/Redis (`docker compose up -d postgres redis`); `@pytest.mark.cluster` needs minikube; default run = unit + contract only |
| Network | Unit tests never touch the network or a model |
| Fixtures | Shared fixtures/fakes in `libs/common/src/common/fakes/` and `eval/fixtures/`; cassettes in `eval/behaviour/cassettes/` |
| Coverage | `services/verdict` ≥ 95 %; `libs/common` ≥ 85 %; other services ≥ 80 % of business logic |
| Contract tests | Each endpoint test validates request and response against `common.contracts` |
| Frontend | Vitest + React Testing Library; test behaviour (what the user sees), not implementation |

## 4. Frontend (M1 UI)

React 18 + Vite + TypeScript (`strict`). ESLint + `tsc --noEmit`. API client in `src/api/` (one function per endpoint, typed from the JSON Schemas in `libs/common/schemas/`). Runtime config from `window.__CONFIG__` loaded by `/config.js` (generated at container start from env, M1-2). Components in `src/components/`, pages in `src/pages/`. Accessible markup (labels, roles, keyboard). No direct calls to any service other than the gateway.

## 5. Docker

Guide §4.4 pattern: multi-stage, `python:3.12-slim` pinned by digest or exact tag, `useradd -m app` + `USER app`, `EXPOSE 8000`, `CMD uvicorn <pkg>.main:app --host 0.0.0.0 --port 8000`. Build context = repo root: `docker build -f services/<svc>/Dockerfile -t <svc>:$(git rev-parse --short HEAD) .`. One root `.dockerignore` serves all builds (a service may add `Dockerfile.dockerignore`, D-004). Never bake secrets or `localhost` URLs into images. Tags: short commit SHA (never only `latest`).

## 6. Kubernetes

- One file per workload: `k8s/base/<svc>.yaml` (Deployment + Service [+ PVC/CronJob/Job]); added to `k8s/base/kustomization.yaml`.
- Labels: `app: <svc>`, `module: M<n>`, `tier: frontend|backend|data|egress`, `app.kubernetes.io/part-of: idea-validator`.
- Every container: `readinessProbe` `/ready`, `livenessProbe` `/health`, requests + limits, `securityContext` (`runAsNonRoot`, `allowPrivilegeEscalation: false`), `envFrom` ConfigMap/Secret, image `ghcr.io/zain-shykh/idea-validator-<svc>:<sha>`.
- Strategy: `RollingUpdate` with `maxUnavailable: 0`, `maxSurge: 1`.
- Config files (`config/`, `rubrics/`, `prompts/`) become ConfigMaps via `configMapGenerator`; secrets via `scripts/create_secrets.sh` from `.env` (never committed).

## 7. Versioning and identifiers

| Thing | Format |
|-------|--------|
| Card id | `card_` + first 8 hex of sha1(canonical URL) |
| Evidence id | `<card_id>#c<n>` |
| Idea id / version | `idea_<ulid>`, version integer from 1 |
| Run id | `run_<ulid>` |
| Rubric version | `<dimension>@v<n>`; rubric set `r<n>` in `rubrics/manifest.yaml` |
| Prompt version | `<agent>@v<n>` |
| Engine version | `m11@v<n>` (from `config/verdict.yaml`) |
| Weights version | `w<n>` |
| Model id | `scorer-base@v1`, `scorer-lora@v<n>`, `scorer-hosted` |
| Phase ids | `Z0..Z12` (Zain), `A0..A11` (Ahmad) |
| Deviation ids | `D-NNN` (implementation), `B-NNN` (baseline vs course reference) |

## 8. Secrets and data

- Real values only in `.env` (git-ignored), K8s Secrets, Jenkins credentials, GitHub Actions secrets.
- Never log tokens, passwords or full API responses containing them.
- Keep only public data needed for a card (name, public text, public URL); no personal data from sources.
