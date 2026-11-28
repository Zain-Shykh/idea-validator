# Resource measurements (K8S-5, spec Z11)

> **STAND-IN load.** M2, M5 and M6 were answered by `scripts/standins` (made-up cards, fixed model
> replies, data in memory). Verdicts, run latencies and costs below are **no result of the system**
> and support none of U1–U10. The one workload that did its real work is the **scorer pod** (the real
> base model, `qwen2.5:1.5b`, on llama.cpp). Only its requests and limits were changed.

Measured 2026-11-28 on one laptop (i3-1215U, no GPU), minikube profile `idea-validator`, one node
(8 CPU, 15.3 GiB). The figures are not valid for another machine.

## Method

1. `scripts/load_validate.py` starts N Validate runs through M3 (labelled ideas, one user per run).
2. `scripts/sample_resources.py` wraps it: `kubectl top pods --containers` every 5 s
   (metrics-server resolves about 15 s, so neighbouring samples repeat), then each container's
   cgroup `memory.peak` and CPU throttling counters (since the container started).
3. Rule: request = p50 + 20 %, limit = p95 x 1.5, rounded up to 10m / 16Mi. The proposals are
   read by a person; where they were not used, the reason is below.

```bash
KUBE_CONTEXT=idea-validator scripts/standins/run.sh up <tag>     # only while M2/M5/M6 are missing
# port-forward orchestrator, profile and agentops, then set the three *_URL variables
KUBE_CONTEXT=idea-validator python scripts/sample_resources.py --out samples.json \
    -- python scripts/load_validate.py -n 20 --idea-timeout 1800 --out load.json
KUBE_CONTEXT=idea-validator scripts/standins/run.sh down
```

**Cold-cache trap (matters for every model server).** llama.cpp maps the model file. The kernel
charges those pages to the cgroup that **first** read them, so after the import Job or an earlier
pod, the `llama` container is not charged for its own model: `VmRSS` showed 952 MiB mapped while
the container's `memory.stat` said `file_mapped 0`, and the sampler read 862 MiB. A limit taken
from that figure (1296Mi) is below what the pod needs to start after a node reboot. To measure,
stop the pod, evict that one file, then start it:

```bash
kubectl --context idea-validator -n idea-validator scale deploy/scorer --replicas=0
minikube -p idea-validator ssh -- "sudo dd if=<PV hostPath>/scorer-base-v1.gguf iflag=nocache count=0"
kubectl --context idea-validator -n idea-validator scale deploy/scorer --replicas=1
```

Cold start, ready, before any scoring: **1727 MiB** (776 anonymous + 945 model file + about 6 other).

## Scorer pod

| Measurement | Container | Samples | CPU m p50 / p95 / max | Memory Mi p50 / p95 / max | memory.peak Mi |
|---|---|---|---|---|---|
| 3 runs in turn, default arguments, warm cache | llama | 98 | 1768 / 1852 / 1852 | 1209 / 1276 / 1276 (undercounts) | 1283 |
| 20 at once, default arguments, warm cache | llama | 116 | 1836 / 1844 / 1844 | 1280 / 1281 / 1281 (undercounts) | 1287 |
| 3 runs in turn, `--cache-ram 0`, warm cache | llama | 52 | 1749 / 1867 / 1867 | 862 / 862 / 862 (undercounts) | 869 |
| 20 at once, `--cache-ram 0`, warm cache | llama | 116 | 1849 / 1866 / 1866 | 863 / 864 / 864 (undercounts) | 870 |
| **10 `/score` calls, `--cache-ram 0`, cold cache** | llama | 60 | 1862 / 1864 / 1864 | **1807 / 1807 / 1807** | **1814** |
| same | scorer (wrapper) | 60 | 5 / 74 / 74 | 49 / 49 / 49 | 63 |
| 20 at once, `--cache-ram 0` | scorer (wrapper) | 116 | 5 / 183 / 183 | 46 / 46 / 46 | 52 |

| Container | request (cpu / mem) | limit (cpu / mem) |
|---|---|---|
| scorer, §17.2 plan | 50m / 96Mi | 500m / 256Mi |
| scorer, rule on the cold run | 10m / 64Mi | 120m / 80Mi |
| scorer (set) | 10m / 64Mi | 500m / 128Mi |
| llama, §17.2 plan | 500m / 1536Mi | 2 / 2560Mi |
| llama, rule on the cold run | 2240m / 2176Mi | 2800m / 2720Mi |
| llama (set) | 1850m / 2176Mi | 2 / 2720Mi |

- **llama CPU:** the figure is cut off by `--threads 2`, so more than 2 cores cannot be used and
  the rule's request would exceed that. Request 1850m (what it used), limit 2 = `--threads`.
- **llama memory:** the rule, on the cold run. It is more than the plan: plan for 2.2 GiB, and for
  twice that during a rolling update (`maxSurge: 1`). On the §17.2 cluster of 7 GiB with every
  service running, that may not schedule (not measured).
- **`--cache-ram 0`:** this llama.cpp build keeps a host-memory prompt cache of up to 8192 MiB by
  default, far above any limit here. It was switched off; with it off, and in a new container, the
  warm-cache figure fell from 1281 to 864 MiB. The old container had also run much longer, so how
  much of that the cache caused was not separated.
- **wrapper:** memory limit 128Mi is twice the 63 MiB peak (the rule's 80Mi leaves little for
  larger evidence). The CPU limit stays at 500m: the rule's 120m comes from samples that do not cover start-up.
- Not covered: the tuned model of M10t (`k8s-lora`) and the §17.2 6-CPU node. Measure again for a
  new model, `--ctx-size` or `--threads`.

## Load: what the runs did (STAND-IN, one scorer slot on CPU)

| Load | Completed | Run latency s p50 / p95 | Dimensions scored / abstained | `validators` restarts |
|---|---|---|---|---|
| 3 in turn (default arguments) | 3/3 | 156 / 229 | 17 / 1 | 0 |
| 20 at once (default arguments) | 20/20 | 588 / 590 | 27 / 93 | 0 (one failed liveness probe) |
| 3 in turn (final scorer) | 3/3 | 93 / 95 | 17 / 1 | 0 |
| 20 at once, liveness 3 s x 3 | 20/20, in 99 s | — | 20 / 100 | **1: killed by the liveness probe** |
| 20 at once, liveness 10 s x 6 | 20/20 | 584 / 587 | 40 / 80 | 0 |

"Completed" means the run ended with an M11 verdict, not that the verdict is useful: **one CPU model
cannot serve 20 concurrent runs.** Most dimensions abstain at M9's 570 s deadline. That is the
designed behaviour (no score is invented), but it does not meet a student's need. Capacity needs the
hosted fallback with quota, more scorer replicas or admission control in the gateway; none was
measured. One further 20-run burst with the old probe did not restart the pod.

## Other containers (STAND-IN, requests and limits NOT changed)

Highest p95 and highest `memory.peak` over the 20-run loads above. Their inputs and the time they
spent waiting came from the stand-in and an overloaded model, so these are not a basis for limits.

| Container | CPU m p95 | memory.peak Mi |
|---|---|---|
| orchestrator | 148 | 82 |
| validators | 264 between samples; 500 (the limit) for about 25 s at the start of a burst | 73 |
| ideation | 13 | 61 |
| critic | 13 | 60 |
| verdict (per replica) | 7 | 62 |
| agentops | 72 | 93 |
| postgres (dev) | 34 | 58 |
| redis (dev) | 27 | 20 |

To do once the real services exist: repeat the load, then set requests and limits for these.
