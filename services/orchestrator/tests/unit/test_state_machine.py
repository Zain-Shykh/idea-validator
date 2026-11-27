"""Every transition of §6.1 and §6.2, written out again here (spec Z7, A4 #1)."""

from itertools import product

import pytest

from common.contracts.core import RunState
from orchestrator.services.state_machine import (
    TERMINAL,
    InvalidTransitionError,
    Step,
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
