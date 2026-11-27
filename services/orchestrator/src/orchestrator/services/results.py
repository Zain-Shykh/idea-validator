"""The run result: the card and one code-written line about it."""

from common.contracts.core import RunResult, Verdict


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


def run_result(card: Verdict) -> RunResult:
    return RunResult(verdicts=[card], reasons={card.idea_id: reason_line(card)})
