# ADR-003: Gemini (free tier) via LiteLLM, routed only through M5

- **Status:** Accepted
- **Date:** 2026-11-27
- **Owner:** Ahmad (M5 proxy), Zain (agents, `common.llm` client)
- **Context:** Agents (Ideator, Skeptic, Critic), label drafting for fine-tuning, and the M10 fallback need a hosted LLM. NetworkPolicy must allow **only M5** to reach the internet (M5-7). Budget: student, free tier preferred.
- **Options:** (1) each agent calls the provider directly (needs egress for many pods); (2) a separate LLM-gateway service; (3) M5 Tool Layer exposes `/llm/chat` using LiteLLM with model aliases, budgets, RPM limiter and response cache.
- **Decision:** Option 3 with Google Gemini (Flash / Flash-Lite, free tier) as the hosted provider; host Ollama for local development behind the same aliases. Agents use `common.llm` (`LLM_BACKEND=gateway` in k8s, `direct` before M5 exists).
- **Why:** Keeps the single-exit rule true for LLM traffic too; API keys live only in the `tools` pod; one place to enforce token/cost budgets (AO-2) and cache temperature-0 calls; provider swap = config change.
- **Cost:** M5 becomes a hot path and single point of failure; free-tier RPM/RPD limits throttle evaluation runs (mitigated by caching, small local scorer, batching). Exact model ids change over time → kept in `config/llm-models.yaml`.
- **AI use:** Drafted by AI; provider choice made by us.
