# Decision log

Running log of **every decision taken while implementing**, so anyone can trace what was built and why. Append-only: add rows at the bottom, never rewrite old ones (to change a decision, add a new row that names the old ID).

What goes where:
- **Here:** any choice not already fixed by `requirements.md` or `architecture/architecture.md`: libraries and versions, schema or field choices, defaults and thresholds, assumptions (spec A9), shortcuts, deferred or skipped work, test strategy, workarounds for blockers.
- [`architecture/deviations.md`](architecture/deviations.md): additionally, when the build **differs** from the designed architecture or a merged spec.
- [`decisions/ADR-*.md`](decisions/README.md): additionally, a full record for a significant design or technology choice.

ID: `<PHASE>-<n>` (e.g. `Z2-3`), or `P-<n>` for process decisions outside a phase.

| ID | Date | Phase | Decision | Why | Alternatives rejected | Affects (files / requirement IDs) |
|----|------|-------|----------|-----|-----------------------|-----------------------------------|
| P-1 | 2026-11-27 | process | Headless phase sessions run Claude Sonnet 5.5 at medium effort with no MCP servers | Owner asked to limit token usage; MCP tool schemas are not needed for implementation | Larger model or higher effort | `scripts/run-phase.sh` |
| P-2 | 2026-11-27 | process | Phases run one after another on stacked branches, each PR targeting the previous phase branch; no PR is merged by an AI session | `main` needs a review by the other member; later phases need earlier code | Merging with admin override; waiting for review between phases | `docs/workflow.md`, all phase PRs |
| P-3 | 2026-11-27 | process | Docker cleanup limited to build cache and images built by this project (no `docker image prune`) | Owner's earlier choice: do not remove other projects' images or volumes; disk is tight | `docker image prune -f` | `docs/workflow.md` headless prompt |
