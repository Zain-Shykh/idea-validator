"""Spec A7 and beyond: float noise, interactions, API error envelopes, odd but schema-valid
configs, and determinism (M11-2).
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import ValidationError
from verdict_factories import CONFIG_PATH, body, duplicate, make_request, objection

from common.contracts.core import Dimension, GateResult, Verdict, VerdictLabel
from common.contracts.verdict import ScoreChange, WhatIfResponse
from verdict.config import VerdictConfig
from verdict.engine import apply_changes, decide
from verdict.main import build_app

client = TestClient(build_app(CONFIG_PATH), raise_server_exceptions=False)


def rules() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(CONFIG_PATH.read_text())
    return data


def blocker(text: str) -> dict[str, Any]:
    return {"type": "legal", "description": text, "evidence_ids": ["card_ab#c0"]}


# --- float noise --------------------------------------------------------------------------------


def test_float_noise_in_a_penalty_does_not_breach_the_floor() -> None:
    data = rules()
    data["challenge"]["score_penalty"] = 0.1  # 1.8 - 3 x 0.1 = 1.4999999999999998
    request = make_request(
        scores=(1.8, 5, 5, 5, 5, 5), objections=[objection(n, "demand_pain") for n in range(3)]
    )
    verdict = decide(request, VerdictConfig.model_validate(data))
    assert verdict.verdict is VerdictLabel.GO and "floor" not in verdict.gate_details


@pytest.mark.parametrize("hours", [3, 7, 15, 35, 105])
def test_float_noise_in_the_effort_limit_does_not_fail_the_edge(
    config: VerdictConfig, hours: int
) -> None:
    request = make_request(est_effort_hours=hours * 8 / 10, available_hours=hours)
    assert decide(request, config).gates.feasibility is GateResult.PASS


def test_penalty_down_to_the_override_still_passes_the_duplicate_gate(
    config: VerdictConfig,
) -> None:
    request = make_request(
        scores=(5, 5, 4.0, 5, 5, 5),
        duplicate=duplicate(),
        objections=[objection(1, "differentiation"), objection(2, "differentiation")],
    )
    assert decide(request, config).gates.duplicate is GateResult.PASS


# --- A7 -----------------------------------------------------------------------------------------


def test_zero_available_hours_fails_any_effort(config: VerdictConfig) -> None:
    verdict = decide(make_request(est_effort_hours=1, available_hours=0), config)
    assert verdict.verdict is VerdictLabel.NO_GO
    assert "gap 1 h" in verdict.gate_details["feasibility"]


def test_least_confident_tie_with_equal_weights_takes_declaration_order(
    config: VerdictConfig,
) -> None:
    # portfolio: demand, competition and differentiation all weigh 15
    request = make_request(
        scores=(4,) * 6, confidences=(0.5, 0.5, 0.5, 0.9, 0.9, 0.9), goal="portfolio"
    )
    score_runs = [f for f in decide(request, config).flips if " score " in f]
    assert all(f.startswith("demand pain score") for f in score_runs)
    values = (3.6,) * 6  # 72: -1 on a 15 weight gives 69
    request = make_request(scores=values, confidences=(0.5, 0.5, 0.5, 0.9, 0.9, 0.9))
    assert "demand pain score -1 -> PIVOT (69.0)" in decide(request, config).flips


def test_gates_are_not_re_evaluated_in_sensitivity_runs(config: VerdictConfig) -> None:
    # -1 on differentiation would fail the duplicate gate (NO-GO); the run only re-scores
    request = make_request(
        scores=(5, 5, 3, 5, 5, 5),
        confidences=(0.9, 0.9, 0.5, 0.9, 0.9, 0.9),
        duplicate=duplicate(),
    )
    verdict = decide(request, config)
    assert (verdict.verdict, verdict.flips) == (VerdictLabel.GO, [])


def test_pivot_and_no_go_can_be_borderline(config: VerdictConfig) -> None:
    near_go = decide(make_request(scores=(3.49,) * 6), config)  # 69.8; +1 on one gives GO
    near_pivot = decide(make_request(scores=(2.49,) * 6), config)  # 49.8
    assert (near_go.verdict, near_go.stability) == (VerdictLabel.PIVOT, "BORDERLINE")
    assert (near_pivot.verdict, near_pivot.stability) == (VerdictLabel.NO_GO, "BORDERLINE")


@pytest.mark.parametrize(
    "inputs",
    [
        {"n_sources": 1},
        {"est_effort_hours": 999},
        {"blockers": [blocker("b")]},
        {"est_effort_hours": None},
    ],
)
def test_failures_a_score_cannot_repair_offer_no_candidate(
    config: VerdictConfig, inputs: dict[str, Any]
) -> None:
    request = make_request(scores=(5, 5, 1, 5, 5, 5), duplicate=duplicate(), **inputs)
    assert decide(request, config).what_would_make_go == []


# --- challenge + what-if ------------------------------------------------------------------------


def test_apply_does_not_penalise_the_changed_score_twice(config: VerdictConfig) -> None:
    request = make_request(objections=[objection(1), objection(2)])
    verdict = apply_changes(request, [ScoreChange(dimension=Dimension.COMPETITION, to=3)], config)
    changed = verdict.scores[1]
    assert (changed.score, verdict.score) == (3.0, 73.5)
    assert changed.confidence == 0.5  # 0.7 - 2 x 0.1 stays applied
    assert changed.features["challenge"]["before"]["score"] == 2.0


def test_duplicate_candidates_reproduce_over_http_with_objections() -> None:
    request = body(
        scores=(5, 5, 3.4, 5, 5, 5),
        duplicate=duplicate(),
        objections=[objection(1, "differentiation")],
    )
    search = client.post("/whatif", json={**request, "mode": "search"}).json()["candidates"]
    assert search and search[0]["from"] == 2.9
    for c in search:
        change = {"dimension": c["dimension"], "to": c["to"]}
        response = client.post("/whatif", json={**request, "mode": "apply", "changes": [change]})
        verdict = WhatIfResponse.model_validate_json(response.text).verdict
        assert verdict and verdict.gates.duplicate is GateResult.PASS
        assert (verdict.verdict, verdict.score) == (c["new_verdict"], c["new_score"])


def test_what_if_does_not_mutate_the_request(config: VerdictConfig) -> None:
    request = make_request(objections=[objection(1)])
    before = request.model_dump_json()
    decide(request, config)
    apply_changes(request, [ScoreChange(dimension=Dimension.RISK, to=5)], config)
    assert request.model_dump_json() == before


# --- API envelopes ------------------------------------------------------------------------------


def post_raw(path: str, content: str) -> Any:
    return client.post(path, content=content, headers={"content-type": "application/json"})


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity", "-0.1", "5.01"])
def test_non_finite_or_out_of_range_score_is_422(literal: str) -> None:
    data = body()
    data["scores"][0]["score"] = "@@"
    response = post_raw("/verdict", json.dumps(data).replace('"@@"', literal))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def _unknown_field(d: dict[str, Any]) -> None:
    d["verdict"] = "GO"  # VE-1: a caller cannot suggest the verdict


def _duplicated_dimension(d: dict[str, Any]) -> None:
    d["scores"][1] = d["scores"][0]


def _score_without_evidence(d: dict[str, Any]) -> None:
    d["scores"][0]["evidence_ids"] = []


def _goal_mismatch(d: dict[str, Any]) -> None:
    d["goal"] = "startup"


def _unsupported_objection(d: dict[str, Any]) -> None:
    d["accepted_objections"] = [{**objection(1), "evidence_ids": []}]


@pytest.mark.parametrize(
    "mutate",
    [
        _unknown_field,
        _duplicated_dimension,
        _score_without_evidence,
        _goal_mismatch,
        _unsupported_objection,
    ],
)
def test_contract_violations_are_422(mutate: Any) -> None:
    data = body()
    mutate(data)
    response = client.post("/verdict", json=data)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize(
    "extra",
    [
        {},
        {"mode": "guess"},
        {"mode": "apply"},
        {"mode": "apply", "changes": [{"dimension": "risk", "to": 6}]},
        {
            "mode": "apply",
            "changes": [{"dimension": "risk", "to": 4}, {"dimension": "risk", "to": 5}],
        },
    ],
    ids=["no-mode", "bad-mode", "no-changes", "out-of-range", "repeated"],
)
def test_bad_whatif_requests_are_422(extra: dict[str, Any]) -> None:
    response = client.post("/whatif", json={**body(), **extra})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_malformed_json_and_wrong_method_use_the_envelope() -> None:
    assert post_raw("/verdict", "{bad").json()["error"]["code"] == "VALIDATION_ERROR"
    response = client.get("/verdict")
    assert response.status_code == 405
    assert response.json()["error"]["code"] == "METHOD_NOT_ALLOWED"


def test_insufficient_evidence_over_http_has_no_score() -> None:
    response = client.post("/verdict", json=body(scores=(None,) * 6, n_sources=0))
    verdict = Verdict.model_validate_json(response.text)
    assert verdict.verdict is VerdictLabel.INSUFFICIENT_EVIDENCE
    assert verdict.score is None and verdict.missing


@pytest.mark.parametrize(
    "content",
    [b"\xff\xfe\x00bad", b"- a\n- b\n", b"version: 2020-13-45\n", b"? [a]\n: 1\n"],
    ids=["not-utf8", "list", "bad-date", "unhashable-key"],
)
def test_unreadable_config_content_leaves_the_app_not_ready(tmp_path: Path, content: bytes) -> None:
    path = tmp_path / "verdict.yaml"
    path.write_bytes(content)
    broken = TestClient(build_app(path))
    assert (broken.get("/health").status_code, broken.get("/ready").status_code) == (200, 503)


def test_config_path_that_is_a_directory_is_not_ready(tmp_path: Path) -> None:
    assert TestClient(build_app(tmp_path)).get("/ready").status_code == 503


# --- odd but schema-valid config ----------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"floor_dimensions": []},
        {"thresholds": {"T_go": 0, "T_no": 0, "floor": 0}},
        {"thresholds": {"T_go": 100, "T_no": 100, "floor": 5}},
        {"whatif": {"step": 0.1, "max_delta": 5}},
        {"whatif": {"step": 5, "max_delta": 5}},
        {"sensitivity": {"weight_delta": 0.999, "score_delta": 5}},
        {"confidence": {"min": 1, "w_mean": 1, "w_sources": 0, "sources_norm": 1}},
    ],
    ids=["no-floor", "T0", "T100", "fine-step", "coarse-step", "big-deltas", "strict-conf"],
)
def test_accepted_config_never_crashes_a_decision(overrides: dict[str, Any]) -> None:
    config = VerdictConfig.model_validate({**rules(), **overrides})
    assert decide(make_request(), config).verdict in VerdictLabel


def test_accepted_config_with_a_single_weighted_dimension_does_not_crash() -> None:
    """A config that loads (so the pod is Ready) must not make /verdict fail at request time.
    Either reject it at load time or handle it."""
    data = rules()
    data["sensitivity"]["weight_delta"] = 0
    data["weights"]["portfolio"] = {d.value: 0 for d in Dimension} | {"feasibility": 100}
    try:
        config = VerdictConfig.model_validate(data)
    except ValidationError:
        return
    decide(make_request(), config)


# --- determinism (M11-2) ------------------------------------------------------------------------


def test_same_json_in_processes_with_different_hash_seeds() -> None:
    code = (
        "from verdict_factories import *\n"
        "from verdict.config import load_config\n"
        "from verdict.engine import decide\n"
        "r = make_request(duplicate=duplicate(), objections=[objection(n) for n in range(5)]"
        " + [objection(9, 'risk')])\n"
        "print(decide(r, load_config(CONFIG_PATH)).model_dump_json())\n"
    )
    outputs = {
        subprocess.run(
            [sys.executable, "-c", code],
            env={
                **os.environ,
                "PYTHONHASHSEED": seed,
                "PYTHONPATH": os.pathsep.join(p for p in sys.path if p),
            },
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout
        for seed in ("0", "1", "12345")
    }
    assert len(outputs) == 1


def test_one_objection_id_on_two_dimensions_does_not_depend_on_list_order(
    config: VerdictConfig,
) -> None:
    """The contract does not forbid it, so spec A4 #15 applies: list order must not matter."""
    first, second = objection(1, "competition"), objection(1, "feasibility")
    one = decide(make_request(objections=[first, second]), config)
    other = decide(make_request(objections=[second, first]), config)
    assert one.model_dump_json() == other.model_dump_json()


def test_blocker_list_order_does_not_change_the_output(config: VerdictConfig) -> None:
    """Spec A4 #15: same input gives identical JSON, independent of input list order."""
    a, b = blocker("a"), blocker("b")
    one = decide(make_request(blockers=[a, b]), config)
    other = decide(make_request(blockers=[b, a]), config)
    assert one.model_dump_json() == other.model_dump_json()
