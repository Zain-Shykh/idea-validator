# ADR-004: Plain Python state machines for the orchestrator

- **Status:** Accepted
- **Date:** 2026-11-27
- **Owner:** Zain (M3)
- **Context:** M3 must run Discover and Validate as testable state machines with parallel agents, retries, timeouts, stop-at-budget-and-ask, re-run-only-what-changed and full tracing (M3-1..6). Both members must be able to explain it.
- **Options:** (1) LangGraph (graph runtime, checkpointers, interrupts); (2) a workflow engine (Temporal/Prefect) — another cluster service; (3) plain Python: explicit `State` enum + transition functions, `asyncio.gather` for parallel scorers, `tenacity` for retries, JSON checkpoint written to M2 after every transition, step memo in Redis.
- **Decision:** Option 3.
- **Why:** Every transition is a plain function that can be unit-tested with fake agents; no framework semantics to learn or explain; checkpoint + `resume(run_id)` gives approval/clarify waits and restart recovery; recorded traces replay through the same code (AO-1).
- **Cost:** We write ~300 lines of plumbing (checkpointing, resume, memo) that a framework would provide; no visual graph tooling.
- **AI use:** Options compared with AI help; decision ours.
