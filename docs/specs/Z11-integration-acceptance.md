# Z11 — Cluster integration, measurements, end-to-end acceptance

| Field | Value |
|-------|-------|
| Phase | `Z11` from `docs/implementation-plan-zain.md` |
| Owner | Zain |
| Author | Claude Code headless session |
| Branch | `feat/z11-integration` (from `feat/z10-evaluation-tuning`, draft PR #23) |
| Status | Implemented as far as possible; **Blocked** (see C6) |
| Requirement IDs | U1–U10, K8S-5, K8S-11, M3-5, M10-1/2 (Z4 open criterion), AO-6, SS-10..16, GIT-4 |
| Architecture refs | `architecture.md` §14.2, §17, §19 |
| Depends on | Z4–Z10. Missing: Ahmad's M1, M2, M4, M5, M6, M7, UI (A-phases), a Gemini key, human labels |
| Issue / PR | #24 / #25 (draft) |

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
- **Stand-in** (`scripts/standins/`): one small FastAPI app, run from an existing image with its code in a ConfigMap, behind Services named `profile`, `kb` and `tools`, so `app-config` is unchanged (M4 and M7 are not stood in for: a Validate run goes on without them). Every object is labelled `idea-validator/stand-in: "true"`; the model it reports is `stand-in`. It is outside `k8s/`, and removed after the check. It refuses the alias `scorer-hosted`, so a failed local score abstains instead of being invented.
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

| # | Task | Files | Verification |
|---|------|-------|--------------|
| T1 | Start the profile; build the model image from the Ollama blob, stream it into the node, remove it from the host | no repo change | `df -h`; `ctr images ls` |
| T2 | Scorer path on the cluster: deploy, import Job, sidecar, probe-gated `/ready`, ten `/score` calls | `docs/evidence/cluster-z11.md`; fixes if needed | `scripts/scorer_check.py` through a port-forward |
| T3 | Build the 8 images at one commit, load them, set the overlay tags, deploy; check probes, migrations, ConfigMaps | `k8s/overlays/dev/kustomization.yaml`, evidence | `kubectl get pods`, logs, DB queries |
| T4 | Drift CronJob and eval Job in the cluster without and with the stand-in | evidence; fixes | exit codes |
| T5 | Stand-in and one end-to-end Validate run through Zain's services | `scripts/standins/`, `scripts/tests/test_standin.py`, `eval/behaviour/test_no_fakes.py` | pytest; run result |
| T6 | Load script and sampler | `scripts/load_validate.py`, `scripts/sample_resources.py`, `scripts/tests/` | pytest |
| T7 | Load run with sampling; resources file; scorer requests/limits | `docs/evidence/resources.md`, `k8s/base/scorer.yaml` | manifest tests, re-deploy |
| T8 | Integration tests | — | `pytest -m integration` |
| T9 | Testing subagent, fixes | tests | full suite |
| T10 | Docs, runbook, draft PR; clean up (stand-in, profile, compose, Ollama, own images) | docs | checks |

Commit plan: scripts and tests per task; each deployment fix as its own `fix(...)` commit with its test; evidence with the task that produced it.

**Definition of done:** A4 rows 1–9 pass (or are reported as failed with the reason), rows 10–12 stay open, plan status Blocked, draft PR open, machine cleaned up.

---

## Part C — Implementation and test report

Built in two headless sessions: the first (spec, plan, two cluster fixes, image tags, stand-in)
stopped on a usage limit while verifying; the second checked that work, redid the checks that had
left no record, and finished.

### C1. What was built
| File | What |
|------|------|
| `scripts/load_validate.py` | N concurrent Validate runs; statuses, latency percentiles, errors, abstentions; exit 0/1/2; marks stand-in reports |
| `scripts/sample_resources.py` | `kubectl top` + cgroup sampler around a command; p50/p95/max and proposed requests/limits |
| `scripts/standins/` | labelled stand-in for M2, M5, M6; hand-applied, removed by label |
| `scripts/check_eval.sh` | takes the deployed `app-config` name; `KUBE_CONTEXT` |
| `jobs/evaluation/{run_eval,drift,reports}.py` | a report made from the stand-in knowledge base is always fake |
| `k8s/base/scorer.yaml`, `k8s-lora/component/scorer.yaml` | measured requests/limits; `--cache-ram 0` |
| `k8s/base/validators.yaml` | liveness 10 s x 6 |
| `k8s/overlays/dev/kustomization.yaml` | image tags `fa9a9e2` (evaluation `7214dd8`) |
| `eval/behaviour/test_no_fakes.py`, `test_packaging.py` | no fake deployed; measured values; probe tolerance |
| `docs/evidence/{cluster-z11,resources}.md` | cluster checks and measurements |

**Acceptance (A4).**

| # | Result |
|---|--------|
| 1 | **Pass.** Full path with the real base model; `/ready` 503 → probe → 200; 10/10 valid scores |
| 2 | **Pass** for Zain's 7: one commit, Ready, probes, migrations `0002`, ConfigMaps. One `validators` restart under load, fixed |
| 3 | **Pass** as far as possible: without M2/M6 both exit 2 (expected); stand-in reports are fake and not stored. `check_eval.sh` was not run against the stand-in (the Job was, with one idea) |
| 4 | **Pass, STAND-IN:** M11 verdict stored; identical second run a cache hit |
| 5 | **Pass:** 7/7 integration tests on compose Postgres and Redis |
| 6, 7 | **Pass:** `test_load_validate.py`, `test_sample_resources.py` |
| 8 | **Pass:** scorer pod only |
| 9 | **Pass:** `test_no_fakes.py` |
| 10, 11, 12 | **Blocked**, not done (U1–U10, SS-10..16, GIT-4) |

### C2. Deviations from spec / architecture
| What | Why | Logged in deviations.md as |
|------|-----|---------------------------|
| §17.2 scorer pod: llama 1850m/2176Mi, limit 2/2720Mi; wrapper 10m/64Mi, limit 500m/128Mi | measured | D-025 |
| Spec A6: the stand-in covers M2, M5, M6 only (not M4, M7) | the run does not need them | spec only (Z11-6, Z11-11) |
| Spec B T4: the stand-in eval used one idea | 40 ideas would take over two hours on one CPU | spec only |
| Spec A4 #8: warm-cache figures are not enough; memory measured with a cold page cache; liveness and `--cache-ram` changed | found under load | spec only (Z11-8..10) |

### C3. Test report
**Testing subagent** (`agent-skills:test-engineer`, test files only). `ruff`, `mypy` clean;
`pytest --cov=scripts --cov=jobs/evaluation`: 2377 passed, 8 failed (bugs, left failing), 2
skipped; `kubeconform` 28/28. Coverage: `load_validate.py` 99 %, `sample_resources.py` 99 %,
`run_eval.py` 97 %, `drift.py` 98 %, `reports.py` 90 %, `standin.py` 77 %. Shell scripts are
tested, not measured.

| Found | Fixed in `52a516e` |
|-------|--------------------|
| Load report's stand-in label depended on one probe | also taken from the verdicts' knowledge-base version |
| An unwritable `--out` lost the whole measurement (both tools) | print first; a failed write keeps the exit code |
| SIGTERM to the sampler left the load running | handled; the child is stopped |
| `--interval -1` killed the running command; other values unchecked | refused before anything starts |
| An unusable service URL exited 1 ("a run failed") | exit 2 |
| `resources.md`: a rule figure was 90m, the rule gives 120m | corrected, with two smaller mismatches |

Not changed: Ctrl-C loses the samples taken so far; `jobs/finetune` (offline, uses `common.fakes`
to sample made-up cards) stays outside the no-fake check; M3 failing `/stats` for **every** idea
with the stand-in otherwise up would leave an eval report unmarked (not reachable with
`standin.py`, which serves `/stats`).

**After the fixes:** `ruff check`, `ruff format --check`, `mypy libs services jobs scripts` clean;
`pytest -q`: **2385 passed, 2 skipped, 7 deselected**; `pytest -m integration`: **7 passed**
(run before the fixes, which touch no code those tests use); `kubectl kustomize
k8s/overlays/dev | kubeconform -strict`: 28/28 valid.
**Images:** the seven services at `fa9a9e2` and evaluation at `7214dd8` were built and run on the
cluster. The last fixes change no image content.
**Bugs found on the cluster** (before the subagent): eval Job ConfigMap name; stand-in manifest
anchor; unmarked stand-in reports; `validators` liveness kill; unbounded llama.cpp cache; memory
figures without the model. `docs/evidence/cluster-z11.md`.

### C4. Measurements
All in [`docs/evidence/resources.md`](../evidence/resources.md); load figures are STAND-IN.
Real: scorer p50 26–37 s per call on 2 CPU threads; llama.cpp about 1.86 cores and 1807Mi from a
cold start. **STAND-IN, not a system result:** 3 runs in turn p50 93 s; 20 at once all end with a
verdict after about 590 s with 80 of 120 dimensions abstained: **one CPU model does not carry 20
concurrent runs.**

### C5. Follow-ups
- Capacity before a demo with several users: hosted fallback with quota, scorer replicas, or
  admission control in the gateway (A8). Not measured.
- Requests and limits of the other services, from load with the real M2/M5/M6; then also the
  `validators` CPU limit (it sat at 500m for about 25 s at the start of a burst).
- The `validators` restart: cause of the long `/health` stall not established. Watch
  `kubectl get pods` and probe events in the next load.
- §17.2's 7 GiB cluster with every service and a surge of the 2.2 GiB scorer is untested.
- Base model output cut off mid-JSON on one dimension, then a retry past the 60 s local timeout:
  check `max_tokens` for the scorer with the tuned model.
- For Ahmad (A0, who takes over `k8s/base/kustomization.yaml` and the dev overlay): nothing to
  change; the overlay's tags must move to CI-built images.

### C6. Owner runbook (what is still blocked, and how to finish)
Blocked on: Ahmad's M1, M2, M4–M7, UI and platform (A0–A10); a Gemini key; human labels
(Z10 C6); a tuned model (Z9 C6).

1. Merge the stacked PRs in order, up to #23, then this one. Ahmad reviews.
2. Once the A-phases are merged, start the cluster and set the `db` and Gemini Secrets
   (`docs/setup.md`). Check no stand-in is left:
   `kubectl --context idea-validator -n idea-validator get all,cm -l idea-validator/stand-in=true`
   must be empty.
3. **Reset the dev M13 data** (it holds stand-in runs, which would show in `/summary` and the drift
   mix). Your decision, because it deletes: empty `traces_index` and `eval_runs` in the `ops`
   database and the files on the `traces` volume.
4. Build the images of one commit (or use CI's), set the tags in the dev overlay, apply. Add
   `profile` to `ORCH_READY_REQUIRES`. The `models` PVC keeps the base model; for a new one load
   its image and use the new Job.
5. `scripts/scorer_check.py`, then one Validate run by hand: no `stand-in` in any version or model.
6. Labels and baseline (spec Z10 C6), then `KUBE_CONTEXT=idea-validator scripts/check_eval.sh`; run
   it once against a perturbed baseline and see exit 1. Run a Job from `drift-report` after an
   ingestion and check `GET /drift/latest`.
7. Load and sample as in `resources.md` (evict the model file first); the report must say
   `"stand_in": false`. Set requests and limits for every workload of yours and update the file.
8. Walk U1–U10 in the UI with Ahmad; note the evidence per capability and tick the rows:

| U | Show |
|---|------|
| U1 | sign up, save a profile, change it, the recommendations change |
| U2 | Discover: ranked ideas, each with a verdict and reason |
| U3 | live view: running agent, tool calls, spend |
| U4 | deep search: cost shown, the run waits, approve and decline |
| U5 | own idea: only questions that could change the result |
| U6 | verdict card with gates, six scores, evidence links, weights, stability (SS-10) |
| U7 | "what would make this GO", apply one, version 2 (SS-12) |
| U8 | challenge a claim: defended or removed (SS-11) |
| U9 | history, versions, comparison of two ideas (Ahmad) |
| U10 | an INSUFFICIENT EVIDENCE card naming what is missing (SS-13) |

   Also SS-14 (a trace), SS-15 (the dashboard) and SS-16 (a drift report on real data).
   Save them in `docs/evidence/screenshots/` with one line each in `captions.md`.
9. **GIT-4, with Ahmad:** two branches change the same line of one file; merge the first PR, bring
   `main` into the second, resolve the conflict there, screenshot the conflict and the resolved PR
   (SS-2).
10. Tick the plan and checklist rows that now pass and set Z11 to Done.
