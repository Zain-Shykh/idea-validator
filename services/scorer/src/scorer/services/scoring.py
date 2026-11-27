"""Scores one dimension (§14.2): local model, one repair retry, hosted fallback, abstention.

A model failure is never an HTTP error and never a made-up score: the dimension abstains.
"""

import asyncio
import json
import logging
import re
import uuid
from collections.abc import Sequence

from pydantic import Field, ValidationError, field_validator

from common import context
from common.contracts.agents import Rubric, ScoreRequest, ScorerOutput
from common.contracts.core import Dimension, DimensionScore, EvidenceItem, TraceKind
from common.contracts.ops import OpsEvent, ScorerFallbackEvent
from common.contracts.tools import ChatMessage
from common.llm import LLMClient, LLMError, strip_fence
from common.metrics import SCORER_FALLBACKS
from common.registry import Prompt
from common.trace import TraceClient
from scorer.services.backend import LocalModel, LocalModelError, reply_schema
from scorer.services.prompt import build_messages, check_template, select_evidence

log = logging.getLogger("scorer")

AGENT = "scorer"
HOSTED_ALIAS = "scorer-hosted"
NO_MODEL = "none"
FAILED_PREFIX = "scoring failed"
MAX_TOKENS = 300
HOSTED_MAX_TOKENS = 400
_REPAIR_ECHO_CHARS = 400
_ID_IN_TEXT = re.compile(r"card_[A-Za-z0-9]+(?:#c\d+)?")


class UnknownRubricError(Exception):
    pass


class ModelReply(ScorerOutput):
    """What a model must send. Ids are checked afterwards (strip, then abstain), so an unknown
    or missing id is not a format error that a retry could answer with another invented one."""

    evidence_ids: list[str] = Field()

    @field_validator("score")
    @classmethod
    def _half_steps(cls, value: float) -> float:
        if not (value * 2).is_integer():
            raise ValueError("score must be a multiple of 0.5")
        return value


class InvalidReplyError(Exception):
    pass


