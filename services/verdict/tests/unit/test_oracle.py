"""Differential test against a second implementation of architecture §13 in exact arithmetic
(``Fraction``), so float noise in the engine shows up as a disagreement. Rules are hard-coded from
the requirements (VE-4..VE-14) on purpose; nothing here is read from the engine or the YAML.
"""

from fractions import Fraction as F
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st
from verdict_factories import CONFIG_PATH, body, duplicate

from common.contracts.core import Dimension, VerdictRequest
from verdict.config import load_config
from verdict.engine import decide

CONFIG = load_config(CONFIG_PATH)
DIMS = [d.value for d in Dimension]
WEIGHTS = {
    "startup": [25, 20, 20, 15, 15, 5],
    "portfolio": [15, 15, 15, 25, 10, 20],
    "course_project": [10, 10, 15, 35, 10, 20],
}
HALF = F(1, 2)


def exact(x: float) -> F:
    return F(str(x))


def total(values: list[F], weights: list[F]) -> F:
    return sum((v * w for v, w in zip(values, weights, strict=True)), F(0)) / 5


def label_of(score: F, values: list[F]) -> str:
    if score >= 70 and all(v >= F(3, 2) for v in values[:5]):
        return "GO"
    return "PIVOT" if score >= 50 else "NO-GO"


def lowest(key: list[F], weights: list[F]) -> int:
    return min(range(6), key=lambda i: (key[i], -weights[i], i))


def runs(values: list[F], confs: list[F], weights: list[F]) -> list[tuple[str, F]]:
    out = []
    for i in range(6):
        for delta in (F(1, 5), F(-1, 5)):
            change = weights[i] * delta
            rest = 100 - weights[i]
            moved = [w + change if j == i else w - change * w / rest for j, w in enumerate(weights)]
            assert sum(moved) == 100
            score = total(values, moved)
            out.append((label_of(score, values), score))
    weak = lowest(confs, weights)
    for delta in (1, -1):
        shifted = list(values)
        shifted[weak] = min(F(5), max(F(0), shifted[weak] + delta))
        score = total(shifted, weights)
        out.append((label_of(score, shifted), score))
    return out


def oracle(data: dict[str, Any], raw: list[F | None], confs: list[F]) -> dict[str, Any]:
    inputs = data["gate_inputs"]
    weights = [F(w) for w in WEIGHTS[data["goal"]]]
    values = [F(0) if s is None else s for s in raw]
    conf = F(7, 10) * sum(confs) / 6 + F(3, 10) * min(F(1), F(inputs["n_sources"], 5))
    result: dict[str, Any] = {"confidence": conf, "score": None, "flips": 0, "runs": 0}
    dup = inputs["duplicate"] is not None and (raw[2] is None or raw[2] < 3)
    effort = inputs["est_effort_hours"]
    if inputs["n_sources"] < 3 or inputs["n_platforms"] < 2:
        return {**result, "label": "INSUFFICIENT_EVIDENCE", "dup_failed": False}
    if effort is not None and exact(effort) > F(4, 5) * exact(inputs["available_hours"]):
        return {**result, "label": "NO-GO", "dup_failed": False}
    if dup:
        return {**result, "label": "NO-GO", "dup_failed": True}
    result["dup_failed"] = False
    if effort is None or conf < HALF:
        return {**result, "label": "INSUFFICIENT_EVIDENCE"}
    score = total(values, weights)
    label = label_of(score, values)
    all_runs = runs(values, confs, weights)
    return {
        **result,
        "label": label,
        "score": score,
        "runs": len(all_runs),
        "flips": sum(other != label for other, _ in all_runs),
        "weakest": DIMS[lowest(values, weights)],
    }


