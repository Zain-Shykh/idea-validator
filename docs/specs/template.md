# <PHASE-ID> — <Phase title>

> Copy this file to `docs/specs/<PHASE-ID>-<slug>.md` (e.g. `Z2-verdict-engine.md`, `A4-knowledge-base.md`).
> **Part A (Spec)** is written and pushed first. **Part B (Plan)** is added and pushed second. **Part C (Implementation & test report)** is filled during/after implementation.
> Keep it concrete: every requirement maps to an ID in `docs/requirements.md` and to at least one test.

| Field | Value |
|-------|-------|
| Phase | `<PHASE-ID>` from `docs/implementation-plan-<owner>.md` |
| Owner | Zain \| Ahmad |
| Author | human \| Claude Code headless session (`scripts/run-phase.sh`) \| other AI tool (name) |
| Branch | `feat/<phase-id>-<slug>` |
| Status | Draft spec → Spec approved → Plan ready → Implementing → Testing → In review → Done |
| Requirement IDs | e.g. `M11-1..5, VE-1..16, U6` |
| Architecture refs | e.g. `architecture.md §13, ADR-001` |
| Depends on | phases / sync points / fakes used |
| Issue / PR | #… / #… |

---

## Part A — Specification

### A1. Objective
One paragraph: what this phase delivers and why it matters for the product.

### A2. Context
- Where this sits in the architecture (module, callers, callees).
- What already exists (previous phases, contracts, fakes) and what is still fake.

### A3. Scope
**In scope**
- …

**Out of scope** (and which phase handles it)
- …

### A4. Requirements and acceptance criteria
Each row is testable. Use Given / When / Then when behaviour matters.

| # | Req ID | Acceptance criterion | Verified by (test name / command) |
|---|--------|----------------------|-----------------------------------|
| 1 | VE-4a | Given 2 sources on 2 platforms, when `/verdict` is called, then verdict = INSUFFICIENT_EVIDENCE and `missing` names the source shortfall | `tests/unit/test_gates.py::test_evidence_gate_two_sources` |
| 2 | … | … | … |

### A5. Interfaces and contracts
- Endpoints (method, path, request model, response model, error codes).
- Contract files touched in `libs/common` (additive? breaking? → deviation).
- Events / trace records / metrics emitted.
- Config keys and env vars (add to `.env.example` / ConfigMap).

### A6. Design
- Components and responsibilities (files/modules to create).
- Data model / migrations.
- Algorithms (pseudo-code for anything non-trivial).
- Sequence or state diagram (Mermaid) if the flow is not obvious.

### A7. Edge cases and failure handling
| Case | Expected behaviour |
|------|--------------------|
| Dependency timeout | … |
| Invalid model output | … |
| Empty input / no evidence | … |

### A8. Test plan
| Level | What | Command |
|-------|------|---------|
| Unit | … | `pytest services/<svc>/tests/unit -q` |
| Contract | request/response validate against `common.contracts` | … |
| Integration | with Postgres/Redis (compose) or fakes | … |
| Behaviour (cassettes) | … | … |
| Cluster smoke | pod Ready, probe, one request | `kubectl …` |
Coverage target: …

### A9. Assumptions and open questions
- Assumption: … (correct me in review)
- Question: … (answer before Part B, or record the default taken)

### A10. Risks
| Risk | Mitigation |
|------|-----------|

---

## Part B — Plan

### B1. Task breakdown
Small, ordered, each independently verifiable. Commit after each task or small group.

| # | Task | Files | Verification | Done |
|---|------|-------|--------------|------|
| 1 | … | … | `pytest …` passes | [ ] |
| 2 | … | … | … | [ ] |

### B2. Commit plan
Conventional commits, e.g.
1. `docs(spec): add <PHASE-ID> spec`
2. `docs(plan): add <PHASE-ID> plan`
3. `feat(<scope>): …`
4. `test(<scope>): …`
5. `fix(<scope>): …` (from test report)
6. `docs(<phase-id>): update checklist, plan progress, deviations, ai-usage log`

### B3. Definition of Done (phase)
- [ ] All A4 criteria pass (tests listed and green).
- [ ] ruff + mypy (or eslint + tsc) clean; coverage target met.
- [ ] Dockerfile builds, non-root; manifest validates (kubeconform) — if the phase touches a service.
- [ ] `docs/checklist.md` rows ticked; `docs/implementation-plan-<owner>.md` progress log + status updated.
- [ ] Deviations logged in `docs/architecture/deviations.md` (or "none").
- [ ] `docs/ai-usage-log.md` row added.
- [ ] PR opened with summary, requirement IDs, test evidence; reviewer = the other member.

---

## Part C — Implementation and test report
*(filled during and after implementation)*

### C1. What was built
- Files/modules created or changed, with one line each.

### C2. Deviations from spec / architecture
| What | Why | Logged in deviations.md as |
|------|-----|---------------------------|

### C3. Test report (from the testing subagent)
- Commands run and results (counts, coverage).
- Bugs found → fixed (commit refs).
- Bugs found → not fixed (why, follow-up issue).

### C4. Measurements (if any)
Latency, memory, card counts, retrieval metrics, etc.

### C5. Follow-ups
- …
