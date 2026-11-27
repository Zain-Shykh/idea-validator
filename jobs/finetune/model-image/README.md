# `scorer-model` image

The model the scorer's llama.cpp sidecar serves, packed as an OCI image (ADR-005, FT-7). The
`model-import` Job (`k8s/base/scorer.yaml`) copies it into the `models` PVC. **No weights in git**
(`*.gguf` is git-ignored).

## Disk space first

The base model is about 1 GB, and every step keeps another copy:

| Where | Copies | Size |
|-------|--------|------|
| build folder (a hard link costs nothing) | 0–1 | 0–1 GB |
| host Docker: image and build cache | 1–2 | 1–2 GB |
| minikube node: image content + unpacked layers | 2 | 2 GB |
| `models` PVC (in the node) | 1 | 1 GB |
| llama.cpp server image in the node (compressed + unpacked) | – | 1.1 GB |

Have **at least 7 GB free** on top of the safety margin before you start (`df -h ~`).

## Get the base model

Either the official file (`Qwen/Qwen2.5-1.5B-Instruct-GGUF`, `qwen2.5-1.5b-instruct-q4_k_m.gguf`,
Apache-2.0) from Hugging Face, or the same model and quantisation already pulled by Ollama:

```bash
ctx=$(mktemp -d -p "$HOME/.cache")          # same file system as ~/.ollama, so ln works
ln "$(ollama show qwen2.5:1.5b --modelfile | awk '/^FROM/{print $2; exit}')" "$ctx/model.gguf"
```

## Build and load

```bash
IMAGE=ghcr.io/zain-shykh/idea-validator-scorer-model:base-v1
docker build -f jobs/finetune/model-image/Dockerfile -t "$IMAGE" "$ctx"
docker save "$IMAGE" | docker exec -i idea-validator ctr -n k8s.io images import -   # no temp tar
docker rmi "$IMAGE" && docker builder prune -f
rm "$ctx/model.gguf" && rmdir "$ctx"
```

A new model (Z9) gets a new tag (`lora-v1`), a new file name on the PVC, a new Job name (Jobs are
immutable) and a new `SCORER_MODEL_ID`, all in `k8s/base/scorer.yaml`.
