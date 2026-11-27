# M13 jobs: evaluation, tuning, replay, drift

> **There are no results yet.** No human labels, no real knowledge base, no hosted key. Everything
> here is tested on fakes, and reports made from fake input are refused under `eval/results/`.
> Owner runbook: [`docs/specs/Z10-evaluation-tuning.md`](../../docs/specs/Z10-evaluation-tuning.md) (C6).

| Command | Needs | Does |
|---------|-------|------|
| `python jobs/evaluation/labelled.py` | label files | validate them; disagreements and inter-rater κ |
| `python jobs/evaluation/run_eval.py [--compare eval/results/baseline.json]` | `ORCHESTRATOR_URL`, `PROFILE_URL`, `AGENTOPS_URL`; `resolved.csv` for agreement | run every labelled idea; matrix, κ, false GO on NO-GO, behaviour checks → `eval/results/eval-<sha>.json` |
| `python jobs/evaluation/tune.py --results eval/results/eval-<sha>.json` | that report + `resolved.csv`; no model | grid + 5-fold CV → `tune-<sha>.json` and `.md`. Never edits the rules |
| `python jobs/evaluation/drift.py` | `KB_URL`, `POSTGRES_*` or `AGENTOPS_DATABASE_URL` | freshness, coverage, verdict-mix PSI → `eval_runs` + `drift-*.json`; `GET /drift/latest` |
| `scripts/check_eval.sh` | cluster + `eval/results/baseline.json` | run the eval Job; exit 1 on a regression (`--report FILE` compares without a cluster) |

**Replay a run** (AO-1; no service, Redis or model). From the repository:

```bash
PYTHONPATH=services/agentops/src:services/orchestrator/src:services/verdict/src \
  python -m agentops.replay <run_id> --agentops-url http://localhost:8013   # or --traces-dir / --trace-file
```

Exit 0: same requests, decisions and verdict; 1: something differs (each difference is printed);
2: cannot replay (verdict from the cache, a Discover parent run, or traced before Z10). A Discover
run is replayed through its child runs. Run it with the rules, rubrics and `ORCH_*` /
`SCORER_MODEL_ID` settings of the original run, or the differences will show that they changed.

Image: `docker build -f jobs/evaluation/Dockerfile -t idea-validator-evaluation:$(git rev-parse --short HEAD) --build-arg GIT_SHA=$(git rev-parse --short HEAD) .`
It holds the commit's own labelled set, rules, rubrics and prompts.
