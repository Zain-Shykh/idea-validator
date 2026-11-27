"""The model judge of §12.5: does the quoted evidence support one claim?

Only its verdict is used. The reason a user sees is written by code, because model text can name
things the evidence does not, or repeat injected text (AVOID-4).
"""

import asyncio
import html
import logging
import re
from collections.abc import Sequence
from typing import Literal

from common.contracts.base import ContractModel
from common.contracts.core import EvidenceItem
from common.contracts.tools import ChatMessage
from common.llm import LLMClient, LLMOutputError
from common.registry import Prompt, RegistryError

log = logging.getLogger("critic")

AGENT = "critic"
ALIAS = "agent-default"
MAX_QUOTE_CHARS = 1500
_SPLIT = "\n<claim>\n"
_SLOT = re.compile(r"\{\{(claim|evidence|standard)\}\}")
_STANDARD = "A claim that the quotes only partly cover may be supported if its main point is."
_STRICT = (
    "Strict check: answer supported only if the quotes state every part of the claim, including "
    "each number, comparison and absolute word such as all, none, only or never."
)

Verdict = Literal["supported", "unsupported", "unverifiable"]


class JudgeReply(ContractModel):
    verdict: Verdict
    reason: str


class JudgeUnavailableError(Exception):
    """No verdict from the model. ``outage`` is true when the next claim would fail too."""

    def __init__(self, why: str, *, outage: bool) -> None:
        super().__init__(why)
        self.why = why
        self.outage = outage


def _escape(text: str) -> str:
    # '{' is escaped so that no inserted text can look like a placeholder or a tag
    return html.escape(text, quote=True)


class Judge:
    def __init__(self, prompt: Prompt, llm: LLMClient | None, timeout_s: float) -> None:
        slots = sorted(_SLOT.findall(prompt.body))
        if prompt.body.count(_SPLIT) != 1 or slots != ["claim", "evidence", "standard"]:
            raise RegistryError(f"{prompt.id}: needs {{{{claim}}}}, evidence and standard slots")
        self.prompt = prompt
        self.llm = llm
        self.timeout_s = timeout_s

    def messages(
        self, claim: str, evidence: Sequence[EvidenceItem], strict: bool
    ) -> list[ChatMessage]:
        blocks = "\n".join(
            f'<evidence id="{item.evidence_id}" source="{_escape(item.source)}">\n'
            f"{_escape(item.quote[:MAX_QUOTE_CHARS])}\n</evidence>"
            for item in evidence
        )
        values = {
            "claim": _escape(claim),
            "evidence": blocks,
            "standard": _STRICT if strict else _STANDARD,
        }
        system, user = self.prompt.body.split(_SPLIT)
        # one pass per part: text that is inserted is never searched for placeholders
        return [
            ChatMessage(role=role, content=_SLOT.sub(lambda m: values[m.group(1)], part).strip())
            for role, part in (("system", system), ("user", _SPLIT + user))
        ]

    async def judge(
        self,
        claim: str,
        evidence: Sequence[EvidenceItem],
        *,
        strict: bool,
        run_id: str,
        deadline: float,
    ) -> Verdict:
        if self.llm is None:
            raise JudgeUnavailableError("no model is configured", outage=True)
        now = asyncio.get_running_loop().time()
        if now >= deadline:
            raise JudgeUnavailableError("the check ran out of time", outage=True)
        try:
            async with asyncio.timeout_at(min(deadline, now + self.timeout_s)):
                result = await self.llm.chat(
                    ALIAS,
                    self.messages(claim, evidence, strict),
                    JudgeReply,
                    agent=AGENT,
                    run_id=run_id,
                    max_tokens=200,
                    temperature=0.0,
                    prompt_version=self.prompt.id,
                )
        except LLMOutputError as exc:
            log.warning("judge reply unusable twice")
            raise JudgeUnavailableError("the model's reply was unusable", outage=False) from exc
        except TimeoutError as exc:
            log.warning("judge timed out")
            raise JudgeUnavailableError("the model timed out", outage=True) from exc
        except Exception as exc:  # LLMError, or anything a backend lets through
            log.warning("judge unavailable: %s", type(exc).__name__)
            raise JudgeUnavailableError("the model is unavailable", outage=True) from exc
        assert result.data is not None  # chat() raises when the schema is not met
        return result.data.verdict
