"""The run lifecycle (architecture §6.1): create, drive, wait, answer, approve, resume, recover.

A run is driven by one task at a time. Waiting ends the task; an answer or approval stores the
new state and starts the next one. Everything needed to continue is in the checkpoint, so a new
process can pick a run up.

# ponytail: tasks and locks are in this process, so one replica (k8s/base/orchestrator.yaml);
# more replicas need a lease per run in Redis.
"""

import asyncio
import contextlib
import logging
from typing import Any

from pydantic import ValidationError
from redis.exceptions import RedisError

from common import context
from common.contracts.core import (
    IdeaInput,
    PendingInput,
    RunEventType,
    RunState,
    RunStatus,
    Workflow,
)
from common.contracts.ops import ApprovalEvent, OpsEvent, RunStartedEvent, RunStatusEvent
from common.contracts.orchestrator import AnswerRequest, ApproveRequest, StartRunRequest
from common.contracts.profile import ApprovalUpdate, IdeaVersionCreate, RunCreate, RunRecord
from common.http import ServiceError
from common.service import ApiError
from orchestrator.services import budget, validate
from orchestrator.services.results import run_result
from orchestrator.services.run import (
    AGENT,
    BudgetStoppedError,
    Deps,
    RunContext,
    RunFailedError,
    StoreUnavailableError,
    new_id,
)
from orchestrator.services.state_machine import (
    TERMINAL,
    WAITING,
    Checkpoint,
    check_step_transition,
)

RECOVERY_ATTEMPTS = 60
RECOVERY_WAIT_S = 5.0
STOP_GRACE_S = 5.0

log = logging.getLogger("orchestrator")


def _unavailable(what: str) -> ApiError:
    return ApiError(503, "DEPENDENCY_UNAVAILABLE", f"{what} is unavailable")


def _conflict(message: str) -> ApiError:
    return ApiError(409, "CONFLICT", message)


def _checkpoint_of(record: RunRecord) -> Checkpoint:
    try:
        return Checkpoint.model_validate(record.state)
    except ValidationError as exc:
        raise ApiError(409, "CONFLICT", "the stored run state cannot be read") from exc


