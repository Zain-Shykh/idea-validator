"""M11 Verdict Engine (architecture §13). The only place a verdict is decided (VE-1).

Pure functions: no I/O, no clock, no randomness, no model. Dimensions are always iterated in
``Dimension`` declaration order. Comparisons ignore float noise (``_exact``); values are rounded
to two decimals only in what is returned.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from common.contracts.core import (
    Dimension,
    DimensionScore,
    GateInputs,
    GateResult,
    Gates,
    Objection,
    Sensitivity,
    Stability,
    Verdict,
    VerdictLabel,
    VerdictRequest,
    Versions,
    Weights,
    WhatWouldMakeGo,
)
from common.contracts.verdict import ScoreChange
from verdict.config import VerdictConfig

Values = Mapping[Dimension, float]


def _exact(value: float) -> float:
    """So that an exact 70 computed as 69.99999999999999 still reaches a threshold of 70."""
    return round(value, 9)


def _label_of(dimension: Dimension) -> str:
    return dimension.value.replace("_", " ")


def _values(scores: Mapping[Dimension, DimensionScore]) -> dict[Dimension, float]:
    """An abstained dimension counts as 0 (§13.1)."""
    return {d: 0.0 if scores[d].score is None else float(scores[d].score or 0) for d in Dimension}


def _weight_map(weights: Weights) -> dict[Dimension, float]:
    return {d: float(getattr(weights, d.value)) for d in Dimension}


def apply_challenge(
    scores: Sequence[DimensionScore], objections: Sequence[Objection], config: VerdictConfig
) -> dict[Dimension, DimensionScore]:
    """VE-12. Returns the effective scores, keyed and ordered by dimension.

    Only evidence-backed objections count, each id once per dimension. An abstained dimension has
    nothing to lower.
    """
    rules = config.challenge
    backed = {(o.dimension, o.objection_id) for o in objections if o.evidence_ids}
    by_dimension = {s.dimension: s for s in scores}
    effective = {}
    for dimension in Dimension:
        item = by_dimension[dimension]
        ids = sorted(i for d, i in backed if d == dimension)
        if ids and item.score is not None:
            penalty = min(len(ids) * rules.score_penalty, rules.max_penalty)
            before = {"score": item.score, "confidence": item.confidence}
            item = item.model_copy(
                update={
                    "score": max(0.0, item.score - penalty),
                    "confidence": max(
                        0.0, _exact(item.confidence - len(ids) * rules.confidence_penalty)
                    ),
                    "features": {
                        **item.features,
                        "challenge": {"objection_ids": ids, "before": before},
                    },
                }
            )
        effective[dimension] = item
    return effective


@dataclass(frozen=True)
class GateOutcome:
    gates: Gates
    failed: VerdictLabel | None  # the verdict a failed gate forces
    missing: list[str]  # what could not be checked or found
    details: dict[str, str]


def check_gates(
    inputs: GateInputs, differentiation: float | None, config: VerdictConfig
) -> GateOutcome:
    """VE-4, VE-15: evidence, feasibility, risk, duplicate. The first failure decides."""
    results = dict.fromkeys(
        ("evidence", "feasibility", "risk", "duplicate"), GateResult.NOT_CHECKED
    )
    missing: list[str] = []
    details: dict[str, str] = {}

    def outcome(failed: VerdictLabel | None = None) -> GateOutcome:
        return GateOutcome(Gates(**results), failed, missing, details)

    def fail(gate: str, verdict: VerdictLabel, detail: str) -> GateOutcome:
        results[gate] = GateResult.FAIL
        details[gate] = detail
        return outcome(verdict)

    rules = config.gates.evidence
    if inputs.n_sources < rules.min_sources:
        missing.append(
            f"independent sources: found {inputs.n_sources}, need at least {rules.min_sources}"
        )
    if inputs.n_platforms < rules.min_platforms:
        missing.append(
            f"platforms: found {inputs.n_platforms}, need at least {rules.min_platforms}"
        )
    if missing:
        return fail("evidence", VerdictLabel.INSUFFICIENT_EVIDENCE, "; ".join(missing))
    results["evidence"] = GateResult.PASS

    margin = config.gates.feasibility.effort_margin
    if inputs.est_effort_hours is None:
        # cannot pass and cannot fail: a later gate may still say NO-GO, otherwise abstain
        missing.append("an estimate of the build effort, so feasibility could not be checked")
    else:
        limit = _exact(margin * inputs.available_hours)
        if _exact(inputs.est_effort_hours) > limit:
            gap = inputs.est_effort_hours - limit
            return fail(
                "feasibility",
                VerdictLabel.NO_GO,
                f"estimated effort {inputs.est_effort_hours:g} h exceeds {limit:g} h "
                f"({margin:g} x {inputs.available_hours:g} available hours): "
                f"gap {_exact(gap):g} h",
            )
        results["feasibility"] = GateResult.PASS

    if inputs.blockers:
        named = "; ".join(sorted(f"{b.type} blocker: {b.description}" for b in inputs.blockers))
        return fail("risk", VerdictLabel.NO_GO, named)
    results["risk"] = GateResult.PASS

    duplicate = inputs.duplicate
    override = config.gates.duplicate.differentiation_override
    if (
        duplicate is not None
        and duplicate.same_core_features
        and duplicate.same_user
        and (differentiation is None or _exact(differentiation) < override)
    ):
        have = "not scored" if differentiation is None else f"{round(differentiation, 2):g}"
        return fail(
            "duplicate",
            VerdictLabel.NO_GO,
            f"{duplicate.product_name} ({duplicate.card_id}) has the same core features for the "
            f"same user; differentiation is {have}, below {override:g}",
        )
    results["duplicate"] = GateResult.PASS
    return outcome()


def weighted_score(values: Values, weights: Values) -> float:
    """VE-5: sum(weight x score) / 5, on 0-100."""
    return _exact(sum(weights[d] * values[d] for d in Dimension) / 5)


def confidence(
    scores: Mapping[Dimension, DimensionScore], n_sources: int, config: VerdictConfig
) -> float:
    """VE-11."""
    rules = config.confidence
    mean = sum(scores[d].confidence for d in Dimension) / len(Dimension)
    return _exact(rules.w_mean * mean + rules.w_sources * min(1.0, n_sources / rules.sources_norm))


def floor_breaches(values: Values, config: VerdictConfig) -> list[Dimension]:
    return [
        d
        for d in Dimension
        if d in config.floor_dimensions and _exact(values[d]) < config.thresholds.floor
    ]


def classify(score: float, values: Values, config: VerdictConfig) -> VerdictLabel:
    """VE-7..9 for an idea that passed the gates and the confidence check."""
    if score >= config.thresholds.T_go and not floor_breaches(values, config):
        return VerdictLabel.GO
    if score >= config.thresholds.T_no:
        return VerdictLabel.PIVOT
    return VerdictLabel.NO_GO


def _by_priority(weights: Values, key: Values) -> Dimension:
    """Lowest ``key``; ties go to the higher weight, then to declaration order (min is stable)."""
    return min(Dimension, key=lambda d: (_exact(key[d]), -weights[d]))


def redistribute(weights: Values, dimension: Dimension, delta: float) -> dict[Dimension, float]:
    """VE-13: scale one weight by ``1 + delta`` and take the difference from (or give it to) the
    other five in proportion to their weights, so the total stays the same."""
    change = weights[dimension] * delta
    rest = sum(weights[d] for d in Dimension if d != dimension)
    return {
        d: weights[d] + change if d == dimension else weights[d] - change * weights[d] / rest
        for d in Dimension
    }


def sensitivity(
    values: Values,
    confidences: Values,
    weights: Values,
    label: VerdictLabel,
    config: VerdictConfig,
) -> tuple[int, list[str]]:
    """VE-13. Returns the number of runs (12 weight + 2 score) and a line per run whose verdict
    differs from ``label``. Gates are not re-evaluated."""
    rules = config.sensitivity
    runs: list[tuple[str, Values, Values]] = [
        (
            f"{_label_of(d)} weight {delta:+.0%}",
            values,
            redistribute(weights, d, delta),
        )
        for d in Dimension
        for delta in (rules.weight_delta, -rules.weight_delta)
    ]
    weak = _by_priority(weights, confidences)
    for delta in (rules.score_delta, -rules.score_delta):
        moved = {**values, weak: min(5.0, max(0.0, values[weak] + delta))}
        runs.append((f"{_label_of(weak)} score {delta:+g}", moved, weights))
    flips = []
    for name, run_values, run_weights in runs:
        score = weighted_score(run_values, run_weights)
        flipped = classify(score, run_values, config)
        if flipped != label:
            flips.append(f"{name} -> {flipped} ({score:.1f})")
    return len(runs), flips


def _evaluate(
    request: VerdictRequest, scores: Mapping[Dimension, DimensionScore], config: VerdictConfig
) -> Verdict:
    """The decision flow of §13.1 on effective scores, without the what-would-make-GO search."""
    values = _values(scores)
    confidences = {d: scores[d].confidence for d in Dimension}
    weights = config.weights[request.goal]
    weight_map = _weight_map(weights)
    conf = confidence(scores, request.gate_inputs.n_sources, config)
    gate = check_gates(request.gate_inputs, scores[Dimension.DIFFERENTIATION].score, config)
    missing = list(gate.missing)
    abstained = [d for d in Dimension if scores[d].score is None]
    missing += [f"an evidence-backed score for {_label_of(d)}" for d in abstained]
    details = dict(gate.details)

    label = gate.failed
    if label is None and gate.gates.feasibility is GateResult.NOT_CHECKED:
        label = VerdictLabel.INSUFFICIENT_EVIDENCE
    if label is None and conf < config.confidence.min:
        label = VerdictLabel.INSUFFICIENT_EVIDENCE
        details["confidence"] = (
            f"confidence too low: {conf:.12g} is below {config.confidence.min:g}"
        )
        missing.append(details["confidence"])

    score: float | None = None
    weakest: Dimension | None = None
    n_runs = 0
    flips: list[str] = []
    if label is None:
        score = weighted_score(values, weight_map)
        label = classify(score, values, config)
        weakest = _by_priority(weight_map, values)
        thresholds = config.thresholds
        breaches = floor_breaches(values, config)
        if breaches:
            details["floor"] = (
                ", ".join(f"{_label_of(d)} {round(values[d], 2):g}" for d in breaches)
                + f" below the floor {thresholds.floor:g}"
            )
        if score < thresholds.T_no:
            details["score"] = f"score too low: {score:.12g} is below {thresholds.T_no:g}"
        n_runs, flips = sensitivity(values, confidences, weight_map, label, config)

    return Verdict(
        idea_id=request.idea_id,
        version=request.version,
        run_id=request.run_id,
        verdict=label,
        stability=Stability.BORDERLINE if flips else Stability.STABLE,
        score=None if score is None else round(score, 2),
        confidence=round(conf, 2),
        gates=gate.gates,
        weights=weights,
        thresholds=config.thresholds,
        scores=[
            s.model_copy(
                update={
                    "score": None if s.score is None else round(s.score, 2),
                    "confidence": round(s.confidence, 2),
                }
            )
            for s in scores.values()
        ],
        weakest_dimension=weakest,
        what_would_make_go=[],
        sensitivity=Sensitivity(runs=n_runs, flipped=len(flips)),
        versions=Versions(
            engine=f"m11@{config.version}",
            rubrics=request.rubric_set,
            profile_weights=config.weights_version,
            prompts=dict(sorted(request.prompt_versions.items())),
            kb_version=request.kb_version,
        ),
        missing=missing,
        gate_details=details,
        flips=flips,
    )


def what_would_make_go(
    request: VerdictRequest,
    scores: Mapping[Dimension, DimensionScore],
    current: Verdict,
    config: VerdictConfig,
) -> list[WhatWouldMakeGo]:
    """VE-14: single-dimension increases that reach GO, judged by the same engine.

    ``scores`` are the effective ones. Nothing to search for a stable GO. A score cannot repair
    missing evidence, confidence, effort or a blocker, so those yield no candidate; the duplicate
    gate can be passed through differentiation.
    """
    if current.verdict is VerdictLabel.GO and current.stability is Stability.STABLE:
        return []
    rules = config.whatif
    weights = _weight_map(config.weights[request.goal])
    steps = int(_exact(rules.max_delta / rules.step))
    found: list[tuple[tuple[bool, float, float], WhatWouldMakeGo]] = []
    for dimension in Dimension:
        item = scores[dimension]
        if item.score is None:  # raising it would be a score without evidence
            continue
        start = item.score
        targets = {min(5.0, start + k * rules.step) for k in range(1, steps + 1)}
        if dimension is Dimension.DIFFERENTIATION and current.gates.duplicate is GateResult.FAIL:
            targets.add(config.gates.duplicate.differentiation_override)
        # evaluate the rounded value that is shown, so applying it gives the same result
        for target in sorted({round(t, 2) for t in targets}):
            if target <= round(start, 2):  # would read "from 5 to 5"
                continue
            changed = {**scores, dimension: item.model_copy(update={"score": target})}
            result = _evaluate(request, changed, config)
            if result.verdict is not VerdictLabel.GO or result.score is None:
                continue
            stable = result.stability is Stability.STABLE
            shown = round(start, 2)
            candidate = WhatWouldMakeGo(
                change=f"raise {_label_of(dimension)} from {shown:g} to {target:g}",
                new_score=result.score,
                new_verdict=result.verdict,
                dimension=dimension,
                from_score=shown,
                to_score=target,
                stable=stable,
            )
            found.append(((not stable, _exact(target - start), -weights[dimension]), candidate))
    return [candidate for _, candidate in sorted(found, key=lambda pair: pair[0])]


def _complete(
    request: VerdictRequest, scores: Mapping[Dimension, DimensionScore], config: VerdictConfig
) -> Verdict:
    result = _evaluate(request, scores, config)
    candidates = what_would_make_go(request, scores, result, config)
    return result.model_copy(update={"what_would_make_go": candidates})


def decide(request: VerdictRequest, config: VerdictConfig) -> Verdict:
    return _complete(
        request, apply_challenge(request.scores, request.accepted_objections, config), config
    )


def apply_changes(
    request: VerdictRequest, changes: Sequence[ScoreChange], config: VerdictConfig
) -> Verdict:
    """The hypothetical verdict if the effective scores were ``changes``. Challenge penalties are
    already in the effective scores and are not applied again.

    Raises ``ValueError`` for a repeated dimension or an abstained one.
    """
    scores = apply_challenge(request.scores, request.accepted_objections, config)
    if len({c.dimension for c in changes}) != len(changes):
        raise ValueError("each dimension may be changed only once")
    for change in changes:
        if scores[change.dimension].score is None:
            raise ValueError(
                f"{change.dimension} has no evidence-backed score, so it cannot be changed"
            )
        scores[change.dimension] = scores[change.dimension].model_copy(update={"score": change.to})
    return _complete(request, scores, config)
