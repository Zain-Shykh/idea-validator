"""M11-2 and monotonicity, over generated requests."""

from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st
from verdict_factories import CONFIG_PATH, body, duplicate

from common.contracts.core import Dimension, Goal, VerdictLabel, VerdictRequest
from verdict.config import load_config
from verdict.engine import decide

CONFIG = load_config(CONFIG_PATH)
RANK = {VerdictLabel.NO_GO: 0, VerdictLabel.PIVOT: 1, VerdictLabel.GO: 2}

score = st.one_of(st.none(), st.floats(0, 5), st.integers(0, 10).map(lambda n: n / 2))


@st.composite
def requests(draw: st.DrawFn) -> dict[str, Any]:
    objections = [
        {
            "objection_id": f"o{n}",
            "dimension": draw(st.sampled_from(list(Dimension))).value,
            "text": "t",
            "evidence_ids": ["card_ab#c0"],
        }
        for n in range(draw(st.integers(0, 3)))
    ]
    return body(
        scores=tuple(draw(score) for _ in Dimension),
        confidences=tuple(draw(st.floats(0, 1)) for _ in Dimension),
        goal=draw(st.sampled_from(list(Goal))).value,
        objections=objections,
        n_sources=draw(st.integers(2, 8)),
        n_platforms=draw(st.integers(1, 3)),
        est_effort_hours=draw(st.sampled_from([None, 100.0, 500.0])),
        duplicate=draw(st.sampled_from([None, duplicate()])),
    )


@settings(deadline=None)
@given(requests())
def test_same_input_gives_identical_json_whatever_the_list_order(data: dict[str, Any]) -> None:
    first = decide(VerdictRequest.model_validate(data), CONFIG).model_dump_json()
    shuffled = {
        **data,
        "scores": data["scores"][::-1],
        "accepted_objections": data["accepted_objections"][::-1],
    }
    assert decide(VerdictRequest.model_validate(data), CONFIG).model_dump_json() == first
    assert decide(VerdictRequest.model_validate(shuffled), CONFIG).model_dump_json() == first


@settings(deadline=None)
@given(requests(), st.sampled_from(range(6)), st.floats(0, 5))
def test_raising_a_score_never_makes_the_verdict_worse(
    data: dict[str, Any], index: int, other: float
) -> None:
    if data["scores"][index]["score"] is None:
        return
    low, high = sorted([data["scores"][index]["score"], other])

    def at(value: float) -> Any:
        scores = [dict(s) for s in data["scores"]]
        scores[index]["score"] = value
        return decide(VerdictRequest.model_validate({**data, "scores": scores}), CONFIG)

    before, after = at(low), at(high)
    if before.gates.duplicate == "fail":
        pass  # passing that gate can reveal any later outcome, IE included
    elif before.verdict in RANK:
        assert after.verdict in RANK and RANK[after.verdict] >= RANK[before.verdict]
    else:  # confidence and the other gates do not depend on scores
        assert after.verdict is before.verdict
    if before.score is not None:
        assert after.score is not None and after.score >= before.score
