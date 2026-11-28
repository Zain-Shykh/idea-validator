"""Async client for service-to-service calls (architecture §21.4)."""

import json
import uuid
from collections.abc import Mapping
from types import TracebackType
from typing import Any, Self, overload

import httpx
from pydantic import BaseModel, TypeAdapter, ValidationError
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential_jitter
from tenacity.wait import wait_base

from common import context

DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=3.0)
LLM_TIMEOUT = httpx.Timeout(60.0, connect=3.0)
SCORER_TIMEOUT = httpx.Timeout(90.0, connect=3.0)
# a validator request may wait for, then make, up to six model calls on one CPU model (Z5). Do not
# retry it on timeout: the abandoned scoring jobs keep running.
VALIDATOR_TIMEOUT = httpx.Timeout(600.0, connect=3.0)
IDEMPOTENCY_HEADER = "Idempotency-Key"


class ServiceError(Exception):
    """A failed call. ``status`` is None when no HTTP response arrived."""

    def __init__(
        self,
        code: str,
        message: str,
        status: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}" + (f" (HTTP {status})" if status else ""))
        self.code = code
        self.message = message
        self.status = status
        self.details = details or {}

    @property
    def summary(self) -> str:
        """Without the message, which came from outside and may echo what was sent."""
        return self.code + (f" (HTTP {self.status})" if self.status else "")


def _retryable(error: BaseException) -> bool:
    if isinstance(error, httpx.TimeoutException):
        return True
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        return status == 429 or status >= 500
    return False


def _status_error(response: httpx.Response) -> ServiceError:
    try:
        error = response.json()["error"]
        return ServiceError(
            str(error["code"]), str(error["message"]), response.status_code, error.get("details")
        )
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
        return ServiceError("HTTP_ERROR", "downstream error", response.status_code)


def _encode(body: object) -> object:
    if isinstance(body, BaseModel):
        return body.model_dump(mode="json", by_alias=True)
    if isinstance(body, list):
        return [_encode(item) for item in body]
    return body


class ServiceClient:
    """1 attempt + ``retries`` on 429, 5xx and timeouts only.

    Writes carry an ``Idempotency-Key`` that is the same on every retry; pass the step id as
    ``idempotency_key`` when there is one, or ``retry=False`` for a call that must not repeat.
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
        retries: int = 2,
        wait: wait_base | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout, transport=transport)
        self._retries = retries
        self._wait = wait if wait is not None else wait_exponential_jitter(multiplier=0.2, max=4)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    @overload
    async def request[T](
        self,
        method: str,
        path: str,
        *,
        response_model: type[T],
        body: object = ...,
        params: Mapping[str, str | int] | None = ...,
        idempotency_key: str | None = ...,
        retry: bool = ...,
        timeout: httpx.Timeout | None = ...,
    ) -> T: ...

    @overload
    async def request(
        self,
        method: str,
        path: str,
        *,
        response_model: None = ...,
        body: object = ...,
        params: Mapping[str, str | int] | None = ...,
        idempotency_key: str | None = ...,
        retry: bool = ...,
        timeout: httpx.Timeout | None = ...,
    ) -> Any: ...

    async def request(
        self,
        method: str,
        path: str,
        *,
        response_model: Any = None,
        body: object = None,
        params: Mapping[str, str | int] | None = None,
        idempotency_key: str | None = None,
        retry: bool = True,
        timeout: httpx.Timeout | None = None,
    ) -> Any:
        """Send ``body`` as JSON; validate the reply against ``response_model`` if given."""
        headers = context.outgoing_headers()
        if method.upper() in {"POST", "PUT", "PATCH"}:
            headers[IDEMPOTENCY_HEADER] = idempotency_key or uuid.uuid4().hex

        async def send() -> httpx.Response:
            response = await self._client.request(
                method,
                path,
                json=None if body is None else _encode(body),
                params=params,
                headers=headers,
                timeout=httpx.USE_CLIENT_DEFAULT if timeout is None else timeout,
            )
            if not response.is_success:
                raise httpx.HTTPStatusError(
                    "unsuccessful response", request=response.request, response=response
                )
            return response

        retrying = AsyncRetrying(
            retry=retry_if_exception(_retryable),
            stop=stop_after_attempt(self._retries + 1 if retry else 1),
            wait=self._wait,
            reraise=True,
        )
        try:
            response: httpx.Response = await retrying(send)
        except httpx.HTTPStatusError as exc:
            raise _status_error(exc.response) from exc
        except httpx.TimeoutException as exc:
            raise ServiceError("TIMEOUT", f"{method} {path} timed out") from exc
        except (httpx.HTTPError, httpx.InvalidURL) as exc:  # e.g. a huge id in a path
            raise ServiceError("UNAVAILABLE", f"{method} {path}: {type(exc).__name__}") from exc
        return _parse(response, response_model)

    async def get(self, path: str, **kwargs: Any) -> Any:
        return await self.request("GET", path, **kwargs)

    async def post(self, path: str, body: object = None, **kwargs: Any) -> Any:
        return await self.request("POST", path, body=body, **kwargs)


def _parse(response: httpx.Response, response_model: Any) -> Any:
    if response_model is None and not response.content:
        return None
    try:
        data = response.json()
        if response_model is None:
            return data
        # validate in JSON mode so dates and enums are read the same way as on the server
        return TypeAdapter(response_model).validate_json(json.dumps(data))
    except (ValueError, ValidationError, RecursionError) as exc:  # e.g. deeply nested JSON
        raise ServiceError(
            "INVALID_RESPONSE", "response does not match the contract", response.status_code
        ) from exc
