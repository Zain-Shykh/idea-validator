"""Per-request correlation ids (architecture §21.2), carried in context variables."""

import re
from collections.abc import Mapping
from contextvars import ContextVar

REQUEST_ID_HEADER = "X-Request-Id"
RUN_ID_HEADER = "X-Run-Id"
PARENT_STEP_HEADER = "X-Parent-Step"
USER_ID_HEADER = "X-User-Id"

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
run_id_var: ContextVar[str | None] = ContextVar("run_id", default=None)
parent_step_var: ContextVar[int | None] = ContextVar("parent_step", default=None)
user_id_var: ContextVar[str | None] = ContextVar("user_id", default=None)

_SAFE_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}")


def safe_id(value: str | None) -> str | None:
    """Header values are untrusted: anything unusual is dropped."""
    return value if value and _SAFE_ID.fullmatch(value) else None


def outgoing_headers() -> dict[str, str]:
    """Headers to send to the next service."""
    parent = parent_step_var.get()
    values: Mapping[str, str | None] = {
        REQUEST_ID_HEADER: request_id_var.get(),
        RUN_ID_HEADER: run_id_var.get(),
        PARENT_STEP_HEADER: None if parent is None else str(parent),
        USER_ID_HEADER: user_id_var.get(),
    }
    return {name: value for name, value in values.items() if value is not None}
