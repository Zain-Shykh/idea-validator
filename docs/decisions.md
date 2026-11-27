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
| Z0-1 | 2026-11-27 | Z0 | Enum for run lifecycle is `RunState`; `RunStatus` is the §7.3 response model | The plan names both `RunStatus`; the public API shape keeps the name | `RunStatusEnum`; renaming the response model | `contracts/core.py` |
| Z0-2 | 2026-11-27 | Z0 | All contracts inherit `ContractModel` (`extra="forbid"`, `allow_inf_nan=False`) | Typos and NaN scores must fail validation (GEN-7); one repo, so producers and consumers upgrade together | Pydantic default `extra="ignore"` | `contracts/base.py`; GEN-7 |
| Z0-3 | 2026-11-27 | Z0 | Safety invariants live in the models: scores/blockers/accepted objections/gaps/ideas need evidence ids; abstained score has confidence 0; six unique dimensions; weights sum to 100; IE needs `missing`; `goal` equals `profile.goal` | Enforced once for every producer and consumer | Checks repeated in each service | `contracts/*.py`; M9-4, AVOID-4, U10, VE-5 |
| Z0-4 | 2026-11-27 | Z0 | `StudentProfile.version` (default 1); `VerdictRequest` carries `rubric_set`, `kb_version`, `prompt_versions` | Cache keys need the profile version; the pure engine must receive the versions it records | Looking versions up inside M11 (needs I/O) | `contracts/core.py`; AO-3, §8.5 |
| Z0-5 | 2026-11-27 | Z0 | `verdict_key` also hashes `user_id`, `prompt_versions`, `scorer_model` | `profile_version` is per user, so the §8.4 key collides across students; prompt or model changes must not serve stale verdicts | Key exactly as in §8.4 | `cache_keys.py`; M2-4, AVOID-2, D-001 |
| Z0-6 | 2026-11-27 | Z0 | DATA-7/DATA-8 modelled now (`FineTuneExample`, `Rubric`, `ScorerOutput`) in `contracts/agents.py`; one-example-per-level enforced in Z3 | Z0 acceptance wants DATA-1..7 examples validated; the Guide rubric example has one example | Deferring both schemas to Z3/Z9 | `contracts/agents.py`; DATA-7, DATA-8 |
