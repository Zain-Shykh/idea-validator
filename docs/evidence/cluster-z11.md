# Cluster checks, Z11

Run on 2026-11-28 (times below are UTC), minikube profile `idea-validator` (v1.39, Kubernetes
v1.37, Calico), one node. Every `kubectl` command used `--context idea-validator -n idea-validator`.

> **Only Zain's services were deployed.** M1, M2, M4, M5, M6, M7 and the UI do not exist. Postgres
> and Redis are the temporary dev manifests. Parts marked **STAND-IN** used
> `scripts/standins` for M2, M5 and M6 (made-up cards, one fixed reply per agent, data in memory).
> Nothing marked so is a result of the system, and nothing here demonstrates U1–U10.

## 1. Model import and scorer (Z4 criterion 4; M10-1, M10-2, K8S-3)

Real base model: Ollama's `qwen2.5:1.5b` blob → image `scorer-model:base-v1` → Job → PVC `models`.

```text
job/model-import-base-v1   Complete 1/1 in 27s
  /models/scorer-base-v1.gguf.tmp: OK
  model imported
PVC /models/scorer-base-v1.gguf   986048512 bytes
```

Readiness is gated on the model answering (scorer wrapper log, first deployment):

```text
22:28:04.00  GET /ready 503
22:28:05.59  model answered the probe prompt
22:28:13.99  GET /ready 200
```

`wait-for-model` finished at once there, because the Job had already completed.

`python scripts/scorer_check.py --url http://localhost:18010` (port-forward):

```text
valid responses 10/10, scored 10, models ['scorer-base@v1']
latency s: p50=26.11 max=33.26
```

Repeated twice with new settings (2 cores, one slot): 10/10 both times, p50 36.7 s (max 45.8) and
36.0 s (max 44.5). Why these two runs were slower than the first was not investigated.

## 2. Deployments (K8S-5, K8S-11)

| Deployment | Image tag | Ready | Restarts at the end |
|---|---|---|---|
| orchestrator, ideation, critic, agentops, scorer (wrapper) | `fa9a9e2` | 1/1 (scorer pod 2/2) | 0 |
| validators | `fa9a9e2` | 1/1 | 0 (an earlier pod was restarted once, see section 6) |
| verdict | `fa9a9e2` | 2/2 | 0 |

- All seven have readiness `/ready` and liveness `/health`; llama.cpp has exec probes.
- Evaluation image: `7214dd8` (rebuilt after the fix in section 3). `services/` and `libs/` are the
  same in both commits.
- `agentops` init container ran the migrations: `alembic_version` = `0002` (head), tables
  `traces_index` and `eval_runs`.
- Mounted ConfigMaps: `app-config` in all seven (`envFrom`); `rubrics` in orchestrator, validators,
  scorer, agentops; `prompts` in all but verdict; `budgets` in orchestrator, critic,
  agentops; `verdict-config` in verdict and agentops. `GET /config` on verdict returned engine `m11@v1`, weights `w1`.
- Postgres and Redis show 2 and 4 restarts from before this session's checks, when the machine and
  cluster were restarted (last state `Unknown`).

## 3. Evaluation Job and drift CronJob (AO-4, AO-5)

### Without M2 and M6: both fail, as documented (expected, not a bug)

| Job | Output | Exit |
|---|---|---|
| `drift-manual` (from the CronJob; 3 attempts, `backoffLimit: 2`) | `error: the knowledge base is not available: UNAVAILABLE` | 2 each |
| `eval` through `KUBE_CONTEXT=idea-validator scripts/check_eval.sh --baseline jobs/evaluation/tests/fixtures/FAKE_baseline.json` | `lab_01 … lab_40: UNAVAILABLE`, `error: no idea got a verdict; no report written` | pod 2, script 2 |

Run twice: by the earlier session, and again at the end with the final evaluation image. These were
the `Error` pods found when this session resumed. All manual Jobs and the test-only
`eval-baseline` ConfigMap were deleted afterwards.

### Two bugs found here and fixed

1. The eval Job named ConfigMap `app-config`, which is deployed with a hash suffix, so its pod
   stayed in `CreateContainerConfigError` (Z11-4).
2. Neither Job passes `--fake`, so reports made from the stand-in would have been marked real,
   stored in `eval_runs` and, for the eval, saved by `check_eval.sh` (Z11-6, found before such a
   run was made).

