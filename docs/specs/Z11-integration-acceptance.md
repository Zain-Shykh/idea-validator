# Z11 — Cluster integration, measurements, end-to-end acceptance

| Field | Value |
|-------|-------|
| Phase | `Z11` from `docs/implementation-plan-zain.md` |
| Owner | Zain |
| Author | Claude Code headless session |
| Branch | `feat/z11-integration` (from `feat/z10-evaluation-tuning`, draft PR #23) |
| Status | Draft (partly blocked: see A3) |
| Requirement IDs | U1–U10, K8S-5, K8S-11, M3-5, M10-1/2 (Z4 open criterion), AO-6, SS-10..16, GIT-4 |
| Architecture refs | `architecture.md` §14.2, §17, §19 |
| Depends on | Z4–Z10. Missing: Ahmad's M1, M2, M4, M5, M6, M7, UI (A-phases), a Gemini key, human labels |
| Issue / PR | filled at PR time |

---

## Part A — Specification

### A1. Objective
Run everything Zain owns on the project's Kubernetes cluster at the current commit, with the real scorer model going image → import Job → PVC → llama.cpp, find and fix what only shows up there, and produce a repeatable load and resource-sampling method with measured requests and limits for the workload that is real today (the scorer).

### A2. Context
- Exists: manifests for orchestrator, ideation, validators, scorer (+ `models` PVC and `model-import` Job), verdict, critic, agentops, drift CronJob, eval Job; temporary dev Postgres and Redis; profile `idea-validator` (Calico). Z4 proved the import path only with a 1 MB stand-in file and the model only through a mount.
- Missing: every service of Ahmad's, so no real knowledge base, profile store, hosted model, gateway or UI exists.

### A3. Scope
**In scope:** the Z4 open criterion (full import path, probe-gated readiness, real `/score`); images of Zain's seven services and the evaluation image built at this commit and deployed with the dev overlay; checks of probes, migrations, ConfigMaps, eval Job, drift CronJob; the 7 integration tests; fixes of real deployment bugs, each with a test; `scripts/load_validate.py` and a sampling script; a **labelled stand-in** for M2/M4/M5/M6/M7 that is applied by hand only for the check and removed again; `docs/evidence/resources.md`; owner runbook.

**Out of scope / blocked (people or Ahmad's work):** U1–U10 end to end; UI screenshots SS-10..13, SS-15, and SS-14/SS-16 as screenshots (a headless session takes none, and a drift report on a stand-in is not evidence); merge-conflict exercise (GIT-4); switching to real M2/M4/M5/M6/M7; right-sizing from end-to-end load; any quality or performance claim about the full system. Editing Ahmad's files (the dev overlay was created by Z1 for Zain's images; only its image tags change).

### A4. Requirements and acceptance criteria

| # | Req ID | Acceptance criterion | Verified by |
|---|--------|----------------------|-------------|
| 1 | M10-1, M10-2, K8S-3 (Z4 #4) | On the cluster: base GGUF → `scorer-model:base-v1` image → import Job (checksum) → `models` PVC → llama.cpp sidecar; the wrapper's `/ready` turns 200 only after the model answered the probe; ten real `/score` calls return valid `DimensionScore`s | manual: `scripts/scorer_check.py`, pod events and logs in `docs/evidence/cluster-z11.md` |
| 2 | K8S-5, K8S-11 | Zain's 7 Deployments run images of one commit, all Ready with both probes (orchestrator included), no unexpected restarts; agentops migrations at head; `verdict-config`, `budgets`, `rubrics`, `prompts`, `app-config` mounted and loaded | manual: same file; `eval/behaviour/test_packaging.py` (static) |
| 3 | AO-4, AO-5 | Eval Job (through `scripts/check_eval.sh`) and a Job made from the drift CronJob run in the cluster as far as the missing services allow, and each ends with the documented exit code; with the stand-in, nothing is saved as a real result | manual: same file |
| 4 | M3-5, WF-V | One Validate run goes through all of Zain's services in the cluster (stand-in for the others) to a stored M11 verdict; a second identical run is a cache hit | manual: same file, labelled STAND-IN |
| 5 | — | The 7 integration tests pass | `pytest -m integration` |
| 6 | K8S-5 | `load_validate.py` starts N concurrent Validate runs and reports statuses, latency percentiles and errors; non-zero exit when a run does not complete | `scripts/tests/test_load_validate.py` |
| 7 | K8S-5 | The sampler computes p50/p95/max CPU and memory per container from `kubectl top` samples; requests ≈ p50 + headroom, limits ≈ p95 × 1.5 | `scripts/tests/test_sample_resources.py` |
| 8 | K8S-5 | Requests/limits changed only for containers whose real workload was measured; method, numbers and the stand-in label in `docs/evidence/resources.md` | `eval/behaviour/test_packaging.py` (scorer values), review of the file |
| 9 | — | No fake or stand-in is used outside tests: no service source imports `common.fakes`, no manifest or compose file names the stand-in or sets `LLM_BACKEND=fake` | `eval/behaviour/test_no_fakes.py` |
| 10 | U1–U10 | **(blocked: A1–A9)** demonstrated on the cluster with real services | runbook C6 |
| 11 | SS-10..16 | **(blocked: UI A9, real data)** screenshots with captions | runbook C6 |
| 12 | GIT-4 | **(blocked: Ahmad)** merge-conflict PR | runbook C6 |

The plan's criterion "numbers for Zain's workloads updated from measurements" is met only for the scorer pod; for the other services it stays open until the real dependencies exist.

### A5. Interfaces and contracts
No contract changes. `load_validate.py` is a client of M2 `POST /internal/ideas` and M3 `POST /runs`, `GET /runs/{id}`, `/answer`, `/approve`, reading `ORCHESTRATOR_URL` and `PROFILE_URL`. The stand-in answers the same `common.contracts` models.

### A6. Design
- **Model image:** the Ollama blob of `qwen2.5:1.5b` (same model and quantisation) is hard-linked into a build folder; the image is streamed into the node with `docker save | ctr images import -` and removed from the host.
- **Stand-in** (`scripts/standins/`): one small FastAPI app, run from an existing image with its code in a ConfigMap, behind Services named `profile`, `kb`, `tools`, `scouts`, `analyst`, so `app-config` is unchanged. Every object is labelled `idea-validator/stand-in: "true"`; the model it reports is `stand-in`. It is outside `k8s/`, and removed after the check. It refuses the alias `scorer-hosted`, so a failed local score abstains instead of being invented.
- **Load:** reuses `run_eval.Harness`'s run protocol where possible (neutral answers, approvals declined). Each run has its own user, so no verdict comes from the cache.
- **Sampling:** `kubectl --context idea-validator top pods --containers` every 5 s during the load (metrics-server resolution is about 15 s), plus each container's cgroup `memory.peak` and CPU throttling counters, which catch peaks between samples.

Disk estimate before copying (free 24 GB, floor 5 GB): GGUF hard link 0; host model image + build cache ≤ 2 GB; node: model image 2 GB, PVC 1 GB, llama.cpp 1.1 GB; eight service images ≈ 0.6 GB on host and in node. Worst case ≈ 7 GB, so ≈ 17 GB stays free.

### A7. Edge cases and failure handling

| Case | Expected behaviour |
|------|--------------------|
| Model file missing or partial | wait-for-model blocks; the Job renames only a checked file |
| Model still loading | `/ready` 503, no traffic |
| Dependency of a service absent | the documented 503 / exit code; no invented data |
| Load larger than one CPU model can serve | scorer calls time out or queue; reported as errors and abstentions, never hidden |
| A run does not finish within the load script's limit | counted as failed; exit 1 |
| Sampler: metrics not yet available, `kubectl` fails | sample skipped and counted; no samples → exit 2 |

### A8. Test plan
- Unit: load script against a fake HTTP transport; sampler parsing and percentile maths.
- Static: manifest tests (existing) plus the no-fake grep test.
- Integration: the 7 existing tests on compose Postgres and Redis.
- Manual cluster checks with commands and output in `docs/evidence/cluster-z11.md`.

### A9. Assumptions and open questions
- A-1: Ollama's `qwen2.5:1.5b` blob is Qwen2.5-1.5B-Instruct Q4_K_M, the same file Z4 used; the official Hugging Face file is not downloaded.
- A-2: Local images are loaded into the node under the GHCR names; nothing is pushed to a registry (A0/A10).
- A-3: Resource numbers come from one laptop (i3-1215U, no GPU) and one node; they are not valid for another machine.
- A-4: Evidence text served by the stand-in is made up, so scores from the cluster check say nothing about quality. The model's latency and memory on those prompts are real.

### A10. Out of scope
See A3.

---

## Part B — Implementation plan

_filled next_

---

## Part C — Report

_filled at the end_
