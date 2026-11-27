# Setup Guide

> How to set up the project on a new machine (for Ahmad, markers, or anyone cloning the repo). Commands are for **Ubuntu 24.04**; macOS/Windows (WSL2) work with the equivalent installers.
> Some steps only become runnable after the phase that creates the files (marked *after A0*, *after Z1*, …). Running the finished system end to end is summarised in the root `README.md`.

## Contents
1. [Prerequisites](#1-prerequisites)
2. [Clone and git](#2-clone-and-git)
3. [Python virtual environment](#3-python-virtual-environment)
4. [Environment variables and keys](#4-environment-variables-and-keys)
5. [Local development with Docker Compose](#5-local-development-with-docker-compose)
6. [Kubernetes (minikube + Calico)](#6-kubernetes-minikube--calico)
7. [Local LLM (Ollama)](#7-local-llm-ollama)
8. [Cleanup, teardown and disk space](#8-cleanup-teardown-and-disk-space)
9. [Claude Code (optional)](#9-claude-code-optional)
10. [Jenkins (after A0)](#10-jenkins-after-a0)
11. [Fine-tuning environment (Colab/Kaggle)](#11-fine-tuning-environment-colabkaggle)
12. [Verify your setup](#12-verify-your-setup)
13. [Troubleshooting](#13-troubleshooting)

---

## 1. Prerequisites

Versions tested on the reference machine (Ubuntu 24.04, 8 CPU, 15 GB RAM). Newer patch versions are fine.

| Tool | Tested version | Needed for | Install |
|------|----------------|-----------|---------|
| git | 2.43 | everything | `sudo apt install git` |
| GitHub CLI `gh` | 2.101 | PRs, issues, branch protection | https://cli.github.com → `gh auth login` |
| Python | 3.12.3 | services, jobs, tests | `sudo apt install python3.12 python3.12-venv` |
| Node.js + npm | 24.17 | UI (React + Vite) | https://nodejs.org (or `nvm install 24`) |
| Docker Engine + Compose | 29.8 / v5.5 | images, local dev | https://docs.docker.com/engine/install/ubuntu/ ; add yourself to the `docker` group |
| kubectl | 1.37 | Kubernetes | https://kubernetes.io/docs/tasks/tools/ |
| minikube | 1.39 | local cluster | https://minikube.sigs.k8s.io/docs/start/ |
| kubeconform | 0.8 | manifest validation | `go install github.com/yannh/kubeconform/cmd/kubeconform@latest` or release binary |
| Ollama | 0.40 | free local LLM for dev | https://ollama.com/download |
| Trivy (optional) | latest | local image scan (CI does it anyway) | https://trivy.dev |

**Hardware/disk:** ≥ 8 GB RAM free for the cluster, **≥ 20 GB free disk** recommended (venv ≈ 1 GB, minikube node + images ≈ 8–10 GB, model ≈ 1 GB). No GPU needed locally (fine-tuning runs on Colab/Kaggle).

**Accounts / keys:** GitHub account (collaborator on the repo), **Google AI Studio API key** for Gemini (free tier: https://aistudio.google.com/apikey), **GitHub personal access token** (fine-grained, public repo read-only — for the GitHub scout's higher rate limit). Optional: Product Hunt developer token, Reddit app credentials.

## 2. Clone and git

```bash
gh auth login                                   # HTTPS, browser login
gh repo clone Zain-Shykh/idea-validator
cd idea-validator
git config user.name  "Your Name"
git config user.email "you@example.com"         # the email linked to your GitHub account (contribution table must match)
```
`main` is protected: work on branches and open PRs (see `docs/workflow.md` §3).

## 3. Python virtual environment

One repo-wide virtualenv for local tooling and tests (each service image has its own pinned `requirements.txt`). Deleting `.venv` frees ~1 GB when the project is over.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements-dev.txt
pip install -e libs/common                      # shared contracts package
# only for LLM_BACKEND=direct: pip install -e 'libs/common[direct]' (already in requirements-dev.txt)
```
Each new terminal: `source .venv/bin/activate`. VS Code: select `.venv/bin/python` as interpreter.

UI dependencies (after A9 creates `services/ui/package.json`): `cd services/ui && npm ci`.

## 4. Environment variables and keys

```bash
cp .env.example .env        # .env is git-ignored — never commit it
# edit .env: GEMINI_API_KEY, GITHUB_TOKEN, passwords, JWT secret
```
- Generate secrets: `python -c "import secrets; print(secrets.token_urlsafe(32))"` for `JWT_SECRET`, `POSTGRES_PASSWORD`, `REDIS_PASSWORD`.
- Kubernetes secrets are created from `.env` by `scripts/create_secrets.sh` (*after A0*).

## 5. Local development with Docker Compose

*After A0 / Z1 / A1* (root `docker-compose.yml` includes each service's `compose.yaml`):

```bash
docker compose up -d postgres redis             # data stores only (for integration tests)
docker compose up --build                       # everything
docker compose ps                               # health status
curl -s localhost:8011/health                   # e.g. verdict service
docker compose down                             # stop (add -v to drop volumes)
```
If a host port is taken (for example a local Postgres on 5432), set `POSTGRES_HOST_PORT` / `REDIS_HOST_PORT`. Tag images with the commit: `IMAGE_TAG=$(git rev-parse --short HEAD) docker compose up --build`. Integration tests read `REDIS_HOST_PORT` and `REDIS_PASSWORD` from the shell: `pytest -m integration`.

Host ports: see `docs/architecture/architecture.md` §19 (gateway 8001 … agentops 8013, UI 5173). Compose is for development only; the deployment target is Kubernetes.

AgentOps integration tests use a throwaway database whose name ends in `_test`; they create and **drop** it:
```bash
AGENTOPS_TEST_DATABASE_URL=postgresql+psycopg://ideaval:localdev@127.0.0.1:5432/ops_test \
  pytest -m integration services/agentops          # use your POSTGRES_PASSWORD and host port
```

## 6. Kubernetes (minikube + Calico)

The cluster's network plugin **must enforce NetworkPolicy** (LOC-1), so we use **Calico**. Use a **dedicated profile** so other minikube clusters on your machine are untouched.

```bash
minikube start -p idea-validator --driver=docker --container-runtime=containerd \
  --kubernetes-version=v1.37.0 --cni=calico --cpus=6 --memory=7g --disk-size=30g
minikube -p idea-validator addons enable ingress
minikube -p idea-validator addons enable metrics-server
kubectl config use-context idea-validator
kubectl get pods -A                              # calico-*, ingress-nginx-*, metrics-server-* Running
```
Lower `--cpus/--memory` if your machine is smaller (minimum ~4 CPU / 6 GB for the full system).

Daily use:
```bash
minikube -p idea-validator start                 # resume (keeps settings)
minikube -p idea-validator stop                  # free RAM when not working
minikube -p idea-validator ip                    # ingress address (add to /etc/hosts as idea-validator.local)
```
Until A0 provides secrets, create the database password once (agentops and the temporary dev Postgres read it; it is not stored in git):
```bash
kubectl create namespace idea-validator --dry-run=client -o yaml | kubectl apply -f -
kubectl -n idea-validator create secret generic db --from-literal=POSTGRES_PASSWORD="$(openssl rand -hex 16)"
```
The temporary Postgres has no volume: if its pod is replaced, restart agentops (`kubectl -n idea-validator rollout restart deploy/agentops`) so `db-migrate` creates the tables again.

Deploy (*after A0/Z1*): `kubectl apply -k k8s/overlays/dev` · status: `kubectl get pods -n idea-validator`.

Images are named `ghcr.io/zain-shykh/idea-validator-<svc>:<sha>` (public GHCR, after A0). Until they are pushed, build and load them, then set the same tag in `k8s/overlays/dev/kustomization.yaml` (`newTag`):
```bash
TAG=$(git rev-parse --short HEAD)
SVCS="orchestrator ideation validators scorer verdict critic agentops"
for s in $SVCS; do docker build -f services/$s/Dockerfile -t ghcr.io/zain-shykh/idea-validator-$s:$TAG . ; done
docker save $(for s in $SVCS; do echo ghcr.io/zain-shykh/idea-validator-$s:$TAG; done) | minikube -p idea-validator image load -
```
The scorer pod also needs the model image `ghcr.io/zain-shykh/idea-validator-scorer-model:base-v1` in the node, or it waits in `Init`. It is not on GHCR yet: build and load it as in [`jobs/finetune/model-image/README.md`](../jobs/finetune/model-image/README.md) (**needs ≥ 7 GB free**).

Add `--keep-context` to `minikube start` and `--context idea-validator` to `kubectl` if you do not want to switch your current context.

## 7. Local LLM (Ollama)

Free, offline model for development (`LLM_BACKEND=direct` with alias `local-dev`, or `SCORER_BACKEND=ollama`).

```bash
ollama serve &                                   # if not running as a service
ollama pull llama3.2                             # agent dev model (already present on the reference machine)
ollama pull qwen2.5:1.5b                         # base scorer for Z4 (≈1 GB) — pull when Z4 starts
curl -s localhost:11434/api/tags | head
```
From containers use `http://host.docker.internal:11434` (Compose adds `extra_hosts: host-gateway`).

**Scorer (M10) on the host** (about 30 s per score on a laptop CPU):
```bash
OLLAMA_BASE_URL=http://127.0.0.1:11434 SCORER_BACKEND=ollama LLM_BACKEND=direct \
  uvicorn scorer.main:app --app-dir services/scorer/src --port 8010   # from the repo root
python scripts/scorer_check.py --url http://127.0.0.1:8010            # ten fixture pairs
```
`--record eval/behaviour/cassettes/scorer_base.jsonl` (instead of `--url`, same variables) records the model's replies again. The hosted fallback needs `GEMINI_API_KEY` with `LLM_BACKEND=direct`, or M5; without it a failed local score abstains. In Compose, Ollama must listen where the container can reach it (`OLLAMA_HOST=0.0.0.0` exposes it to your network, so use a trusted one).

**Validators (M9)** need M6 (`KB_URL`) and M10 (`SCORER_URL`). M6 is not built yet, so a running service answers 503 `KB_UNAVAILABLE`; tests use `common.fakes.FakeKB` and `FakeAnalyst`. Without M5 or `LLM_BACKEND=direct` the effort estimate is the middle of the code's band. With one CPU model keep `VALIDATORS_SCORER_CONCURRENCY=1`, and give each request up to 600 s.
```bash
pytest -q services/validators eval/behaviour       # no model needed
```

**Critic (M12)** answers `/rail/input`, `/scan` and (with `config/budgets.yaml`) `/policy/approval` on its own. `/check` also needs M6 (`KB_URL`), which is not built yet, so it answers 503 `KB_UNAVAILABLE`; without a model (M5, or `LLM_BACKEND=direct`) claims that pass the code checks are `unverifiable`.
```bash
KB_URL=http://127.0.0.1:8005 LLM_BACKEND=direct uvicorn critic.main:app --app-dir services/critic/src --port 8012   # from the repo root
pytest -q services/critic eval/behaviour           # no model needed
```

## 8. Cleanup, teardown and disk space

Check: `df -h ~` and `docker system df`.

Routine (safe):
```bash
docker image prune -f              # dangling images
docker builder prune -f            # build cache
```

Project teardown (frees everything this project created):
```bash
minikube delete -p idea-validator  # deletes ONLY this project's cluster
rm -rf .venv                       # Python deps (~1 GB)
rm -rf services/ui/node_modules    # UI deps
docker compose down -v             # dev containers + volumes
docker images | grep idea-validator | awk '{print $3}' | xargs -r docker rmi -f
ollama rm qwen2.5:1.5b             # optional
```

## 9. Claude Code (optional)

Zain runs phases with headless Claude Code sessions (`scripts/run-phase.sh <PHASE>`, `docs/workflow.md` §5). `CLAUDE.md` is git-ignored (personal), so a new user creates their own at the repo root. Minimal template:

```markdown
# CLAUDE.md
Project: Idea Generator with Go / No-Go (CS4032). I am <name>, owner of modules <list> (docs/work-distribution.md).
Always read before working: docs/workflow.md (process), docs/requirements.md (IDs), docs/architecture/architecture.md,
docs/conventions.md, my plan docs/implementation-plan-<me>.md.
Rules: spec -> plan -> implement -> test subagent -> docs -> PR; never push to main; edit only my files;
LLM never decides the verdict; every score cites evidence ids; no secrets; use .venv.
```
Requires the `claude` CLI (`npm i -g @anthropic-ai/claude-code`) and `jq` for log viewing (`sudo apt install jq`).

## 10. Jenkins (after A0)

Jenkins runs in Docker on the machine that hosts the cluster (JEN-3); a GitHub webhook cannot reach a laptop, so it **polls SCM every 2 minutes** (JEN-4). Outline (exact files come in A0):

```bash
docker run -d --name jenkins --network minikube -p 8080:8080 -p 50000:50000 \
  -v jenkins_home:/var/jenkins_home -v /var/run/docker.sock:/var/run/docker.sock \
  idea-validator-jenkins:lts          # image built from ci/jenkins/Dockerfile (Jenkins LTS + docker CLI + kubectl)
docker exec jenkins cat /var/jenkins_home/secrets/initialAdminPassword
```
Then: install suggested plugins → add credentials `kubeconfig` (Secret file: `minikube -p idea-validator kubectl -- config view --flatten --minify`, with the server address reachable from the `minikube` Docker network) and `ghcr-token` (username + PAT with `write:packages`) → create a Pipeline job from SCM (this repo, `Jenkinsfile`, poll `H/2 * * * *`).

## 11. Fine-tuning environment (Colab/Kaggle)

Not installed locally (no GPU, saves disk). Open `jobs/finetune/train.ipynb` (*after Z9*) in Google Colab or Kaggle with a free **T4 GPU**; the notebook installs Unsloth/PEFT/TRL itself. Upload `jobs/finetune/data/train.jsonl` and `test.jsonl`; download the GGUF and attach it to a GitHub Release / build the `scorer-model` image (commands in the notebook and `jobs/finetune/README` section of the spec).

## 12. Verify your setup

```bash
git --version && gh auth status
source .venv/bin/activate && python -c "import fastapi, pydantic, litellm, fastembed; print('python deps ok')"
docker run --rm hello-world >/dev/null && echo "docker ok"
minikube -p idea-validator status && kubectl get nodes
kubectl get pods -n kube-system | grep calico      # Calico running = NetworkPolicy enforced
kubeconform -v
curl -s localhost:11434/api/tags >/dev/null && echo "ollama ok"
```

## 13. Troubleshooting

| Problem | Fix |
|---------|-----|
| `permission denied` on `/var/run/docker.sock` | `sudo usermod -aG docker $USER`, log out/in |
| minikube fails: not enough memory | lower `--memory`, close other apps, or `minikube -p idea-validator stop` other profiles |
| NetworkPolicy has no effect | cluster not started with `--cni=calico`; recreate the profile |
| `ImagePullBackOff` | image not pushed to GHCR or package not public; or use `minikube image load` |
| Pods `Pending` | `kubectl describe pod …` — usually CPU/memory requests too high for the node |
| Gemini `429` | free-tier rate limit: wait, rely on caching/cassettes, or use `local-dev` alias |
| Disk full | §8 routine cleanup; check `docker system df` |
| `ollama` not reachable from containers | run `ollama serve` with `OLLAMA_HOST=0.0.0.0` and use `host.docker.internal` |
