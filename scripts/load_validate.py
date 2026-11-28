#!/usr/bin/env python
"""Run N Validate runs at once and report statuses, latencies and errors (K8S-5).

    ORCHESTRATOR_URL=... PROFILE_URL=... AGENTOPS_URL=... python scripts/load_validate.py -n 20

The ideas are those of ``eval/labelled``, repeated as needed. Every run has its own user, so no
verdict comes from the cache; the users start with ``u_eval_``, which keeps them out of the drift
report. Questions get a non-answer and approvals are declined, as in ``run_eval.py``.

Exit code 0: every run completed with a verdict; 1: at least one did not; 2: nothing was run.
A report with ``"stand_in": true`` was made against ``scripts/standins``: it is no result.
"""

import argparse
import asyncio
import json
import os
import sys
import time
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "jobs/evaluation"), str(ROOT / "scripts")]

import labelled  # noqa: E402
import reports  # noqa: E402
import run_eval  # noqa: E402
from sample_resources import percentile  # noqa: E402

URL_VARIABLES = ("ORCHESTRATOR_URL", "PROFILE_URL", "AGENTOPS_URL")


def usable(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.hostname)


def stand_in(profile_url: str, transport: httpx.BaseTransport | None = None) -> bool | None:
    """True: the stand-in answers for M2. False: something else does. None: nothing answers."""
    try:
        with httpx.Client(base_url=profile_url, transport=transport, timeout=5) as client:
            reply = client.get("/stand-in")
        return reply.status_code == 200 and reply.json().get("stand_in") is True
    except httpx.HTTPError:
        return None
    except (ValueError, AttributeError):
        return False


async def load(
    harness: Any, ideas: Sequence[labelled.LabelledIdea], runs: int, concurrency: int
) -> list[dict[str, Any]]:
    slots = asyncio.Semaphore(concurrency)

    async def one(number: int) -> dict[str, Any]:
        base = ideas[number % len(ideas)]
        idea = base.model_copy(update={"idea_id": f"{base.idea_id}-{number}"})
        async with slots:
            start = time.monotonic()
            try:
                entry: dict[str, Any] = await harness.run(idea)
            except Exception as exc:  # one run must not hide the others; the type only
                entry = {"idea_id": idea.idea_id, "status": None, "verdict": None,
                         "error": type(exc).__name__}  # fmt: skip
            entry["seconds"] = round(time.monotonic() - start, 1)
        print(
            f"{idea.idea_id}: {entry['verdict'] or entry['error']} in {entry['seconds']}s",
            file=sys.stderr,
        )
        return entry

    return list(await asyncio.gather(*(one(n) for n in range(runs))))


def latencies(entries: Sequence[dict[str, Any]]) -> dict[str, float] | None:
    seconds = [e["seconds"] for e in entries]
    if not seconds:
        return None
    return {"p50": percentile(seconds, 0.5), "p95": percentile(seconds, 0.95), "max": max(seconds)}


def summarise(entries: Sequence[dict[str, Any]]) -> dict[str, Any]:
    done = [e for e in entries if e["error"] is None]
    return {
        "runs": len(entries),
        "completed": len(done),
        "statuses": dict(Counter(str(e["status"]) for e in entries)),
        "errors": dict(Counter(e["error"] for e in entries if e["error"] is not None)),
        "verdicts": dict(Counter(e["verdict"] for e in done)),
        # a model too slow for the load shows up here, not as an error
        "abstained_dimensions": sum(
            score is None for e in done for score in e.get("scores", {}).values()
        ),
        "scored_dimensions": sum(
            score is not None for e in done for score in e.get("scores", {}).values()
        ),
        "latency_s_completed": latencies(done),
        "latency_s_not_completed": latencies([e for e in entries if e["error"] is not None]),
    }


def main(argv: Sequence[str] | None = None, harness: Any = None, is_stand_in: Any = None) -> int:
    """``harness`` and ``is_stand_in``: the tests' way in."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("-n", "--runs", type=int, default=20)
    parser.add_argument("-c", "--concurrency", type=int, help="default: all at once")
    parser.add_argument("--labelled", type=Path, default=labelled.LABELLED_DIR)
    parser.add_argument("--out", type=Path, help="the report as JSON")
    parser.add_argument("--idea-timeout", type=float, default=900)
    parser.add_argument("--poll", type=float, default=2)
    args = parser.parse_args(argv)
    concurrency = args.concurrency if args.concurrency is not None else args.runs
    if args.runs < 1 or concurrency < 1:
        parser.error("--runs and --concurrency must be at least 1")
    if not (args.poll > 0 and args.idea_timeout > 0):
        parser.error("--poll and --idea-timeout must be above 0")
    sha = reports.git_sha()
    started = datetime.now(UTC)
    try:
        ideas = labelled.load_ideas(args.labelled / "ideas.jsonl")
        if harness is None:
            harness = run_eval.harness_from_env(args, f"load_{sha}_{int(time.time())}")
            # an unusable URL: stop before every run is counted as failed
            if bad := [n for n in URL_VARIABLES if not usable(os.environ[n])]:
                print(f"error: not an HTTP URL: {', '.join(bad)}", file=sys.stderr)
                return 2
            is_stand_in = stand_in(os.environ["PROFILE_URL"])
    except labelled.LabelError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    clock = time.monotonic()
    entries = asyncio.run(load(harness, ideas, args.runs, concurrency))
    # the data says so itself too (Z11-6): the probe asks only M2, once, and may time out
    if any((e.get("versions") or {}).get("kb_version") == reports.STAND_IN_KB for e in entries):
        is_stand_in = True
    report = {
        "kind": "load",
        "git_sha": sha,
        "created_at": started.isoformat(),
        "stand_in": is_stand_in,
        "concurrency": concurrency,
        "wall_s": round(time.monotonic() - clock, 1),
        **summarise(entries),
        "entries": [
            {k: e.get(k) for k in ("idea_id", "run_id", "status", "verdict", "error", "seconds")}
            for e in entries
        ],
    }
    # printed before it is written: a wrong --out must not lose a long load
    print(json.dumps({k: v for k, v in report.items() if k != "entries"}, indent=2))
    if is_stand_in:
        print("STAND-IN: these numbers are no result of the system", file=sys.stderr)
    if args.out:
        try:
            reports.write_text(args.out, json.dumps(report, indent=2) + "\n")
        except reports.OutputError as exc:
            print(f"error: {exc}", file=sys.stderr)
    return 0 if report["completed"] == args.runs else 1


if __name__ == "__main__":
    sys.exit(main())
