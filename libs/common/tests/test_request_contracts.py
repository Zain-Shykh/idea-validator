"""Cross-field rules of request models."""

from typing import Any

import pytest
from pydantic import ValidationError

from common.contracts.core import PendingInput, VerdictRequest
from common.contracts.orchestrator import StartRunRequest
from common.contracts.verdict import WhatIfRequest


def test_verdict_request_valid(verdict_request: dict[str, Any]) -> None:
    assert VerdictRequest.model_validate(verdict_request)


@pytest.mark.parametrize(
    "change",
    [
        {"goal": "course_project"},
        {"accepted_objections": [
            {"objection_id": "o1", "dimension": "risk", "text": "t", "evidence_ids": []}
        ]},
        {"scores": []},
    ],
)  # fmt: skip
def test_verdict_request_rejects(
    change: dict[str, object], verdict_request: dict[str, Any]
) -> None:
    with pytest.raises(ValidationError):
        VerdictRequest.model_validate({**verdict_request, **change})


def test_whatif_apply_needs_changes(verdict_request: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        WhatIfRequest.model_validate({**verdict_request, "mode": "apply"})


def test_whatif_is_a_flat_verdict_request(verdict_request: dict[str, Any]) -> None:
    assert WhatIfRequest.model_validate({**verdict_request, "mode": "search"}).mode == "search"
    with pytest.raises(ValidationError):  # the inherited rules still apply
        WhatIfRequest.model_validate({**verdict_request, "mode": "search", "scores": []})


def test_validate_run_needs_idea_version(profile: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        StartRunRequest.model_validate({"workflow": "validate", "user_id": "u", "profile": profile})


def test_pending_input_payload_matches_kind() -> None:
    with pytest.raises(ValidationError):
        PendingInput(kind="approval")
