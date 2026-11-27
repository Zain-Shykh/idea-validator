"""The three-way comparison (spec Z9 A4 10). The "models" here are hand-written FAKE replies in
temporary folders: the numbers only check the arithmetic and say nothing about any model."""

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from finetune_factories import check, grid, write_checks, write_dataset, write_labelled

import evaluate as ev
from common.contracts.tools import ChatMessage
from common.llm import Cassette, LLMClient
from common.settings import LlmSettings
from dataset import DATA_DIR, RESULTS_DIR, DataError, read_checks
from scorer.services.backend import LocalModel, LocalModelError
from split import split


def make_test_set(folder: Path) -> list[ev.Row]:
    """A split of FAKE rows with four test rows, each with human score 3."""
    rows = grid(12)
    write_dataset(folder, rows)
    write_checks(folder / "checked.csv", [check(r, "Zain", 3.0, "fix") for r in rows[:4]])
    listed = write_labelled(folder / "l.jsonl", [("x", "volcanoes")])
    split(folder, listed, min_checked=4, quotas={})
    return ev.load_test_set(folder)[0]


def answer(row: ev.Row, score: float, ids: list[str] | None = None) -> str:
    return json.dumps(
        {
            "score": score,
            "confidence": 0.5,
            "evidence_ids": row.allowed[:1] if ids is None else ids,
            "reason": "FAKE",
        }
    )


def scripted(replies: dict[str, Any], model: str, cost: float = 0.0) -> ev.Ask:
    async def ask(item: str, _: list[ChatMessage], __: int) -> ev.Reply:
        reply = replies[item]
        if isinstance(reply, Exception):
            raise reply
        return ev.Reply(reply, model, 100, 20, cost)

    return ask


