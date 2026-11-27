# Requirements — Idea Generator with Go / No-Go

> **Single source of truth.** This file restates every requirement from `docs/Project.pdf` (the *Description*: **what** to build) and `docs/Project_Guide.pdf` (the *Guide*: **how**), so nobody has to re-read the PDFs.
> If this file and the PDFs ever disagree, the PDFs win — fix this file in a PR and note it in `docs/architecture/deviations.md`.
>
> - Every requirement has a stable ID. Specs, plans, commits, PRs and `docs/checklist.md` reference these IDs.
> - "MUST" = graded requirement from the PDFs. "SHOULD" = our own design decision (see `docs/architecture/architecture.md` and `docs/decisions/`).
> - Field lists and numbers in the Guide are **minimums**: we may add fields, never remove them.
> - Course: CS4032 Software Construction and Development, FAST-NUCES. Group of 2: **Zain** and **Ahmad**.

---

## 0. Product summary

An interactive multi-agent system that helps a **student choose a project**. It collects startup ideas, products and niches from public sources (YC directory, Hacker News, GitHub, optionally Product Hunt / Reddit), organises them into a map of niches and trends, and works in two modes:

| Mode | Input | Output |
|------|-------|--------|
| **Discover** | Student profile (skills, hours, team size, compute, goal = `startup` \| `portfolio` \| `course_project`) + optional niche wish | Ranked ideas and niches that fit the student, including **new ideas built from gaps** found in the data, each with a verdict and short reason |
| **Validate** | An idea (the student's own, or one the system proposed) + profile | A verdict **GO / PIVOT / NO-GO / INSUFFICIENT EVIDENCE**, with the evidence behind it, what would change it, and how stable it is |

It is a complete product: web UI, multi-agent backend, knowledge base (RAG), a fine-tuned scoring model, AgenticOps (monitoring + evaluation of agents), and a delivery pipeline that builds, tests and deploys everything on Kubernetes.

**Core principle (non-negotiable):** the language model **never** outputs or adjusts the verdict. Models produce evidence-backed scores; a deterministic, unit-tested program (M11 Verdict Engine) turns them into the verdict. It is *decision support*, not a prediction of success.

The same idea can get different verdicts for different students (e.g. GO for a team with 200 free hours, NO-GO for a solo student with 20).

---

## 1. General rules (GEN)

| ID | Requirement |
|----|-------------|
| GEN-1 | Group of 2. **Both members commit code**, **review each other's pull requests**, and **can explain the whole system**. |
| GEN-2 | AI coding tools and any library/framework are allowed. We are responsible for every line. Keep an **AI-usage log** (what AI wrote, what we changed, why) → `docs/ai-usage-log.md`. |
| GEN-3 | Collect data **only through official APIs or pages that allow it**; respect `robots.txt` and rate limits; keep **no private data**. |
| GEN-4 | **Never commit keys or passwords.** Commit a `.env.example` with names only. |
| GEN-5 | Required technology: GitHub, GitHub Actions, Jenkins, Docker, Kubernetes, RAG, fine-tuning, AgenticOps. |
| GEN-6 | The **whole system runs on Kubernetes, one container per module**. Compose is for local development only. |
| GEN-7 | All modules exchange **JSON**. Each schema is defined **once** (Pydantic), every input and output is validated against it, and schema files are kept in the repo (`libs/common/src/common/contracts/`, exported JSON Schema in `libs/common/schemas/`). |
| GEN-8 | Every service exposes `GET /health` (process alive) and `GET /ready` (can serve now, e.g. DB reachable). |
| GEN-9 | Settings in environment variables or ConfigMaps; never hard-code `localhost` in an image. |

---

## 2. User capabilities (U) — from Description §2

| ID | The user can | Done when |
|----|--------------|-----------|
| U1 | Sign up, log in and save a profile. | The profile is stored, editable, and **changes later recommendations**. |
| U2 | Ask for ideas in a niche or in general (**Discover**). | A **ranked list** of ideas is returned, each with a **verdict** and a **short reason**. |
| U3 | Watch the agents work live. | The screen shows the **running agent, its tool calls and its spend** as they happen. |
| U4 | Approve or decline an expensive deep search. | The system **states the cost and waits** before going over budget. |
| U5 | Submit their own idea (**Validate**) and answer clarifying questions. | **Only questions that change the result** are asked, then a verdict card is shown. |
| U6 | Read a verdict card. | It shows the **verdict, gates, six scores with evidence links, weights, confidence and stability**. |
| U7 | Ask "what would make this GO?" | **Concrete changes** are proposed and **one can be applied**, giving **version 2 computed by the engine**. |
| U8 | Press "Challenge" on a claim. | The **critic re-checks** it against its evidence and **defends or removes** it. |
| U9 | Compare ideas and see history. | Past ideas and versions are listed and **two ideas compare on all six dimensions**. |
| U10 | See why a verdict is INSUFFICIENT EVIDENCE. | The card says **what was missing** and **does not guess**. |

---

## 3. Workflows (WF)

### 3.1 Discover (Description Fig. 2)

| ID | Step | Modules | Requirement |
|----|------|---------|-------------|
| WF-D1 | Request | M1, M2 | Student sends profile and niche wish. |
| WF-D2 | Niches | M7 + M6 | Pick niches and trends that fit the profile. |
| WF-D3 | Ideator | M8 | Proposes ideas **citing gap evidence**. |
| WF-D4 | Gap Analyst | M8 + M6 | Adds complaints and missing features (from the KB). |
| WF-D5 | Skeptic | M8 | Argues against each idea **with evidence**. |
| WF-D6 | Critic | M12 | Keeps **only objections backed by evidence**. |
| WF-D7 | Debate loop | M8, M12 | Steps 3–6 repeat **up to 3 rounds**; ideas the Skeptic **defeats with evidence are dropped**. |
| WF-D8 | Validate | M9–M11 | **Survivors go through the Validate workflow** like any other idea. |
| WF-D9 | Result | M1, M13 | Ranked ideas with verdict cards; run traced. |

### 3.2 Validate (Description Fig. 3)

| ID | Step | Modules | Requirement |
|----|------|---------|-------------|
| WF-V1 | Intake | M1, M3 | Idea + profile; **clarify if needed** (only result-changing questions, U5). |
| WF-V2 | Evidence | M6, M4, M5 | Retrieve cards; **scout on demand** (deep search; needs approval when over budget, U4). |
| WF-V3 | Scorers | M9 + M10 | **4 scorers run in parallel**, each with its own rubric. |
| WF-V4 | Features | M6, M7 | **Computed numbers** from the evidence (code, not model). |
| WF-V5 | Verdict | M11 | Gates, weights, thresholds, confidence. |
| WF-V6 | Challenge | M8, M12 | Skeptic objects; critic checks claims. |
| WF-V7 | Stability | M11 | Sensitivity check and what-would-make-GO. |
| WF-V8 | Card | M1, M13 | Verdict card; **trace and cost recorded**. |
| WF-V9 | Rule | — | The model gives scores with evidence; **only the Verdict Engine decides the verdict**. |

---

## 4. Module requirements (M1–M14) — Description §4

Every module: own container + own Dockerfile, reachable in Kubernetes, `/health` + `/ready`, JSON contracts validated.

### M1 — Web UI and API Gateway (the only entrance)
| ID | Requirement |
|----|-------------|
| M1-1 | UI: chat, live progress, verdict card, approval prompt, history (+ profile, compare, challenge, what-if, ops dashboard). |
| M1-2 | **API address set at runtime** (not baked into the UI bundle). |
| M1-3 | **Login** (register + login; protected endpoints). |
| M1-4 | **Rate limits** on the public API. |
| M1-5 | **Input validation** on every public endpoint. |
| M1-6 | **Component tests** for the UI. |
| M1-7 | Gateway is the **only** public entry point; the browser never calls models or scouts directly. |
| M1-8 | Exposes every public endpoint in §6.1 (API-P*). |

### M2 — Profile, Session and Cache
| ID | Requirement |
|----|-------------|
| M2-1 | **Postgres** for users, profiles, ideas, versions and runs (tables in §5.4). |
| M2-2 | **Redis** for cached verdicts and retrieval, with TTL. |
| M2-3 | **Migrations** (Alembic). |
| M2-4 | **Cache keys include profile version and rubric versions** (and engine/weights versions). |
| M2-5 | **Cache hits are visible** (trace `cache_hit`, metric, UI badge). |
| M2-6 | **Layered backend** (routes → services → repositories) as in Assignment 1. |

### M3 — Orchestrator
| ID | Requirement |
|----|-------------|
| M3-1 | Supervisor that runs Discover and Validate as **testable state machines**. |
| M3-2 | **Parallel agents** (4 scorers in parallel). |
| M3-3 | **Retries and timeouts** on every agent/tool call. |
| M3-4 | **Stop at budget and ask the user** (approval flow, U4). |
| M3-5 | **Re-run only what changed** (e.g. profile change re-runs only profile-dependent steps). |
| M3-6 | **Every decision traced** (M13). |

### M4 — Scout agents
| ID | Requirement |
|----|-------------|
| M4-1 | Bring in new data from **at least 3 sources**. |
| M4-2 | **Scheduled (CronJob)** and **on demand**. |
| M4-3 | **Per-source rate limits** and **failure handling**. |
| M4-4 | Internet **only through M5**. |

### M5 — Tool Layer (single controlled exit to the internet)
| ID | Requirement |
|----|-------------|
| M5-1 | Connectors (per source). |
| M5-2 | **Domain allow-list**. |
| M5-3 | **Rate limits**. |
| M5-4 | **robots.txt** respected. |
| M5-5 | **Cache**. |
| M5-6 | **Sanitiser** that strips instructions aimed at models. |
| M5-7 | **NetworkPolicy** so only M5 reaches the internet. |
| M5-8 | Internal `POST /fetch` (API-I1). |

### M6 — Knowledge Base (RAG)
| ID | Requirement |
|----|-------------|
| M6-1 | Searchable memory of ideas; **source of every piece of evidence**. |
| M6-2 | **Hybrid search with reranking**. |
| M6-3 | **Filters by date, source and niche**. |
| M6-4 | **Card ids for citations** (evidence ids `card_x#cN`). |
| M6-5 | **Freshness and coverage tracking**. |
| M6-6 | **Retrieval quality measured on ≥ 20 hand-written queries**. |
| M6-7 | Internal `POST /search` (API-I2). |

### M6i — Ingestion jobs
| ID | Requirement |
|----|-------------|
| M6i-1 | Turn fetched pages into idea cards: **dedupe, chunk, embed, index**. |
| M6i-2 | **Repeatable without duplicates** (idempotent). |
| M6i-3 | **≥ 1,500 cards from ≥ 3 sources**. |
| M6i-4 | Records each run in `ingestion_runs` (added, dropped). |

### M7 — Niche and Trend Analyst
| ID | Requirement |
|----|-------------|
| M7-1 | Clusters cards into **named niches**. |
| M7-2 | **Monthly time series** per niche. |
| M7-3 | Classifies niches **rising, flat, saturated**. |
| M7-4 | A **list of gaps** that feeds M8 and M9. |

### M8 — Idea Generator agents
| ID | Requirement |
|----|-------------|
| M8-1 | Ideator, Gap Analyst and Skeptic **debate** to propose ideas that fit the profile. |
| M8-2 | **Limited debate rounds** (max 3). |
| M8-3 | **Every idea cites its gap evidence**. |
| M8-4 | **Ideas defeated with evidence are dropped**. |
| M8-5 | No renamed copies of existing products; ideas respect the student's hours. |

### M9 — Validation agents
| ID | Requirement |
|----|-------------|
| M9-1 | **Four scorers in parallel**: Demand & Pain; Competition & Differentiation; Feasibility for this student; Risk. (Together they produce all six dimensions — see VE-2.) |
| M9-2 | **Own rubric, tools and output schema** each. |
| M9-3 | **Computed features plus model judgment**. |
| M9-4 | **Every score cites evidence ids, otherwise the scorer abstains**. |

### M10 — Scoring Model
| ID | Requirement |
|----|-------------|
| M10-1 | **Fine-tuned 1B–3B model (LoRA or QLoRA)** in **its own container** that applies the rubrics. |
| M10-2 | Internal `POST /score` (API-I3), plus `/health`, `/ready`. |
| M10-3 | **Fallback to a hosted model**: validate every output against the schema; if invalid **retry once**, then fall back to hosted and **log the fallback**. |
| M10-4 | **Comparison of base, fine-tuned and hosted models** (FT-8). |
| M10-5 | Large weights **not in Git** (release asset or registry). Requests/limits from measurements. |

### M10t — Fine-tuning pipeline (offline)
| ID | Requirement |
|----|-------------|
| M10t-1 | Data, training, evaluation, model — **data, scripts and documented commands in the repo** (`jobs/finetune/`). |
| M10t-2 | See FT-1 … FT-8. |

### M11 — Verdict Engine
| ID | Requirement |
|----|-------------|
| M11-1 | Turns the six scores into a verdict by **fixed rules** (§7). **Plain code, no LLM.** |
| M11-2 | **Deterministic**: same input → same output. |
| M11-3 | **Rules in a ConfigMap** (weights, thresholds, floor, confidence threshold, challenge penalties, sensitivity settings). |
| M11-4 | **Unit tests for every gate, threshold edge, sensitivity check and what-if** (incl. the worked example §7.6). |
| M11-5 | Internal `POST /verdict`, `POST /whatif` (API-I4). |

### M12 — Critic and Guardrails
| ID | Requirement |
|----|-------------|
| M12-1 | **Claim check against evidence** (`POST /check`, API-I5). |
| M12-2 | **Injection defence**: fetched text is data, never instructions. |
| M12-3 | **Input rail** on user input. |
| M12-4 | **Approval above budget**. |

### M13 — AgenticOps
| ID | Requirement |
|----|-------------|
| M13-1 | **Tracing per run** (`POST /trace`, `GET /runs/{id}/trace`, API-I6). |
| M13-2 | **Budgets**. |
| M13-3 | **Prompt and rubric registry**. |
| M13-4 | **Evaluation harness**. |
| M13-5 | **Drift reports**. |
| M13-6 | **Dashboard**. |
| M13-7 | Details in AO-1 … AO-7. |

### M14 — Platform
| ID | Requirement |
|----|-------------|
| M14-1 | GitHub with **protected main and pull requests**. |
| M14-2 | **GitHub Actions on every pull request**. |
| M14-3 | **Jenkins for delivery**. |
| M14-4 | **One Docker image per module**. |
| M14-5 | **Full Kubernetes deployment with rollout and rollback**. |
| M14-6 | Details in GIT-*, DOCK-*, CI-*, JEN-*, K8S-*. |

---

## 5. Data contracts (DATA) — Guide §2

All schemas live once in `libs/common` (Pydantic v2) and are exported to JSON Schema. Fields below are **minimums**.

### 5.1 Idea card (stored in M6) — DATA-1
```json
{ "id": "card_8f21", "name": "ExampleApp", "summary": "One line on what it does",
  "problem": "...", "customer": "...", "niche_tags": ["devtools"],
  "stage": "launched | seed | ... | unknown", "funding_usd": 1500000,
  "source": "hackernews", "url": "https://...", "published_at": "2026-03-02",
  "fetched_at": "2026-11-10", "text": "Cleaned text used for embeddings" }
```

### 5.2 Student profile (stored in M2) — DATA-2
```json
{ "user_id": "u_12", "skills": ["python", "react"], "hours_per_week": 10,
  "weeks_available": 12, "team_size": 2, "compute": "laptop | free_gpu | paid",
  "goal": "startup | portfolio | course_project", "interests": ["education"] }
```

### 5.3 Evidence item (from M6) — DATA-3
```json
{ "evidence_id": "card_8f21#c2", "card_id": "card_8f21", "quote": "Export is slow",
  "source": "hackernews", "url": "https://...", "retrieved_at": "..." }
```

### 5.4 Dimension score (from an M9 scorer using M10) — DATA-4
```json
{ "dimension": "demand_pain", "score": 3.5, "confidence": 0.7,
  "evidence_ids": ["card_8f21#c2", "card_77a1#c0"],
  "features": { "n_sources": 4, "trend_growth": 0.31 },
  "reason": "Short explanation that cites the ids above",
  "rubric_version": "demand_pain@v3", "model": "scorer-lora@v2" }
```
Dimension keys (fixed): `demand_pain`, `competition`, `differentiation`, `feasibility`, `risk`, `learning`.

### 5.5 Verdict (from M11) — DATA-5
```json
{ "idea_id": "idea_5", "version": 2, "run_id": "run_90",
  "verdict": "GO | PIVOT | NO-GO | INSUFFICIENT_EVIDENCE",
  "stability": "STABLE | BORDERLINE", "score": 70.5, "confidence": 0.74,
  "gates": { "evidence": "pass", "feasibility": "pass", "risk": "pass", "duplicate": "pass" },
  "weights": { "demand_pain": 15, "competition": 15, "differentiation": 15,
               "feasibility": 25, "risk": 10, "learning": 20 },
  "thresholds": { "T_go": 70, "T_no": 50, "floor": 1.5 },
  "scores": [ /* six dimension scores */ ], "weakest_dimension": "competition",
  "what_would_make_go": [ { "change": "Narrow to X segment", "new_score": 73.5, "new_verdict": "GO" } ],
  "sensitivity": { "runs": 14, "flipped": 3 },
  "versions": { "engine": "m11@v1", "rubrics": "r5", "profile_weights": "w2" } }
```
We add (allowed, Guide §1): `missing` (list of what was missing, for INSUFFICIENT_EVIDENCE, U10), `gate_details` (hours gap / blocker / duplicate product named), `flips` (which sensitivity runs flipped and to what).

### 5.6 Trace record (written to M13 for every step) — DATA-6
```json
{ "run_id": "run_90", "step_id": 12, "parent_step": 9, "agent": "competition_scorer",
  "kind": "tool_call | llm_call | decision", "tool": "search_kb", "input": "...", "output": "...",
  "tokens_in": 812, "tokens_out": 140, "cost_usd": 0.0004, "latency_ms": 930,
  "retries": 0, "model": "scorer-lora@v2", "cache_hit": false, "error": null, "ts": "..." }
```

### 5.7 Fine-tuning example (one JSON object per line) — DATA-7
```json
{"messages": [
 {"role": "system", "content": "Score the idea on demand_pain using rubric demand_pain@v3. Reply with JSON only."},
 {"role": "user", "content": "IDEA: ... EVIDENCE: [card_8f21#c2] ... [card_77a1#c0] ..."},
 {"role": "assistant", "content": "{\"score\": 3.5, \"confidence\": 0.7, \"evidence_ids\": [\"card_8f21#c2\"], \"reason\": \"...\"}"}]}
```

### 5.8 Rubric file (one versioned file per dimension) — DATA-8
```yaml
dimension: demand_pain
version: 3
scale: 0-5      # higher is better for the student
anchors:
  5: "Five or more independent sources on 2+ platforms show the same recurring problem, with growth in the last 6 months."
  3: "Two or three sources show the problem, no clear growth."
  1: "A single weak or old mention."
  0: "No evidence of the problem."
features: [n_sources, n_platforms, complaint_count, trend_growth]
output_schema: score, confidence, evidence_ids, reason
examples:
  - { input: "...", expected: { score: 5, evidence_ids: ["..."] } }
```
**MUST:** an anchor for **every** score 0–5 and **at least one worked example per level**.

### 5.9 Storage model — DATA-9 (Guide Fig. 1)
| Store | Tables / files (minimum columns) |
|-------|----------------------------------|
| Postgres app (M2) | `users(id, email, hash, created_at)`, `profiles(user_id, skills, hours, team, goal, …)`, `ideas(id, user_id, title, source=own/generated)`, `idea_versions(idea_id, version, text, created_at)`, `verdicts(version_id, verdict, score, confidence)`, `dimension_scores(verdict_id, dimension, score, conf, evidence)`, `approvals(run_id, action, decision)`, `conversations(idea_id, turns)`, `runs` |
| Postgres KB (M6/M7) | `cards(id, name, one_liner, problem, customer, niche)`, `card_sources(card_id, platform, url, fetched_at)`, `niches(id, name, size, trend_slope)`, `niche_stats(niche_id, month, launches, mentions)`, `ingestion_runs(run_id, source, added, dropped)`, vector store `chunks(chunk_id, card_id, embedding, date)` |
| Postgres ops (M13) | `traces_index(run_id, cost, latency, status)` |
| Redis | Cache keys for verdicts and retrieval, **with TTL**. Redis only holds cache entries (+ run event streams, our addition). |
| Files on volumes | `traces/*.jsonl`; `prompts/` and `rubrics/` (versioned in git); `eval/results/*.json` |
| Relations | cards link to niches; chunks link to cards; **every claim links to card ids** |

---

## 6. Interfaces (API) — Guide §3

Names may differ if documented (see architecture §7), but **every capability must exist**.

### 6.1 Public endpoints (M1 gateway)
| ID | Endpoint | Purpose |
|----|----------|---------|
| API-P1 | `POST /auth/register`, `POST /auth/login` | Create an account and log in |
| API-P2 | `GET /profile`, `PUT /profile` | Read and update the profile |
| API-P3 | `POST /discover`, `POST /validate` | Start a run. Returns a run id |
| API-P4 | `GET /runs/{id}`, `GET /runs/{id}/events` | Status and result; live progress stream (SSE or WebSocket) |
| API-P5 | `POST /runs/{id}/answer`, `POST /runs/{id}/approve` | Answer a clarifying question; approve or decline an expensive step |
| API-P6 | `POST /ideas/{id}/challenge`, `POST /ideas/{id}/whatif` | Challenge a claim; apply a change and get a new version |
| API-P7 | `GET /ideas`, `GET /ideas/{id}/versions`, `GET /compare` | History, versions, comparison |
| API-P8 | `GET /metrics` | Prometheus-style metrics (or JSON) |

### 6.2 Internal endpoints
| ID | Module | Endpoint | Purpose |
|----|--------|----------|---------|
| API-I1 | M5 | `POST /fetch` | Fetch for a scout. Applies allow-list, rate limit, robots.txt, cache, sanitiser |
| API-I2 | M6 | `POST /search` | Query with filters. Returns ranked evidence items with ids |
| API-I3 | M10 | `POST /score` | In: rubric, idea, evidence. Out: dimension score JSON |
| API-I4 | M11 | `POST /verdict`, `POST /whatif` | In: six scores, profile, config. Out: verdict JSON |
| API-I5 | M12 | `POST /check` | In: claims with evidence ids. Out: supported or not, per claim |
| API-I6 | M13 | `POST /trace`, `GET /runs/{id}/trace` | Write and read trace records |
| API-I7 | all | `GET /health`, `GET /ready` | Liveness / readiness |

---

## 7. Verdict rules (VE) — Description §5, Guide §10

| ID | Rule |
|----|------|
| VE-1 | The LLM **never** outputs or adjusts the verdict. M11 (plain code) decides. |
| VE-2 | **Six dimensions**, each scored **0–5** (higher = better for the student), with **confidence 0–1** and **evidence ids**. Each score = computed features (code, from KB) + fine-tuned model judgment, under a **versioned rubric**. |

### 7.1 Dimensions — VE-3
| Dimension | 5 means |
|-----------|---------|
| Demand and pain | Many independent sources show the same strong, recurring problem. |
| Competition | Few or weak direct competitors, so there is room to enter. |
| Differentiation | A clear gap that existing products do not address. |
| Feasibility for this student | Fits the student's hours, skills and compute with margin. |
| Risk | No legal, privacy, safety or platform-dependency concern. |
| Learning and portfolio value | Teaches skills the student wants and shows well on a CV. |

### 7.2 Hard gates — VE-4 (checked first, in this order; a failed gate decides the verdict whatever the score)
| ID | Gate | Passes when | If it fails |
|----|------|-------------|-------------|
| VE-4a | Evidence | **≥ 3 independent sources on ≥ 2 platforms** | INSUFFICIENT EVIDENCE (says what is missing, does not guess) |
| VE-4b | Feasibility | Estimated build effort fits the student's available hours | NO-GO, **hours gap stated** |
| VE-4c | Risk | No legal or safety blocker | NO-GO, **blocker named** |
| VE-4d | Duplicate | No existing product with the same core features for the same user, **unless Differentiation ≥ 3** | NO-GO, **product named** |

### 7.3 Score and thresholds
| ID | Rule |
|----|------|
| VE-5 | **Weighted score** = Σ(weight × score) / 5 → 0–100. Weights come from the **goal** and **add to 100**. |
| VE-6 | Weight presets (starting values; may change with justification): |

| Goal | Demand | Compet. | Differ. | Feasib. | Risk | Learn. |
|------|-------:|--------:|--------:|--------:|-----:|-------:|
| startup | 25 | 20 | 20 | 15 | 15 | 5 |
| portfolio | 15 | 15 | 15 | 25 | 10 | 20 |
| course_project | 10 | 10 | 15 | 35 | 10 | 20 |

| ID | Rule |
|----|------|
| VE-7 | **GO** needs score **≥ T_go** and **no score below the floor on the first five dimensions** (demand, competition, differentiation, feasibility, risk). |
| VE-8 | Score between **T_no and T_go**, **or** a floor breach (with score ≥ T_no) → **PIVOT**, and the card **names the weakest dimension**. |
| VE-9 | Score **below T_no** → **NO-GO** ("score too low"). |
| VE-10 | Starting values: **T_go = 70, T_no = 50, floor = 1.5**. Tuned per §10 (EVAL). |
| VE-11 | **Confidence** = 0.7 × mean(dimension confidence) + 0.3 × min(1, sources / 5). **Below 0.5 → INSUFFICIENT EVIDENCE** ("confidence too low"): the system abstains instead of guessing. Checked after gates, before thresholds. |
| VE-12 | **Challenge**: the Skeptic argues the opposite. Each objection the critic accepts as evidence-backed **lowers the matching score by 0.5 (at most 1.0 total per dimension)** and its **confidence by 0.1**. Unsupported objections are discarded. |
| VE-13 | **Sensitivity**: recompute with **each weight moved up and down by 20 %** (the difference shared among the others **in proportion**) and the **least confident score moved by ±1 point** (14 runs). If **any** run changes the verdict → **BORDERLINE**, and the card shows what flips it. Otherwise **STABLE**. |
| VE-14 | **What would make this GO**: the engine finds the **smallest change** that changes the verdict (to GO). Applying it **re-runs the scorers and the engine** and **creates a new version**. |
| VE-15 | Decision order (Description Fig. 4): Evidence gate → Feasibility gate → Risk gate → Duplicate gate → weighted score → confidence check → threshold/floor → sensitivity. |

### 7.4 Worked example — VE-16 (must be a unit test)
Goal **portfolio** (weights 15/15/15/25/10/20). Scores: Demand 3.5, Competition 2.0, Differentiation 3.0, Feasibility 4.0, Risk 3.5, Learning 4.5. All gates pass.
1. Weighted sum = 52.5 + 30 + 45 + 100 + 35 + 90 = 352.5 → /5 = **70.5** ≥ 70, no floor breach → **GO**.
2. Feasibility weight −20 % (25 → 20), 5 points shared in proportion → weights ≈ 16/16/16/20/10.67/21.33 → score ≈ **69.9** → PIVOT → card says **GO, BORDERLINE**.
3. What would make this GO: Competition 2.0 → 3.0 → **73.5**, and under the same weight change ≈ **73.1** → **stable GO**.

---

## 8. Fine-tuning (FT) — Guide §6

| ID | Requirement |
|----|-------------|
| FT-1 | Goal: a **cheaper, faster, more consistent** model that follows our rubric format and evidence rule (not a smarter one). |
| FT-2 | **≥ 300 (idea, evidence, dimension) examples** from our KB, covering **all six dimensions and all score levels**. |
| FT-3 | Draft labels with a hosted model using our rubric; **check ≥ 60 by hand** and fix them; **mark which were checked**; use those for evaluation. |
| FT-4 | **Split by idea, not by row** (e.g. 80/20). **Never** train on ideas from the labelled tuning set (EVAL-1). |
| FT-5 | Format: one JSON object per line (DATA-7). |
| FT-6 | Train on a **free GPU (Colab/Kaggle)** with **LoRA/QLoRA** on a **1B–3B open model** (HF PEFT + TRL, or Unsloth). Few epochs, watch validation loss, **record settings** (rank, LR, epochs, batch size) in the repo. |
| FT-7 | **Merge** adapter into base; optionally quantise (GGUF) for CPU. Serve in own container (llama.cpp / Ollama / FastAPI) behind `/score`, `/health`, `/ready`. Weights **not in Git**. Requests/limits from measurements. Validate every output; invalid → retry once → hosted fallback, logged. |
| FT-8 | **Evaluate** base vs fine-tuned vs hosted on the hand-checked test examples. One table: **MAE vs human**, **share within 1 point**, **valid-JSON rate**, **share of real evidence ids**, **latency** and **cost per score**. Plus **one example where fine-tuning did not help**, explained. |

---

## 9. AgenticOps (AO) — Guide §7

| ID | Capability | Requirement |
|----|-----------|-------------|
| AO-1 | Tracing | Every agent step, tool call and model call writes a trace record (DATA-6) under **one run id**. A **replay command** re-runs a stored run using **recorded tool results**. |
| AO-2 | Budgets | **Caps on tokens, pages, steps and cost per run**, enforced in the **orchestrator and tool layer**. Over budget → **stop or ask the user**. |
| AO-3 | Prompt & rubric registry | Versioned files. **Each verdict records the versions used.** A change means a **new version and a new evaluation**. |
| AO-4 | Evaluation harness | Runs the system on **labelled ideas**, scores verdicts (**agreement, confusion matrix**). Also checks **behaviour**: tools used, evidence cited, budget kept, injection resisted. Runs as a **Kubernetes Job** and **nightly from Jenkins**. |
| AO-5 | Drift reports | **After each ingestion**: data freshness, niche coverage, shift in the verdict distribution. |
| AO-6 | Dashboard | **Cost per verdict, latency per agent, cache hit rate, fallbacks, approvals, blocked injections** (Prometheus+Grafana, or a page reading `/metrics`). |
| AO-7 | Behaviour tests | **Record model responses once and replay them in tests** (fast, free, no model). Cover at least: **invalid JSON from a scorer**, **a tool timeout**, **an empty source**, **a poisoned page**. |

---

## 10. Verdict tuning and evaluation (EVAL) — Guide §10

| ID | Requirement |
|----|-------------|
| EVAL-1 | Build our own **labelled set**: ideas + profiles + a **human verdict** each, including **clear GOs, clear NO-GOs, borderline and thin-evidence** cases. Ideally **both members label independently**, then resolve differences; **note who labelled what**. Use only for tuning/measuring, **never for training** the scoring model. |
| EVAL-2 | Run the system on every labelled idea → **confusion matrix** vs human verdicts. |
| EVAL-3 | Tune T_go, T_no, floors and weights to improve agreement. **GO on an idea humans marked NO-GO** is the worst error → **report it separately**. |
| EVAL-4 | Final values in the **ConfigMap**; **before and after matrices** in the report. **Do not overfit** to the labelled set. |
| EVAL-5 | Retrieval quality: **≥ 20 hand-written queries** with expected cards; metrics (recall@k, MRR); a **baseline** that the CI gate compares against. |

---

## 11. Data collection and ethics (ETH) — Guide §5

| ID | Requirement |
|----|-------------|
| ETH-1 | **Official APIs first**: Hacker News (Firebase API), GitHub (REST API), Reddit and Product Hunt (official APIs with keys). Avoid scraping HTML where an API exists. For any site read its terms and `robots.txt`. |
| ETH-2 | **Per-domain rate limits, a clear User-Agent, caching, back-off on errors.** |
| ETH-3 | **No logins, no private data.** Keep only what an idea card needs (name, public text, public URL). |
| ETH-4 | **Credit sources**: every card keeps its source and URL; the verdict card links to them. |
| ETH-5 | **Fetched text is data**; it may contain instructions aimed at models; never obey it (M12). |
| ETH-6 | Keep a **snapshot of card data** so the system does not depend on a website being up. |

---

## 12. Platform (GIT, DOCK, CI, JEN, K8S) — Guide §4, §8, §9

### 12.1 Git workflow
| ID | Requirement |
|----|-------------|
| GIT-1 | Repo created on day 1. **Protect `main`**: no direct pushes, PRs only, **required status checks**, **one review from the other member**. |
| GIT-2 | **One issue per task**, linked to its PR. Small branches with one purpose. |
| GIT-3 | **Conventional commits** (`feat(verdict): add duplicate gate`, `fix(kb): dedupe by url`, `test(scouts): add source failure case`). |
| GIT-4 | **Create one merge conflict on purpose** (two branches edit the same lines), resolve it in a PR, keep the screenshot. |
| GIT-5 | Repo layout per Guide §4.1 (README, docs/, services/, jobs/, rubrics/, config/, eval/, k8s/, .github/workflows/, Jenkinsfile, docker-compose.yml). Each module = own deployable unit with own Dockerfile. |

### 12.2 Local setup
| ID | Requirement |
|----|-------------|
| LOC-1 | Docker, kubectl and a local Kubernetes whose **network plugin enforces NetworkPolicy** (we use minikube + Calico). |
| LOC-2 | Develop with `docker-compose.yml` (Postgres, Redis, vector store, services). Compose = dev only. |
| LOC-3 | `.env.example` with names, no secrets. |
| LOC-4 | README lets a new person **start the system with a short list of commands**. |

### 12.3 Docker
| ID | Requirement |
|----|-------------|
| DOCK-1 | **One image per module, at least 6 distinct images.** |
| DOCK-2 | **Multi-stage**, **non-root**, **pinned versions**, a **`.dockerignore`**. |
| DOCK-3 | **Tagged by commit SHA** (never only `latest`). |
| DOCK-4 | **Scanned in CI** (Trivy; fail on CRITICAL). |

### 12.4 GitHub Actions (every PR) — all are required checks on `main`
| ID | Check |
|----|-------|
| CI-1 | Lint and type checks (ruff + mypy; eslint + tsc for UI). |
| CI-2 | Unit tests (including the Verdict Engine). |
| CI-3 | Component tests for the UI. |
| CI-4 | **Retrieval quality gate** (fails if score drops below baseline). |
| CI-5 | Docker build and image scan. |
| CI-6 | Kubernetes manifest validation (kubeconform). |

### 12.5 Jenkins (after merge, and nightly)
| ID | Requirement |
|----|-------------|
| JEN-1 | `Jenkinsfile` stages: **checkout, build and tag by commit SHA, push, deploy to the test cluster, smoke test**, and (**nightly**) **full evaluation compared with the stored baseline**. |
| JEN-2 | **A failure stops the pipeline and nothing is deployed.** |
| JEN-3 | Jenkins runs **in a container** with access to Docker and the cluster kubeconfig (a **Jenkins credential**, never in the repo). |
| JEN-4 | Trigger via **SCM polling** (e.g. every 2 min) or a tunnel (ngrok) — a GitHub webhook cannot reach a laptop. |

### 12.6 Kubernetes (namespace `idea-validator`)
| ID | Object | Used for |
|----|--------|----------|
| K8S-1 | Deployments + Services | M1, M3, M5, M6, M7, M8, M9, M10, M11, M12, M13 (we also deploy M2 `profile` and M4 `scouts` on-demand API) |
| K8S-2 | StatefulSet or PVC | Postgres, Redis (if persistent), vector store |
| K8S-3 | CronJobs and Job | Scouts and ingestion (CronJobs); evaluation runs (Job); fine-tuned model import (Job) |
| K8S-4 | ConfigMaps and Secrets | Weights, thresholds, budgets, rubric versions (ConfigMaps); API keys, passwords (Secrets) |
| K8S-5 | Probes, requests, limits | **Liveness and readiness on every service**; requests/limits **from measurements** (`kubectl top`) |
| K8S-6 | Autoscaler | HPA **on the API gateway** |
| K8S-7 | NetworkPolicy | **Default-deny egress** for the namespace, then allow DNS, traffic between our services, and internet egress **only for M5 pods** (by label). **ui reaches api only.** |
| K8S-8 | Rollout | **RollingUpdate, maxUnavailable 0**; update an image, then **roll back** a bad release (`kubectl rollout undo` demonstrated). |
| K8S-9 | Ingress | Single entry point. |
| K8S-10 | NetworkPolicy test | From a non-M5 pod reach an external address and **show it failing**; from M5 **show it working**. |
| K8S-11 | Every workload: non-root, pinned image, probes, requests/limits. |
| K8S-12 | Observability: traces to stdout (JSON lines) and a trace volume; metrics endpoint, dashboard, cost per verdict. |

---

## 13. Submission (SUB) — Description §6, Guide §11

| ID | Deliverable |
|----|-------------|
| SUB-1 | **GitHub repo link** (markers can open it): code, Dockerfiles, workflows, Jenkinsfile, `k8s/`, fine-tuning data and scripts, rubrics, evaluation sets, README that lets someone else start the system. |
| SUB-2 | **Report PDF, ≤ 10 pages** (excluding screenshots) → `docs/report/`. Sections: (a) summary and changes from the given architecture; (b) modules and architecture figure; (c) verdict design (gates, weights, thresholds, rubrics, tuning results with **before/after confusion matrix**, one worked example); (d) knowledge base (sources, card counts, retrieval results); (e) fine-tuning (data, settings, comparison table, one case where it did not help); (f) AgenticOps results; (g) platform (pipeline, Kubernetes design, **how we measured requests and limits**); (h) known limits and next steps. |
| SUB-3 | **Screenshots with one-line captions** → `docs/evidence/screenshots/` (list in §13.1). |
| SUB-4 | **AI-usage log** (`docs/ai-usage-log.md`) and **≥ 3 short design decision records** (`docs/decisions/`, ≤ 1 page each, template Guide §11.1). |
| SUB-5 | **Contribution table, signed by both members** (`docs/contribution.md`). **Must match the git history.** |

### 13.1 Required screenshots (SUB-3)
| ID | Screenshot |
|----|-----------|
| SS-1 | Protected `main` and a PR with required checks |
| SS-2 | The merge-conflict PR |
| SS-3 | A green and a failing GitHub Actions run |
| SS-4 | A green and a failing Jenkins run |
| SS-5 | All pods running |
| SS-6 | A rollout and a rollback |
| SS-7 | The NetworkPolicy test |
| SS-8 | UI: profile |
| SS-9 | UI: Discover results |
| SS-10 | UI: verdict card |
| SS-11 | UI: challenge |
| SS-12 | UI: what-if version 2 |
| SS-13 | UI: INSUFFICIENT EVIDENCE card |
| SS-14 | A trace |
| SS-15 | The dashboard |
| SS-16 | A drift report |
| SS-17 | The retrieval result |
| SS-18 | The fine-tuning comparison |

### 13.2 Templates
- **Design decision record** (Guide §11.1): Title, Status, Context, Options, Decision, Why, Cost, AI use.
- **AI-usage log**: Date | Tool | What you asked for | What you changed | Why.
- **Contribution table**: Modules owned | PRs authored / reviewed | Approximate hours | Signature — per member (name, roll no.).

---

## 14. Anti-requirements (AVOID) — Guide §12 "Common mistakes"

| ID | Never do this |
|----|---------------|
| AVOID-1 | One long prompt that "does everything", with no limit on retries, debate rounds or spending. |
| AVOID-2 | Caching a verdict without the profile and rubric version in the key. |
| AVOID-3 | Letting a model, instead of M11, decide or adjust the verdict. |
| AVOID-4 | Scores or claims with no evidence id, or a competitor that is not in the knowledge base. |
| AVOID-5 | Calling the model or scouts directly from the browser, or hard-coding `localhost` in an image. |
| AVOID-6 | Testing retrieval only by eye, or splitting text without keeping the card id. |
| AVOID-7 | Ideas that are renamed copies of existing products, or that ignore the student's hours. |
| AVOID-8 | Leaving Docker, Jenkins and Kubernetes to the last days. **Get a thin version of everything running end to end on Kubernetes early**, then improve each part. |
