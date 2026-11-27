# Design decision records

Template (Guide §11.1). One page or less each. At least 3 are required for submission (SUB-4).
New ADR: copy the template below into `ADR-NNN-short-title.md`, add a row to the index in `../architecture/architecture.md` §24.

```text
Title:    <decision>
Status:   Proposed | Accepted | Superseded by ADR-NNN
Date:     YYYY-MM-DD
Owner:    Zain | Ahmad
Context:  <the problem and forces>
Options:  (1) … (2) … (3) …
Decision: <chosen option>
Why:      <evidence; numbers when measured>
Cost:     <what we pay for it>
AI use:   <what AI drafted, what we checked/changed>
```

| ADR | Title | Status |
|-----|-------|--------|
| [001](ADR-001-deterministic-verdict-engine.md) | Deterministic rules engine decides the verdict | Accepted |
| [002](ADR-002-pgvector-hybrid-search.md) | pgvector + hybrid search with reranking | Accepted (numbers pending retrieval eval) |
| [003](ADR-003-llm-routing-gemini-via-m5.md) | Gemini free tier via LiteLLM, only through M5 | Accepted |
| [004](ADR-004-plain-python-state-machines.md) | Plain Python state machines for the orchestrator | Accepted |
| [005](ADR-005-scorer-serving-llamacpp-gguf.md) | QLoRA Qwen2.5-1.5B → GGUF on llama.cpp, model shipped as image | Accepted (revisit after FT eval) |
| [006](ADR-006-dashboard-in-ui.md) | AgenticOps dashboard as a UI page | Accepted |
