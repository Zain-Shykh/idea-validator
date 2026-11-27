"""Effort estimate (§12.4): a model's estimate clamped to a band computed by code. Without a
usable estimate the middle of the band is used, so M11 always gets one (D-006)."""

import asyncio
import html
import logging
from typing import Literal

from pydantic import Field

from common.contracts.base import ContractModel
from common.contracts.core import IdeaInput
from common.contracts.tools import ChatMessage
from common.llm import LLMClient, LLMError
from common.registry import Prompt, RegistryError
from validators.services.features import clamp_effort

log = logging.getLogger("validators")

AGENT = "validators"
ALIAS = "agent-cheap"
_SPLIT = "\n<idea>\n"

EffortSource = Literal["model", "band_midpoint"]


class EffortReply(ContractModel):
    hours: float = Field(gt=0)


class EffortEstimator:
    def __init__(self, prompt: Prompt, llm: LLMClient | None, timeout_s: float) -> None:
        if prompt.body.count(_SPLIT) != 1 or "{{idea}}" not in prompt.body:
            raise RegistryError(f"{prompt.id}: needs one <idea> block with {{{{idea}}}}")
        self.prompt = prompt
        self.llm = llm
        self._timeout_s = timeout_s

    def messages(self, idea: IdeaInput) -> list[ChatMessage]:
        system, user = self.prompt.body.split(_SPLIT)
        text = html.escape(f"{idea.title}\n{idea.text}", quote=False)
        return [
            ChatMessage(role="system", content=system.strip()),
            ChatMessage(role="user", content=(_SPLIT + user).replace("{{idea}}", text).strip()),
        ]

    async def estimate(
        self, idea: IdeaInput, band: tuple[int, int], run_id: str
    ) -> tuple[float, EffortSource]:
        """Hours and where they came from."""
        if self.llm is not None:
            try:
                async with asyncio.timeout(self._timeout_s):
                    result = await self.llm.chat(
                        ALIAS,
                        self.messages(idea),
                        EffortReply,
                        agent=AGENT,
                        run_id=run_id,
                        max_tokens=50,
                        prompt_version=self.prompt.id,
                    )
                assert result.data is not None  # chat() raises when the schema is not met
                return clamp_effort(result.data.hours, band), "model"
            except (LLMError, TimeoutError, ValueError) as exc:
                log.warning("no model effort estimate: %s", type(exc).__name__)
        return sum(band) / 2, "band_midpoint"
