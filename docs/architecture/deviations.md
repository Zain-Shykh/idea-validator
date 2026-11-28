# Deviations Log

> Every difference between what is **built** and what [`architecture.md`](architecture.md) (or a merged spec) **designed** is logged here — append-only, newest at the bottom. Process: [`../workflow.md`](../workflow.md) §8.
> This log feeds report section (a) "summary and changes from the architecture" (SUB-2a). Differences between **our architecture and the course's given architecture** (Description Fig. 1 / Guide) are listed separately in §2, because the report must explain those too.

## 1. Deviations from our baseline architecture

| ID | Date | Phase | Owner | What changed (designed → built) | Why | Impact / requirement IDs | Follow-up |
|----|------|-------|-------|--------------------------------|-----|--------------------------|-----------|
| D-001 | 2026-11-27 | Z0 | Zain | Verdict cache key (§8.4): designed `sha256(idea_text_hash, profile_version, rubric_set, engine_version, weights_version, kb_version)` → built with `user_id`, `prompt_versions` and `scorer_model` added | `profile_version` is counted per user, so two students with the same idea text could get each other's verdict; a prompt or scorer-model change would serve stale verdicts | M2-4, AVOID-2, AO-3. M2 and M3 must build keys with `common.cache_keys.verdict_key` | Z7 passes all arguments; Ahmad (A7) only stores the key |
| D-002 | 2026-11-27 | Z0 | Zain | Trace lines on stdout (§15.1, K8S-12): the full record → for `llm_call` records `input`/`output` are replaced by length + hash; M13 gets the full record | conventions §1 forbids full prompts in logs; prompts carry student data | AO-1 replay reads from M13, not from container logs; while M13 is down for longer than the buffer holds, LLM contents are lost | Z3 stores the full record; Z10 replay uses it |
| D-003 | 2026-11-27 | Z0 | Zain | `step_id` (§15 Tracing): per-run counter in M3 → time-ordered unique integer from `common.trace` (a caller may still pass its own); `parent_step` travels in `X-Parent-Step` as designed | Other services create child steps and have no access to M3's counter | AO-1, DATA-6: ids are unique and roughly ordered, not consecutive | M13 (Z3) orders by `ts`, then `step_id` |
| D-004 | 2026-11-27 | Z1 | Zain | `.dockerignore` per service (§20, conventions §5, plan Z1) → one root `.dockerignore` (decision Z0-10) | With the repo root as build context Docker ignores `services/<svc>/.dockerignore` | DOCK-2 still met. Ahmad's services need no own file | None |
| D-005 | 2026-11-27 | Z1 | Zain | M13 report endpoints in the §5 catalogue (`/drift`, `/eval/results`) → `/drift/latest`, `/eval/latest` | §5 and §7.2 disagreed; §7.2 is the API table | API-I7, AO-4; §5 corrected | Z10 implements these paths |
| D-006 | 2026-11-27 | Z2 | Zain | Feasibility gate (§13.1) has pass/fail only → third outcome `not_checked` when `est_effort_hours` is null: later gates still run, and if none fails the verdict is INSUFFICIENT_EVIDENCE | The contract allows a missing estimate; the engine must not guess | VE-4b, VE-15, U10 | Z5 should always provide an estimate or this abstains |

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
