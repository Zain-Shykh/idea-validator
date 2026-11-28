"""Base model and constrained types shared by all contract files."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class ContractModel(BaseModel):
    """Unknown fields and NaN/inf are errors, so typos and bad scores never pass silently."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


CARD_ID_PATTERN = r"^card_[A-Za-z0-9]+$"
EVIDENCE_ID_PATTERN = r"^card_[A-Za-z0-9]+#c\d+$"

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
CardId = Annotated[str, StringConstraints(pattern=CARD_ID_PATTERN)]
EvidenceId = Annotated[str, StringConstraints(pattern=EVIDENCE_ID_PATTERN)]
Score = Annotated[float, Field(ge=0, le=5)]
Score100 = Annotated[float, Field(ge=0, le=100)]
Unit = Annotated[float, Field(ge=0, le=1)]
Count = Annotated[int, Field(ge=0)]
Amount = Annotated[float, Field(ge=0)]
