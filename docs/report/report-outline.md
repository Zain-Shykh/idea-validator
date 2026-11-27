# Report Outline (≤ 10 pages excluding screenshots) — SUB-2

Final PDF: `docs/report/report.pdf`. Source can be Markdown (`docs/report/report.md`) exported to PDF.

| § | Section (required content) | Owner | Inputs |
|---|---------------------------|-------|--------|
| a | Summary and changes from the given architecture | Zain | `architecture/deviations.md` §1 + §2 |
| b | Modules and architecture figure | Both | `architecture/architecture.md` §4–5 |
| c | Verdict design: gates, weights, thresholds, rubrics, tuning results with **before/after confusion matrix**, one worked example | Zain | `config/verdict.yaml`, `rubrics/`, `eval/results/eval-*.json`, Z10 tuning output |
| d | Knowledge base: sources, card counts, retrieval results | Ahmad | `/stats`, `ingestion_runs`, `eval/retrieval` results |
| e | Fine-tuning: data, settings, comparison table, one case where it did not help | Zain | `jobs/finetune/train_config.yaml`, `eval/results/finetune-comparison.json` |
| f | AgenticOps results: traces, budgets, eval harness, behaviour tests, drift, dashboard | Zain | M13 `/summary`, drift reports, behaviour test results |
| g | Platform: pipeline, Kubernetes design, **how requests and limits were measured** | Ahmad (Zain adds own workloads) | Jenkinsfile, `k8s/`, `docs/evidence/resources.md` |
| h | Known limits and next steps | Both | spec follow-ups, deviations |

Appendix (not counted): screenshots SS-1..18 with one-line captions (`docs/evidence/screenshots/captions.md`), AI-usage log, decision records, contribution table.