class Clock:
    """Time that passes only in model calls and sleeps."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def taking(self, ask: ev.Ask, *durations: float) -> ev.Ask:
        left = list(durations)

        async def slow(item: str, messages: list[ChatMessage], n: int) -> ev.Reply:
            self.now += left.pop(0) if len(left) > 1 else left[0]
            return await ask(item, messages, n)

        return slow


def run(folder: Path, asks: dict[str, ev.Ask], **kwargs: Any) -> dict[str, Any]:
    return asyncio.run(ev.evaluate(folder, asks, **kwargs))


def three(rows: list[ev.Row]) -> dict[str, ev.Ask]:
    ids = [r.item_id for r in rows]
    base = {
        ids[0]: answer(rows[0], 3.0),  # error 0
        ids[1]: answer(rows[1], 5.0),  # error 2
        ids[2]: "not json",
        ids[3]: answer(rows[3], 2.0, [rows[3].allowed[0], "card_invented#c9"]),  # error 1
    }
    tuned = {
        ids[0]: answer(rows[0], 4.5),  # error 1.5: worse than base
        ids[1]: answer(rows[1], 3.0),
        ids[2]: f"```json\n{answer(rows[2], 3.5)}\n```",  # fenced, as serving accepts it
        ids[3]: LocalModelError("boom"),
    }
    hosted = {r.item_id: answer(r, 3.0, [f"[{r.allowed[0]}]"]) for r in rows}
    return {
        "base": scripted(base, "FAKE-base"),
        "tuned": scripted(tuned, "FAKE-tuned"),
        "hosted": scripted(hosted, "FAKE-hosted", cost=0.001),
    }


def test_the_six_metrics(tmp_path: Path) -> None:
    rows = make_test_set(tmp_path)
    clock = Clock()
    asks = three(rows)
    asks["base"] = clock.taking(asks["base"], 1.0, 2.0, 3.0, 10.0)
    report = run(tmp_path, asks, clock=clock)
    base, tuned, hosted = (report["models"][m] for m in ev.MODELS)
    assert (base["valid"], base["mae"], base["within_1"]) == (3, 1.0, pytest.approx(2 / 3))
    assert base["valid_json_rate"] == 0.75
    # 4 ids cited over the valid replies, 3 of them in their prompt
    assert base["real_evidence_id_rate"] == 0.75
    assert (base["latency_p50_s"], base["latency_mean_s"]) == (2.5, 4.0)
    assert (base["cost_per_score_usd"], base["tokens_in_per_score"]) == (0.0, 100)
    # a failed call is an error, not invalid JSON, and has no latency
    assert (tuned["errors"], tuned["replies"], tuned["valid_json_rate"]) == (1, 3, 1.0)
    assert tuned["mae"] == pytest.approx((1.5 + 0 + 0.5) / 3)
    assert (hosted["mae"], hosted["real_evidence_id_rate"]) == (0.0, 1.0)
    assert hosted["cost_per_score_usd"] == pytest.approx(0.001)
    # only the rows all three scored: the first two
    assert report["rows_all_models_scored"] == 2
    assert base["mae_on_rows_all_models_scored"] == 1.0
    assert tuned["mae_on_rows_all_models_scored"] == 0.75
    case = report["did_not_help"]
    assert (case["item_id"], case["kind"]) == (rows[0].item_id, "worse than base")
    assert report["fake"] is True and len(report["rows"]) == 4


def test_markdown_table_leaves_the_explanation_to_a_person(tmp_path: Path) -> None:
    rows = make_test_set(tmp_path)
    report = run(tmp_path, three(rows))
    text = ev.markdown(report)
    for title in (
        "MAE vs human", "Within 1 point", "Valid-JSON rate", "Real-evidence-id rate",
        "Latency p50", "API cost per score",
    ):  # fmt: skip
        assert f"| {title}" in text
    assert "| Metric | base | tuned | hosted |" in text
    assert "FAKE: made from fake data" in text and "Known bias" in text
    assert ev.TODO in text and rows[0].item_id in text


def test_markdown_cannot_be_injected_by_a_reply(tmp_path: Path) -> None:
    rows = make_test_set(tmp_path)
    report = run(tmp_path, three(rows))
    report["did_not_help"]["replies"]["tuned"]["raw"] = "a\n<script>x</script>\n# H\x1b[2J"
    text = ev.markdown(report)
    assert "\n    <script>x</script>\n    # H[2J\n" in text and "\x1b" not in text


def case_for(base: float | str, tuned: float | str) -> dict[str, Any] | None:
    row = ev.Row("a", "risk", [ChatMessage(role="user", content="u")] * 2, [], 3.0)

    def result(value: float | str) -> dict[str, Any]:
        if value == "error":
            return {"error": "x"}
        if value == "invalid":
            return {"valid": False, "raw": "?"}
        return {"valid": True, "score": value}

    results = {"base": {"a": result(base)}, "tuned": {"a": result(tuned)}}
    results["hosted"] = results["base"]
    return ev.did_not_help([row], results)


@pytest.mark.parametrize(
    ("base", "tuned", "kind"),
    [
        (3.0, 1.0, "worse than base"),
        (3.0, "invalid", "worse than base"),
        (1.0, 1.0, "no better than base"),
        (1.0, 3.0, None),  # it helped
        (3.0, 3.0, None),  # both right
        ("error", 1.0, None),
        (3.0, "error", None),
    ],
)
def test_did_not_help_case(base: float | str, tuned: float | str, kind: str | None) -> None:
    case = case_for(base, tuned)
    assert (case and case["kind"]) == kind


def test_waits_between_calls_are_not_latency(tmp_path: Path) -> None:
    rows = make_test_set(tmp_path)
    waited: list[float] = []
    clock = Clock()

    async def sleep(seconds: float) -> None:
        waited.append(seconds)
        clock.now += seconds

    asks = three(rows)
    asks["hosted"] = clock.taking(asks["hosted"], 2.0)
    report = run(tmp_path, asks, waits={"hosted": 6.0}, sleep=sleep, clock=clock)
    assert waited == [6.0] * 3
    assert report["models"]["hosted"]["latency_mean_s"] == 2.0


def test_a_model_without_any_reply_gives_no_report(tmp_path: Path) -> None:
    rows = make_test_set(tmp_path)
    asks = three(rows)
    asks["tuned"] = scripted({r.item_id: LocalModelError("refused") for r in rows}, "x")
    with pytest.raises(DataError, match="tuned: no reply"):
        run(tmp_path, asks)


@pytest.mark.parametrize("case", ["unchecked", "edited_label", "no_checks", "twice", "empty"])
def test_only_a_hand_checked_test_set_is_accepted(tmp_path: Path, case: str) -> None:
    make_test_set(tmp_path)
    path, checks = tmp_path / "test.jsonl", tmp_path / "checked.csv"
    lines = path.read_text().splitlines(keepends=True)
    if case == "unchecked":
        train = (tmp_path / "train.jsonl").read_text().splitlines(keepends=True)[0]
        path.write_text("".join([*lines, train]))
    elif case == "edited_label":
        path.write_text(path.read_text().replace('\\"score\\":3.0', '\\"score\\":4.0', 1))
    elif case == "no_checks":
        assert read_checks(checks)
        checks.unlink()
    elif case == "twice":
        path.write_text("".join([*lines, lines[0]]))
    else:
        path.write_text("")
    with pytest.raises(DataError):
        ev.load_test_set(tmp_path)


def test_local_ask_is_one_unconstrained_call_unless_asked() -> None:
    bodies: list[dict[str, Any]] = []

    def serve(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "nonsense"}}]})

    async def go() -> None:
        server = LocalModel(
            "http://model.test", "m", timeout_s=5, transport=httpx.MockTransport(serve)
        )
        messages = [ChatMessage(role="user", content="u")]
        await ev.local_ask(server, "m", 5, constrained=False)("a", messages, 2)
        await ev.local_ask(server, "m", 5, constrained=True)("a", messages, 2)
        await server.aclose()

    asyncio.run(go())
    assert len(bodies) == 2
    assert "response_format" not in bodies[0]
    assert bodies[1]["response_format"]["type"] == "json_schema"


def test_hosted_ask_never_repairs() -> None:
    """One cassette line: a repair retry would need a second one."""
    cassette = Cassette([{"content": "not json"}, {"content": "second"}])
    llm = LLMClient(LlmSettings(backend="fake"), cassette=cassette)
    reply = asyncio.run(ev.hosted_ask(llm)("a", [ChatMessage(role="user", content="u")], 1))
    assert reply.content == "not json"
    assert cassette.next("scorer-hosted", ev.AGENT)["content"] == "second"


def command(folder: Path, out: Path, *extra: str) -> int:
    args = ["--data", str(folder), "--out", str(out), "--rpm", "100000"]
    return ev.main([*args, "--base-url", "http://b.test", "--tuned-url", "http://t.test", *extra])


def test_command_writes_both_files_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rows = make_test_set(tmp_path)
    cassette = tmp_path / "cassette.jsonl"
    lines = [json.dumps({"content": answer(r, 3.0)}) for r in rows]
    cassette.write_text("\n".join(lines * 2))
    monkeypatch.setenv("LLM_BACKEND", "fake")
    monkeypatch.setenv("LLM_CASSETTE_PATH", str(cassette))
    asks = three(rows)
    monkeypatch.setattr(ev, "local_ask", lambda server, name, *_: asks["base"])
    out = tmp_path / "out"
    assert command(tmp_path, out) == 0
    report = json.loads((out / "finetune-comparison.json").read_text())
    assert report["fake"] is True and report["local_decoding"] == "unconstrained"
    assert "| Metric |" in (out / "finetune-comparison.md").read_text()
    # a written explanation is not overwritten by accident
    assert command(tmp_path, out) == 1
    assert "--force" in capsys.readouterr().err
    assert command(tmp_path, out, "--force") == 0


@pytest.mark.parametrize("target", [RESULTS_DIR, DATA_DIR])
def test_command_refuses_fake_results_in_real_places(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: Path
) -> None:
    make_test_set(tmp_path)
    monkeypatch.setenv("LLM_BACKEND", "fake")
    monkeypatch.setenv("LLM_CASSETTE_PATH", str(write_labelled(tmp_path / "c.jsonl", [])))
    before = sorted(target.glob("*"))
    assert command(tmp_path, target) == 1
    assert sorted(target.glob("*")) == before


def test_command_without_a_test_set(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert command(tmp_path, tmp_path / "out") == 1
    assert "test.jsonl" in capsys.readouterr().err
