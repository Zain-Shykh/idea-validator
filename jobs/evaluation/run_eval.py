"""Run every labelled idea through the orchestrator and score the verdicts (AO-4, EVAL-2).

    python jobs/evaluation/run_eval.py [--compare eval/results/baseline.json]
    python jobs/evaluation/run_eval.py --results report.json --compare baseline.json

Services come from ORCHESTRATOR_URL, PROFILE_URL (M2 stores the ideas) and AGENTOPS_URL (traces),
so the same command works on the cluster and on compose. Without ``resolved.csv`` the verdicts
and behaviour checks are recorded and no agreement is reported.

The run is not interactive: a question gets a fixed non-answer and an approval is declined.
Exit code 0: done, and no worse than the baseline; 1: worse; 2: it could not be done.
"""

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

import eval_metrics
import labelled
import reports
from common import context
from common.contracts.core import (
    RunState,
    RunStatus,
    TraceKind,
    TraceRecord,
    Verdict,
    VerdictLabel,
    Workflow,
)
from common.contracts.ops import OpsEvent
from common.contracts.orchestrator import (
    Answer,
    AnswerRequest,
    ApproveRequest,
    StartRunRequest,
    StartRunResponse,
)
from common.contracts.profile import Idea, IdeaCreate
from common.http import ServiceClient, ServiceError

NO_ANSWER = "No further information."
REPORT_LINE = "REPORT_JSON "
# one clarify round and one approval are all a run can ask for
MAX_REPLIES = 4
TERMINAL = frozenset(
    {RunState.COMPLETED, RunState.FAILED, RunState.BUDGET_STOPPED, RunState.CANCELLED}
)


class IdeaError(Exception):
    """One idea got no verdict."""


