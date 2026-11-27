"""Reads the run facts that producers write into decision records (``contracts.ops``)."""

from pydantic import ValidationError

from common.contracts.base import ContractModel
from common.contracts.core import TraceKind, TraceRecord
from common.contracts.ops import OPS_EVENT_PAYLOADS, OpsEvent


class BadEventError(ValueError):
    pass


def parse_event(record: TraceRecord) -> ContractModel | None:
    """The payload of an ops event; None for any other record."""
    if record.kind is not TraceKind.DECISION or record.tool not in set(OpsEvent):
        return None
    try:
        payload = OPS_EVENT_PAYLOADS[OpsEvent(record.tool)].model_validate_json(record.output or "")
    except ValidationError as exc:
        raise BadEventError(f"bad {record.tool} payload") from exc
    # Postgres rejects NUL in text and JSONB; one such value would fail every retry
    if "\\u0000" in payload.model_dump_json():
        raise BadEventError(f"NUL character in {record.tool} payload")
    return payload
