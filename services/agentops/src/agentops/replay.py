"""``python -m agentops.replay <run_id>``: re-run a stored Validate run from its trace (AO-1).

M3's real state machine runs again. Every tool call is answered with the reply the trace holds,
including recorded failures and memo hits, so no service, Redis or model is needed. The one
exception is M11: the real engine decides again, in process, so a changed rule shows up. The
command prints what differs. Exit code 0: same requests, decisions and verdict; 1: something
differs; 2: the run cannot be replayed.

It needs the orchestrator and verdict sources (the repository or the evaluation image).
"""

import argparse
import asyncio
import io
import json
import logging
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import httpx
from pydantic import TypeAdapter, ValidationError

from agentops.repositories.files import TraceFiles
from agentops.settings import AgentOpsSettings
from common import context
from common.contracts.base import ContractModel
from common.contracts.core import RunEvent, RunState, TraceKind, TraceRecord
from common.contracts.ops import OpsEvent
from common.contracts.orchestrator import AnswerRequest, ApproveRequest
from common.contracts.profile import CachedVerdict, IdeaVersion, IdeaVersionCreate
from common.http import ServiceError
from common.registry import RegistryError
from common.service import ApiError
from common.trace import TraceClient
from orchestrator.services.downstream import Peers
from orchestrator.services.rules import load_rules
from orchestrator.services.run import AGENT, Deps, RunContext
from orchestrator.services.runner import Runner
from orchestrator.services.state_machine import Checkpoint
from orchestrator.settings import OrchestratorSettings
from verdict.main import build_app as build_verdict

# which trace tool name a call of M3 is recorded under
TOOLS = {
    ("kb", "/stats"): "kb_stats",
    ("kb", "/search"): "search_kb",
    ("kb", "/evidence"): "kb_evidence",
    ("critic", "/rail/input"): "input_rail",
    ("critic", "/scan"): "scan",
    ("critic", "/policy/approval"): "approval_policy",
    ("critic", "/check"): "claim_check",
    ("ideation", "/intake"): "intake",
    ("ideation", "/skeptic"): "skeptic",
    ("ideation", "/concretize"): "concretize",
    ("scouts", "/scout"): "deep_search",
}
# decisions that say what the run did; cache bookkeeping is left out
DECISIONS = frozenset(
    {"step", "evidence", "approval_policy", "clarify", "challenge", "evidence_ids", "scan"}
)
_FAILURE = re.compile(r"^(?:ServiceError|scorer failed): ([A-Z0-9_]+)")
MAX_PAUSES = 4
# an answer attempt that was refused or not stored leaves these behind
ANSWER_RAIL = "answer_rail"
# a call the recorded run cancelled stays unanswered this long, for replay to cancel it too
CANCELLED_WAIT_S = 5.0


class ReplayError(Exception):
    """This run cannot be replayed. The message says why."""


@dataclass
class ReplayResult:
    run_id: str
    status: str
    verdict: str | None
    recorded_status: str | None
    recorded_verdict: str | None
    differences: list[str] = field(default_factory=list)


def _canonical(text: str | None) -> Any:
    try:
        return None if text is None else json.loads(text)
    except ValueError:
        return text


class Recording:
    """The recorded tool calls of one run, handed out once each."""

    def __init__(self, records: Sequence[TraceRecord]) -> None:
        self.calls: dict[str, list[TraceRecord]] = {}
        # calls that were abandoned before any reply, e.g. when a parallel scorer failed the run
        self.cancelled: dict[str, int] = {}
        for record in records:
            if record.agent != AGENT or record.kind is not TraceKind.TOOL_CALL or not record.tool:
                continue
            # a record with neither a reply nor a failure is bookkeeping, not a call
            if record.reply is not None or _FAILURE.match(record.error or ""):
                self.calls.setdefault(record.tool, []).append(record)
            elif (record.error or "").startswith("CancelledError"):
                self.cancelled[record.tool] = self.cancelled.get(record.tool, 0) + 1
        self.differences: list[str] = []

    def memo(self, tool: str) -> str | None:
        queue = self.calls.get(tool)
        if queue and queue[0].cache_hit and queue[0].input is None:
            return queue.pop(0).reply
        return None

    def abandoned(self, tool: str) -> bool:
        """True once for each cancelled call, when no answered call of ``tool`` is left."""
        if self.calls.get(tool) or not self.cancelled.get(tool):
            return False
        self.cancelled[tool] -= 1
        return True

    def take(self, tools: Sequence[str], sent: str | None) -> TraceRecord:
        """The next call of one of ``tools``: the one with the same request, else the oldest."""
        queued = sorted(
            (r for t in tools for r in self.calls.get(t, [])), key=lambda r: (r.ts, r.step_id)
        )
        if not queued:
            self.differences.append(f"{tools[0]}: a call the recorded run did not make")
            raise ServiceError("REPLAY_MISSING", "no recorded reply", 400)
        wanted = _canonical(sent)
        same = [r for r in queued if _canonical(r.input) == wanted]
        # of several answer attempts with one text, the last is the one that took effect
        if same and same[-1].tool == ANSWER_RAIL:
            same.reverse()
        found = same[0] if same else None
        if found is None:
            found = queued[0]
            self.differences.append(f"{found.tool}: the request differs from the recorded one")
        self.calls[cast(str, found.tool)].remove(found)
        return found

    def unused(self) -> list[str]:
        return [
            f"{t}: {len(q)} recorded call(s) not made"
            for t, q in self.calls.items()
            if q and t != ANSWER_RAIL
        ]