class Harness:
    def __init__(
        self,
        orchestrator: ServiceClient,
        profile: ServiceClient,
        agentops: ServiceClient,
        *,
        tag: str,
        timeout_s: float = 900,
        poll_s: float = 2,
        trace_wait_s: float = 30,
    ) -> None:
        self.orchestrator, self.profile, self.agentops = orchestrator, profile, agentops
        self.tag = tag
        self.timeout_s, self.poll_s, self.trace_wait_s = timeout_s, poll_s, trace_wait_s

    async def run(self, idea: labelled.LabelledIdea) -> dict[str, Any]:
        """One entry of the report. A failure is an entry with ``error``, never an exception."""
        entry: dict[str, Any] = {
            "idea_id": idea.idea_id, "run_id": None, "status": None, "verdict": None,
            "error": None,
        }  # fmt: skip
        # a user of its own: a cached verdict of an earlier run would hide a regression
        user = f"u_eval_{self.tag}_{idea.idea_id}"
        token = context.user_id_var.set(user)
        try:
            async with asyncio.timeout(self.timeout_s):
                await self._run(idea, user, entry)
        except TimeoutError:
            entry["error"] = "TIMEOUT"
        except ServiceError as exc:
            entry["error"] = exc.summary
        except IdeaError as exc:
            entry["error"] = str(exc)
        finally:
            context.user_id_var.reset(token)
        return entry

    async def _run(self, idea: labelled.LabelledIdea, user: str, entry: dict[str, Any]) -> None:
        stored: Idea = await self.profile.post(
            "/internal/ideas",
            IdeaCreate(user_id=user, title=idea.title, text=idea.text, source="own"),
            response_model=Idea,
            idempotency_key=user,
        )
        if not stored.versions:
            raise IdeaError("M2 returned the idea without a version")
        started: StartRunResponse = await self.orchestrator.post(
            "/runs",
            StartRunRequest(
                workflow=Workflow.VALIDATE,
                user_id=user,
                idea_version_id=stored.versions[-1].id,
                profile=idea.student(user),
            ),
            response_model=StartRunResponse,
            retry=False,  # a second POST would be a second, paid run
        )
        entry["run_id"] = run_id = started.run_id
        status = await self._wait(run_id)
        entry["status"] = status.status.value
        records = await self._trace(run_id, status.status)
        if status.status is not RunState.COMPLETED:
            raise IdeaError(f"the run ended {status.status.value}")
        if status.result is None or not status.result.verdicts:
            raise IdeaError("the run has no verdict")
        card = status.result.verdicts[0]
        if card.from_cache:
            raise IdeaError("the verdict came from the cache, so nothing was evaluated")
        entry.update(self._scored(card, records))
        entry["behaviour"] = eval_metrics.behaviour(records, card, status.spend, status.budget)
        entry["cost_usd"] = status.spend.cost_usd

    async def _wait(self, run_id: str) -> RunStatus:
        replies = 0
        while True:
            status: RunStatus = await self.orchestrator.get(
                f"/runs/{run_id}", response_model=RunStatus
            )
            if status.status in TERMINAL:
                return status
            pending = status.pending
            if pending is not None:
                replies += 1
                if replies > MAX_REPLIES:
                    raise IdeaError("the run keeps asking")
                if pending.approval is not None:
                    await self.orchestrator.post(
                        f"/runs/{run_id}/approve",
                        ApproveRequest(
                            approval_id=pending.approval.approval_id, decision="decline"
                        ),
                    )
                else:
                    answers = [
                        Answer(question_id=q.question_id, answer=NO_ANSWER)
                        for q in pending.questions
                    ]
                    await self.orchestrator.post(
                        f"/runs/{run_id}/answer", AnswerRequest(answers=answers)
                    )
            await asyncio.sleep(self.poll_s)

    async def _trace(self, run_id: str, status: RunState) -> list[TraceRecord]:
        """M3 sends its trace in batches: wait until the run's end has arrived."""
        deadline = time.monotonic() + self.trace_wait_s
        while True:
            records: list[TraceRecord] = await self.agentops.get(
                f"/runs/{run_id}/trace", response_model=list[TraceRecord]
            )
            if any(
                r.kind is TraceKind.DECISION
                and r.tool == OpsEvent.RUN_STATUS
                and status.value in (r.output or "")
                for r in records
            ):
                return records
            if time.monotonic() >= deadline:
                raise IdeaError("the trace of the run is incomplete")
            await asyncio.sleep(self.poll_s)

    @staticmethod
    def _scored(card: Verdict, records: Sequence[TraceRecord]) -> dict[str, Any]:
        inputs = [r.input for r in records if r.tool == "final_verdict" and r.input]
        if not inputs:
            raise IdeaError("the trace has no verdict request")
        return {
            "verdict": card.verdict.value,
            "score": card.score,
            "confidence": card.confidence,
            "stability": card.stability.value,
            "scores": {s.dimension.value: s.score for s in card.scores},
            "models": sorted({s.model for s in card.scores}),
            "versions": card.versions.model_dump(mode="json"),
            # M3's own request, scores before challenge penalties: what tune.py replays (Z2-6)
            "request": json.loads(inputs[-1]),
        }


def labelled_hash(folder: Path) -> str:
    """Of the ideas and the agreed verdicts: two reports compare only on the same set."""
    digest = hashlib.sha256()
    for name in ("ideas.jsonl", "resolved.csv"):
        path = folder / name
        digest.update(path.read_bytes() if path.exists() else b"")
        digest.update(b"\0")
    return digest.hexdigest()


def build_report(
    entries: Sequence[dict[str, Any]],
    human: Mapping[str, VerdictLabel] | None,
    *,
    fake: bool,
    sha: str,
    labelled_sha256: str,
) -> dict[str, Any]:
    done = [e for e in entries if e["error"] is None]
    metrics: dict[str, Any] = {
        "n_ideas": len(entries),
        "errors": len(entries) - len(done),
        "verdict_counts": dict(sorted(Counter(e["verdict"] for e in done).items())),
        **eval_metrics.behaviour_metrics([e["behaviour"] for e in done]),
    }
    if human is not None:
        system = {e["idea_id"]: VerdictLabel(e["verdict"]) if e["verdict"] else None
                  for e in entries}  # fmt: skip
        metrics |= eval_metrics.verdict_metrics(human, system)
        for entry in entries:
            entry["human"] = human[entry["idea_id"]].value
    return {
        "kind": "eval",
        "git_sha": sha,
        "created_at": datetime.now(UTC).isoformat(),
        "fake": fake,
        "labelled_sha256": labelled_sha256,
        "labels": "resolved.csv" if human is not None else None,
        "metrics": metrics,
        "ideas": list(entries),
    }


