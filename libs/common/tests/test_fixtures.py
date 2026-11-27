"""The shared fixtures (used by both tracks' fakes) match the contracts."""

import hashlib
import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from common.contracts.core import IdeaCard, RunEvent, RunEventType, Verdict
from common.contracts.export import DEFAULT_DIR
from common.contracts.ops import OpsSummary

FIXTURES = Path(__file__).resolve().parents[3] / "eval" / "fixtures"


def lines(name: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (FIXTURES / name).read_text().splitlines()]


def schema(name: str) -> dict[str, Any]:
    return json.loads((DEFAULT_DIR / f"{name}.json").read_text())


@pytest.fixture(scope="module")
def cards() -> list[IdeaCard]:
    return [IdeaCard.model_validate_json(json.dumps(c)) for c in lines("cards_small.jsonl")]


def test_cards_cover_three_sources_with_unique_conventional_ids(cards: list[IdeaCard]) -> None:
    assert len(cards) >= 60
    assert {c.source for c in cards} >= {"hackernews", "github", "yc"}
    assert len({c.id for c in cards}) == len(cards)
    for card in cards:
        assert card.id == "card_" + hashlib.sha1(card.url.encode()).hexdigest()[:8]


def test_exactly_one_poisoned_card(cards: list[IdeaCard]) -> None:
    poisoned = [c for c in cards if "ignore all previous instructions" in c.text.lower()]
    assert len(poisoned) == 1


def test_run_events_match_contract_and_cite_existing_cards(cards: list[IdeaCard]) -> None:
    raw = lines("run_events_validate.jsonl")
    for event in raw:
        jsonschema.validate(event, schema("core.RunEvent"))
    events = [RunEvent.model_validate_json(json.dumps(e)) for e in raw]
    assert [e.seq for e in events] == list(range(len(events)))
    assert events[0].type is RunEventType.RUN_STARTED
    assert events[-1].type is RunEventType.RUN_FINISHED
    (card_event,) = [e for e in events if e.type is RunEventType.VERDICT]
    assert card_event.data is not None
    verdict = Verdict.model_validate_json(json.dumps(card_event.data["verdict"]))
    known = {c.id for c in cards}
    cited = {i.split("#")[0] for s in verdict.scores for i in s.evidence_ids}
    assert cited and cited <= known


def test_ops_summary_matches_contract() -> None:
    raw = json.loads((FIXTURES / "ops_summary.json").read_text())
    jsonschema.validate(raw, schema("ops.OpsSummary"))
    summary = OpsSummary.model_validate(raw)
    assert sum(summary.verdict_counts.values()) == summary.verdicts_total
