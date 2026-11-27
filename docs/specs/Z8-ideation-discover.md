# Z8 — M8 Ideation + Discover workflow + challenge / what-if

| Field | Value |
|-------|-------|
| Phase | `Z8` from `docs/implementation-plan-zain.md` |
| Owner | Zain |
| Author | Claude Code headless session |
| Branch | `feat/z8-ideation-discover` (parent `feat/z7-orchestrator-validate`, PR #17 unmerged) |
| Status | Plan ready |
| Requirement IDs | `M8-1..5, WF-D1..D9, U2, U7, U8, U9 (versions), VE-12, VE-14, API-P6 (orchestrator side), AVOID-1, AVOID-7` |
| Architecture refs | `architecture.md §6.4, §6.6, §7.1–7.2, §11, §12.2–12.3, §12.5`, ADR-001, ADR-004 |
| Depends on | Z7 (real). Fakes: M2, M4, M5 (cassettes), M6, M7 (`FakeAnalyst`), gateway |
| Issue / PR | — |

---

## Part A — Specification

### A1. Objective
Generate ideas that fit a student's profile from data gaps through a bounded, evidence-checked debate, validate the survivors with the same workflow as any other idea, and let the student challenge a claim or apply a "what would make this GO" change. Every verdict, including new versions, comes from M11.

### A2. Context
- M8 is a stub (Z1). M3 runs Validate only (Z7); `POST /runs` for Discover and `/ideas/{id}/challenge|whatif` answer 501.
- M2, M4, M5, M6, M7 and the gateway are not built. M3 and M8 reach them over HTTP through `common.contracts`; tests use fakes. No Gemini key: model replies are cassettes.
- Z7 follow-up: M9 sends the first 1200 characters of an idea to M10, so answers appended to a long idea are cut off. The same cut would hide an applied what-if change.

### A3. Scope
**In scope:** real M8 agents (intake, ideator, gap analyst, skeptic, concretizer); Discover state machine with child Validate runs and a shared budget; challenge; what-if list and apply; one shared way to append to and shorten idea text, used by M3, M9 and M10; image, manifest, compose and `.env.example` for M8 and M3.

**Out of scope:** real M2/M6/M7 (A1, A3, A4, A6); gateway routes and UI (A8, A9); replay, evaluation of agent quality and threshold tuning (Z10); live model runs; cluster run and measurements (Z11).

### A4. Requirements and acceptance criteria
`O/` is `services/orchestrator/tests/unit/`, `I/` is `services/ideation/tests/unit/`.

| # | Req ID | Acceptance criterion | Verified by |
|---|--------|----------------------|-------------|
| 1 | M8-2, WF-D7, AVOID-1 | With a Skeptic that always raises a fatal objection the Critic accepts, `/ideate` is called exactly 3 times, every idea is dropped, each drop is an `idea_dropped` event carrying the objection and its evidence ids, and the run completes with no verdicts and no child run | `O/test_discover.py::test_debate_never_exceeds_three_rounds` |
| 2 | M8-4, WF-D5, WF-D6 | An idea is dropped only if the Critic finds a **fatal** objection supported. A fatal objection the Critic rejects, or that cites evidence the Skeptic was not given, drops nothing. Supported non-fatal objections go to the next round's `/ideate` as `prior_objections` | `O/test_discover.py::test_only_supported_fatal_objections_drop`, `::test_non_fatal_objections_are_revision_hints` |
| 3 | M8-3, WF-D3 | Every survivor has ≥ 1 gap evidence id that was offered to the Ideator and that M6 has. Ids not offered are stripped by M8; an idea left with none is not returned. M3 drops (with an event) an idea whose ids M6 does not know | `I/test_agents.py::test_ideas_without_offered_gap_evidence_are_rejected`, `O/test_discover.py::test_idea_without_existing_gap_evidence_is_dropped` |
| 4 | M8-5, AVOID-7 | An idea that renames a fixture product (same summary and customer) is dropped as a duplicate before the Skeptic sees it, with the card id and its evidence in the event | `O/test_discover.py::test_renamed_copy_is_dropped_as_duplicate` |
| 5 | M8-5, AVOID-7 | An idea whose smallest plausible effort exceeds `effort_margin × available hours` is dropped with a `feasibility` objection stating both numbers | `O/test_discover.py::test_idea_over_the_students_hours_gets_a_feasibility_objection` |
| 6 | M8-1, WF-D2..D4 | M8's five endpoints each make a traced model call with their prompt file and a schema; evidence goes in `<evidence>` blocks, flagged evidence is withheld; ids outside the given evidence are stripped; output that stays invalid after one repair is an error, and a reply carrying a verdict is invalid. The gap analyst searches M6 per idea | `I/test_agents.py`, `eval/behaviour/test_discover.py` (cassettes) |
| 7 | WF-D1, WF-D8, M3-1 | Discover walks REQUEST → NICHES → (IDEATE → GAP_ANALYZE → SKEPTIC → CRITIC → FILTER) × ≤ 3 → VALIDATE_EACH → RESULT; other transitions are rejected. Each survivor is stored in M2 as a `generated` idea and validated by a child run with `parent_run_id`, at most 2 at a time | `O/test_discover.py::test_happy_path`, `::test_children_run_two_at_a_time`, `O/test_state_machine.py` |
| 8 | AO-2, AVOID-1 | Children share the parent's budget: parent spend = debate + children; a used-up cap stops further paid steps. With some verdicts the run completes and names the ideas not validated; with none it ends `BUDGET_STOPPED`. A child never waits: it asks no clarifying question, and a deep search needing approval is not run | `O/test_discover.py::test_shared_budget`, `::test_children_never_wait` |
| 9 | U2, WF-D9 | The result lists the cards ranked GO > PIVOT > NO-GO > INSUFFICIENT_EVIDENCE, then score, then confidence, each with one reason line | `O/test_discover.py::test_ranking_rule`, `::test_happy_path` |
| 10 | M3-1 | A new orchestrator process resumes a Discover run interrupted during the debate or during VALIDATE_EACH without creating a second child for an idea; a child run cannot be resumed on its own (409) | `O/test_discover.py::test_resume_*` |
| 11 | U8, API-P6, VE-1, M12-1 | Challenge: the claim is sent to `/check` with `strict: true`. Supported → `defended`, the critic's rationale, the verdict unchanged, nothing stored. Not supported → `removed`: the claim's evidence ids are dropped, a dimension with none left abstains, M11 computes the verdict, and version v+1 (`origin=challenge`) with its verdict is stored. A check that could not run changes nothing (503) | `O/test_challenge.py`, `eval/behaviour/test_discover.py::test_challenge_with_the_real_critic_and_engine` |
| 12 | U7, VE-14, API-P6 | What-if list: every proposal's candidate is one M11 `/whatif` search returned (new score and verdict), one per dimension, with M8's wording. Apply: version v+1 (`origin=whatif`, text = idea + change) and a new Validate run that calls the scorers and M11; the same apply again returns the same run. No score is copied or edited | `O/test_whatif.py` |
| 13 | VE-12 | In every card M3 stores, the only verdict source is an M11 reply; challenge removal changes M11's **input** only | `O/test_challenge.py::test_stored_verdict_is_m11s_reply`, `O/test_verdict_guard.py` |
| 14 | U1 (Description) | The same idea with 200 team hours and with 20 solo hours: real M9 and M11 give different verdicts (the 20 h one is NO-GO on the feasibility gate) | `eval/behaviour/test_discover.py::test_profile_changes_the_verdict` |
| 15 | Z7 follow-up, U5, U7 | However long the idea, appended clarification answers and what-if changes survive shortening: they are in the text M9 sends to M10, in M10's prompt, in M3's search query and in the stored idea text (5000) | `libs/common/tests/test_idea_text.py`, `services/validators/tests/unit/test_scoring.py::test_amendments_reach_the_scoring_model` |
| 16 | Z7-21 | Challenge and what-if need `X-User-Id` (422 without); another user's idea and an unknown version are 404; a child run cannot be driven by itself (409, row 10). Ideas made by Discover can be challenged like any other | `O/test_challenge.py::test_ownership`, `O/test_whatif.py::test_ownership` |

### A5. Interfaces and contracts
**No shared contract changes.** `VerdictStore` is reused as the reply of the proposed verdict read.

**M3**

| Endpoint | Success | Errors |
|----------|---------|--------|
| `POST /runs` (`discover`) | 202 | as for validate; `niche_wish` passes the input rail inside the run |
| `POST /ideas/{id}/challenge` | 200 `ChallengeResponse` | 404 idea, version or claim unknown / another user's; 409 the stored run lacks the engine input; 503 M2, M6, M11, M12 down or the claim could not be checked; 422 |
| `POST /ideas/{id}/whatif` `{version}` | 200 `WhatIfProposalsResponse` (may be empty) | 404; 409; 503 M2, M8, M11 or Redis down |
| `POST /ideas/{id}/whatif` `{version, apply}` | 202 `WhatIfApplyResponse` | 404 unknown version or proposal (list again); 503 |
| answer / approve / resume on a child run | — | 409 (the parent drives it) |

**Calls to services that do not exist** (bodies are existing contracts; all in §7.2 except the third):

| Call | Body → reply |
|------|--------------|
| `POST /internal/ideas` (M2) | `IdeaCreate` → `Idea` with version 1 in `versions` |
| `GET /internal/ideas/{id}` (M2) | → `Idea` with all versions |
| `GET /internal/ideas/{id}/versions/{n}/verdict` (M2, **proposed**) | → `VerdictStore` (latest verdict of that version, with claims) |
| `GET /internal/profiles/{user_id}` (M2) | → `StudentProfile` (current) |
| `POST /niches/match`, `GET /gaps?niche_id=` (M7) | `contracts/analyst.py` |
| `GET /cards/{id}` (M6) | → `IdeaCard` |

**M8:** `/intake`, `/ideate`, `/gap-analyze`, `/skeptic`, `/concretize` with the contracts of `agents.py`. Errors: 502 `LLM_OUTPUT_INVALID`, 502 `LLM_UNAVAILABLE`, 503 `KB_UNAVAILABLE`, 503 `NOT_READY`. Prompt `gap_analyst` becomes v2 (the model returns idea indexes, not copies of ideas).

**Config:** M3: `ANALYST_URL`, `ORCH_DISCOVER_TARGET` (5), `ORCH_DISCOVER_CONCURRENCY` (2), `ORCH_DUPLICATE_SIMILARITY` (0.9). M8 (prefix `IDEATION_`): `KB_URL`, `PROMPTS_DIR`, `SEARCH_K` (8), `LLM_TIMEOUT_S` (50); shared `LLM_BACKEND`, `TOOLS_URL`, `AGENTOPS_URL`.

**Events:** `idea_dropped` with `data = {title, reason: gap_evidence|duplicate|feasibility|defeated, objections[], evidence_ids[], card_id?}`. Child events are written to the child's stream and mirrored to the parent's with `data.child_run_id` (not the child's `run_started`/`run_finished`). Trace decisions hold ids and labels only.

### A6. Design

| File | Responsibility |
|------|----------------|
| `libs/common/src/common/idea_text.py` | append sections to an idea text; shorten keeping them |
| `libs/common/src/common/idea_rules.py` | pure rules M3 and M9 both need: `words`, `same_customer`, `is_complaint`, `effort_band` (moved from M9) |
| `ideation/services/agents.py` | the five agents: prompt rendering, model call, grounding |
| `ideation/{main,settings}.py`, `services/kb.py`, `api/routes.py` | app, readiness, M6 search client, routes |
| `orchestrator/services/state_machine.py` | `DiscoverStep` table; `BaseCheckpoint`, `Checkpoint`, `DiscoverCheckpoint` |
| `orchestrator/services/discover.py` | one function per Discover step, child runs, ranking |
| `orchestrator/services/revise.py` | challenge and what-if |
| `orchestrator/services/run.py`, `runner.py` | shared budget between parent and child; drive either workflow |
| `orchestrator/repositories/profile.py`, `downstream.py` | the new M2, M6, M7 calls |

**Debate round** (state in the checkpoint, so each step is resumable):
1. IDEATE: `/ideate` with profile, niches, gaps, round, `prior_objections` (supported non-fatal ones). Ideas flagged by the injection rules and repeats of an idea already seen with the same text are discarded.
2. GAP_ANALYZE: `/gap-analyze`; the ids its claims cite (checked against M6) are added to what the Skeptic sees. Claim texts are not shown to the student.
3. SKEPTIC: first the code filters, cheapest first: gap evidence exists in M6 → one M6 search per idea → duplicate of a product card (similarity ≥ threshold, not a complaint, same customer) → hours. Then `/skeptic` for each idea still standing, with its gap evidence, the analyst's evidence and the search results (flagged ones withheld, ≤ 20).
4. CRITIC: one `/check` call for all objections (≤ 5 per idea).
5. FILTER: drop ideas with a supported fatal objection; add the rest to the survivors (a newer revision of the same title replaces the older one). Another round if `round < 3` and survivors < target.

**Hours rule** (code, no model): `effort_band(title + text)[0] > effort_margin × hours_per_week × weeks × team_size`, with `effort_margin` from M11's active config. It cites no evidence because it is arithmetic; it never reaches M11. Validate's feasibility gate still judges every survivor.

**Children:** ordinary Validate runs driven inside the parent's task. A paid step must fit both the child's and the parent's remaining budget and is charged to both. Checkpoint of the parent keeps child ids before they are created; on resume, parent spend = debate spend + the children's stored spend.

**Ranking:** `(label order, -score (none last), -confidence)`.

**Challenge:** M2 idea (owner) → M2 verdict of the version → claim by id → engine input = the `VerdictRequest` in the checkpoint of the verdict's run (a cache-hit run points to its source run) → `/check` strict. Claim text = the dimension's reason without the code's own "score from model" prefix and "Cites …" suffix. On removal: new version (same text), request with the dimension abstaining, `/verdict`, store as a new completed run so that version can be challenged again. The new run id is derived from (idea, version, claim), so a repeat returns the same result.

**What-if:** list = M11 `/whatif` search on the stored request → one candidate per dimension → `/concretize` → proposals kept in Redis 24 h (id = digest of idea, version, dimension, scores). Apply = new version whose text is the old one plus a `Changes:` entry, then an ordinary Validate run with the **current** profile.

### A7. Edge cases and failure handling
| Case | Expected behaviour |
|------|--------------------|
| M7 down | `FAILED` (`ANALYST_UNAVAILABLE`) |
| No niche, no gap, or no gap with usable evidence | no model call; run completes with no ideas and says why |
| Gap evidence flagged as injection | withheld from the Ideator |
| `niche_wish` refused by the rail | `FAILED` (`INPUT_REJECTED`) |
| `/ideate` fails in round 1 / later | `FAILED` (`IDEATION_UNAVAILABLE`) / debate ends with the survivors |
| `/gap-analyze` or `/skeptic` fails | the idea goes on without it; run marked degraded |
| `/check` down or unavailable | objections not applied; degraded |
| M6 search, card or evidence fails | `FAILED` (`KB_UNAVAILABLE`) |
| Ideator returns nothing in a round | next round while rounds remain |
| A child fails | its idea is listed as not validated; the others continue |
| Store down during a child | the parent pauses and stays resumable |
| Challenge of a claim whose dimension abstains | 404 (there is no claim) |
| Concretizer down | what-if list 503; no proposal without a concrete change |
| Concretizer words only some changes | only those become proposals |
| Concrete change looks like an order to a model | that proposal is left out |
| Redis lost between list and apply | 404 "list the proposals again" |

### A8. Test plan
| Level | What | Command |
|-------|------|---------|
| Unit | M8 agents with cassettes and a fake M6; Discover, challenge, what-if on the fake stack with the real M11; idea text | `pytest services/ideation services/orchestrator libs/common services/validators services/scorer -q` |
| Contract | fakes validate every request against `common.contracts`; M3 validates replies | same |
| Behaviour (cassettes) | real M8, M9, M11, M12 in process | `pytest eval/behaviour -q` |
| Image | build M8, M3, M9, M10 images; run, non-root, `/health`, `/ready` | recorded in C3 |
| Manifests | `kubectl kustomize k8s/overlays/dev \| kubeconform -strict` | recorded in C3 |

Coverage target: ≥ 90 % of `services/orchestrator/src` and `services/ideation/src`.

### A9. Assumptions and open questions
- **Child runs are not interactive.** A parent waiting on several children's questions and approvals would need an interaction model nothing specifies. Not exceeding the budget without asking (U4) still holds, because the unapproved search is not run.
- **"Defeated by hours" is a code rule**, not a Skeptic opinion: the Skeptic has no profile, and the Critic can only confirm what evidence says.
- **Only a supported claim is defended.** A judge's "unverifiable" removes the claim; only "the check could not run" keeps it (Z7 follow-up, §12.5).
- A `max_ideas` request sets the target of survivors (default 5); at most that many are validated, those with fewer objections first.
- Debate steps charged: ideate, gap analysis, each Skeptic call, each Critic call. M6 lookups of the code filters are free (at most one search per idea per round).
- M2's reply to `POST /internal/ideas` carries version 1. M2 honours `Idempotency-Key`.
- `SearchResult.score` is a similarity in 0..1 (as Z5); values above 1 never mean duplicate.
- One orchestrator replica (as Z7): per-idea locks are in process.

### A10. Risks
| Risk | Mitigation |
|------|-----------|
| A strict real judge removes most challenged claims, because reasons summarise counts over more cards than they cite | follow-up for Z10 with a real model; only the claim text builder changes |
| The duplicate threshold means something else on real retrieval scores | `ORCH_DUPLICATE_SIMILARITY` knob; tune in Z10 |
| The 40 h lowest band drops every idea for very small time budgets | intended (M11 would say NO-GO); the result says why |
| Discover exceeds `max_steps` 80 with 5 children | partial result with names of ideas not validated; tune in Z11 |

---

## Part B — Plan

### B1. Task breakdown
| # | Task | Files | Verification | Done |
|---|------|-------|--------------|------|
| 1 | Idea text sections and shared idea rules; use them in M3, M9, M10 (Z7 follow-up) | `common/{idea_text,idea_rules}.py`, `validators/services/{engine,features}.py`, `scorer/services/prompt.py`, `orchestrator/services/validate.py` | `pytest libs/common services/validators services/scorer services/orchestrator` | [ ] |
| 2 | Real M8 agents, `gap_analyst@v2`, app and settings | `services/ideation/**`, `prompts/`, `config/registry.lock.json` | `pytest services/ideation`; `python scripts/check_registry.py` | [ ] |
| 3 | Discover: state machine, checkpoint, downstream calls, steps, child runs, shared budget, ranking | `orchestrator/**` | `pytest services/orchestrator` | [ ] |
| 4 | Challenge and what-if | `orchestrator/services/revise.py`, routes | `test_challenge.py`, `test_whatif.py` | [ ] |
| 5 | Behaviour tests with real M8, M9, M11, M12 and cassettes | `eval/behaviour/test_discover.py`, cassettes | `pytest eval/behaviour` | [ ] |
| 6 | Packaging: images, compose, manifests, `.env.example` | `services/{ideation,orchestrator}`, `k8s/base`, dev overlay | build, run, kubeconform; images removed | [ ] |
| 7 | Testing subagent, fixes | tests | full suite | [ ] |
| 8 | Docs, issue, PR | `docs/**`, README | — | [ ] |

### B2. Commit plan
1. `docs(spec)`, 2. `docs(plan)`, 3. `fix(common): keep appended answers when idea text is shortened`, 4. `feat(ideation): …`, 5. `feat(orchestrator): discover workflow`, 6. `feat(orchestrator): challenge and what-if`, 7. `test: behaviour …`, 8. `build: …`, 9. `fix: …` (test report), 10. `docs(Z8): …`. Push after every task.

### B3. Definition of Done (phase)
- [ ] All A4 criteria pass.
- [ ] ruff, ruff format and mypy clean; coverage target met.
- [ ] Changed images build, run non-root and are removed; manifests validate.
- [ ] Checklist, plan, decisions, deviations, AI log updated.
- [ ] PR opened against `feat/z7-orchestrator-validate`; never merged.
