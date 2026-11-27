# AI-Usage Log

> Required by GEN-2 / SUB-4: what AI wrote, what we changed, and why. **Append-only**, one row per PR (or per significant AI-assisted task). Template from Guide §11.2.
> Tip: headless phase sessions add their own row; review and edit it before merging.

| Date | Member | Tool | What we asked for | What AI produced | What we changed | Why |
|------|--------|------|-------------------|------------------|-----------------|-----|
| 2026-11-27 | Zain | Claude Code (Opus 5.5) | Read both project PDFs and set up the project baseline: repo, docs (requirements, architecture with diagrams, ADRs, checklist, work distribution, implementation plan, workflow, setup, conventions, spec template), folder skeleton, local environment | All baseline docs and folder structure, `scripts/run-phase.sh`, `.venv` with dev deps, minikube profile with Calico | Chose LLM provider (Gemini), vector store (pgvector), work split (Zain AI core / Ahmad data+UI+platform) when asked; _review pending_ | Establish a single baseline so later sessions don't drift |
