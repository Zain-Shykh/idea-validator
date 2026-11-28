from collections.abc import AsyncIterator

import httpx
import pytest
import respx
from tenacity import wait_none

from common import context
from common.contracts.core import HealthStatus
from common.http import ServiceClient, ServiceError

BASE = "http://svc:8000"


@pytest.fixture
async def service() -> AsyncIterator[ServiceClient]:
    async with ServiceClient(BASE, wait=wait_none()) as client:
        yield client


OK = httpx.Response(200, json={"status": "ok"})


@pytest.mark.parametrize(
    "failure", [httpx.Response(503), httpx.Response(429), httpx.ReadTimeout("slow")]
)
@respx.mock
async def test_retries_then_succeeds(service: ServiceClient, failure: object) -> None:
    route = respx.post(f"{BASE}/x").mock(side_effect=[failure, OK])
    result = await service.post("/x", {"a": 1}, response_model=HealthStatus)
    assert result == HealthStatus(status="ok")
    assert route.call_count == 2
    keys = {call.request.headers["Idempotency-Key"] for call in route.calls}
    assert len(keys) == 1


@respx.mock
async def test_gives_up_after_two_retries_with_downstream_error(service: ServiceClient) -> None:
    body = {"error": {"code": "NOT_READY", "message": "warming up"}}
    route = respx.get(f"{BASE}/x").respond(503, json=body)
    with pytest.raises(ServiceError) as caught:
        await service.get("/x")
    assert route.call_count == 3
    assert (caught.value.status, caught.value.code) == (503, "NOT_READY")


@pytest.mark.parametrize("status", [400, 404, 422])
@respx.mock
async def test_no_retry_on_client_error(service: ServiceClient, status: int) -> None:
    route = respx.get(f"{BASE}/x").respond(status, text="nope")
    with pytest.raises(ServiceError) as caught:
        await service.get("/x")
    assert (route.call_count, caught.value.status) == (1, status)


@pytest.mark.parametrize(
    ("failure", "code", "calls"),
    [(httpx.ReadTimeout("t"), "TIMEOUT", 3), (httpx.ConnectError("c"), "UNAVAILABLE", 1)],
)
@respx.mock
async def test_transport_errors(
    service: ServiceClient, failure: Exception, code: str, calls: int
) -> None:
    route = respx.get(f"{BASE}/x").mock(side_effect=failure)
    with pytest.raises(ServiceError) as caught:
        await service.get("/x")
    assert (caught.value.code, caught.value.status, route.call_count) == (code, None, calls)


@respx.mock
async def test_retry_false_sends_once(service: ServiceClient) -> None:
    route = respx.post(f"{BASE}/x").respond(503)
    with pytest.raises(ServiceError):
        await service.post("/x", retry=False)
    assert route.call_count == 1


@respx.mock
async def test_propagates_context_headers(service: ServiceClient) -> None:
    route = respx.get(f"{BASE}/x").mock(return_value=OK)
    run = context.run_id_var.set("run_7")
    step = context.parent_step_var.set(3)
    try:
        await service.get("/x")
    finally:
        context.run_id_var.reset(run)
        context.parent_step_var.reset(step)
    sent = route.calls.last.request.headers
    assert (sent["X-Run-Id"], sent["X-Parent-Step"]) == ("run_7", "3")
    assert "Idempotency-Key" not in sent


@respx.mock
async def test_explicit_idempotency_key(service: ServiceClient) -> None:
    route = respx.post(f"{BASE}/x").mock(return_value=OK)
    await service.post("/x", [HealthStatus(status="a")], idempotency_key="step-12")
    request = route.calls.last.request
    assert request.headers["Idempotency-Key"] == "step-12"
    assert request.content == b'[{"status":"a"}]'


@pytest.mark.parametrize(
    "reply", [httpx.Response(200, json={"wrong": 1}), httpx.Response(200, text="<html>")]
)
@respx.mock
async def test_invalid_response_is_not_retried(
    service: ServiceClient, reply: httpx.Response
) -> None:
    route = respx.get(f"{BASE}/x").mock(return_value=reply)
    with pytest.raises(ServiceError) as caught:
        await service.get("/x", response_model=HealthStatus)
    assert (caught.value.code, route.call_count) == ("INVALID_RESPONSE", 1)


@respx.mock
async def test_list_response_model_and_empty_body(service: ServiceClient) -> None:
    respx.get(f"{BASE}/list").respond(200, json=[{"status": "a"}])
    respx.post(f"{BASE}/accepted").respond(202)
    assert await service.get("/list", response_model=list[HealthStatus]) == [
        HealthStatus(status="a")
    ]
    assert await service.post("/accepted") is None