### STAND-IN, after the fixes

| Job | Result |
|---|---|
| `drift-standin` | Complete; `kb_version: "stand-in"`, report `/tmp/results/FAKE-drift-…json`; `eval_runs` 0 rows |
| `eval` with a **one-idea** set (a hand-edited copy of the Job; 40 ideas would take over two hours) | `lab_01: INSUFFICIENT_EVIDENCE`; `FAKE-eval-7214dd8.json`, `"fake": true`; `error: a fake report is not stored in eval_runs`; exit 1 (one idea cannot match the 40-idea test baseline); `eval_runs` 0 rows |

Not run: `check_eval.sh` against the stand-in (its refusal to save a fake report is covered by
`jobs/evaluation/tests/test_check_eval.py`).

In that run, `differentiation` abstained: the base model's first reply was cut off mid-JSON, the
retry passed the 60 s local timeout, and the hosted fallback was refused by the stand-in. M10
returned no score, as designed.

## 4. One Validate run through Zain's services — STAND-IN (M3-5, WF-V)

From M13's stored traces:

| | Run 1 `run_01M8FKAZH28Z0R3EPQSHX6FCP9` | Run 2, identical `run_01M8FKH8WRCQD8MBGSVC4G9M89` |
|---|---|---|
| Duration | 22:52:09 → 22:55:31 (3 min 22 s) | 0.15 s |
| Trace records | 76: orchestrator 36, validators 21, scorer 12, critic 4, intake 1, skeptic 1, concretizer 1 | 11, orchestrator only |
| Models | `scorer-base@v1` 12 records (**real**); `stand-in` 4 | none |
| Verdict cache | miss, then stored | **hit** |
| Status | `COMPLETED`, M11 verdict stored (`engine m11@v1`, `kb_version stand-in`) | `COMPLETED`, same stored verdict |
| Guardrail | M12 blocked 1 poisoned fixture card | — |

The verdict label itself means nothing (made-up evidence).

Calls to the two services the stand-in lacks failed with `UNAVAILABLE` and the run went on:
M9 → M7 `niches` (its feature becomes null) and, in loaded runs, M3 → M4 `deep_search`.

## 5. Integration tests

See spec Z11 C3 (compose Postgres and Redis, not the cluster).

## 6. Load and what it found — STAND-IN

Numbers and method: [`resources.md`](resources.md). Faults that only showed here:

1. **`validators` killed mid-burst.** In one of the 20-run bursts the kubelet restarted the pod:
   `Liveness probe failed: … context deadline exceeded` three times, `Killing`, exit 137 (not
   out of memory). All 20 runs then ended within 99 s with 100 of 120 dimensions abstained. An
   earlier burst had one failed probe. In a repro with a monitor inside the pod, the pod used its
   whole 500m for about 25 s (throttled in every period) and `/health` rose from 0.00 s to 0.63 s;
   it did not restart. A local profile of the same 80 requests showed no blocking call in project
   code. Why `/health` stayed above 3 s for about a minute in the failed burst is **not
   established**. Fix: liveness 10 s x 6. With it, 20/20 runs completed with 0 restarts and no
   failed liveness probe (one readiness probe timed out). One passing run does not prove the fault
   cannot recur.
2. **llama.cpp host prompt cache** of up to 8192 MiB by default, above the pod's limit
   (`--cache-ram 0`).
3. **Memory figures leave the model out** when its pages are already in the node's cache (see
   `resources.md`).
4. **Capacity:** one CPU model cannot serve 20 concurrent runs; 80–93 of 120 dimensions abstain.

## 7. State left behind

- Removed: the stand-in (every object with `idea-validator/stand-in=true`), the manual eval and
  drift Jobs, ConfigMaps `eval-baseline` and `standin-eval-set`. The model-import Job stays.
- The dev `ops` database and the `traces` volume still hold the **stand-in runs** (`traces_index`:
  140 rows). They count in `GET /summary` and in the drift verdict mix; those of the eval harness
  and load users (`u_eval_*`) are left out of the mix, the hand-made runs are not. Reset the dev
  database before collecting real data (runbook, spec Z11 C6).
- No screenshots (SS-10..16): a headless session takes none, and there is no UI or real data.
