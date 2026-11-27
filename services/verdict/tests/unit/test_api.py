from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from verdict_factories import CONFIG_PATH, body

from common.contracts.core import Verdict, VerdictLabel
from common.contracts.verdict import VerdictConfigResponse, WhatIfResponse
from verdict.main import app, build_app

client = TestClient(app)
RULES = CONFIG_PATH.read_text()


@pytest.mark.parametrize("path", ["/health", "/ready", "/metrics"])
def test_health_ready_metrics(path: str) -> None:
    assert client.get(path).status_code == 200


def test_post_verdict_returns_the_worked_example() -> None:
    response = client.post("/verdict", json=body())
    assert response.status_code == 200
    verdict = Verdict.model_validate_json(response.text)
    assert (verdict.verdict, verdict.score) == (VerdictLabel.GO, 70.5)
    # serialised with the DATA-5 names "from" and "to"
    (candidate,) = [
        c
        for c in response.json()["what_would_make_go"]
        if (c["dimension"], c["to"]) == ("competition", 3.0)
    ]
    assert (candidate["from"], candidate["new_score"], candidate["stable"]) == (2.0, 73.5, True)


def test_invalid_request_gets_error_envelope() -> None:
    request = body()
    request["scores"] = request["scores"][:5]
    response = client.post("/verdict", json=request)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_whatif_search_and_apply() -> None:
    search = WhatIfResponse.model_validate_json(
        client.post("/whatif", json={**body(), "mode": "search"}).text
    )
    assert search.candidates and search.verdict is None
    applied = client.post(
        "/whatif",
        json={**body(), "mode": "apply", "changes": [{"dimension": "competition", "to": 3}]},
    )
    result = WhatIfResponse.model_validate_json(applied.text)
    assert result.verdict and result.verdict.score == 73.5 and result.candidates == []


def test_whatif_apply_on_abstained_dimension_is_422() -> None:
    request = {
        **body(scores=(3, 3, 3, 3, 3, None)),
        "mode": "apply",
        "changes": [{"dimension": "learning", "to": 5}],
    }
    response = client.post("/whatif", json=request)
    assert response.status_code == 422
    assert "no evidence-backed score" in response.json()["error"]["message"]


def test_config_reports_active_rules_and_versions() -> None:
    result = VerdictConfigResponse.model_validate_json(client.get("/config").text)
    assert (result.versions.engine, result.versions.profile_weights) == ("m11@v1", "w1")
    assert result.config["thresholds"] == {"T_go": 70, "T_no": 50, "floor": 1.5}


def test_changing_the_file_changes_behaviour(tmp_path: Path) -> None:
    path = tmp_path / "verdict.yaml"
    path.write_text(RULES.replace("T_go: 70", "T_go: 75").replace("version: v1 ", "version: v2 "))
    verdict = TestClient(build_app(path)).post("/verdict", json=body()).json()
    assert (verdict["verdict"], verdict["score"]) == ("PIVOT", 70.5)
    assert verdict["thresholds"]["T_go"] == 75 and verdict["versions"]["engine"] == "m11@v2"


def test_path_comes_from_the_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VERDICT_CONFIG_PATH", str(tmp_path / "absent.yaml"))
    assert TestClient(build_app()).get("/ready").status_code == 503
    monkeypatch.setenv("VERDICT_CONFIG_PATH", str(CONFIG_PATH))
    assert TestClient(build_app()).get("/ready").status_code == 200


@pytest.mark.parametrize(
    "content",
    [
        None,  # no file
        "",
        "just text",
        "version: [unclosed",
        RULES.replace("learning: 20}", "learning: 25}"),
    ],
    ids=["missing", "empty", "not-a-mapping", "bad-yaml", "bad-weight-sum"],
)
def test_invalid_config_is_not_ready_and_decides_nothing(
    tmp_path: Path, content: str | None
) -> None:
    path = tmp_path / "verdict.yaml"
    if content is not None:
        path.write_text(content)
    broken = TestClient(build_app(path))
    assert broken.get("/health").status_code == 200
    assert broken.get("/ready").status_code == 503
    for response in (
        broken.post("/verdict", json=body()),
        broken.post("/whatif", json={**body(), "mode": "search"}),
        broken.get("/config"),
    ):
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "NOT_READY"
