"""Step 2: a hosted model drafts a label for each candidate, using the rubric in its prompt (FT-3).

Rate-limited, bounded and resumable: a row that has a draft is never asked again. One reply per
row; a reply that is not a grounded label is recorded as rejected, never repaired into one.
Uses the existing LLM settings: ``LLM_BACKEND=direct`` with ``GEMINI_API_KEY``, or ``gateway``.
"""

import argparse
import asyncio
import logging
import os
import sys
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from common.contracts.tools import ChatMessage
from common.llm import LLMClient, LLMError, LLMResult, LLMUnavailableError, strip_fence
from common.settings import LlmSettings
from common.trace import TraceClient
from dataset import (
    DATA_DIR,
    Candidate,
    DataError,
    Draft,
    append_jsonl,
    grounded_label,
    guard_output,
    keyed,
    read_jsonl,
    stable_order,
)

log = logging.getLogger("finetune")

AGENT = "finetune_labeller"
ALIAS = "agent-default"
MAX_TOKENS = 400
_ATTEMPTS = 3
_BACKOFF_S = 60.0

Sleep = Callable[[float], Awaitable[None]]


@dataclass
class Summary:
    calls: int = 0
    labelled: int = 0
    rejected: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    remaining: int = 0
    stopped: str | None = None


def _drafted(path: Path) -> dict[str, Draft]:
    return keyed(read_jsonl(path, Draft), path) if path.exists() else {}


async def draft_labels(
    candidates: Sequence[Candidate],
    drafts_path: Path,
    llm: LLMClient,
    *,
    alias: str = ALIAS,
    rpm: float = 10.0,
    max_calls: int = 600,
    sleep: Sleep = asyncio.sleep,
) -> Summary:
    fake_model = llm.settings.backend == "fake"
    guard_output(drafts_path, fake_model or any(c.fake for c in candidates))
    done = _drafted(drafts_path)
    # one idea at a time, so an interrupted run leaves whole ideas that can be reviewed
    todo = sorted(
        (c for c in candidates if c.item_id not in done),
        key=lambda c: (stable_order(c.idea_id), c.dimension),
    )
    summary = Summary(remaining=len(todo))
    for row in todo:
        if summary.calls >= max_calls:
            summary.stopped = f"reached --max-calls {max_calls}"
            break
        reply: LLMResult[BaseModel] | None = None
        for attempt in range(1, _ATTEMPTS + 1):
            if summary.calls:
                await sleep(60.0 / rpm)
            summary.calls += 1
            try:
                reply = await llm.chat(
                    alias,
                    [
                        ChatMessage(role="system", content=row.system),
                        ChatMessage(role="user", content=row.user),
                    ],
                    agent=AGENT,
                    run_id=f"run_ft_draft_{row.item_id}",
                    max_tokens=MAX_TOKENS,
                )
                break
            except LLMUnavailableError as exc:
                log.warning("teacher unavailable (attempt %d): %s", attempt, exc)
                if attempt == _ATTEMPTS or summary.calls >= max_calls:
                    summary.stopped = f"teacher unavailable: {exc}"
                    break
                await sleep(_BACKOFF_S * attempt)
        if reply is None:
            break
        label, problem = None, None
        try:
            label = grounded_label(strip_fence(reply.content), row.evidence_ids)
        except DataError as exc:
            problem = str(exc)[:300]
        append_jsonl(
            drafts_path,
            Draft(
                item_id=row.item_id,
                label=label,
                problem=problem,
                model=reply.model,
                drafted_at=datetime.now(UTC),
                fake=fake_model or row.fake,
            ),
        )
        summary.labelled += label is not None
        summary.rejected += label is None
        summary.tokens_in += reply.tokens_in
        summary.tokens_out += reply.tokens_out
        summary.cost_usd += reply.cost_usd
        summary.remaining -= 1
    return summary


async def _run(args: argparse.Namespace) -> Summary:
    candidates = list(keyed(read_jsonl(args.candidates, Candidate), args.candidates).values())
    settings = LlmSettings()
    guard_output(args.out, settings.backend == "fake" or any(c.fake for c in candidates))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    # redacted trace lines go to a git-ignored file; full records to M13 when it is reachable
    with args.out.with_suffix(".trace.log").open("a", encoding="utf-8") as trace_log:
        tracer = TraceClient(os.environ.get("AGENTOPS_URL"), stream=trace_log)
        llm = LLMClient(settings, tracer=tracer)
        try:
            return await draft_labels(
                candidates,
                args.out,
                llm,
                alias=args.alias,
                rpm=args.rpm,
                max_calls=args.max_calls,
            )
        finally:
            await tracer.aclose()
            await llm.aclose()


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--candidates", type=Path, default=DATA_DIR / "candidates.jsonl")
    parser.add_argument("--out", type=Path, default=DATA_DIR / "drafts.jsonl")
    parser.add_argument("--alias", default=ALIAS, help="model alias of the teacher")
    parser.add_argument("--rpm", type=float, default=10.0, help="calls per minute")
    parser.add_argument("--max-calls", type=int, default=600, help="budget of this run")
    args = parser.parse_args(argv)
    if args.rpm <= 0 or args.max_calls < 1:
        parser.error("--rpm and --max-calls must be positive")
    try:
        summary = asyncio.run(_run(args))
    except (DataError, LLMError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(
        f"calls {summary.calls}: labelled {summary.labelled}, rejected {summary.rejected}, "
        f"left {summary.remaining}; tokens {summary.tokens_in}/{summary.tokens_out}, "
        f"cost ${summary.cost_usd:.4f}"
    )
    if summary.stopped:
        print(f"stopped: {summary.stopped}. Run again to resume.")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