def _failure(record: TraceRecord) -> ServiceError:
    match = _FAILURE.match(record.error or "")
    code = match.group(1) if match else "INVALID_RESPONSE"
    # a 4xx is not retried, so one recorded failure answers one call
    return ServiceError(code, "recorded failure", 400)


class ReplayPeers(Peers):
    """Answers from the recording; the verdict service is the real app, in process."""

    def __init__(self, recording: Recording, verdict_config: Path | None) -> None:
        self._clients = {}
        self.recording = recording
        self.engine = httpx.AsyncClient(
            transport=httpx.ASGITransport(build_verdict(verdict_config)),
            base_url="http://verdict",
        )

    async def aclose(self) -> None:
        await self.engine.aclose()

    async def request(
        self,
        peer: str,
        method: str,
        path: str,
        *,
        limit_s: float | None = None,
        **kwargs: Any,
    ) -> Any:
        if peer == "agentops":
            return []  # spend as M13 had it is not replayed; the checkpoint's own counts are
        body: ContractModel | None = kwargs.get("body")
        sent = None if body is None else body.model_dump_json(by_alias=True)
        model = kwargs["response_model"]
        if peer == "verdict":
            return await self._verdict(method, path, sent, model)
        tool = (
            f"score_{path.removeprefix('/score/')}"
            if peer == "validators"
            else TOOLS.get((peer, path))
        )
        if tool is None:
            self.recording.differences.append(f"{peer} {path}: a call replay does not know")
            raise ServiceError("REPLAY_MISSING", "unknown call", 400)
        if self.recording.abandoned(tool):
            await asyncio.sleep(CANCELLED_WAIT_S)
            self.recording.differences.append(f"{tool}: cancelled in the recorded run only")
            raise ServiceError("REPLAY_MISSING", "no recorded reply", 400)
        tools = [tool, ANSWER_RAIL] if tool == "input_rail" else [tool]
        record = self.recording.take(tools, sent)
        if record.reply is None:
            raise _failure(record)
        return TypeAdapter(model).validate_json(record.reply)

    async def _verdict(self, method: str, path: str, sent: str | None, model: Any) -> Any:
        if path == "/config":
            tools = ["verdict_config"]
        elif path == "/verdict":
            tools = ["prelim_verdict", "final_verdict"]
        else:
            tools = ["whatif_search", "whatif_apply"]
        record = self.recording.take(tools, sent)
        if record.reply is None:
            raise _failure(record)
        reply = await self.engine.request(
            method, path, content=sent, headers={"content-type": "application/json"}
        )
        if reply.status_code != 200:
            self.recording.differences.append(
                f"{record.tool}: the engine answered HTTP {reply.status_code}"
            )
            raise ServiceError("REPLAY_ENGINE", "the engine refused", reply.status_code)
        answer = TypeAdapter(model).validate_json(reply.content)
        if json.loads(reply.content) != _canonical(record.reply):
            self.recording.differences.append(
                f"{record.tool}: M11 decides differently from the record"
            )
        return answer


class ReplayMemo:
    """Serves what the recorded run took from the memo, at the same point."""

    def __init__(self, recording: Recording) -> None:
        self.recording = recording

    async def get[M: ContractModel](self, key: str, model: type[M]) -> M | None:
        parts = key.split(":")  # cache:step:evidence:<hash> or cache:step:score:<name>:<hash>
        tool = "search_kb" if parts[2] == "evidence" else f"score_{parts[3]}"
        value = self.recording.memo(tool)
        return None if value is None else model.model_validate_json(value)

    async def put(self, key: str, value: ContractModel) -> None:
        pass


