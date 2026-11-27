"""Cache keys (architecture §8.4). Every version that can change a result is part of the key
(M2-4, AVOID-2), so all arguments are required and keyword-only.
"""

import hashlib
import json
from collections.abc import Mapping
from datetime import date
from typing import Any

from pydantic import BaseModel


def _jsonable(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", by_alias=True)
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"cannot build a stable cache key from {type(value).__name__}")


def digest(value: object) -> str:
    """sha256 of canonical JSON."""
    canonical = json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=_jsonable, allow_nan=False
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def verdict_key(
    *,
    idea_text: str,
    user_id: str,
    profile_version: int,
    rubric_set: str,
    engine_version: str,
    weights_version: str,
    kb_version: str,
    prompt_versions: Mapping[str, str],
    scorer_model: str,
) -> str:
    """``user_id`` is needed because ``profile_version`` is counted per user."""
    parts = {
        "idea_text": digest(" ".join(idea_text.split())),
        "user_id": user_id,
        "profile_version": profile_version,
        "rubric_set": rubric_set,
        "engine_version": engine_version,
        "weights_version": weights_version,
        "kb_version": kb_version,
        "prompt_versions": dict(prompt_versions),
        "scorer_model": scorer_model,
    }
    return f"cache:verdict:{digest(parts)}"


def step_key(step: str, inputs: Mapping[str, Any]) -> str:
    """Memo key to re-run only what changed (M3-5). ``inputs`` must include relevant versions."""
    return f"cache:step:{step}:{digest(dict(inputs))}"


def retrieval_key(
    *,
    query: str,
    filters: Mapping[str, Any] | BaseModel,
    k: int,
    rerank: bool,
    index_version: str,
) -> str:
    raw = filters.model_dump(mode="json") if isinstance(filters, BaseModel) else dict(filters)
    # list filters are sets: their order must not change the key; unset equals absent
    normalised = {
        name: sorted(value) if isinstance(value, list) else value
        for name, value in raw.items()
        if value is not None
    }
    parts = {
        "query": " ".join(query.split()),
        "filters": normalised,
        "k": k,
        "rerank": rerank,
        "index_version": index_version,
    }
    return f"cache:retrieval:{digest(parts)}"
