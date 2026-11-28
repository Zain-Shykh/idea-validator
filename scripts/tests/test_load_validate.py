"""The load script: what it counts, when it fails, and how it names the stand-in."""

import asyncio
import contextlib
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

import labelled
import load_validate
import run_eval
from common.http import ServiceClient
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


# --- edge cases (spec Z11, A7) ---


def real_harness(handle: Any, **options: Any) -> run_eval.Harness:
    transport = httpx.MockTransport(handle)
    clients = [
        ServiceClient(f"http://{name}", transport=transport, retries=0)
        for name in ("orchestrator", "profile", "agentops")
    ]
    return run_eval.Harness(*clients, tag="load_t", poll_s=0.01, **options)


def test_a_run_that_never_ends_is_counted_as_failed(tmp_path: Path) -> None:
    users: list[str] = []
    polls: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.host == "profile":
            body = json.loads(request.content)
            users.append(body["user_id"])
            version = {"id": "iv_1", "idea_id": "idea_1", "version": 1, "text": "x",
                       "origin": "initial", "created_at": "2026-01-01T00:00:00Z"}  # fmt: skip
            return httpx.Response(200, json={
                "id": "idea_1", "user_id": body["user_id"], "title": "t", "source": "own",
                "created_at": "2026-01-01T00:00:00Z", "versions": [version],
            })  # fmt: skip
        if request.method == "POST":
            return httpx.Response(200, json={"run_id": "run_1"})
        polls.append(request.url.path)
        return httpx.Response(200, json={
            "run_id": "run_1", "workflow": "validate", "status": "RUNNING",
            "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
            "budget": {"max_tokens": 1, "max_cost_usd": 1, "max_pages": 1, "max_steps": 1},
            "spend": {},
        })  # fmt: skip

    out = tmp_path / "load.json"
    harness = real_harness(handle, timeout_s=0.2)
    assert load_validate.main(["-n", "2", "--out", str(out)], harness, False) == 1
    report = json.loads(out.read_text())
    assert report["completed"] == 0 and report["errors"] == {"TIMEOUT": 2}
    assert len(polls) > 2
    assert len(set(users)) == 2 and all(u.startswith("u_eval_load_") for u in users)


def test_a_hanging_service_ends_at_the_idea_timeout(tmp_path: Path) -> None:
    async def hang(_: httpx.Request) -> httpx.Response:
        await asyncio.sleep(30)
        return httpx.Response(200)

    out = tmp_path / "load.json"
    assert load_validate.main(["-n", "2", "--out", str(out)],
                              real_harness(hang, timeout_s=0.1), False) == 1  # fmt: skip
    assert json.loads(out.read_text())["errors"] == {"TIMEOUT": 2}


def test_neither_idea_text_nor_a_services_message_reaches_the_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = "sk-FAKE-secret"

    def echo(request: httpx.Request) -> httpx.Response:
        message = request.content.decode() + secret
        return httpx.Response(422, json={"error": {"code": "BAD", "message": message}})

    out = tmp_path / "load.json"
    assert load_validate.main(["-n", "3", "--out", str(out)], real_harness(echo), False) == 1
    printed = capsys.readouterr()
    shown = out.read_text() + printed.out + printed.err
    ideas = labelled.load_ideas()[:3]
    for text in [secret, *(i.text for i in ideas), *(i.title for i in ideas)]:
        assert text not in shown
    assert json.loads(out.read_text())["errors"] == {"BAD (HTTP 422)": 3}


def test_latency_percentiles_split_completed_from_not_completed() -> None:
    entries = [
        {"status": "COMPLETED", "verdict": "GO", "error": None, "seconds": float(n)}
        for n in range(1, 21)
    ] + [{"status": None, "verdict": None, "error": "TIMEOUT", "seconds": 900.0}]
    summary = load_validate.summarise(entries)
    assert summary["latency_s_completed"] == {"p50": 10.0, "p95": 19.0, "max": 20.0}
    assert summary["latency_s_not_completed"] == {"p50": 900.0, "p95": 900.0, "max": 900.0}
    assert summary["statuses"] == {"COMPLETED": 20, "None": 1}


def test_a_missing_labelled_set_is_exit_2(tmp_path: Path) -> None:
    assert load_validate.main(["--labelled", str(tmp_path)], FakeHarness(), False) == 2


# --- BUGS (left failing on purpose, see the Z11 testing report) ---


def test_an_unwritable_out_still_shows_the_result_of_the_load(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """After a 10-minute load a wrong --out must not throw the numbers away."""
    out = tmp_path / "missing-folder" / "load.json"
    with contextlib.suppress(OSError):
        load_validate.main(["-n", "2", "--out", str(out)], FakeHarness(), False)
    assert '"completed": 2' in capsys.readouterr().out


def test_an_unusable_service_url_is_exit_2_not_a_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing was run, so the documented code is 2; a traceback exits 1, "a run failed"."""
    for name in ("ORCHESTRATOR_URL", "PROFILE_URL", "AGENTOPS_URL"):
        monkeypatch.setenv(name, "http://[bad")
    assert load_validate.main(["-n", "1"]) == 2


@pytest.mark.parametrize("probe", [None, False])
def test_stand_in_data_marks_the_report_when_the_probe_did_not(
    tmp_path: Path, probe: bool | None
) -> None:
    """Z11-6: the data says so itself. The one probe has a 5 s limit and PROFILE_URL only."""

    class StandInData(FakeHarness):
        async def run(self, idea: labelled.LabelledIdea) -> dict[str, Any]:
            return await super().run(idea) | {"versions": {"kb_version": "stand-in"}}

    out = tmp_path / "load.json"
    load_validate.main(["-n", "2", "--out", str(out)], StandInData(), probe)
    assert json.loads(out.read_text())["stand_in"] is True
