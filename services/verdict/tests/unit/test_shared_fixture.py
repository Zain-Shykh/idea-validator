"""The verdict card in the shared Validate fixture (used by the UI and gateway fakes) is what the
engine really returns for its scores, so the fake cannot drift from the rules.
"""

import json
from typing import Any

from verdict_factories import CONFIG_PATH, body

from common.contracts.core import VerdictRequest
from verdict.config import VerdictConfig
from verdict.engine import decide

FIXTURE = CONFIG_PATH.parents[1] / "eval" / "fixtures" / "run_events_validate.jsonl"


def fixture_card() -> dict[str, Any]:
    events = [json.loads(line) for line in FIXTURE.read_text().splitlines()]
    (event,) = [e for e in events if e["type"] == "verdict"]
    card: dict[str, Any] = event["data"]["verdict"]
    return card


def request_for(card: dict[str, Any]) -> VerdictRequest:
    data = body(n_sources=4)
    data.update(idea_id=card["idea_id"], run_id=card["run_id"], scores=card["scores"])
    data.update(kb_version=card["versions"]["kb_version"])
    return VerdictRequest.model_validate(data)


def test_fixture_card_is_the_engine_output(config: VerdictConfig) -> None:
    card = fixture_card()
    result = decide(request_for(card), config)
    assert result.model_dump(mode="json", by_alias=True) == card
