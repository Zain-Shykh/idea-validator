"""Readiness, bounded calls and a shutdown that fits Kubernetes' 30 s (spec Z7, A4 #17, #18)."""

import asyncio
import re
import time

import httpx
import pytest
from orchestrator_factories import ROOT, Orchestrator, Stack, error, profile, settings
from test_resume import hang, until

from common.contracts.core import TraceRecord
from orchestrator import main
from orchestrator.services import downstream
from orchestrator.services.runner import STOP_GRACE_S


async def ready(m3: Orchestrator) -> int:
    return (await m3.client.get("/ready")).status_code


async def test_ready_when_everything_is_there() -> None:
    stack = Stack()
    assert await ready(Orchestrator(stack)) == 200
    assert sorted(stack.calls) == [("profile", "/ready"), ("verdict", "/ready")]


async def test_ready_fails_without_redis() -> None:
    stack = Stack()
    stack.redis.down = True
    m3 = Orchestrator(stack)
    assert await ready(m3) == 503
    assert (await m3.client.get("/health")).status_code == 200


@pytest.mark.parametrize("name", ["profile", "verdict"])
async def test_ready_fails_when_a_required_service_is_not_ready(name: str) -> None:
    stack = Stack()
    stack.overrides[(name, "/ready")] = lambda _: error(503, "NOT_READY")
    assert await ready(Orchestrator(stack)) == 503


async def test_ready_can_leave_out_the_store() -> None:
    stack = Stack()
    stack.overrides[("profile", "/ready")] = lambda _: error(503, "NOT_READY")
    assert await ready(Orchestrator(stack, ready_requires="verdict")) == 200


@pytest.mark.parametrize(
    "changes",
    [
        {"VERDICT_URL": None},
        {"VERDICT_URL": "verdict:8000"},
        {"KB_URL": "not a url"},
        {"VALIDATORS_URL": ""},
        {"CRITIC_URL": None},
        {"ready_requires": "verdict,scorer"},
        {"rubrics_dir": "/nonexistent"},
        {"budgets_path": "/nonexistent"},
    ],
)
async def test_ready_fails_on_bad_configuration(changes: dict) -> None:
    stack = Stack()
    app = main.build_app(settings(**changes), redis=stack.redis, transport=stack)  # type: ignore[arg-type]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://o"
    ) as client:
        assert (await client.get("/ready")).status_code == 503
        assert (await client.get("/health")).status_code == 200


def test_the_shutdown_budget_fits_the_grace_period() -> None:
    dockerfile = (ROOT / "services/orchestrator/Dockerfile").read_text()
    found = re.search(r'"--timeout-graceful-shutdown", "(\d+)"', dockerfile)
    assert found is not None
    assert int(found.group(1)) + STOP_GRACE_S + main.FLUSH_LIMIT_S < 30
    assert downstream.PEERS["verdict"] == ("verdict_url", 5.0, 15.0)


async def test_hanging_verdict_service_is_given_up_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(downstream.PEERS, "verdict", ("verdict_url", 5.0, 0.2))
    stack = Stack()
    stack.overrides[("verdict", "/verdict")] = hang
    m3 = Orchestrator(stack)
    start = time.perf_counter()
    run_id = await m3.run()
    assert time.perf_counter() - start < 5
    assert m3.status(run_id) == "FAILED" and m3.state(run_id)["error_code"] == "TIMEOUT"


async def test_shutdown_with_a_hanging_agentops_and_a_running_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main, "FLUSH_LIMIT_S", 0.3)
    stack = Stack()
    stack.overrides[("validators", "/score")] = hang
    m3 = Orchestrator(stack)

    async def stuck(_: list[TraceRecord]) -> None:
        await asyncio.sleep(3600)

    m3.trace._sink = stuck
    start = time.perf_counter()
    async with m3.app.router.lifespan_context(m3.app):
        run_id = (
            await m3.client.post(
                "/runs",
                json={
                    "workflow": "validate",
                    "user_id": "u_1",
                    "idea_version_id": "iv_1",
                    "profile": profile(),
                },
            )
        ).json()["run_id"]
        await until(lambda: bool(stack.paths("validators")))
    assert time.perf_counter() - start < 5
    assert m3.status(run_id) == "RUNNING"  # resumable
    assert all(t.done() for t in m3.runner.tasks.values())
