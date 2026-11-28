"""The run result: the cards, ranked, and one code-written line about each."""

from collections.abc import Iterable

from common.contracts.core import RunResult, RunState, Verdict, VerdictLabel
from orchestrator.services.state_machine import BaseCheckpoint, Checkpoint, DiscoverCheckpoint

# U2: the order of Discover's list
RANK = (
    VerdictLabel.GO,
    VerdictLabel.PIVOT,
    VerdictLabel.NO_GO,
    VerdictLabel.INSUFFICIENT_EVIDENCE,
)


def reason_line(card: Verdict) -> str:
    if card.missing:
        why = f"missing {card.missing[0]}"
    elif card.gate_details:
        why = next(iter(card.gate_details.values()))
    elif card.weakest_dimension:
        why = f"weakest dimension: {card.weakest_dimension.value.replace('_', ' ')}"
    else:
        why = card.stability.value.lower()
    return f"{card.verdict.value}: {why}"


def run_result(*cards: Verdict) -> RunResult:
    return RunResult(verdicts=list(cards), reasons={c.idea_id: reason_line(c) for c in cards})


def ranked(cards: Iterable[Verdict]) -> list[Verdict]:
    """GO > PIVOT > NO-GO > INSUFFICIENT_EVIDENCE, then score (none last), then confidence."""
    return sorted(
        cards,
        key=lambda c: (RANK.index(c.verdict), c.score is None, -(c.score or 0), -c.confidence),
    )


def result_of(cp: BaseCheckpoint) -> RunResult | None:
    """What a completed run shows."""
    if cp.status is not RunState.COMPLETED:
        return None
    if isinstance(cp, DiscoverCheckpoint):
        return cp.result
    assert isinstance(cp, Checkpoint)
    return None if cp.verdict is None else run_result(cp.verdict)
