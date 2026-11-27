# Deviations Log

> Every difference between what is **built** and what [`architecture.md`](architecture.md) (or a merged spec) **designed** is logged here — append-only, newest at the bottom. Process: [`../workflow.md`](../workflow.md) §8.
> This log feeds report section (a) "summary and changes from the architecture" (SUB-2a). Differences between **our architecture and the course's given architecture** (Description Fig. 1 / Guide) are listed separately in §2, because the report must explain those too.

## 1. Deviations from our baseline architecture

| ID | Date | Phase | Owner | What changed (designed → built) | Why | Impact / requirement IDs | Follow-up |
|----|------|-------|-------|--------------------------------|-----|--------------------------|-----------|
| — | — | — | — | _No deviations yet (baseline 2026-11-27)._ | — | — | — |

<!-- Template row:
| D-001 | 2026-12-02 | Z2 | Zain | `whatif` step 0.5 → 0.25 | worked example needed finer steps | VE-14 | none |
-->

## 2. Design choices that differ from the course's reference architecture (by design, baseline)

These are not implementation deviations; they are baseline decisions that the report must explain.

| ID | Reference (Description / Guide) | Our design | Justification |
|----|----------------------------------|------------|---------------|
| B-001 | Separate "vector-store" StatefulSet (Guide Fig. 3) | pgvector inside the single Postgres StatefulSet (`kb` database) | [ADR-002](../decisions/ADR-002-pgvector-hybrid-search.md): one SQL query for hybrid + filters, fewer stateful services on a laptop |
| B-002 | M8 and M9 as one "agents" Deployment (Guide §9.1) | Separate `ideation` and `validators` Deployments | Description §4: each module in its own container |
| B-003 | M2 not listed as a Deployment (Guide §9.1) | `profile` Deployment (layered API) + Postgres + Redis | M2-6 requires a layered backend; keeps DB access in one service |
| B-004 | M4 only as CronJobs | CronJobs **plus** a small `scouts` Deployment for on-demand deep search | M4-2 "scheduled and on demand"; U4 deep search |
| B-005 | Hosted LLM reached by agents | All hosted LLM calls go through M5 `/llm/chat` | Keeps "only M5 reaches the internet" true ([ADR-003](../decisions/ADR-003-llm-routing-gemini-via-m5.md)) |
| B-006 | Validate Fig. 3 order: Scorers (3) then Features (4) | Features are computed inside each scorer before its model call | Scores "combine computed features with model judgment" (Description §5.1), so features must exist first |
| B-007 | Four scorers, six dimensions | `competition` scorer → competition + differentiation; `feasibility` scorer → feasibility + learning | Description lists four scorers but six dimensions; learning value depends on the student profile like feasibility |
| B-008 | Clarifying questions at intake | Candidate questions at intake, asked only after a preliminary verdict shows the answer would flip it | U5 "only questions that change the result" needs a verdict to test against |
| B-009 | Dashboard via Prometheus + Grafana or a page | Page in our UI reading M13 `/summary` | [ADR-006](../decisions/ADR-006-dashboard-in-ui.md) |
| B-010 | Model import Job pulls weights | Weights shipped as an OCI image; Job copies them into a PVC | No pod besides M5 needs internet ([ADR-005](../decisions/ADR-005-scorer-serving-llamacpp-gguf.md)) |
