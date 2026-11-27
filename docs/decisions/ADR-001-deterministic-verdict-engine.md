# ADR-001: Deterministic rules engine decides the verdict

- **Status:** Accepted
- **Date:** 2026-11-27
- **Owner:** Zain (M11)
- **Context:** The project must output GO / PIVOT / NO-GO / INSUFFICIENT EVIDENCE that is explainable, repeatable and testable. LLM judges drift unless rubrics are locked and evidence is required; weighted totals near a threshold flip with small changes.
- **Options:** (1) LLM outputs the verdict directly; (2) LLM scores, LLM aggregates; (3) LLM produces evidence-backed dimension scores, a plain-code engine applies gates, weights, thresholds, confidence, challenge penalties and sensitivity.
- **Decision:** Option 3. M11 is a pure Python function with rules in a ConfigMap (`config/verdict.yaml`), exposed as `/verdict` and `/whatif`.
- **Why:** Required by the Description (§5, VE-1). Same input → same output, so every gate and threshold edge is unit-tested (worked example 70.5 → GO, 69.9 under −20 % feasibility weight → BORDERLINE, 73.5 after competition +1). Thresholds can be tuned offline by replaying stored scores without calling any model.
- **Cost:** Rules must be defended and tuned by us (labelled set, confusion matrices). Nuance the rules do not encode is lost.
- **AI use:** Drafted by AI from the project PDFs; rules checked against Description §5 by us.
