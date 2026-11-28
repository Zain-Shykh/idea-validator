import httpx
import pytest
from scorer_factories import Replies, completion

from common.contracts.tools import ChatMessage
from scorer.services.backend import LocalModel, LocalModelError, reply_schema

HELLO = [ChatMessage(role="user", content="hi")]


def model(handler: Replies) -> LocalModel:
    return LocalModel("http://model", "m1", timeout_s=1, transport=httpx.MockTransport(handler))


async def test_request_is_deterministic_and_schema_constrained() -> None:
    server = Replies(completion("{}", 11, 3))
    result = await model(server).complete(HELLO, max_tokens=7, schema=reply_schema(2))
    assert (result.content, result.tokens_in, result.tokens_out) == ("{}", 11, 3)
    (body,) = server.bodies
    assert (body["model"], body["temperature"], body["max_tokens"]) == ("m1", 0, 7)
    schema = body["response_format"]["json_schema"]["schema"]
    assert schema["properties"]["score"]["enum"] == [0, 0.5, 1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5]
    assert schema["properties"]["evidence_ids"]["maxItems"] == 2


@pytest.mark.parametrize(
    "answer",
    [
        httpx.ConnectError("refused"),
        httpx.ReadTimeout("slow"),
        httpx.Response(500, text="boom"),
        httpx.Response(503, json={"error": {"code": 503, "message": "Loading model"}}),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"choices": []}),
        httpx.Response(200, json=[1]),
        httpx.Response(200, json={"choices": [{"message": {"content": None}}]}),
    ],
)
async def test_unusable_answers_raise_without_retry(answer: Exception | httpx.Response) -> None:
    server = Replies(answer)
    with pytest.raises(LocalModelError):
        await model(server).complete(HELLO, max_tokens=4)
    assert len(server.bodies) == 1