class Scorer:
    def __init__(
        self,
        rubrics: dict[Dimension, Rubric],
        prompt: Prompt,
        *,
        local: LocalModel | None,
        hosted: LLMClient | None,
        tracer: TraceClient,
        model_id: str,
        local_timeout_s: float,
        hosted_timeout_s: float,
    ) -> None:
        """``local=None`` is ``SCORER_BACKEND=hosted``: the hosted model is then the model
        under test, not a fallback."""
        check_template(prompt)
        self._rubrics = rubrics
        self._prompt = prompt
        self.local = local
        self.hosted = hosted
        self.tracer = tracer
        self._model_id = model_id
        self._local_timeout_s = local_timeout_s
        self._hosted_timeout_s = hosted_timeout_s

    def rubric_for(self, rubric_id: str) -> Rubric:
        name, _, version = rubric_id.partition("@v")
        rubric = self._rubrics.get(Dimension(name)) if name in set(Dimension) else None
        if rubric is None or version != str(rubric.version):
            raise UnknownRubricError(rubric_id)
        return rubric

    async def score(self, request: ScoreRequest) -> DimensionScore:
        rubric = self.rubric_for(request.rubric_id)
        evidence = select_evidence(request.evidence)
        if not evidence:
            return self._abstain(request, rubric, "no evidence", NO_MODEL)
        run_id = context.run_id_var.get() or f"run_adhoc_{uuid.uuid4().hex}"
        messages = build_messages(self._prompt, rubric, request, evidence)
        with self.tracer.step(AGENT, TraceKind.DECISION, tool="score", run_id=run_id) as step:
            result = await self._score(request, rubric, evidence, messages, run_id)
            step.model = result.model
            step.output = result.model_dump_json(exclude={"features"})
        return result

    async def _score(
        self,
        request: ScoreRequest,
        rubric: Rubric,
        evidence: Sequence[EvidenceItem],
        messages: list[ChatMessage],
        run_id: str,
    ) -> DimensionScore:
        cause = ""
        if self.local is not None:
            try:
                reply = await self._ask_local(self.local, messages, len(evidence), run_id)
                return self._grounded(request, rubric, evidence, reply, self._model_id)
            except TimeoutError:
                cause = "local model timed out"
            except (LocalModelError, InvalidReplyError) as exc:
                cause = f"local model: {exc}"
            self._record_fallback(rubric.dimension, cause, run_id)
        try:
            reply = await self._ask_hosted(messages, run_id)
        except (LLMError, TimeoutError, ValueError) as exc:
            problem = str(exc) or type(exc).__name__
            log.error("hosted model failed: %s", problem[:300])
            detail = f"{cause}; " if cause else ""
            reason = f"{FAILED_PREFIX}: {detail}hosted model: {problem}"
            return self._abstain(request, rubric, reason[:500], NO_MODEL)
        return self._grounded(request, rubric, evidence, reply, HOSTED_ALIAS)

    async def _ask_local(
        self, local: LocalModel, messages: list[ChatMessage], n_evidence: int, run_id: str
    ) -> ModelReply:
        """At most two calls: the first and one repair retry, under one deadline."""
        deadline = asyncio.get_running_loop().time() + self._local_timeout_s
        attempt_messages = messages
        problem = ""
        for attempt in range(2):
            with self.tracer.step(
                AGENT,
                TraceKind.LLM_CALL,
                input=json.dumps([m.model_dump() for m in attempt_messages]),
                run_id=run_id,
                prompt_version=self._prompt.id,
            ) as step:
                step.model = self._model_id
                step.retries = attempt
                async with asyncio.timeout_at(deadline):
                    reply = await local.complete(
                        attempt_messages, max_tokens=MAX_TOKENS, schema=reply_schema(n_evidence)
                    )
                step.output = reply.content
                step.tokens_in = reply.tokens_in
                step.tokens_out = reply.tokens_out
            try:
                return ModelReply.model_validate_json(strip_fence(reply.content))
            except ValidationError as exc:
                # location and message only: no model text reaches logs through this
                problem = "; ".join(
                    f"{'.'.join(map(str, e['loc'])) or '(root)'}: {e['msg']}"
                    for e in exc.errors()[:3]
                )[:300]
                log.warning("invalid local reply (attempt %d): %s", attempt + 1, problem)
                attempt_messages = [
                    *messages,
                    ChatMessage(role="assistant", content=reply.content[:_REPAIR_ECHO_CHARS]),
                    ChatMessage(
                        role="user",
                        content=f"Your reply did not match the schema: {problem}. "
                        "Reply again with valid JSON only.",
                    ),
                ]
        raise InvalidReplyError(f"invalid after one retry: {problem}")

    async def _ask_hosted(self, messages: list[ChatMessage], run_id: str) -> ModelReply:
        if self.hosted is None:
            raise LLMError("not configured")
        async with asyncio.timeout(self._hosted_timeout_s):
            result = await self.hosted.chat(
                HOSTED_ALIAS,
                messages,
                ModelReply,
                agent=AGENT,
                run_id=run_id,
                max_tokens=HOSTED_MAX_TOKENS,
                prompt_version=self._prompt.id,
            )
        assert result.data is not None  # chat() raises when the schema is not met
        return result.data

    def _record_fallback(self, dimension: Dimension, cause: str, run_id: str) -> None:
        log.warning("falling back to the hosted model for %s: %s", dimension.value, cause)
        SCORER_FALLBACKS.labels(dimension.value).inc()
        with self.tracer.step(
            AGENT, TraceKind.DECISION, tool=OpsEvent.SCORER_FALLBACK, input=cause, run_id=run_id
        ) as step:
            step.model = HOSTED_ALIAS
            step.output = ScorerFallbackEvent(dimension=dimension).model_dump_json()

    def _grounded(
        self,
        request: ScoreRequest,
        rubric: Rubric,
        evidence: Sequence[EvidenceItem],
        reply: ModelReply,
        model: str,
    ) -> DimensionScore:
        """Keep only ids that were in the prompt; without one there is no score (M9-4)."""
        provided = {item.evidence_id for item in evidence}
        # "[card_x#c0]" is how the prompt's own examples bracket ids
        cited = dict.fromkeys(i.strip(" []") for i in reply.evidence_ids)
        ids = [i for i in cited if i in provided]
        if len(ids) < len(cited):
            log.warning("stripped %d unknown evidence ids", len(cited) - len(ids))
        if not ids:
            return self._abstain(request, rubric, "the model cited none of the evidence", model)
        known = provided | {item.card_id for item in evidence}
        reason = _ID_IN_TEXT.sub(
            lambda m: m.group(0) if m.group(0) in known else "[unknown id]", reply.reason
        )
        return DimensionScore(
            dimension=rubric.dimension,
            score=reply.score,
            confidence=reply.confidence,
            evidence_ids=ids,
            features=request.features,
            reason=reason,
            rubric_version=request.rubric_id,
            model=model,
        )

    def _abstain(
        self, request: ScoreRequest, rubric: Rubric, reason: str, model: str
    ) -> DimensionScore:
        return DimensionScore(
            dimension=rubric.dimension,
            score=None,
            confidence=0,
            evidence_ids=[],
            features=request.features,
            reason=reason,
            rubric_version=request.rubric_id,
            model=model,
        )
