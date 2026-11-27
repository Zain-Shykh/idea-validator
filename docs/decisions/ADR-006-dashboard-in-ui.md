# ADR-006: AgenticOps dashboard as a UI page reading M13's summary

- **Status:** Accepted
- **Date:** 2026-11-27
- **Owner:** Zain (M13 `/summary`), Ahmad (UI page)
- **Context:** AO-6 needs a dashboard with cost per verdict, latency per agent, cache hit rate, fallbacks, approvals, blocked injections. Guide allows Prometheus + Grafana **or** a page reading `/metrics`. Laptop RAM/disk are tight.
- **Options:** (1) Prometheus + Grafana in the cluster; (2) a page in our UI reading an aggregated JSON summary from M13 (which reads `traces_index` and service metrics).
- **Decision:** Option 2. Every service still exposes Prometheus `/metrics`, so Prometheus/Grafana can be added later without code changes.
- **Why:** Two fewer stateful workloads (~500 MB RAM, ~1 GB images); the dashboard shows run-level numbers (cost per verdict) that come from traces, not raw counters.
- **Cost:** No alerting, limited history and ad-hoc querying compared with Grafana.
- **AI use:** Drafted by AI; decision ours.
