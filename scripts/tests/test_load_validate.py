"""The load script: what it counts, when it fails, and how it names the stand-in."""

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

import labelled
import load_validate
from standin import build_app


class FakeHarness:
    def __init__(self, fail: frozenset[int] = frozenset()) -> None:
        self.fail = fail
        self.seen: list[str] = []
        self.now = self.most = 0

    async def run(self, idea: labelled.LabelledIdea) -> dict[str, Any]:
        number = int(idea.idea_id.rsplit("-", 1)[1])
        self.seen.append(idea.idea_id)
        self.now += 1
        self.most = max(self.most, self.now)
        await asyncio.sleep(0.01)
        self.now -= 1
        if number in self.fail:
            return {"idea_id": idea.idea_id, "status": "FAILED", "verdict": None,
                    "error": "the run ended FAILED"}  # fmt: skip
        return {"idea_id": idea.idea_id, "status": "COMPLETED", "verdict": "GO", "error": None,
                "scores": {"risk": None, "learning": 4.0}}  # fmt: skip


def test_all_runs_complete_and_each_has_its_own_idea_id(tmp_path: Path) -> None:
    harness = FakeHarness()
    out = tmp_path / "load.json"
    assert load_validate.main(["-n", "45", "--out", str(out)], harness, False) == 0
    report = json.loads(out.read_text())
    assert (report["runs"], report["completed"], report["concurrency"]) == (45, 45, 45)
    assert report["verdicts"] == {"GO": 45} and report["errors"] == {}
    assert (report["abstained_dimensions"], report["scored_dimensions"]) == (45, 45)
    assert report["stand_in"] is False
    assert report["latency_s_not_completed"] is None
    assert set(report["latency_s_completed"]) == {"p50", "p95", "max"}
    # 40 labelled ideas, so five are used twice, under different ids (hence different users)
    assert len(set(harness.seen)) == 45 and harness.most == 45


def test_concurrency_is_bounded() -> None:
    harness = FakeHarness()
    assert load_validate.main(["-n", "6", "-c", "2"], harness, False) == 0
    assert harness.most == 2


def test_one_run_that_does_not_complete_fails_the_load(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "load.json"
    harness = FakeHarness(fail=frozenset({1}))
    assert load_validate.main(["-n", "3", "--out", str(out)], harness, True) == 1
    report = json.loads(out.read_text())
    assert report["statuses"] == {"COMPLETED": 2, "FAILED": 1}
    assert report["errors"] == {"the run ended FAILED": 1} and report["stand_in"] is True
    assert "STAND-IN" in capsys.readouterr().err


def test_a_crash_in_one_run_is_counted_without_its_message(tmp_path: Path) -> None:
    class Crashing(FakeHarness):
        async def run(self, idea: labelled.LabelledIdea) -> dict[str, Any]:
            raise RuntimeError("idea text must not be printed")

    out = tmp_path / "load.json"
    assert load_validate.main(["-n", "2", "--out", str(out)], Crashing(), False) == 1
    assert json.loads(out.read_text())["errors"] == {"RuntimeError": 2}
    assert "idea text" not in out.read_text()


def test_without_service_urls_nothing_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORCHESTRATOR_URL", raising=False)
    assert load_validate.main(["-n", "1"]) == 2


@pytest.mark.parametrize("arguments", [["-n", "0"], ["-c", "0"]])
def test_nothing_to_run_is_refused(arguments: list[str]) -> None:
    with pytest.raises(SystemExit) as stopped:
        load_validate.main(arguments, FakeHarness(), False)
    assert stopped.value.code == 2


def transport(reply: httpx.Response | Exception) -> httpx.MockTransport:
    def handle(_: httpx.Request) -> httpx.Response:
        if isinstance(reply, Exception):
            raise reply
        return reply

    return httpx.MockTransport(handle)


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        (httpx.Response(200, json={"stand_in": True}), True),
        (httpx.Response(404, json={}), False),
        (httpx.Response(200, text="<html>"), False),
        (httpx.Response(200, json=[1]), False),
        (httpx.ConnectError("refused"), None),
    ],
)
def test_the_stand_in_is_found_by_itself(
    reply: httpx.Response | Exception, expected: bool | None
) -> None:
    assert load_validate.stand_in("http://profile", transport(reply)) is expected


async def test_the_marker_the_script_looks_for_is_the_one_the_stand_in_serves() -> None:
    app = build_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://standin"
    ) as client:
        assert (await client.get("/stand-in")).json() == {"stand_in": True}