class Runner:
    def __init__(self, deps: Deps) -> None:
        self.deps = deps
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, run_id: str) -> asyncio.Lock:
        return self._locks.setdefault(run_id, asyncio.Lock())

    # --- reading ---------------------------------------------------------------------------

    async def _record(self, run_id: str) -> RunRecord:
        try:
            return await self.deps.store.get_run(run_id)
        except ServiceError as exc:
            if exc.status == 404:
                raise ApiError(404, "NOT_FOUND", "unknown run") from exc
            raise _unavailable("the run store") from exc

    async def load(self, run_id: str) -> RunContext:
        return RunContext(run_id, _checkpoint_of(await self._record(run_id)), self.deps)

    async def status(self, run_id: str) -> RunStatus:
        cp = (await self.load(run_id)).cp
        done = cp.status is RunState.COMPLETED and cp.verdict is not None
        return RunStatus(
            run_id=run_id,
            workflow=cp.workflow,
            status=cp.status,
            created_at=cp.created_at,
            updated_at=cp.updated_at,
            budget=cp.budget,
            spend=cp.spend,
            pending=cp.pending,
            result=run_result(cp.verdict) if done and cp.verdict else None,
        )

    # --- starting --------------------------------------------------------------------------

    async def create(self, request: StartRunRequest) -> str:
        if request.workflow is not Workflow.VALIDATE:
            raise ApiError(501, "NOT_IMPLEMENTED", "Discover arrives with phase Z8")
        if request.profile.user_id != request.user_id:
            raise ApiError(422, "VALIDATION_ERROR", "the profile belongs to another user")
        assert request.idea_version_id is not None  # the contract requires it for validate
        try:
            idea = await self.deps.store.idea_of_version(request.idea_version_id)
        except ServiceError as exc:
            if exc.status == 404:
                raise ApiError(404, "NOT_FOUND", "unknown idea version") from exc
            raise _unavailable("the idea store") from exc
        version = next((v for v in idea.versions if v.id == request.idea_version_id), None)
        if version is None or idea.user_id != request.user_id:
            raise ApiError(404, "NOT_FOUND", "unknown idea version")
        try:
            content = IdeaInput(title=idea.title, text=version.text)
        except ValidationError as exc:
            raise ApiError(422, "VALIDATION_ERROR", "the stored idea is empty or too long") from exc

        now = self.deps.clock()
        cp = Checkpoint(
            workflow=request.workflow,
            user_id=request.user_id,
            created_at=now,
            updated_at=now,
            idea_id=idea.id,
            idea_version_id=version.id,
            version=version.version,
            idea=content,
            profile=request.profile,
            budget=self.deps.rules.budgets[request.workflow],
        )
        r = RunContext(new_id("run"), cp, self.deps)
        token = context.run_id_var.set(r.run_id)
        try:
            await self.deps.store.create_run(
                RunCreate(
                    run_id=r.run_id,
                    user_id=request.user_id,
                    workflow=request.workflow,
                    profile_version=request.profile.version,
                    budget=cp.budget,
                )
            )
            await r.save()
            # required: a run id is only handed out for a run whose progress can be followed.
            # A RedisError becomes 503 and the stored QUEUED run is never started
            await r.emit(RunEventType.RUN_STARTED, "Validate run started", required=True)
        except (ServiceError, StoreUnavailableError) as exc:
            raise _unavailable("the run store") from exc
        else:
            r.ops(
                OpsEvent.RUN_STARTED,
                RunStartedEvent(workflow=cp.workflow, user_id=cp.user_id),
            )
            r.ops(OpsEvent.RUN_STATUS, RunStatusEvent(status=cp.status))
            await self.deps.active.add(r.run_id)
            self._spawn(r.run_id)
        finally:
            context.run_id_var.reset(token)
        return r.run_id

    # --- the student's side ----------------------------------------------------------------

    async def _waiting(self, run_id: str, state: RunState) -> tuple[RunContext, PendingInput]:
        """The run, if it waits for exactly this. Call with the run's lock held."""
        r = await self.load(run_id)
        if r.cp.status is not state:
            raise _conflict(f"the run is {r.cp.status}, not {state}")
        # the task that paused it may still be sending its last events
        await self._settle(run_id)
        await self._expire(r)
        assert r.cp.pending is not None
        return r, r.cp.pending

    async def _expire(self, r: RunContext) -> None:
        """A run that waited too long is cancelled when someone comes back to it."""
        since = r.cp.waiting_since
        limit = self.deps.settings.wait_timeout_s
        if since is None or (self.deps.clock() - since).total_seconds() <= limit:
            return
        r.set_status(RunState.CANCELLED)
        r.cp.pending = None
        await self._finish(r, "Run cancelled: no reply in time", "WAIT_TIMEOUT")
        raise _conflict("the run was cancelled: it waited too long")

    async def approve(self, run_id: str, request: ApproveRequest) -> None:
        token = context.run_id_var.set(run_id)
        try:
            async with self._lock(run_id):
                r, pending = await self._waiting(run_id, RunState.AWAITING_APPROVAL)
                approval = pending.approval
                assert approval is not None
                if approval.approval_id != request.approval_id:
                    raise _conflict("this is not the pending approval")
                approved = request.decision == "approve"
                decision = "approved" if approved else "declined"
                try:
                    await self.deps.store.decide_approval(
                        approval.approval_id, ApprovalUpdate(decision=decision)
                    )
                except ServiceError as exc:
                    raise _unavailable("the approval store") from exc
                if approved:
                    estimate = budget.deep_search_estimate(
                        approval.estimated_pages, self.deps.settings.deep_search_page_cost_usd
                    ).model_copy(update={"cost_usd": approval.estimated_cost_usd})
                    r.cp.budget = budget.raised_for(r.cp.spend, r.cp.budget, estimate)
                    r.cp.deep_search = "approved"
                else:
                    r.cp.deep_search = "declined"
                    r.cp.degrade("deep search declined")
                r.ops(
                    OpsEvent.APPROVAL,
                    ApprovalEvent(approval_id=approval.approval_id, decision=decision),
                )
                await self._continue(r)
        finally:
            context.run_id_var.reset(token)

    async def answer(self, run_id: str, request: AnswerRequest) -> None:
        token = context.run_id_var.set(run_id)
        try:
            async with self._lock(run_id):
                r, pending = await self._waiting(run_id, RunState.AWAITING_INPUT)
                await self._apply_answers(r, pending, request)
                await self._continue(r)
        finally:
            context.run_id_var.reset(token)

    async def _apply_answers(
        self, r: RunContext, pending: PendingInput, request: AnswerRequest
    ) -> None:
        cp = r.cp
        asked = {q.question_id: q.text for q in pending.questions}
        given = {a.question_id: a.answer for a in request.answers}
        if len(given) != len(request.answers) or set(given) != set(asked):
            raise ApiError(
                422, "VALIDATION_ERROR", "answer each pending question exactly once",
                {"question_ids": sorted(asked)},
            )  # fmt: skip
        pairs = []
        for question_id, text in asked.items():
            # an answer is student text like the idea, so it passes the same rail
            try:
                reply = await validate.rail(r, given[question_id])
            except RunFailedError as exc:
                raise _unavailable("the input check") from exc
            if not reply.allowed:
                raise ApiError(
                    422, "INPUT_REJECTED", "an answer was refused by the input check",
                    {"question_id": question_id, "reasons": reply.reasons},
                )  # fmt: skip
            pairs.append((text, reply.redacted_text))
        text = validate.with_clarifications(cp.idea.text, pairs)
        try:
            version = await self.deps.store.add_version(
                cp.idea_id,
                IdeaVersionCreate(
                    text=text, origin="clarify", parent_version_id=cp.idea_version_id
                ),
                key=f"{r.run_id}:clarify",
            )
        except ServiceError as exc:
            raise _unavailable("the idea store") from exc
        cp.idea = IdeaInput(title=cp.idea.title, text=text)
        cp.idea_version_id, cp.version = version.id, version.version
        assert cp.safe_idea is not None
        cp.safe_idea = IdeaInput(
            title=cp.safe_idea.title,
            text=validate.with_clarifications(cp.safe_idea.text, pairs),
        )
        cp.clarified = True
        # only the scorers an answer can move run again, on the new text (M3-5)
        for name in validate.affected_scorers(cp.candidate_questions):
            cp.scorer_results.pop(name, None)
            cp.scorer_ideas[name] = cp.safe_idea
        r.decision(
            "clarify",
            {"answered": sorted(asked),
             "rescore": sorted(validate.affected_scorers(cp.candidate_questions))},
        )  # fmt: skip

    async def _continue(self, r: RunContext) -> None:
        r.set_status(RunState.RUNNING)
        r.cp.pending = None
        r.cp.waiting_since = None
        try:
            await r.save()
        except StoreUnavailableError as exc:
            raise _unavailable("the run store") from exc
        await self.deps.active.add(r.run_id)
        self._spawn(r.run_id)

    async def resume(self, run_id: str) -> None:
        """Pick up a run whose process stopped. A waiting run needs its answer instead."""
        async with self._lock(run_id):
            r = await self.load(run_id)
            if r.cp.status in TERMINAL:
                raise _conflict(f"the run is already {r.cp.status}")
            if r.cp.status in WAITING:
                await self._expire(r)
                raise _conflict(f"the run is {r.cp.status}")
            if not self._busy(run_id):
                await self.deps.active.add(run_id)
                self._spawn(run_id)

    async def recover(self) -> None:
        """At startup: continue the runs that were in progress. Waiting runs stay waiting."""
        for attempt in range(RECOVERY_ATTEMPTS):
            if attempt:
                await asyncio.sleep(RECOVERY_WAIT_S)
            try:
                left = 0
                for run_id in await self.deps.active.members():
                    try:
                        status = (await self.load(run_id)).cp.status
                    except ApiError as exc:
                        if exc.status_code == 503:
                            left += 1
                        else:
                            await self.deps.active.remove(run_id)
                        continue
                    if status in TERMINAL:
                        await self.deps.active.remove(run_id)
                    elif status not in WAITING and not self._busy(run_id):
                        log.info("resuming run", extra={"run_id": run_id})
                        self._spawn(run_id)
                if not left:
                    return
            except RedisError:
                log.warning("cannot read the active runs yet", exc_info=True)
        log.error("some runs were not recovered; POST /runs/{id}/resume continues one")

    # --- driving ---------------------------------------------------------------------------

    def _busy(self, run_id: str) -> bool:
        task = self.tasks.get(run_id)
        return task is not None and not task.done()

    async def _settle(self, run_id: str) -> None:
        task = self.tasks.get(run_id)
        if task is not None:
            await asyncio.wait({task})

    def _spawn(self, run_id: str) -> None:
        task = asyncio.create_task(self._drive(run_id))
        self.tasks[run_id] = task

        def forget(done: asyncio.Task[None]) -> None:
            if self.tasks.get(run_id) is done:
                del self.tasks[run_id]

        task.add_done_callback(forget)

    async def _drive(self, run_id: str) -> None:
        """Run steps until the run waits or ends. Never raises, except when cancelled."""
        context.run_id_var.set(run_id)
        context.parent_step_var.set(None)
        try:
            r = await self.load(run_id)
            await self._steps(r)
        except asyncio.CancelledError:
            raise
        except (ApiError, StoreUnavailableError):
            # no checkpoint, no progress: the run stays resumable
            log.error("run paused: the store is unavailable", exc_info=True)
        except Exception:
            log.exception("run crashed", extra={"run_id": run_id})

    async def _steps(self, r: RunContext) -> None:
        cp = r.cp
        try:
            if cp.status is RunState.QUEUED:
                r.set_status(RunState.RUNNING)
                await r.save()
            while cp.status is RunState.RUNNING:
                step = cp.step
                await r.emit(RunEventType.STEP_STARTED, f"{step} started", agent=AGENT, step=step)
                following = await validate.STEPS[step](r)
                if following is not None:
                    check_step_transition(step, following)
                    cp.step = following
                    r.decision("step", {"from": step, "to": following})
                    await r.emit(
                        RunEventType.STEP_FINISHED, f"{step} finished", agent=AGENT, step=step
                    )
                await r.save()
                announcements, r.after_save = r.after_save, []
                for announce in announcements:
                    await announce()
            if cp.status in TERMINAL:
                await self._forget(r.run_id)
        except StoreUnavailableError:
            await r.emit(
                RunEventType.ERROR, "The run store is unavailable; the run can be resumed",
                data={"code": "STORE_UNAVAILABLE"},
            )  # fmt: skip
            raise
        except BudgetStoppedError as exc:
            await self._stop(
                r, RunState.BUDGET_STOPPED, "BUDGET_EXCEEDED",
                "Run stopped: the budget is used up", {"limits": exc.problems},
            )  # fmt: skip
        except Exception as exc:
            await self._fail(r, exc)

    async def _fail(self, r: RunContext, error: BaseException) -> None:
        if isinstance(error, RunFailedError):
            await self._stop(r, RunState.FAILED, error.code, error.message, error.data)
            return
        log.error("run failed", exc_info=error, extra={"run_id": r.run_id})
        await self._stop(r, RunState.FAILED, "INTERNAL_ERROR", "Run failed", {})

    async def _stop(
        self, r: RunContext, status: RunState, code: str, message: str, data: dict[str, Any]
    ) -> None:
        r.set_status(status)
        r.cp.pending = None
        r.cp.error_code = code
        r.after_save = []
        await self._finish(r, message, code, data)

    async def _finish(
        self, r: RunContext, message: str, code: str, data: dict[str, Any] | None = None
    ) -> None:
        r.cp.error_code = code
        try:
            await r.save()
        except StoreUnavailableError:
            log.error("could not store the final state", extra={"run_id": r.run_id})
        else:
            await self._forget(r.run_id)
        await r.emit(
            RunEventType.ERROR, message, step=r.cp.step, data={"code": code, **(data or {})}
        )
        await r.emit(RunEventType.RUN_FINISHED, f"Run {r.cp.status.lower().replace('_', ' ')}")

    async def _forget(self, run_id: str) -> None:
        await self.deps.active.remove(run_id)
        self._locks.pop(run_id, None)
        self.deps.event_locks.pop(run_id, None)

    # --- shutdown --------------------------------------------------------------------------

    async def close(self, grace_s: float = STOP_GRACE_S) -> None:
        """Cancel the drives. Their runs resume from the last checkpoint at the next start."""
        tasks = [t for t in self.tasks.values() if not t.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(grace_s):
                    await asyncio.gather(*tasks, return_exceptions=True)
