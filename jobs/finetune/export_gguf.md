# Merged model → GGUF Q4_K_M → release asset → image (FT-7, M10-5)

Starts where [`train.ipynb`](train.ipynb) ends: `outputs/merged/` holds the base model with the
adapter merged in, 16 bit. **No weights in git**: `outputs/` and `*.gguf` are git-ignored and
`tests/test_packaging.py` fails on a weight file.

> Not yet run: these steps need a trained model.

## 1. Convert and quantise (in the same Colab/Kaggle session, after training)

llama.cpp's requirements may replace training libraries, so do this only when training is done.
Run these as shell commands (Colab: a `%%bash` cell) from the `FT_DIR` folder.

```bash
git clone --depth 1 https://github.com/ggml-org/llama.cpp /tmp/llama.cpp
git -C /tmp/llama.cpp rev-parse HEAD                 # note it for the release notes
pip install -q -r /tmp/llama.cpp/requirements/requirements-convert_hf_to_gguf.txt
python /tmp/llama.cpp/convert_hf_to_gguf.py outputs/merged \
  --outfile outputs/scorer-lora-v1-f16.gguf --outtype f16
cmake -S /tmp/llama.cpp -B /tmp/llama.cpp/build
cmake --build /tmp/llama.cpp/build --target llama-quantize -j
/tmp/llama.cpp/build/bin/llama-quantize \
  outputs/scorer-lora-v1-f16.gguf outputs/scorer-lora-v1-q4_k_m.gguf Q4_K_M
sha256sum outputs/scorer-lora-v1-q4_k_m.gguf         # note it for the release notes
```

Download `outputs/scorer-lora-v1-q4_k_m.gguf` (about 1 GB) and
`outputs/finetune-train-log.json`. Keep `outputs/adapter/` too if you may retrain or re-export.

## 2. Release asset (on the laptop)

Publishing is public and the base model's licence is Apache-2.0: say so in the notes.

```bash
gh release create scorer-lora-v1 ~/Downloads/scorer-lora-v1-q4_k_m.gguf \
  --title "scorer-lora v1 (GGUF Q4_K_M)" \
  --notes "Qwen2.5-1.5B-Instruct (Apache-2.0) with our QLoRA adapter. sha256: <sum>.
llama.cpp commit: <commit>. Settings: jobs/finetune/train_config.yaml at <git sha>."
```

## 3. Image, cluster and switch

Check `df -h ~` first (sizes in [`model-image/README.md`](model-image/README.md)).

```bash
ctx=$(mktemp -d -p "$HOME/.cache")
cp ~/Downloads/scorer-lora-v1-q4_k_m.gguf "$ctx/model.gguf"
sha256sum "$ctx/model.gguf"                          # must equal the release notes
IMAGE=ghcr.io/zain-shykh/idea-validator-scorer-model:v1
docker build -f jobs/finetune/model-image/Dockerfile -t "$IMAGE" "$ctx"
docker save "$IMAGE" | docker exec -i idea-validator ctr -n k8s.io images import -
docker rmi "$IMAGE" && docker builder prune -f
rm "$ctx/model.gguf" && rmdir "$ctx"

kubectl apply -k jobs/finetune/k8s-lora/dev          # not k8s/overlays/dev, see below
kubectl -n idea-validator wait --for=condition=complete job/model-import-lora-v1 --timeout=15m
kubectl -n idea-validator rollout status deployment/scorer --timeout=15m
kubectl -n idea-validator rollout status deployment/orchestrator
```

`kubectl apply -k k8s/overlays/dev` switches back to `scorer-base@v1`. During the rollout two
scorer pods run for a short time (about 3 GiB of memory requests for the two model servers).
To make the switch permanent, add the component to the dev overlay once the image exists.

## 4. Check that it serves, and that the fallback still works

```bash
kubectl -n idea-validator port-forward deployment/scorer 8010:8000 &
forward=$!
python scripts/scorer_check.py --url http://127.0.0.1:8010   # model=scorer-lora@v1
```

Fallback: stop the model server inside the pod (the kubelet restarts it) and run the check again
at once. The rows scored meanwhile must show `model=scorer-hosted` with a configured hosted
model (M5 or `GEMINI_API_KEY`), or abstain with `model=none` without one. The scorer log says
`falling back to the hosted model`, and `ideaval_scorer_fallbacks_total` on `/metrics` rises.

```bash
kubectl -n idea-validator exec deployment/scorer -c llama -- sh -c 'kill 1'
python scripts/scorer_check.py --url http://127.0.0.1:8010
kill "$forward"
```
