"""Teacher drafts (spec Z9 A4 3). A cassette stands in for the teacher: replies are made up."""

import io
import json
from pathlib import Path

import pytest
from finetune_factories import grid

from common.llm import Cassette, LLMClient, LLMUnavailableError
from common.settings import LlmSettings
from common.trace import TraceClient
from dataset import DATA_DIR, DataError, Draft, grounded_label, read_jsonl, write_jsonl
from draft_labels import draft_labels, main


def reply(ids: list[str], **changes: object) -> str:
    body = {"score": 3.5, "confidence": 0.6, "evidence_ids": ids, "reason": "FAKE reply"}
    return json.dumps(body | changes)


def teacher(replies: list[str]) -> LLMClient:
    return LLMClient(
        LlmSettings(LLM_BACKEND="fake"),
        cassette=Cassette([{"content": r} for r in replies]),
        tracer=TraceClient(stream=io.StringIO()),
    )


class Naps(list[float]):
    async def __call__(self, seconds: float) -> None:
        self.append(seconds)


def in_order(rows):  # type: ignore[no-untyped-def]
    """The order ``draft_labels`` asks in."""
    from dataset import stable_order

    return sorted(rows, key=lambda c: (stable_order(c.idea_id), c.dimension))


async def test_drafts_are_rate_limited_and_resumable(tmp_path: Path) -> None:
    rows = grid(1)
    out = tmp_path / "drafts.jsonl"
    first = in_order(rows)
    naps = Naps()
    summary = await draft_labels(
        rows, out, teacher([reply(r.evidence_ids) for r in first[:2]]),
        rpm=6, max_calls=2, sleep=naps,
    )  # fmt: skip
    assert (summary.calls, summary.labelled, summary.remaining) == (2, 2, 4)
    assert naps == [10.0] and summary.stopped and "max-calls" in summary.stopped
    summary = await draft_labels(
        rows, out, teacher([reply(r.evidence_ids) for r in first[2:]]), sleep=Naps()
    )
    assert (summary.calls, summary.remaining, summary.stopped) == (4, 0, None)
    drafts = read_jsonl(out, Draft)
    assert [d.item_id for d in drafts] == [r.item_id for r in first]
    assert all(d.fake and d.label and d.label.score == 3.5 for d in drafts)
    # nothing left: the teacher is not asked again
    assert (await draft_labels(rows, out, teacher([]), sleep=Naps())).calls == 0


@pytest.mark.parametrize(
    ("changes", "problem"),
    [
        ({"evidence_ids": ["card_invented#c0"]}, "not in the prompt"),
        ({"evidence_ids": []}, "cites no evidence"),
        ({"reason": "see card_invented#c3"}, "reason names a card"),
        ({"score": 3.3}, "reply format"),
        ({"score": 6}, "reply format"),
        ({"verdict": "GO"}, "reply format"),
    ],
)
async def test_invented_evidence_id_is_not_kept(
    tmp_path: Path, changes: dict[str, object], problem: str
) -> None:
    row = grid(1)[0]
    out = tmp_path / "drafts.jsonl"
    # one reply only: no repair call is made
    summary = await draft_labels(
        [row], out, teacher([reply(row.evidence_ids, **changes)]), sleep=Naps()
    )
    (saved,) = read_jsonl(out, Draft)
    assert summary.rejected == 1 and saved.label is None and problem in (saved.problem or "")


def test_a_real_id_mixed_with_an_invented_one_is_rejected() -> None:
    with pytest.raises(DataError):
        grounded_label(reply(["card_a#c0", "card_b#c0"]), ["card_a#c0"])


def test_fenced_or_bare_text() -> None:
    with pytest.raises(DataError):
        grounded_label("I think it is a 4", ["card_a#c0"])


class Down:
    settings = LlmSettings(LLM_BACKEND="fake")

    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, *_: object, **__: object) -> None:
        self.calls += 1
        raise LLMUnavailableError("429")


async def test_unavailable_teacher_backs_off_then_stops(tmp_path: Path) -> None:
    down, naps = Down(), Naps()
    out = tmp_path / "drafts.jsonl"
    summary = await draft_labels(grid(1), out, down, rpm=60, sleep=naps)  # type: ignore[arg-type]
    assert down.calls == 3 and not out.exists()
    assert summary.stopped and "unavailable" in summary.stopped and summary.remaining == 6
    assert naps == [60.0, 1.0, 120.0, 1.0]


def test_command_uses_the_fake_backend_from_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    row = grid(1)[0]
    candidates = tmp_path / "candidates.jsonl"
    write_jsonl(candidates, [row])
    cassette = tmp_path / "cassette.jsonl"
    cassette.write_text(json.dumps({"content": reply(row.evidence_ids)}) + "\n")
    monkeypatch.setenv("LLM_BACKEND", "fake")
    monkeypatch.setenv("LLM_CASSETTE_PATH", str(cassette))
    args = ["--candidates", str(candidates), "--out", str(tmp_path / "drafts.jsonl")]
    assert main(args) == 0
    assert "labelled 1" in capsys.readouterr().out
    assert "FAKE reply" not in (tmp_path / "drafts.trace.log").read_text()
    # and never into the real data folder
    assert main([*args[:2], "--out", str(DATA_DIR / "drafts.jsonl")]) == 1
    assert not (DATA_DIR / "drafts.jsonl").exists()