class ReplayStore:
    """Stands in for M2: nothing is read from it except the new version after a clarify."""

    def __init__(self, answered_version: int) -> None:
        self.answered_version = answered_version

    async def add_version(self, idea_id: str, body: IdeaVersionCreate, key: str) -> IdeaVersion:
        return IdeaVersion(
            **body.model_dump(),
            id="replay_version",
            idea_id=idea_id,
            version=self.answered_version,
            created_at=datetime.now(UTC),
        )

    async def cached_verdict(self, cache_key: str) -> CachedVerdict:
        return CachedVerdict(hit=False)

    async def _nothing(self, *args: Any, **kwargs: Any) -> None:
        pass

    update_run = create_approval = decide_approval = _nothing
    store_verdict = cache_verdict = _nothing


class Silent:
    """Run events and the active-run set: nothing listens during a replay."""

    async def append(self, event: RunEvent) -> None:
        pass

    async def next_seq(self, run_id: str) -> int:
        return 0

    async def add(self, run_id: str) -> None:
        pass

    async def remove(self, run_id: str) -> None:
        pass


def _decisions(records: Sequence[TraceRecord]) -> list[str]:
    return [
        f"{r.tool} {r.output}"
        for r in records
        if r.agent == AGENT and r.kind is TraceKind.DECISION and r.tool in DECISIONS
    ]


def _outcome(records: Sequence[TraceRecord]) -> tuple[str | None, str | None]:
    """The last status and verdict label M3 reported."""
    status = verdict = None
    for record in records:
        if record.agent != AGENT or record.kind is not TraceKind.DECISION:
            continue
        data = _canonical(record.output)
        if record.tool == OpsEvent.RUN_STATUS and isinstance(data, dict):
            status = data.get("status")
        if record.tool == OpsEvent.VERDICT and isinstance(data, dict):
            verdict = data.get("verdict")
    return status, verdict


def _inputs[M: ContractModel](records: Sequence[TraceRecord], tool: str, model: type[M]) -> list[M]:
    return [
        model.model_validate_json(r.input or "")
        for r in records
        if r.agent == AGENT and r.kind is TraceKind.TOOL_CALL and r.tool == tool
    ]


async def replay(
    records: Sequence[TraceRecord],
    run_id: str,
    *,
    settings: OrchestratorSettings | None = None,
    verdict_config: Path | None = None,
) -> ReplayResult:
    """``records``: the run's trace in order. Raises ``ReplayError``."""
    mine = [r for r in records if r.run_id == run_id]
    if not mine:
        raise ReplayError(f"no trace records of {run_id}")
    if any(r.tool == "verdict_cache" and r.cache_hit for r in mine):
        raise ReplayError("the verdict came from the cache: replay the run that computed it")
    try:
        starts = _inputs(mine, "run_input", Checkpoint)
        answers = _inputs(mine, "student_answer", AnswerRequest)
        approvals = _inputs(mine, "student_approval", ApproveRequest)
    except ValidationError as exc:
        raise ReplayError("a recorded input no longer matches the contracts") from exc
    if not starts:
        raise ReplayError(
            "no run_input record: not a Validate run (replay a Discover run's child runs), "
            "or traced before replay existed"
        )
    settings = settings or OrchestratorSettings()
    try:
        rules = load_rules(
            Path(settings.rubrics_dir), Path(settings.prompts_dir), Path(settings.budgets_path)
        )
    except RegistryError as exc:
        raise ReplayError(f"cannot load rubrics, prompts or budgets: {exc}") from exc

    recording = Recording(mine)
    peers = ReplayPeers(recording, verdict_config)
    produced: list[TraceRecord] = []

    async def sink(batch: list[TraceRecord]) -> None:
        produced.extend(batch)

    tracer = TraceClient(sink=sink, stream=io.StringIO())
    try:
        asked = [
            int(json.loads(r.input or "")["version"])
            for r in mine
            if r.tool in ("prelim_verdict", "final_verdict") and r.kind is TraceKind.TOOL_CALL
        ]
    except (ValueError, KeyError, TypeError, RecursionError) as exc:
        raise ReplayError("a recorded verdict request cannot be read") from exc
    silent = Silent()
    deps = Deps(
        settings=settings,
        rules=rules,
        peers=peers,
        # structural stand-ins for the Redis and M2 repositories
        store=cast(Any, ReplayStore(asked[-1] if asked else starts[0].version + 1)),
        events=cast(Any, silent),
        memo=cast(Any, ReplayMemo(recording)),
        proposals=cast(Any, None),
        active=cast(Any, silent),
        trace=tracer,
        retry_wait_s=0,
    )
    cp = starts[0].model_copy(deep=True)
    runner = Runner(deps)
    r = RunContext(run_id, cp, deps)
    token = context.run_id_var.set(run_id)
    try:
        for _ in range(MAX_PAUSES):
            await runner.drive_in_place(r)
            if cp.status is RunState.AWAITING_APPROVAL and approvals:
                assert cp.pending is not None and cp.pending.approval is not None
                runner.apply_decision(r, cp.pending.approval, approvals.pop(0))
            elif cp.status is RunState.AWAITING_INPUT and answers:
                assert cp.pending is not None
                try:
                    await runner.apply_answers(r, cp.pending, answers.pop(0))
                except ApiError as exc:
                    recording.differences.append(f"the recorded answer is refused: {exc.code}")
                    break
            else:
                break
            r.set_status(RunState.RUNNING)
            cp.pending = cp.waiting_since = None
    finally:
        context.run_id_var.reset(token)
        await tracer.aclose()
        await peers.aclose()

    status, verdict = _outcome(mine)
    result = ReplayResult(
        run_id=run_id,
        status=cp.status.value,
        verdict=cp.verdict.verdict.value
        if cp.status is RunState.COMPLETED and cp.verdict
        else None,
        recorded_status=status,
        recorded_verdict=verdict,
        differences=list(dict.fromkeys(recording.differences + recording.unused())),
    )
    if result.status != status:
        result.differences.append(f"status: {result.status}, recorded {status}")
    if result.verdict != verdict:
        result.differences.append(f"verdict: {result.verdict}, recorded {verdict}")
    before, after = _decisions(mine), _decisions(produced)
    if before != after:
        first = next(
            (i for i, (a, b) in enumerate(zip(before, after, strict=False)) if a != b),
            min(len(before), len(after)),
        )
        result.differences.append(
            f"decision {first + 1}: {after[first:][:1]}, recorded {before[first:][:1]}"
        )
    return result


