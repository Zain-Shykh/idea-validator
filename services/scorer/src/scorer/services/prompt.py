"""Builds the scoring prompt from ``prompts/scorer@vN.md`` (§12.2, §14.2). Pure, so the
fine-tuning dataset (Z9) can be built with exactly the text the model is served.
"""

import html
import json
import re
from collections.abc import Sequence

from common.contracts.agents import Rubric, ScoreRequest
from common.contracts.core import EvidenceItem
from common.contracts.tools import ChatMessage
from common.registry import Prompt, RegistryError

# the model's context is 4096 tokens: these bounds leave room for the reply and one repair
MAX_EVIDENCE = 12
MAX_QUOTE_CHARS = 500
MAX_FEATURE_CHARS = 2000

# everything before this line is the system message (instructions and rubric)
_SPLIT = "\n<idea>\n"
_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")
_PLACEHOLDERS = {"dimension", "rubric_version", "rubric", "idea", "features", "evidence"}


def check_template(prompt: Prompt) -> None:
    """Fail at start-up, not on the first request."""
    if prompt.body.count(_SPLIT) != 1:
        raise RegistryError(f"{prompt.id}: needs exactly one line that is only <idea>")
    found = set(_PLACEHOLDER.findall(prompt.body))
    if found != _PLACEHOLDERS:
        raise RegistryError(f"{prompt.id}: placeholders must be {sorted(_PLACEHOLDERS)}")


def _text(value: str) -> str:
    """Outside text can neither close a block nor open one."""
    return html.escape(value, quote=False)


def select_evidence(evidence: Sequence[EvidenceItem]) -> list[EvidenceItem]:
    """The items that go into the prompt; only their ids may be cited."""
    unique: dict[str, EvidenceItem] = {}
    for item in evidence:
        unique.setdefault(item.evidence_id, item)
    return list(unique.values())[:MAX_EVIDENCE]


def render_rubric(rubric: Rubric) -> str:
    # worked examples are left out: CPU time, and a small model copies their ids
    anchors = [f"{level}: {rubric.anchors[level]}" for level in sorted(rubric.anchors)[::-1]]
    return "\n".join([rubric.scale, *anchors])


def build_messages(
    prompt: Prompt, rubric: Rubric, request: ScoreRequest, evidence: Sequence[EvidenceItem]
) -> list[ChatMessage]:
    features = json.dumps(request.features, sort_keys=True, default=str)
    values = {
        "dimension": rubric.dimension.value,
        "rubric_version": request.rubric_id,
        "rubric": render_rubric(rubric),
        "idea": _text(f"{request.idea.title}\n{request.idea.text}"),
        "features": _text(features[:MAX_FEATURE_CHARS]),
        "evidence": "\n".join(
            f'<evidence id="{item.evidence_id}" source="{html.escape(item.source[:80])}">'
            f"{_text(item.quote[:MAX_QUOTE_CHARS])}</evidence>"
            for item in evidence
        ),
    }

    def fill(part: str) -> str:
        # one pass: a "{{evidence}}" inside the idea stays literal
        return _PLACEHOLDER.sub(lambda m: values[m.group(1)], part)

    system, user = prompt.body.split(_SPLIT)
    return [
        ChatMessage(role="system", content=fill(system).strip()),
        ChatMessage(role="user", content=fill(_SPLIT + user).strip()),
    ]
