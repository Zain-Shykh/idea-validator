# AI-Usage Log

> Required by GEN-2 / SUB-4: what AI wrote, what we changed, and why. **Append-only**, one row per PR (or per significant AI-assisted task). Template from Guide §11.2.
> Tip: headless phase sessions add their own row; review and edit it before merging.

| Date | Member | Tool | What we asked for | What AI produced | What we changed | Why |
|------|--------|------|-------------------|------------------|-----------------|-----|
| 2026-11-27 | Zain | Claude Code (Opus 5.5) | Read both project PDFs and set up the project baseline: repo, docs (requirements, architecture with diagrams, ADRs, checklist, work distribution, implementation plan, workflow, setup, conventions, spec template), folder skeleton, local environment | All baseline docs and folder structure, `scripts/run-phase.sh`, `.venv` with dev deps, minikube profile with Calico | Chose LLM provider (Gemini), vector store (pgvector), work split (Zain AI core / Ahmad data+UI+platform) when asked; _review pending_ | Establish a single baseline so later sessions don't drift |
| 2026-11-27 | Zain | Claude Code (Sonnet 5.5, headless) + test-engineer subagent | Implement phase Z0: shared contracts, `libs/common` helpers, repo tooling, fixtures | Spec, plan, `libs/common` with 136 JSON Schemas, 161 tests, fictional fixtures; the subagent found 3 bugs and an automated security hook flagged prompts in stdout traces, all fixed | _review pending_ | Both tracks need frozen contracts before any service work |
