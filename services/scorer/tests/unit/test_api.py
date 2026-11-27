import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient
from scorer_factories import ROOT, Replies, Rig, completion, reply, request

from common.contracts.core import DimensionScore
from scorer.main import build_app, build_scorer, probe
from scorer.services.backend import LocalModel
from scorer.settings import ScorerSettings


def settings(**changes: object) -> ScorerSettings:
    values = {
        "backend": "llamacpp",
        "model_url": "http://model.invalid",
        "rubrics_dir": str(ROOT / "rubrics"),
        "prompts_dir": str(ROOT / "prompts"),
        "probe_interval_s": 0.01,
    }
    return ScorerSettings(**{**values, **changes})  # type: ignore[arg-type]


def test_score_route_returns_a_dimension_score() -> None:
    app = build_app(settings(), scorer=Rig(Replies(reply())).scorer)
    response = TestClient(app).post(
        "/score", json=request().model_dump(mode="json"), headers={"X-Run-Id": "run_1"}
    )
    assert DimensionScore.model_validate_json(response.text).score == 3.5


def test_unknown_rubric_version_is_422() -> None:
    app = build_app(settings(), scorer=Rig(Replies()).scorer)
    body = {**request().model_dump(mode="json"), "rubric_id": "demand_pain@v9"}
    response = TestClient(app).post("/score", json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize(
    "changes",
    [{"model_url": None}, {"rubrics_dir": "/nonexistent"}, {"prompts_dir": "/nonexistent"}],
)
def test_misconfigured_service_is_unready_and_says_503(changes: dict[str, object]) -> None:
    with TestClient(build_app(settings(**changes))) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/ready").status_code == 503
        response = client.post("/score", json=request().model_dump(mode="json"))
        assert response.status_code == 503


def test_ready_only_after_probe() -> None:
    state = {"loaded": False}
    prompts: list[str] = []

    def server(req: httpx.Request) -> httpx.Response:
        prompts.append(req.content.decode())
        if not state["loaded"]:
            return httpx.Response(503, json={"error": {"code": 503, "message": "Loading model"}})
        return completion("ok")

    scorer = Rig(server).scorer
    with TestClient(build_app(settings(), scorer=scorer)) as client:
        assert client.get("/ready").status_code == 503
        state["loaded"] = True
        for _ in range(200):
            if client.get("/ready").status_code == 200:
                break
            client.portal.call(asyncio.sleep, 0.01)  # type: ignore[union-attr]
        assert client.get("/ready").status_code == 200
    assert "Reply with the word ok" in prompts[-1]


async def test_probe_stops_after_the_first_answer() -> None:
    server = Replies(httpx.ConnectError("not yet"), "ok")
    local = LocalModel("http://m", "m", timeout_s=1, transport=httpx.MockTransport(server))
    ready = asyncio.Event()
    await asyncio.wait_for(probe(local, ready, 0.001), 2)
    assert ready.is_set() and len(server.bodies) == 2


def test_hosted_backend_needs_no_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_BACKEND", "gateway")
    monkeypatch.setenv("TOOLS_URL", "http://tools.invalid")
    with TestClient(build_app(settings(backend="hosted"))) as client:
        assert client.get("/ready").status_code == 200


def test_unconfigured_hosted_model_only_stops_the_hosted_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_BACKEND", "gateway")
    monkeypatch.delenv("TOOLS_URL", raising=False)
    assert build_scorer(settings(backend="hosted")) is None
    local_only = build_scorer(settings())
    assert local_only is not None and local_only.hosted is None


def test_backend_picks_its_address(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama:11434")
    monkeypatch.setenv("SCORER_BACKEND", "ollama")
    monkeypatch.setenv("SCORER_MODEL_URL", "http://llama:8080")
    assert ScorerSettings().local_url() == "http://ollama:11434"
    monkeypatch.setenv("SCORER_BACKEND", "llamacpp")
    assert ScorerSettings().local_url() == "http://llama:8080"