def oracle_candidates(
    data: dict[str, Any], raw: list[F | None], confs: list[F]
) -> list[tuple[str, F, F, bool]]:
    now = oracle(data, raw, confs)
    if now["label"] == "GO" and not now["flips"]:
        return []
    weights = WEIGHTS[data["goal"]]
    found = []
    for i, start in enumerate(raw):
        if start is None:
            continue
        targets = {min(F(5), start + k * HALF) for k in range(1, 5)}
        if i == 2 and now["dup_failed"]:
            targets.add(F(3))
        for target in targets:
            if target <= start:
                continue
            then = oracle(data, [*raw[:i], target, *raw[i + 1 :]], confs)
            if then["label"] == "GO" and then["score"] is not None:
                stable = not then["flips"]
                key = (not stable, target - start, -weights[i], i)
                found.append((key, (DIMS[i], target, then["score"], stable)))
    return [c for _, c in sorted(found)]


def mostly(common: st.SearchStrategy[Any], rare: st.SearchStrategy[Any]) -> Any:
    """About one draw in ten is ``rare``, so most examples get past the gates to the scoring."""
    return st.integers(0, 9).flatmap(lambda n: rare if n == 9 else common)


# a 0.1 grid hits thresholds, floor and override exactly far more often than random floats
score_st = mostly(
    st.integers(28, 48).map(lambda n: n / 10),
    st.one_of(st.integers(0, 50).map(lambda n: n / 10), st.none()),
)
conf_st = mostly(
    st.integers(12, 20).map(lambda n: n / 20), st.integers(0, 11).map(lambda n: n / 20)
)


@settings(deadline=None, max_examples=300)
@given(
    st.lists(score_st, min_size=6, max_size=6),
    st.lists(conf_st, min_size=6, max_size=6),
    st.sampled_from(list(WEIGHTS)),
    st.lists(st.integers(0, 5), max_size=5),
    mostly(st.integers(3, 7), st.just(2)),
    mostly(st.sampled_from([100, 192]), st.sampled_from([None, 193])),
    mostly(st.just(False), st.just(True)) | st.booleans(),
)
def test_engine_agrees_with_exact_arithmetic_oracle(
    scores: list[float | None],
    confidences: list[float],
    goal: str,
    objected: list[int],
    n_sources: int,
    effort: int | None,
    has_duplicate: bool,
) -> None:
    data = body(
        scores=tuple(scores),
        confidences=tuple(confidences),
        goal=goal,
        n_sources=n_sources,
        est_effort_hours=effort,
        duplicate=duplicate() if has_duplicate else None,
        objections=[
            {
                "objection_id": f"o{n}",
                "dimension": DIMS[i],
                "text": "t",
                "evidence_ids": ["card_ab#c0"],
            }
            for n, i in enumerate(objected)
        ],
    )
    raw: list[F | None] = [None if s is None else exact(s) for s in scores]
    confs = [F(0) if s is None else exact(c) for s, c in zip(scores, confidences, strict=True)]
    for i in range(6):
        n = objected.count(i)
        start = raw[i]
        if n and start is not None:
            raw[i] = max(F(0), start - min(F(1), n * HALF))
            confs[i] = max(F(0), confs[i] - F(n, 10))

    expected = oracle(data, raw, confs)
    got = decide(VerdictRequest.model_validate(data), CONFIG)

    assert got.verdict.value == expected["label"]
    assert abs(got.confidence - float(expected["confidence"])) <= 0.005 + 1e-9
    assert (got.sensitivity.runs, got.sensitivity.flipped) == (expected["runs"], expected["flips"])
    assert (got.stability.value == "BORDERLINE") == bool(expected["flips"])
    if expected["score"] is None:
        assert got.score is None
    else:
        assert got.score is not None and got.weakest_dimension is not None
        assert abs(got.score - float(expected["score"])) <= 0.005 + 1e-9
        assert got.weakest_dimension.value == expected["weakest"]
    wanted = oracle_candidates(data, raw, confs)
    actual = [(c.dimension, c.to_score, c.new_score, c.stable) for c in got.what_would_make_go]
    assert [(d, t, s) for d, t, _, s in actual] == [(d, float(t), s) for d, t, _, s in wanted]
    for (_, _, new_score, _), (_, _, exact_score, _) in zip(actual, wanted, strict=True):
        assert abs(new_score - float(exact_score)) <= 0.005 + 1e-9
