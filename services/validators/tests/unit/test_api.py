import pytest
from fastapi.testclient import TestClient
from validator_factories import IDEAS, body, engine, kb

from common.contracts.agents import ScorerResponse
from common.contracts.core import Dimension
from common.contracts.kb import SearchRequest, SearchResponse
from common.http import ServiceError
from validators.main import build_app
from validators.settings import ValidatorSettings

EXPECTED = {
    "demand": ["demand_pain"],
    "competition": ["competition", "differentiation"],
    "feasibility": ["feasibility", "learning"],
    "risk": ["risk"],
}


def client(**changes: object) -> TestClient:
    return TestClient(build_app(ValidatorSettings(), engine=engine(**changes)))  # type: ignore[arg-type]


@pytest.mark.parametrize("path", ["/health", "/ready", "/metrics"])
def test_health_ready_metrics(path: str) -> None:
    assert client().get(path).status_code == 200


def test_unconfigured_service_is_unready_and_says_so() -> None:
    bare = TestClient(build_app(ValidatorSettings()))
    assert bare.get("/health").status_code == 200
    assert bare.get("/ready").status_code == 503
    response = bare.post("/score/demand", json=body().model_dump(mode="json"))
    assert response.json()["error"]["code"] == "NOT_READY"


@pytest.mark.parametrize("idea", IDEAS)
def test_five_ideas_give_six_dimensions(idea: str) -> None:
    seen = []
    # one event loop for all four requests, as in a running service
    with client() as api:
        responses = {
            name: api.post(f"/score/{name}", json=body(idea).model_dump(mode="json"))
            for name in EXPECTED
        }
    for scorer, dimensions in EXPECTED.items():
        response = responses[scorer]
        assert response.status_code == 200, response.text
        result = ScorerResponse.model_validate_json(response.text)
        assert [s.dimension.value for s in result.scores] == dimensions
        for score in result.scores:
            assert score.rubric_version == f"{score.dimension.value}@v1" and score.features
        seen += [s.dimension for s in result.scores]
    assert sorted(seen) == sorted(Dimension)


def test_unknown_scorer_is_404() -> None:
    response = client().post("/score/vibes", json=body().model_dump(mode="json"))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


@pytest.mark.parametrize("run_id", ["run 1", "run\n1", "run_é", "x" * 129])
def test_unsafe_run_id_is_422(run_id: str) -> None:
    payload = body().model_dump(mode="json") | {"run_id": run_id}
    assert client().post("/score/demand", json=payload).status_code == 422


def test_kb_outage_is_503() -> None:
    class Down(type(kb())):  # type: ignore[misc]
        async def search(self, request: SearchRequest) -> SearchResponse:
            raise ServiceError("UNAVAILABLE", "secret internal detail")

    response = client(knowledge=Down()).post("/score/demand", json=body().model_dump(mode="json"))
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "KB_UNAVAILABLE"
    assert "secret" not in response.text
