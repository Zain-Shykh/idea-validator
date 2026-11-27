"""``run_eval`` against the real M3 and M11 on M3's fake stack (spec Z10, A4 #3)."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from orchestrator_factories import Orchestrator, Stack, question

import labelled
import reports
import run_eval
from common.contracts.core import VerdictRequest
from common.http import ServiceClient


def labelled_set(folder: Path, verdicts: list[str] | None, count: int = 3) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    ideas = labelled.load_ideas()[:count]
    (folder / "ideas.jsonl").write_text("".join(i.model_dump_json() + "\n" for i in ideas))
    if verdicts is not None:
        rows = "".join(f"{i.idea_id},{v},test,\n" for i, v in zip(ideas, verdicts, strict=True))
        (folder / "resolved.csv").write_text("idea_id,verdict,labelled_by,resolution\n" + rows)
    return folder


class Router(httpx.AsyncBaseTransport):
    """The harness's three services: M3's app, the fake M2 and the traces M3 sent."""

    def __init__(self, m3: Orchestrator) -> None:
        self.m3 = m3
        self.app = httpx.ASGITransport(m3.app)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "orchestrator":
            return await self.app.handle_async_request(request)
        if request.url.host == "profile":
            return await self.m3.stack.handle_async_request(request)
        await self.m3.trace.flush()
        run_id = request.url.path.split("/")[2]
        records = [r for r in self.m3.stack.records if r.run_id == run_id]
        return httpx.Response(200, json=[r.model_dump(mode="json") for r in records])


def harness(m3: Orchestrator, **changes: Any) -> run_eval.Harness:
    transport = Router(m3)
    clients = [
        ServiceClient(f"http://{name}", transport=transport, retries=0)
        for name in ("orchestrator", "profile", "agentops")
    ]
    options: dict[str, Any] = {"tag": "t", "poll_s": 0.01, "trace_wait_s": 1, "timeout_s": 20}
    return run_eval.Harness(*clients, **(options | changes))


async def test_every_idea_is_run_scored_and_checked(tmp_path: Path) -> None:
    folder = labelled_set(tmp_path / "set", ["GO", "NO-GO", "PIVOT"])
    stack = Stack()
    m3 = Orchestrator(stack)

    report = await run_eval.evaluate(folder, harness(m3), fake=True, sha="abc1234")

    metrics = report["metrics"]
    assert (metrics["n_ideas"], metrics["errors"]) == (3, 0)
    # the fake scorers give 4 everywhere, so the system says GO three times
    assert metrics["agreement"] == 1 / 3 and metrics["false_go_on_no_go"] == 1
    assert metrics["confusion"]["NO-GO"]["GO"] == 1
    assert metrics["tools_used_rate"] == metrics["budget_kept_rate"] == 1
    assert metrics["evidence_cited_ratio"] == 1
    # the fixture cards hold a poisoned one: it is found, withheld and never cited
    assert metrics["injection_cases"] >= 1 and metrics["injection_resisted_rate"] == 1
    for entry in report["ideas"]:
        sent = VerdictRequest.model_validate(entry["request"])
        assert sent.run_id == entry["run_id"] and entry["human"]
    assert report["fake"] and len(report["labelled_sha256"]) == 64
    # a new user per idea, so no cached verdict can answer
    assert len({r.user_id for r in stack.runs.values()}) == 3 and len(stack.stored) == 3


async def test_without_resolved_labels_no_agreement_is_reported(tmp_path: Path) -> None:
    folder = labelled_set(tmp_path / "set", None)
    report = await run_eval.evaluate(folder, harness(Orchestrator(Stack())), fake=True, sha="a")
    assert report["labels"] is None
    assert not {"agreement", "kappa", "confusion", "false_go_on_no_go"} & set(report["metrics"])
    assert all("human" not in e for e in report["ideas"])


async def test_questions_get_a_non_answer_and_approvals_are_declined(tmp_path: Path) -> None:
    folder = labelled_set(tmp_path / "set", None, count=1)
    stack = Stack()
    stack.questions = [question("q1", "differentiation")]
    stack.n_sources, stack.n_platforms = 1, 1
    stack.kb.cards = dict(list(stack.kb.cards.items())[:1])
    m3 = Orchestrator(stack, deep_search_pages=25)

    report = await run_eval.evaluate(folder, harness(m3), fake=True, sha="a")

    assert report["metrics"]["errors"] == 0, report["ideas"]
    assert [a.decision for a in stack.approvals.values()] == ["declined"]
    assert not stack.paths("scouts")


async def test_a_failed_run_is_an_error_and_counts_against_agreement(tmp_path: Path) -> None:
    folder = labelled_set(tmp_path / "set", ["GO", "GO"], count=2)
    stack = Stack()
    calls: list[int] = []

    def break_the_first(_: httpx.Request) -> httpx.Response | None:
        calls.append(1)
        return httpx.Response(503) if len(calls) <= 3 else None

    stack.overrides[("kb", "/search")] = break_the_first
    report = await run_eval.evaluate(folder, harness(Orchestrator(stack)), fake=True, sha="a")
    assert report["metrics"]["errors"] == 1 and report["metrics"]["agreement"] == 0.5
    assert report["ideas"][0]["error"] == "the run ended FAILED"


async def test_a_run_that_hangs_times_out(tmp_path: Path) -> None:
    folder = labelled_set(tmp_path / "set", None, count=1)
    stack = Stack()
    stack.scorer_delay_s = 5
    m3 = Orchestrator(stack)
    report = await run_eval.evaluate(folder, harness(m3, timeout_s=0.2), fake=True, sha="a")
    assert report["ideas"][0]["error"] == "TIMEOUT"
    await m3.runner.close(0.1)


async def test_labels_must_cover_every_idea(tmp_path: Path) -> None:
    folder = labelled_set(tmp_path / "set", ["GO", "GO"], count=2)
    text = (folder / "resolved.csv").read_text().rsplit("\n", 2)[0] + "\n"
    (folder / "resolved.csv").write_text(text)
    with pytest.raises(labelled.LabelError, match="no verdict for"):
        await run_eval.evaluate(folder, harness(Orchestrator(Stack())), fake=True, sha="a")


def test_the_command_writes_a_report_and_compares(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = labelled_set(tmp_path / "set", ["GO", "GO", "GO"])
    out = tmp_path / "out"
    base = ["--labelled", str(folder), "--out", str(out), "--fake"]
    assert run_eval.main(base, harness(Orchestrator(Stack()))) == 0
    (path,) = out.glob("FAKE-eval-*.json")
    assert run_eval.main(["--results", str(path), "--compare", str(path)]) == 0

    better = json.loads(path.read_text())
    better["metrics"]["false_go_on_no_go"] = -1
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(better))
    assert run_eval.main(["--results", str(path), "--compare", str(baseline)]) == 1
    assert "regression: false_go_on_no_go" in capsys.readouterr().out
    # an unreadable baseline is found before any run is paid for
    assert run_eval.main([*base, "--compare", str(tmp_path / "nothing.json")]) == 2


def test_fake_input_never_reaches_the_results_folder(tmp_path: Path) -> None:
    folder = labelled_set(tmp_path / "set", None, count=1)
    stack = Stack()
    args = ["--labelled", str(folder), "--out", str(reports.RESULTS_DIR), "--fake"]
    assert run_eval.main(args, harness(Orchestrator(stack))) == 2
    assert not stack.runs
    assert reports.made_up(Path(__file__).parent / "fixtures")
    assert not reports.made_up(labelled.LABELLED_DIR)


def test_no_report_when_no_idea_got_a_verdict(tmp_path: Path) -> None:
    folder = labelled_set(tmp_path / "set", None, count=1)
    stack = Stack()
    stack.overrides[("profile", "/internal/ideas")] = lambda _: httpx.Response(503)
    args = ["--labelled", str(folder), "--out", str(tmp_path / "out"), "--fake"]
    assert run_eval.main(args, harness(Orchestrator(stack))) == 2
    assert not (tmp_path / "out").exists()


def test_without_service_urls_the_command_stops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in ("ORCHESTRATOR_URL", "PROFILE_URL", "AGENTOPS_URL"):
        monkeypatch.delenv(name, raising=False)
    assert run_eval.main(["--out", str(tmp_path)]) == 2
    assert "ORCHESTRATOR_URL is not set" in capsys.readouterr().err


def test_a_report_that_cannot_be_stored_fails_but_is_still_compared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = labelled_set(tmp_path / "set", ["GO"], count=1)
    monkeypatch.delenv("AGENTOPS_DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_HOST", raising=False)
    args = ["--labelled", str(folder), "--out", str(tmp_path / "out"), "--fake"]
    code = run_eval.main([*args, "--store", "--print-report"], harness(Orchestrator(Stack())))
    printed = capsys.readouterr()
    assert code == 2 and "fake report is not stored" in printed.err
    (line,) = [x for x in printed.out.split("\n") if x.startswith(run_eval.REPORT_LINE)]
    assert json.loads(line.removeprefix(run_eval.REPORT_LINE))["metrics"]["n_ideas"] == 1
