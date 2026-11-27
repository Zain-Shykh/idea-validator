"""Z1 stub run: replays the shared fixture's Validate events and asks M11 for the verdict, which
proves the chain orchestrator -> Redis stream and orchestrator -> verdict. Z7 replaces it.
"""

import logging
import secrets
import time
from datetime import UTC, datetime
from pathlib import Path

from redis.exceptions import RedisError

from common import context
from common.contracts.core import (
    Dimension,
    DimensionScore,
    GateInputs,
    RunEvent,
    RunEventType,
    StudentProfile,
    TraceKind,
    Verdict,
    VerdictRequest,
)
from common.http import ServiceClient, ServiceError
from common.trace import TraceClient
from orchestrator.repositories.events import EventStream

# a copy of eval/fixtures/ (not in the image); a test keeps the two identical
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "run_events_validate.jsonl"
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

log = logging.getLogger("orchestrator.stub_run")


def new_id(prefix: str) -> str:
    """``<prefix>_<ulid>`` (conventions §7)."""
    value = ((time.time_ns() // 1_000_000) << 80) | secrets.randbits(80)
    return prefix + "_" + "".join(_CROCKFORD[(value >> s) & 31] for s in range(125, -1, -5))


def fixture_events() -> list[RunEvent]:
    return [RunEvent.model_validate_json(line) for line in FIXTURE.read_text().splitlines()]


def _stub_request(
    run_id: str, idea_id: str, version: int, profile: StudentProfile
) -> VerdictRequest:
    """What the stub really knows about the idea: nothing was searched or scored."""
    return VerdictRequest(
        idea_id=idea_id,
        version=version,
        run_id=run_id,
        goal=profile.goal,
        profile=profile,
        scores=[
            DimensionScore(
                dimension=dimension,
                score=None,
                confidence=0,
                evidence_ids=[],
                reason="stub: no scorer was called",
                rubric_version=f"{dimension}@v1",
                model="stub",
            )
            for dimension in Dimension
        ],
        gate_inputs=GateInputs(
            n_sources=0, n_platforms=0, est_effort_hours=None, available_hours=0
        ),
        rubric_set="stub",
    )


class StubRunner:
    def __init__(
        self, events: EventStream, verdict: ServiceClient | None, trace: TraceClient
    ) -> None:
        self.events = events
        self._verdict = verdict
        self._trace = trace
        self._first, *self._rest = fixture_events()

    async def request_verdict(
        self, run_id: str, idea_id: str, version: int, profile: StudentProfile
    ) -> Verdict:
        """The verdict always comes from M11; the orchestrator never builds one."""
        if self._verdict is None:
            raise ServiceError("UNAVAILABLE", "VERDICT_URL is not set")
        token = context.run_id_var.set(run_id)
        try:
            with self._trace.step("orchestrator", TraceKind.DECISION, run_id=run_id) as step:
                verdict = await self._verdict.request(
                    "POST",
                    "/verdict",
                    body=_stub_request(run_id, idea_id, version, profile),
                    response_model=Verdict,
                )
                step.output = f"verdict={verdict.verdict}"
                return verdict
        finally:
            context.run_id_var.reset(token)

    async def start(self, run_id: str) -> None:
        """Write the first event. Raises ``RedisError``, so a run id is only handed out for a
        run whose progress can be followed."""
        await self._emit(run_id, self._first)

    async def finish(
        self, run_id: str, idea_id: str, version: int, profile: StudentProfile
    ) -> None:
        """Background part of a run. Never raises: nobody is waiting for the result."""
        try:
            for template in self._rest:
                if template.type is not RunEventType.VERDICT:
                    await self._emit(run_id, template)
                    continue
                try:
                    verdict = await self.request_verdict(run_id, idea_id, version, profile)
                except ServiceError as exc:
                    log.error("verdict call failed: %s", exc, extra={"run_id": run_id})
                    await self._fail(run_id, template, exc.code)
                    return
                await self._emit(
                    run_id,
                    template,
                    message=verdict.verdict.value,
                    data={"verdict": verdict.model_dump(mode="json", by_alias=True)},
                )
        except RedisError:
            log.error("could not write run events", exc_info=True, extra={"run_id": run_id})

    async def _fail(self, run_id: str, at: RunEvent, code: str) -> None:
        await self._emit(
            run_id,
            at,
            type=RunEventType.ERROR,
            message="The verdict service call failed",
            data={"code": code},
        )
        await self._emit(
            run_id,
            at,
            seq=at.seq + 1,
            type=RunEventType.RUN_FINISHED,
            agent=None,
            step=None,
            message="Run failed",
            data=None,
        )

    async def _emit(self, run_id: str, template: RunEvent, **changes: object) -> None:
        update = {"run_id": run_id, "ts": datetime.now(UTC), **changes}
        await self.events.append(template.model_copy(update=update))
