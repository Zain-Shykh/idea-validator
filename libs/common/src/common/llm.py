"""LLM access for agents (architecture §12.1, §12.2).

Agents pass a model **alias**, never a provider id. ``LLM_BACKEND`` selects the route:
``gateway`` (M5 ``/llm/chat``), ``direct`` (LiteLLM, local development) or ``fake``
(cassette replay; tests never call a real model).
"""

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from common import context
from common.contracts.core import TraceKind
from common.contracts.tools import ChatMessage, LlmChatRequest, LlmChatResponse
from common.http import LLM_TIMEOUT, ServiceClient, ServiceError
from common.settings import LlmSettings
from common.trace import TraceClient


class LLMError(Exception):
    pass


class LLMUnavailableError(LLMError):
    """The backend failed or timed out."""


class LLMOutputError(LLMError):
    """Still not valid against the schema after one repair retry: abstain or fail the step."""


class LLMCassetteMissError(LLMError):
    """The fake backend has no recorded reply left for this call."""


@dataclass(frozen=True)
class LLMResult[T: BaseModel]:
    content: str
    data: T | None
    model: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    cache_hit: bool
    repaired: bool


_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL | re.IGNORECASE)


def _strip_fence(content: str) -> str:
    text = content.strip()
    match = _FENCE.match(text)
    return match.group(1) if match else text


class Cassette:
    """JSONL of recorded replies. A call consumes the first unused line whose ``agent`` and
    ``alias`` (when the line has them) match."""

    def __init__(self, entries: Sequence[dict[str, Any]]) -> None:
        if not all(isinstance(e, dict) and isinstance(e.get("content"), str) for e in entries):
            raise LLMCassetteMissError("every cassette line needs a string 'content'")
        self._entries = list(entries)

    @classmethod
    def load(cls, path: str | Path) -> "Cassette":
        try:
            lines = Path(path).read_text(encoding="utf-8").splitlines()
            entries = [json.loads(line) for line in lines if line.strip()]
        except (OSError, ValueError) as exc:
            raise LLMCassetteMissError(f"cannot read cassette {path}: {exc}") from exc
        return cls(entries)

    def next(self, alias: str, agent: str) -> dict[str, Any]:
        for index, entry in enumerate(self._entries):
            if entry.get("alias", alias) == alias and entry.get("agent", agent) == agent:
                return self._entries.pop(index)
        raise LLMCassetteMissError(f"no recorded reply left for agent={agent} alias={alias}")


