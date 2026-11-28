import asyncio
import json
import logging

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

from common import context
from common.service import ApiError, JsonFormatter, ReadyCheck, create_app


def client(app: FastAPI) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


class Login(BaseModel):
    password: int


def demo_app() -> FastAPI:
    app = create_app("demo")

    @app.get("/ids")
    async def ids() -> dict[str, str]:
        return context.outgoing_headers()

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("secret internals")

    @app.get("/budget")
    async def budget() -> None:
        raise ApiError(402, "BUDGET_EXCEEDED", "Run budget exceeded", {"pages": 0})

    @app.get("/limited")
    async def limited() -> None:
        raise HTTPException(429, "slow down", headers={"Retry-After": "3"})

    @app.post("/login")
    async def login(body: Login) -> None: ...

    @app.get("/items/{item_id}")
    async def item(item_id: str) -> str:
        return item_id

    return app


def test_health() -> None:
    response = client(create_app("demo")).get("/health")
    assert (response.status_code, response.json()) == (200, {"status": "ok"})


def failing() -> bool:
    raise ConnectionError("db down")


async def hanging() -> bool:
    await asyncio.sleep(5)
    return True


async def async_true() -> bool:
    return True


@pytest.mark.parametrize("check", [None, lambda: True, async_true])
def test_ready_when_check_passes(check: ReadyCheck | None) -> None:
    assert client(create_app("demo", check)).get("/ready").status_code == 200


@pytest.mark.parametrize("check", [lambda: False, failing, hanging])
def test_ready_503_when_check_fails_raises_or_hangs(check: ReadyCheck) -> None:
    response = client(create_app("demo", check, ready_timeout_s=0.05)).get("/ready")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "NOT_READY"
    assert "db down" not in response.text


def test_metrics_prometheus_text() -> None:
    http = client(demo_app())
    http.get("/items/abc123")
    http.get("/no/such/path")
    response = http.get("/metrics")
    assert response.headers["content-type"].startswith("text/plain")
    assert 'route="/items/{item_id}",service="demo"' in response.text
    assert 'route="unmatched"' in response.text
    assert "abc123" not in response.text


def test_request_ids_propagate() -> None:
    http = client(demo_app())
    headers = {"X-Run-Id": "run_1", "X-Request-Id": "req-1", "X-Parent-Step": "7"}
    response = http.get("/ids", headers=headers)
    assert response.json() == {**headers}
    assert response.headers["X-Run-Id"] == "run_1"
    assert response.headers["X-Request-Id"] == "req-1"
    # nothing leaks into the next request
    second = http.get("/ids")
    assert second.json() == {"X-Request-Id": second.headers["X-Request-Id"]}


@pytest.mark.parametrize("bad", ["a b", "x" * 200, "id;drop", ""])
def test_hostile_id_headers_ignored(bad: str) -> None:
    response = client(demo_app()).get(
        "/ids", headers={"X-Request-Id": bad, "X-Run-Id": bad, "X-Parent-Step": bad}
    )
    assert set(response.json()) == {"X-Request-Id"}
    assert response.json()["X-Request-Id"] != bad


@pytest.mark.parametrize(
    ("path", "status", "code"),
    [
        ("/boom", 500, "INTERNAL_ERROR"),
        ("/budget", 402, "BUDGET_EXCEEDED"),
        ("/limited", 429, "RATE_LIMITED"),
        ("/missing", 404, "NOT_FOUND"),
    ],
)
def test_error_envelope(path: str, status: int, code: str) -> None:
    response = client(demo_app()).get(path, headers={"X-Request-Id": "req-9"})
    assert response.status_code == status
    error = response.json()["error"]
    assert (error["code"], error["request_id"]) == (code, "req-9")
    assert "secret internals" not in response.text
    if status == 429:
        assert response.headers["Retry-After"] == "3"


def test_error_envelope_validation_does_not_echo_input() -> None:
    response = client(demo_app()).post("/login", json={"password": "hunter2"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert response.json()["error"]["details"]["errors"][0]["loc"] == ["body", "password"]
    assert "hunter2" not in response.text


def test_json_log_has_run_id() -> None:
    token = context.run_id_var.set("run_42")
    try:
        record = logging.LogRecord("x", logging.INFO, __file__, 1, "hi %s", ("there",), None)
        entry = json.loads(JsonFormatter("demo").format(record))
    finally:
        context.run_id_var.reset(token)
    assert (entry["service"], entry["run_id"], entry["msg"]) == ("demo", "run_42", "hi there")
    assert {"ts", "level"} <= set(entry)


def test_empty_log_level_means_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "")
    create_app("svc")
    assert logging.getLogger().level == logging.INFO
