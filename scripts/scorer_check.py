#!/usr/bin/env python
"""Run the ten fixture (idea, evidence) pairs through M10 and report validity and latency.

``--url http://127.0.0.1:8010``   against a running scorer service.
``--record FILE``                 in this process (SCORER_* / OLLAMA_BASE_URL from the
                                  environment), writing every local model reply to FILE so the
                                  behaviour tests can replay it (AO-7).
Exit code 1 when a response is not a valid ``DimensionScore`` for its request.
"""

import argparse
import asyncio
import io
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from common.contracts.agents import ScoreRequest
from common.contracts.core import DimensionScore

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "eval/fixtures/score_requests.jsonl"


def load_requests(path: Path = FIXTURES) -> list[ScoreRequest]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [ScoreRequest.model_validate_json(line) for line in lines if line.strip()]


def check(request: ScoreRequest, result: DimensionScore) -> list[str]:
    problems = []
    if result.rubric_version != request.rubric_id:
        problems.append("wrong rubric_version")
    if not set(result.evidence_ids) <= {e.evidence_id for e in request.evidence}:
        problems.append("cites an id that was not provided")
    if result.score is not None and not (result.score * 2).is_integer():
        problems.append("score is not a half step")
    return problems


async def run(args: argparse.Namespace) -> int:
    records: list[dict[str, Any]] = []
    client: httpx.AsyncClient | None = None
    scorer: Any = None
    if args.record:
        sys.path.insert(0, str(ROOT / "services/scorer/src"))
        from scorer.main import build_scorer
        from scorer.settings import ScorerSettings

        scorer = build_scorer(ScorerSettings())
        if scorer is None or scorer.local is None:
            print("recording needs a local backend and its address", file=sys.stderr)
            return 2
        scorer.tracer._stream = io.StringIO()  # keeps trace lines out of the report
        inner = scorer.local.complete

        async def recording(*a: Any, **kw: Any) -> Any:
            reply = await inner(*a, **kw)
            records.append(
                {
                    "agent": "scorer",
                    "alias": "scorer-local",
                    "case": current,
                    "model": ScorerSettings().local_model,
                    "content": reply.content,
                    "tokens_in": reply.tokens_in,
                    "tokens_out": reply.tokens_out,
                }
            )
            return reply

        scorer.local.complete = recording
    else:
        client = httpx.AsyncClient(base_url=args.url, timeout=120)

    latencies, rows, failed, invalid = [], [], 0, 0
    for current, request in enumerate(load_requests()):
        start = time.perf_counter()
        if client is None:
            result = await scorer.score(request)
        else:
            response = await client.post("/score", json=request.model_dump(mode="json"))
            if response.status_code != 200:
                print(f"{current} {request.rubric_id}: HTTP {response.status_code}")
                failed += 1
                continue
            result = DimensionScore.model_validate_json(response.text)
        seconds = time.perf_counter() - start
        latencies.append(seconds)
        problems = check(request, result)
        invalid += bool(problems)
        rows.append(result)
        print(
            f"{current} {request.rubric_id:20} score={result.score} conf={result.confidence} "
            f"ids={len(result.evidence_ids)} model={result.model} {seconds:.1f}s"
            + (f" PROBLEMS {problems}" if problems else "")
        )
        if result.score is None:
            print(f"    abstained: {result.reason}")
    if client is not None:
        await client.aclose()
    if args.record:
        text = "".join(json.dumps(r) + "\n" for r in records)
        await asyncio.to_thread(Path(args.record).write_text, text, encoding="utf-8")
    if latencies:
        ordered = sorted(latencies)
        scored = sum(r.score is not None for r in rows)
        print(
            f"valid responses {len(rows) - invalid}/10, scored {scored},"
            f" models {sorted({r.model for r in rows})}\n"
            f"latency s: p50={statistics.median(ordered):.2f} max={ordered[-1]:.2f}"
        )
    return 1 if failed or invalid or len(rows) != 10 else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--url")
    target.add_argument("--record")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