class LLMClient:
    def __init__(
        self,
        settings: LlmSettings | None = None,
        *,
        tracer: TraceClient | None = None,
        cassette: Cassette | None = None,
        gateway: ServiceClient | None = None,
    ) -> None:
        self.settings = settings or LlmSettings()
        self._tracer = tracer or TraceClient()
        self._cassette = cassette
        self._gateway = gateway
        backend = self.settings.backend
        if backend == "fake" and cassette is None:
            if not self.settings.cassette_path:
                raise LLMCassetteMissError("LLM_BACKEND=fake needs LLM_CASSETTE_PATH")
            self._cassette = Cassette.load(self.settings.cassette_path)
        if backend == "gateway" and gateway is None:
            if not self.settings.tools_url:
                raise ValueError("LLM_BACKEND=gateway needs TOOLS_URL")
            self._gateway = ServiceClient(self.settings.tools_url, timeout=LLM_TIMEOUT)

    async def aclose(self) -> None:
        if self._gateway:
            await self._gateway.aclose()

    async def chat[T: BaseModel](
        self,
        alias: str,
        messages: Sequence[ChatMessage],
        schema: type[T] | None = None,
        *,
        agent: str,
        run_id: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
        prompt_version: str | None = None,
    ) -> LLMResult[T]:
        """One call, plus exactly one repair retry when the reply does not match ``schema``.

        Token and cost figures cover both attempts.
        """
        run_id = run_id or context.run_id_var.get()
        if not run_id:
            raise ValueError("an LLM call needs a run_id (budgets and traces are per run)")
        request = LlmChatRequest(
            run_id=run_id,
            agent=agent,
            model_alias=alias,
            messages=list(messages),
            response_format=None if schema is None else schema.model_json_schema(),
            max_tokens=max_tokens,
            temperature=temperature,
        )
        tokens_in = tokens_out = 0
        cost = 0.0
        problem = ""
        for attempt in range(2):
            reply = await self._call(request, attempt, prompt_version)
            tokens_in += reply.tokens_in
            tokens_out += reply.tokens_out
            cost += reply.cost_usd
            data: T | None = None
            if schema is not None:
                try:
                    data = schema.model_validate_json(_strip_fence(reply.content))
                except ValidationError as exc:
                    problem = "; ".join(
                        f"{'.'.join(map(str, e['loc'])) or '(root)'}: {e['msg']}"
                        for e in exc.errors()[:5]
                    )
                    request = request.model_copy(
                        update={
                            "messages": [
                                *messages,
                                ChatMessage(role="assistant", content=reply.content),
                                ChatMessage(
                                    role="user",
                                    content=f"Your reply did not match the schema: {problem}. "
                                    "Reply again with valid JSON only.",
                                ),
                            ]
                        }
                    )
                    continue
            return LLMResult(
                content=reply.content,
                data=data,
                model=reply.model,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_usd=cost,
                cache_hit=reply.cache_hit,
                repaired=attempt > 0,
            )
        raise LLMOutputError(f"{agent}: invalid output after one repair retry: {problem}")

    async def _call(
        self, request: LlmChatRequest, attempt: int, prompt_version: str | None
    ) -> LlmChatResponse:
        with self._tracer.step(
            request.agent,
            TraceKind.LLM_CALL,
            input=json.dumps([m.model_dump() for m in request.messages]),
            run_id=request.run_id,
            prompt_version=prompt_version,
        ) as step:
            step.retries = attempt
            step.model = request.model_alias
            if self.settings.backend == "fake":
                reply = self._call_fake(request)
            elif self.settings.backend == "direct":
                reply = await self._call_direct(request)
            else:
                reply = await self._call_gateway(request)
            step.output = reply.content
            step.model = reply.model
            step.tokens_in = reply.tokens_in
            step.tokens_out = reply.tokens_out
            step.cost_usd = reply.cost_usd
            step.cache_hit = reply.cache_hit
            return reply

    def _call_fake(self, request: LlmChatRequest) -> LlmChatResponse:
        assert self._cassette is not None
        entry = self._cassette.next(request.model_alias, request.agent)
        return LlmChatResponse(
            content=entry["content"],
            model=entry.get("model", "fake"),
            tokens_in=entry.get("tokens_in", 0),
            tokens_out=entry.get("tokens_out", 0),
            cost_usd=entry.get("cost_usd", 0.0),
            latency_ms=0,
            cache_hit=False,
        )

    async def _call_gateway(self, request: LlmChatRequest) -> LlmChatResponse:
        assert self._gateway is not None
        try:
            return await self._gateway.request(
                "POST", "/llm/chat", body=request, response_model=LlmChatResponse
            )
        except ServiceError as exc:
            raise LLMUnavailableError(str(exc)) from exc

    async def _call_direct(self, request: LlmChatRequest) -> LlmChatResponse:
        model = self.settings.provider_model(request.model_alias)
        try:
            import litellm
        except ImportError as exc:
            raise LLMUnavailableError(
                "LLM_BACKEND=direct needs: pip install -e 'libs/common[direct]'"
            ) from exc
        kwargs: dict[str, Any] = {}
        if model.startswith("ollama") and self.settings.ollama_base_url:
            kwargs["api_base"] = self.settings.ollama_base_url
        if request.response_format is not None:
            kwargs["response_format"] = {"type": "json_object"}
            kwargs["messages"] = [
                {
                    "role": "system",
                    "content": "Reply with JSON only, matching this JSON Schema: "
                    + json.dumps(request.response_format),
                },
                *(m.model_dump() for m in request.messages),
            ]
        else:
            kwargs["messages"] = [m.model_dump() for m in request.messages]
        try:
            response = await litellm.acompletion(
                model=model,
                max_tokens=request.max_tokens,
                temperature=request.temperature,
                timeout=LLM_TIMEOUT.read,
                num_retries=2,
                **kwargs,
            )
            content = response.choices[0].message.content or ""
        except Exception as exc:  # LiteLLM raises many provider-specific types
            raise LLMUnavailableError(f"{model}: {type(exc).__name__}") from exc
        try:
            cost = float(litellm.completion_cost(completion_response=response))
        except Exception:  # unknown price (local models)
            cost = 0.0
        usage = getattr(response, "usage", None)
        return LlmChatResponse(
            content=content,
            model=model,
            tokens_in=getattr(usage, "prompt_tokens", 0) or 0,
            tokens_out=getattr(usage, "completion_tokens", 0) or 0,
            cost_usd=cost,
            latency_ms=0,
            cache_hit=False,
        )
