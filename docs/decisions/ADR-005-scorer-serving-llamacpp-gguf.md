# ADR-005: QLoRA Qwen2.5-1.5B, merged to GGUF, served by llama.cpp; model shipped as an image

- **Status:** Accepted (revisit after the base / fine-tuned / hosted comparison)
- **Date:** 2026-11-27
- **Owner:** Zain (M10, M10t)
- **Context:** M10 must be a fine-tuned 1B–3B model (LoRA/QLoRA) in its own container, with hosted fallback, weights not in git, on a laptop without an NVIDIA GPU. Training happens on Colab/Kaggle.
- **Options:** (1) serve with vLLM / HF transformers (needs GPU or large RAM); (2) Ollama container (large image); (3) GGUF Q4_K_M served by the small `llama.cpp` server image as a sidecar to a FastAPI wrapper that owns `/score`, schema validation, retry and fallback.
- **Decision:** Option 3. Base model Qwen2.5-1.5B-Instruct (Apache-2.0, good JSON following); alternatives Llama-3.2-1B/3B-Instruct, Gemma-3-1B. The GGUF is packaged into an OCI image (`scorer-model:<version>`) and copied into a PVC by the `model-import` Job; also attached to a GitHub Release.
- **Why:** Runs on CPU in ~1.5 GB RAM; OpenAI-compatible API so the wrapper code is identical for llama.cpp, Ollama (dev) and hosted (comparison); kubelet pulls the model image, so no pod needs internet egress (keeps M5-7 true).
- **Cost:** Q4 quantisation may lose some accuracy (measured in the comparison table); CPU latency per score ~1–4 s; one extra image to build.
- **Measured (Z4, 2026-11-27, i3-1215U, 2 threads):** p50 35 s per score in the pod, not 1–4 s; llama.cpp container 1.1 GiB working set (≈ 2 GiB with file cache). The decision stands; D-009 records the effect on call deadlines.
- **AI use:** Drafted by AI; model choice to be confirmed by our evaluation.
