"""sample → draft → assemble → review → split → evaluate in a temporary folder (spec Z9 A8).
FAKE knowledge base, FAKE teacher, scripted FAKE reviewer and FAKE models: plumbing only."""

import asyncio
import io
import json
from pathlib import Path

from finetune_factories import fake_candidates, write_labelled

import evaluate as ev
from build_dataset import assemble
from common.contracts.tools import ChatMessage
from common.llm import Cassette, LLMClient
from common.settings import LlmSettings
from dataset import prompt_ids, stable_order, write_jsonl
from draft_labels import draft_labels
from review import review
from split import split


async def no_wait(_: float) -> None:
    return None


def test_pipeline(tmp_path: Path) -> None:
    candidates = asyncio.run(fake_candidates())
    write_jsonl(tmp_path / "candidates.jsonl", candidates)
    # the teacher is asked in the drafting order, so each reply can cite its own prompt
    ordered = sorted(candidates, key=lambda c: (stable_order(c.idea_id), c.dimension))
    replies = [
        {
            "content": json.dumps(
                {
                    "score": float(n % 6),
                    "confidence": 0.5,
                    "evidence_ids": prompt_ids(c.user)[:1],
                    "reason": "FAKE",
                }
            )
        }
        for n, c in enumerate(ordered)
    ]
    llm = LLMClient(LlmSettings(backend="fake"), cassette=Cassette(replies))
    summary = asyncio.run(draft_labels(candidates, tmp_path / "drafts.jsonl", llm, sleep=no_wait))
    assert (summary.labelled, summary.rejected) == (len(candidates), 0)
    listed = write_labelled(tmp_path / "labelled.jsonl", [("FAKE", "volcanoes")])

    passed, built = assemble(
        tmp_path / "candidates.jsonl",
        tmp_path / "drafts.jsonl",
        tmp_path,
        labelled=[],
        target=len(candidates),
        min_rows=12,
    )
    assert passed, built["problems"]
    assert all(built["by_dimension"].values()) and all(built["by_level"].values())

    decided = review(tmp_path, "Zain", ask=lambda _: "a", out=io.StringIO(), limit=8)
    assert decided == 8
    parts = split(tmp_path, listed, min_checked=8, quotas={})
    assert parts["rows"]["test"] == 8 and parts["rows"]["train"] and parts["rows"]["val"]

    async def echo(_: str, messages: list[ChatMessage], __: int) -> ev.Reply:
        reply = {
            "score": 3.0,
            "confidence": 0.5,
            "evidence_ids": prompt_ids(messages[1].content)[:1],
            "reason": "FAKE",
        }
        return ev.Reply(json.dumps(reply), "FAKE-model", 1, 1)

    report = asyncio.run(ev.evaluate(tmp_path, dict.fromkeys(ev.MODELS, echo)))
    assert report["fake"] is True and report["test_set"]["rows"] == 8
    assert report["models"]["tuned"]["real_evidence_id_rate"] == 1.0
    assert "FAKE: made from fake data" in ev.markdown(report)
