"""The local scoring model: an OpenAI-compatible server (llama.cpp sidecar or Ollama)."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from common.contracts.tools import ChatMessage
from common.http import ServiceClient, ServiceError


class LocalModelError(Exception):
    """No usable HTTP reply: the caller falls back without retrying."""


@dataclass(frozen=True)
class LocalReply:
    content: str
    tokens_in: int
    tokens_out: int


def reply_schema(max_ids: int) -> dict[str, Any]:
    """Constrains decoding. Written by hand: llama.cpp's grammar converter handles only part of
    JSON Schema. Ids are not an enum, so invented ones stay visible and are stripped."""
    return {
        "type": "object",
        "properties": {
            "score": {"type": "number", "enum": [half / 2 for half in range(11)]},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "evidence_ids": {"type": "array", "items": {"type": "string"}, "maxItems": max_ids},
            "reason": {"type": "string"},
        },
        "required": ["score", "confidence", "evidence_ids", "reason"],
        "additionalProperties": False,
    }


class LocalModel:
    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        timeout_s: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._model = model
        self._client = ServiceClient(
            base_url,
            timeout=httpx.Timeout(timeout_s, connect=3.0),
            retries=0,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
        schema: dict[str, Any] | None = None,
    ) -> LocalReply:
        body: dict[str, Any] = {
            "model": self._model,
            "messages": [m.model_dump() for m in messages],
            "temperature": 0,
            "seed": 0,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "score", "strict": True, "schema": schema},
            }
        try:
            data = await self._client.request("POST", "/v1/chat/completions", body=body)
            content = data["choices"][0]["message"]["content"]
            usage = data.get("usage") or {}
            if not isinstance(content, str):
                raise TypeError("content is not text")
            return LocalReply(
                # a lone surrogate cannot be sent on (repair retry, traces)
                content=content.encode("utf-8", "replace").decode(),
                tokens_in=max(0, int(usage.get("prompt_tokens") or 0)),
                tokens_out=max(0, int(usage.get("completion_tokens") or 0)),
            )
        except ServiceError as exc:
            raise LocalModelError(str(exc)) from exc
        except (
            KeyError,
            IndexError,
            TypeError,
            ValueError,
            AttributeError,
            OverflowError,
            RecursionError,
        ) as exc:
            raise LocalModelError(f"unexpected reply shape: {type(exc).__name__}") from exc
