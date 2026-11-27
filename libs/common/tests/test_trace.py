import asyncio
import io
import json

import pytest
import respx

from common import context
from common.contracts.core import TraceKind, TraceRecord
from common.trace import TraceClient, new_step_id


class Collector:
    def __init__(self, fail: int = 0) -> None:
        self.batches: list[list[TraceRecord]] = []
        self.fail = fail

    async def __call__(self, batch: list[TraceRecord]) -> None:
        if self.fail:
            self.fail -= 1
            raise ConnectionError("M13 down")
        self.batches.append(batch)


def emit(tracer: TraceClient, count: int) -> None:
    for index in range(count):
        with tracer.step("agent", TraceKind.DECISION, run_id="run_1", step_id=index):
            pass


async def test_writes_valid_json_lines_and_batches() -> None:
    out, sink = io.StringIO(), Collector()
    tracer = TraceClient(sink=sink, batch_size=2, stream=out)
    emit(tracer, 5)
    await tracer.aclose()
    records = [TraceRecord.model_validate_json(line) for line in out.getvalue().splitlines()]
    assert [r.step_id for r in records] == [0, 1, 2, 3, 4]
    assert [[r.step_id for r in b] for b in sink.batches] == [[0, 1], [2, 3], [4]]


async def test_full_batch_is_sent_without_explicit_flush() -> None:
    sink = Collector()
    tracer = TraceClient(sink=sink, batch_size=2, stream=io.StringIO())
    emit(tracer, 2)
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert len(sink.batches) == 1


async def test_failed_delivery_is_kept_and_never_raises() -> None:
    sink = Collector(fail=1)
    tracer = TraceClient(sink=sink, batch_size=10, stream=io.StringIO())
    emit(tracer, 3)
    await tracer.flush()
    assert sink.batches == []
    await tracer.flush()
    assert [r.step_id for r in sink.batches[0]] == [0, 1, 2]


async def test_buffer_is_bounded() -> None:
    sink = Collector()
    tracer = TraceClient(sink=sink, batch_size=100, max_buffer=3, stream=io.StringIO())
    emit(tracer, 5)
    await tracer.flush()
    assert [r.step_id for r in sink.batches[0]] == [2, 3, 4]


@respx.mock
async def test_posts_batch_to_agentops() -> None:
    route = respx.post("http://agentops:8000/trace").respond(202)
    tracer = TraceClient("http://agentops:8000", stream=io.StringIO())
    emit(tracer, 2)
    await tracer.aclose()
    assert route.call_count == 1
    assert [r["step_id"] for r in json.loads(route.calls.last.request.content)] == [0, 1]


async def test_without_url_only_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    tracer = TraceClient()
    emit(tracer, 1)
    await tracer.aclose()
    assert json.loads(capsys.readouterr().out)["run_id"] == "run_1"


def test_step_measures_latency_nests_and_records_errors() -> None:
    ticks = iter([10.0, 10.5, 11.0, 11.25])  # outer start, inner start, inner end, outer end
    out = io.StringIO()
    tracer = TraceClient(stream=out, clock=lambda: next(ticks))
    token = context.run_id_var.set("run_ctx")
    try:
        with tracer.step("orchestrator", TraceKind.DECISION, step_id=1) as outer:
            outer.output = "go on"
            with (
                pytest.raises(TimeoutError),
                tracer.step("scorer", TraceKind.TOOL_CALL, tool="search_kb", step_id=2),
            ):
                raise TimeoutError("kb")
            assert context.parent_step_var.get() == 1
    finally:
        context.run_id_var.reset(token)
    assert context.parent_step_var.get() is None
    inner, parent = (json.loads(line) for line in out.getvalue().splitlines())
    assert (inner["parent_step"], inner["latency_ms"], inner["error"]) == (
        1,
        500,
        "TimeoutError: kb",
    )
    assert (parent["latency_ms"], parent["output"], parent["run_id"]) == (1250, "go on", "run_ctx")


def test_step_without_run_id_is_an_error() -> None:
    with pytest.raises(ValueError, match="run_id"), TraceClient().step("a", TraceKind.DECISION):
        pass


def test_step_ids_are_unique_and_js_safe() -> None:
    ids = {new_step_id() for _ in range(50)}
    assert len(ids) >= 49 and max(ids) < 2**53
