"""Across all seven stubs: the §7.2 routes exist, and bad input gets the 422 envelope without
echoing what was sent (spec Z1, A4 #6 and A7).
"""

import importlib
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from common.contracts.core import ErrorEnvelope

OPS = {"/health", "/ready"}
ROUTES: dict[str, dict[str, set[str]]] = {
    "orchestrator": {
        "post": {
            "/runs",
            "/runs/{}/answer",
            "/runs/{}/approve",
            "/runs/{}/resume",
            "/ideas/{}/challenge",
            "/ideas/{}/whatif",
        },
        "get": {"/runs/{}"},
    },
    "ideation": {
        "post": {"/intake", "/ideate", "/gap-analyze", "/skeptic", "/concretize"},
        "get": set(),
    },
    "validators": {"post": {"/score/{}"}, "get": set()},
    "scorer": {"post": {"/score"}, "get": set()},
    "verdict": {"post": {"/verdict", "/whatif"}, "get": {"/config"}},
    "critic": {"post": {"/check", "/rail/input", "/scan", "/policy/approval"}, "get": set()},
    "agentops": {
        "post": {"/trace"},
        "get": {"/runs/{}/trace", "/summary", "/registry", "/drift/latest", "/eval/latest"},
    },
}
SECRET = "hunter2-do-not-echo"
WITH_BODY = [
    (service, path)
    for service, methods in ROUTES.items()
    for path in sorted(methods["post"])
    if not path.endswith("/resume")
]


def app_of(service: str) -> FastAPI:
    app: FastAPI = importlib.import_module(f"{service}.main").app
    return app


@pytest.mark.parametrize("service", ROUTES)
def test_service_exposes_exactly_its_routes(service: str) -> None:
    found: dict[str, set[str]] = {"post": set(), "get": set()}
    for path, operations in app_of(service).openapi()["paths"].items():
        for method in operations:
            if path not in OPS:
                found[method].add(re.sub(r"\{[^}]+\}", "{}", path))
    assert found == ROUTES[service]


@pytest.mark.parametrize(("service", "path"), WITH_BODY)
@pytest.mark.parametrize(
    "content",
    [b"", b"{not json " + SECRET.encode(), b"null", f'{{"password": "{SECRET}"}}'.encode()],
    ids=["empty", "malformed", "null", "wrong-fields"],
)
def test_invalid_body_gets_422_envelope_without_values(
    service: str, path: str, content: bytes
) -> None:
    response = TestClient(app_of(service)).post(
        path.replace("{}", "demand"), content=content, headers={"content-type": "application/json"}
    )
    assert response.status_code == 422
    envelope = ErrorEnvelope.model_validate_json(response.text)
    assert envelope.error.code == "VALIDATION_ERROR" and envelope.error.request_id
    assert SECRET not in response.text