async def _fetch(url: str, run_id: str) -> list[TraceRecord]:
    async with httpx.AsyncClient(base_url=url, timeout=30) as client:
        reply = await client.get(f"/runs/{run_id}/trace")
        reply.raise_for_status()
        return TypeAdapter(list[TraceRecord]).validate_json(reply.content)


def load_records(args: argparse.Namespace) -> list[TraceRecord]:
    if args.agentops_url:
        return asyncio.run(_fetch(args.agentops_url, args.run_id))
    if args.trace_file:
        lines = args.trace_file.read_text(encoding="utf-8").split("\n")
        records = [TraceRecord.model_validate_json(line) for line in lines if line.strip()]
        return sorted(records, key=lambda r: (r.ts, r.step_id))
    folder = args.traces_dir or Path(AgentOpsSettings().traces_dir)
    return TraceFiles(folder).read(args.run_id)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("run_id")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--agentops-url", help="read the trace from M13")
    source.add_argument("--traces-dir", type=Path, help="M13's trace volume")
    source.add_argument("--trace-file", type=Path, help="one JSONL file of trace records")
    parser.add_argument("--verdict-config", type=Path, help="default: VERDICT_CONFIG_PATH")
    args = parser.parse_args(argv)
    if context.safe_id(args.run_id) is None:
        print("error: invalid run id", file=sys.stderr)
        return 2
    # the in-process verdict app logs every request: not part of the answer
    before = logging.root.manager.disable
    logging.disable(logging.INFO)
    try:
        records = load_records(args)
        result = asyncio.run(replay(records, args.run_id, verdict_config=args.verdict_config))
    except (ReplayError, OSError, ValidationError, httpx.HTTPError) as exc:
        # a validation error quotes the record: name the kind only
        detail = str(exc) if isinstance(exc, ReplayError | OSError) else type(exc).__name__
        print(f"error: {detail}", file=sys.stderr)
        return 2
    except Exception as exc:  # exit 1 means "differs": a crash must not read as that
        print(f"error: replay crashed: {type(exc).__name__}", file=sys.stderr)
        return 2
    finally:
        logging.disable(before)
    print(f"recorded: {result.recorded_status} {result.recorded_verdict}")
    print(f"replayed: {result.status} {result.verdict}")
    for difference in result.differences:
        print(f"differs: {difference}")
    if not result.differences:
        print("same requests, decisions and verdict")
    return 1 if result.differences else 0


if __name__ == "__main__":
    sys.exit(main())