async def evaluate(folder: Path, harness: Harness, *, fake: bool, sha: str) -> dict[str, Any]:
    """Raises ``LabelError``. Ideas run one after another (the model's rate limit)."""
    ideas = labelled.load_ideas(folder / "ideas.jsonl")
    ids = {i.idea_id for i in ideas}
    human = None
    if (resolved := folder / "resolved.csv").exists():
        human = labelled.load_resolved(resolved, ids)
        if missing := sorted(ids - human.keys()):
            raise labelled.LabelError(f"resolved.csv has no verdict for {', '.join(missing)}")
    digest = labelled_hash(folder)
    entries = []
    for idea in ideas:
        entry = await harness.run(idea)
        print(f"{idea.idea_id}: {entry['verdict'] or entry['error']}", file=sys.stderr)
        entries.append(entry)
    return build_report(entries, human, fake=fake, sha=sha, labelled_sha256=digest)


def compare_files(report: Mapping[str, Any], baseline_path: Path) -> int:
    problems = eval_metrics.compare(report, reports.read_report(baseline_path))
    for problem in problems:
        print(f"regression: {problem}")
    if not problems:
        print(f"no regression against {baseline_path}")
    return 1 if problems else 0


def harness_from_env(args: argparse.Namespace, tag: str) -> Harness:
    clients = []
    for name in ("ORCHESTRATOR_URL", "PROFILE_URL", "AGENTOPS_URL"):
        url = os.environ.get(name)
        if not url:
            raise labelled.LabelError(f"{name} is not set")
        clients.append(ServiceClient(url, timeout=httpx.Timeout(30, connect=5)))
    return Harness(*clients, tag=tag, timeout_s=args.idea_timeout, poll_s=args.poll)


def main(argv: Sequence[str] | None = None, harness: Harness | None = None) -> int:
    """``harness``: the tests' way in; otherwise the environment names the services."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--labelled", type=Path, default=labelled.LABELLED_DIR)
    parser.add_argument("--out", type=Path, default=reports.RESULTS_DIR)
    parser.add_argument("--compare", type=Path, help="fail when worse than this report")
    parser.add_argument("--results", type=Path, help="compare this report; run nothing")
    parser.add_argument("--store", action="store_true", help="keep the metrics in eval_runs")
    parser.add_argument("--print-report", action="store_true", help="the report on one line")
    parser.add_argument("--fake", action="store_true", help="the services or labels are made up")
    parser.add_argument("--idea-timeout", type=float, default=900)
    parser.add_argument("--poll", type=float, default=2)
    args = parser.parse_args(argv)
    try:
        if args.results:
            if not args.compare:
                parser.error("--results needs --compare")
            return compare_files(reports.read_report(args.results), args.compare)
        if args.compare:  # an unusable baseline must not cost a whole run first
            reports.read_report(args.compare)
        fake = args.fake or reports.made_up(args.labelled)
        reports.guard(args.out / "eval.json", fake)
        sha = reports.git_sha()
        harness = harness or harness_from_env(args, f"{sha}_{int(time.time())}")
        report = asyncio.run(evaluate(args.labelled, harness, fake=fake, sha=sha))
        if report["metrics"]["errors"] == report["metrics"]["n_ideas"]:
            print("error: no idea got a verdict; no report written", file=sys.stderr)
            return 2
        path = reports.write_report(args.out, "eval", report)
        print(json.dumps(report["metrics"], indent=2, sort_keys=True))
        print(f"report: {path}")
        if args.print_report:
            # a Job's files die with its pod: check_eval.sh saves this line from the log
            print(REPORT_LINE + json.dumps(report, sort_keys=True))
        if report["labels"] is None:
            print("no resolved.csv: agreement, confusion matrix and false GO are not measured")
        stored = True
        if args.store:
            try:
                reports.store(report, path)
            except reports.OutputError as exc:  # the comparison still has to be made
                print(f"error: {exc}", file=sys.stderr)
                stored = False
        worse = compare_files(report, args.compare) if args.compare else 0
        return worse or (0 if stored else 2)
    except (labelled.LabelError, reports.OutputError, eval_metrics.ReportError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
