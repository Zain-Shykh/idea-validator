"""Every transition of §6.1 and §6.2, written out again here (spec Z7, A4 #1)."""

from itertools import product

import pytest

from common.contracts.core import RunState
from orchestrator.services.state_machine import (
    TERMINAL,
    DiscoverStep,
    InvalidTransitionError,
    Step,
    check_discover_transition,
    check_run_transition,
    check_step_transition,
)

LIFECYCLE = {  # architecture §6.1
    ("QUEUED", "RUNNING"),
    ("RUNNING", "AWAITING_INPUT"),
    ("AWAITING_INPUT", "RUNNING"),
    ("RUNNING", "AWAITING_APPROVAL"),
    ("AWAITING_APPROVAL", "RUNNING"),
    ("RUNNING", "COMPLETED"),
    ("RUNNING", "BUDGET_STOPPED"),
    ("RUNNING", "FAILED"),
    ("AWAITING_INPUT", "CANCELLED"),
    ("AWAITING_APPROVAL", "CANCELLED"),
}
VALIDATE = {  # architecture §6.2, plus the cache shortcut of §6.3
    ("INTAKE", "EVIDENCE"),
    ("INTAKE", "CARD"),
    ("EVIDENCE", "DEEP_SEARCH"),
    ("EVIDENCE", "FEATURES"),
    ("DEEP_SEARCH", "EVIDENCE"),
    ("FEATURES", "SCORING"),
    ("SCORING", "PRELIM_VERDICT"),
    ("PRELIM_VERDICT", "CLARIFY"),
    ("PRELIM_VERDICT", "CHALLENGE"),
    ("CLARIFY", "SCORING"),
    ("CHALLENGE", "VERDICT"),
    ("VERDICT", "STABILITY"),
    ("STABILITY", "CARD"),
}


DISCOVER = {  # architecture §6.4; the three shortcuts are in spec Z8 A6/A7
    ("REQUEST", "NICHES"),
    ("NICHES", "IDEATE"),
    ("NICHES", "RESULT"),
    ("IDEATE", "GAP_ANALYZE"),
    ("IDEATE", "VALIDATE_EACH"),
    ("GAP_ANALYZE", "SKEPTIC"),
    ("SKEPTIC", "CRITIC"),
    ("CRITIC", "FILTER"),
    ("FILTER", "IDEATE"),
    ("FILTER", "VALIDATE_EACH"),
    ("VALIDATE_EACH", "RESULT"),
}


@pytest.mark.parametrize(("current", "new"), list(product(DiscoverStep, DiscoverStep)))
def test_discover_transitions(current: DiscoverStep, new: DiscoverStep) -> None:
    if (current, new) in DISCOVER:
        check_discover_transition(current, new)
    else:
        with pytest.raises(InvalidTransitionError):
            check_discover_transition(current, new)


@pytest.mark.parametrize(("current", "new"), list(product(RunState, RunState)))
def test_run_transitions(current: RunState, new: RunState) -> None:
    if (current, new) in LIFECYCLE:
        check_run_transition(current, new)
    else:
        with pytest.raises(InvalidTransitionError):
            check_run_transition(current, new)


@pytest.mark.parametrize(("current", "new"), list(product(Step, Step)))
def test_step_transitions(current: Step, new: Step) -> None:
    if (current, new) in VALIDATE:
        check_step_transition(current, new)
    else:
        with pytest.raises(InvalidTransitionError):
            check_step_transition(current, new)


def test_terminal_states() -> None:
    assert {"COMPLETED", "FAILED", "BUDGET_STOPPED", "CANCELLED"} == TERMINAL
