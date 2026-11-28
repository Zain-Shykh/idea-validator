import pytest
from verdict_factories import duplicate, make_request, objection

from common.contracts.core import Dimension, Stability, VerdictLabel, WhatWouldMakeGo
from common.contracts.verdict import ScoreChange
from verdict.config import VerdictConfig
from verdict.engine import apply_changes, decide


def find(candidates: list[WhatWouldMakeGo], dimension: Dimension, to: float) -> WhatWouldMakeGo:
    (match,) = [c for c in candidates if (c.dimension, c.to_score) == (dimension, to)]
    return match


def test_worked_example_competition_to_3_is_a_stable_go(config: VerdictConfig) -> None:
    candidate = find(decide(make_request(), config).what_would_make_go, Dimension.COMPETITION, 3.0)
    assert (candidate.from_score, candidate.new_score) == (2.0, 73.5)
    assert (candidate.new_verdict, candidate.stable) == (VerdictLabel.GO, True)
    assert candidate.change == "raise competition from 2 to 3"


def test_worked_example_applying_it_gives_a_stable_go(config: VerdictConfig) -> None:
    change = ScoreChange(dimension=Dimension.COMPETITION, to=3.0)
    verdict = apply_changes(make_request(), [change], config)
    assert (verdict.verdict, verdict.stability, verdict.score) == (
        VerdictLabel.GO,
        Stability.STABLE,
        73.5,
    )
    assert verdict.flips == [] and verdict.what_would_make_go == []


def test_worked_example_73_07_under_the_flip_run(config: VerdictConfig) -> None:
    from verdict.engine import _weight_map, redistribute, weighted_score

    values = dict(zip(Dimension, (3.5, 3.0, 3.0, 4.0, 3.5, 4.5), strict=True))
    weights = redistribute(
        _weight_map(config.weights[make_request().goal]), Dimension.FEASIBILITY, -0.2
    )
    assert round(weighted_score(values, weights), 2) == 73.07


def test_candidates_are_sorted_stable_first_then_smallest_then_heaviest(
    config: VerdictConfig,
) -> None:
    request = make_request()
    candidates = decide(request, config).what_would_make_go
    weights = config.weights[request.goal]
    keys = []
    for c in candidates:
        assert c.dimension and c.to_score is not None and c.from_score is not None
        assert c.new_verdict is VerdictLabel.GO and c.to_score <= 5
        assert 0 < c.to_score - c.from_score <= 2
        weight = getattr(weights, c.dimension.value)
        keys.append((not c.stable, c.to_score - c.from_score, -weight))
    assert keys == sorted(keys) and candidates[0].stable
    assert (candidates[0].dimension, candidates[0].to_score) == (Dimension.FEASIBILITY, 4.5)


def test_every_candidate_reproduces_when_applied(config: VerdictConfig) -> None:
    request = make_request(objections=[objection(1)])
    candidates = decide(request, config).what_would_make_go
    assert candidates
    for c in candidates:
        assert c.dimension and c.to_score is not None
        change = ScoreChange(dimension=c.dimension, to=c.to_score)
        verdict = apply_changes(request, [change], config)
        assert (verdict.verdict, verdict.score) == (c.new_verdict, c.new_score)
        assert (verdict.stability is Stability.STABLE) == c.stable


def test_from_is_the_score_after_the_challenge_penalty(config: VerdictConfig) -> None:
    request = make_request(objections=[objection(1)])
    competition = [
        c
        for c in decide(request, config).what_would_make_go
        if c.dimension is Dimension.COMPETITION
    ]
    assert competition and {c.from_score for c in competition} == {1.5}


def test_no_candidate_gives_an_empty_list(config: VerdictConfig) -> None:
    verdict = decide(make_request(scores=(1,) * 6), config)
    assert verdict.verdict is VerdictLabel.NO_GO and verdict.what_would_make_go == []


def test_increases_are_capped_at_5_without_duplicates(config: VerdictConfig) -> None:
    candidates = decide(make_request(scores=(4.8, 1.4, 5, 5, 5, 5)), config).what_would_make_go
    assert [c.to_score for c in candidates if c.dimension is Dimension.DEMAND_PAIN] == []
    assert {c.dimension for c in candidates} == {Dimension.COMPETITION}
    assert [c.to_score for c in candidates] == [1.9, 2.4, 2.9, 3.4]


@pytest.mark.parametrize("start", [2.5, 0.5])
def test_duplicate_gate_is_removed_through_differentiation(
    config: VerdictConfig, start: float
) -> None:
    # from 0.5 the override is 2.5 away, more than the 2.0 of the normal steps
    request = make_request(scores=(5, 5, start, 5, 5, 5), duplicate=duplicate())
    candidates = decide(request, config).what_would_make_go
    assert {c.dimension for c in candidates} == {Dimension.DIFFERENTIATION}
    assert min(c.to_score or 0 for c in candidates) == 3.0


def test_abstained_dimension_is_never_a_candidate(config: VerdictConfig) -> None:
    request = make_request(scores=(3.5, 3.5, 3.5, 3.5, 3.5, None), confidences=(0.9,) * 6)
    candidates = decide(request, config).what_would_make_go
    assert Dimension.LEARNING not in {c.dimension for c in candidates}


def test_apply_can_lower_a_score(config: VerdictConfig) -> None:
    change = ScoreChange(dimension=Dimension.FEASIBILITY, to=1.0)
    assert apply_changes(make_request(), [change], config).verdict is VerdictLabel.PIVOT


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ([(Dimension.LEARNING, 4.0)], "no evidence-backed score"),
        ([(Dimension.RISK, 4.0), (Dimension.RISK, 5.0)], "only once"),
    ],
)
def test_apply_rejects(
    config: VerdictConfig, changes: list[tuple[Dimension, float]], message: str
) -> None:
    request = make_request(scores=(3, 3, 3, 3, 3, None))
    with pytest.raises(ValueError, match=message):
        apply_changes(request, [ScoreChange(dimension=d, to=v) for d, v in changes], config)
