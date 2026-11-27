"""Builders for the fine-tuning tests. Everything here is FAKE: hand-made cards, a fake knowledge
base and made-up labels that only exercise the code. None of it is data or a result."""

import json
from datetime import UTC, date, datetime
from pathlib import Path

from build_dataset import sample
from common.contracts.agents import ScorerOutput
from common.contracts.core import Dimension
from common.fakes.kb import FakeKB
from dataset import ROOT, Candidate, Check, Draft, append_check, write_jsonl

CARDS = (ROOT / "eval/fixtures/cards_small.jsonl", ROOT / "eval/fixtures/validator_cards.jsonl")
TODAY = date(2026, 11, 27)
NOW = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)

_SUBJECTS = (
    "astronomy", "beekeeping", "carpentry", "drumming", "embroidery", "falconry", "gardening",
    "horology", "origami", "juggling", "kayaking", "lacemaking", "mycology", "numismatics",
    "orienteering", "pottery", "quilting", "robotics", "sailing", "topiary",
)  # fmt: skip


def fake_kb() -> FakeKB:
    return FakeKB.from_jsonl(*CARDS)


async def fake_candidates(max_ideas: int = 12) -> list[Candidate]:
    return await sample(fake_kb(), analyst=None, max_ideas=max_ideas, fake=True, today=TODAY)


def candidate(idea: int, dimension: Dimension, *, title: str | None = None) -> Candidate:
    subject = _SUBJECTS[idea % len(_SUBJECTS)] + ("x" * (idea // len(_SUBJECTS)))
    title = title or f"FAKE {subject} helper"
    text = f"FAKE idea about {subject} {subject}club {subject}diary for {subject}fans"
    evidence_id = f"card_fake{idea:04d}#c0"
    system = f"Score the idea on {dimension.value} using rubric {dimension.value}@v1."
    user = (
        f"<idea>\n{title}\n{text}\n</idea>\nEvidence:\n"
        f'<evidence id="{evidence_id}" source="fake">FAKE quote {idea}</evidence>'
    )
    from dataset import item_id

    return Candidate(
        item_id=item_id(system, user),
        idea_id=f"idea_fake{idea:04d}",
        origin="card",
        idea_title=title,
        idea_text=text,
        dimension=dimension,
        rubric_id=f"{dimension.value}@v1",
        evidence_ids=[evidence_id],
        system=system,
        user=user,
        kb_version="fake-kb",
        built_on=TODAY,
        fake=True,
    )


def label(row: Candidate, score: float = 3.0) -> ScorerOutput:
    return ScorerOutput(
        score=score, confidence=0.5, evidence_ids=row.evidence_ids[:1], reason="FAKE label"
    )


def draft(row: Candidate, score: float = 3.0) -> Draft:
    return Draft(
        item_id=row.item_id, label=label(row, score), model="fake", drafted_at=NOW, fake=True
    )


def grid(ideas: int) -> list[Candidate]:
    """``ideas`` x six dimensions."""
    return [candidate(i, d) for i in range(ideas) for d in Dimension]


def write_inputs(folder: Path, rows: list[Candidate]) -> tuple[Path, Path]:
    """Candidates and drafts whose scores run through 0..5."""
    candidates, drafts = folder / "candidates.jsonl", folder / "drafts.jsonl"
    write_jsonl(candidates, rows)
    write_jsonl(drafts, [draft(r, float((i + i // 6) % 6)) for i, r in enumerate(rows)])
    return candidates, drafts


def write_labelled(path: Path, ideas: list[tuple[str, str]]) -> Path:
    path.write_text("".join(json.dumps({"title": t, "text": x}) + "\n" for t, x in ideas))
    return path


def check(row: Candidate, who: str, score: float = 3.0, action: str = "accept") -> Check:
    return Check(
        item_id=row.item_id,
        checked_by=who,
        checked_at=NOW,
        action=action,
        label=None if action == "reject" else label(row, score),
    )


def write_checks(path: Path, checks: list[Check]) -> None:
    for entry in checks:
        append_check(path, entry)
