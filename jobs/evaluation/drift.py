"""The drift report made after each ingestion (AO-5, M13-5, architecture §15).

    python jobs/evaluation/drift.py

Freshness and niche coverage come from M6 ``GET /stats`` (KB_URL); the verdict mix from M13's
``ops`` database, where the report is also stored, so ``GET /drift/latest`` returns it. Runs of
the evaluation harness are not students' verdicts and are left out.
"""

import argparse
import asyncio
import json
import math
import os
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

import reports
from agentops.repositories.index import TraceIndex
from common.contracts.core import VerdictLabel
from common.contracts.kb import KbStats
from common.http import ServiceClient, ServiceError

THIN_NICHE_CARDS = 10
CURRENT_WINDOW = timedelta(days=7)
BASELINE_WINDOW = timedelta(days=28)  # the days before the current window
MIN_VERDICTS = 10  # in each window; fewer say nothing about a distribution
SMOOTHING = 0.5  # added to every count, so an unseen label does not make the index infinite
# user ids of run_eval.py
EVAL_USER_PREFIX = "u_eval_"


def freshness(stats: KbStats) -> dict[str, Any]:
    return {
        "median_age_days": stats.median_age_days,
        "pct_fresh_90d_by_source": stats.pct_fresh_90d_by_source,
        "last_ingestion_by_source": {
            source: when.isoformat() for source, when in stats.last_ingestion_by_source.items()
        },
        "cards_by_source": stats.cards_by_source,
    }


def coverage(stats: KbStats, previous: Mapping[str, Any] | None) -> dict[str, Any]:
    """The share of niches with enough cards, and what changed since the last report."""
    niches = stats.cards_by_niche
    thin = sorted(name for name, cards in niches.items() if cards < THIN_NICHE_CARDS)
    share = (len(niches) - len(thin)) / len(niches) if niches else None
    old = (previous or {}).get("coverage") or {}
    before = old.get("covered_share")
    return {
        "niches": len(niches),
        "cards": sum(stats.cards_by_source.values()),
        "thin_niche_limit": THIN_NICHE_CARDS,
        "thin_niches": thin,
        "covered_share": share,
        "previous_covered_share": before,
        "change": None if share is None or before is None else share - before,
        "newly_thin": sorted(set(thin) - set(old.get("thin_niches", []))) if old else [],
    }


def psi(current: Mapping[str, int], baseline: Mapping[str, int]) -> float:
    """Population stability index over the four verdict labels."""
    labels = [label.value for label in VerdictLabel]
    totals = [sum(c.get(x, 0) for x in labels) + SMOOTHING * len(labels)
              for c in (current, baseline)]  # fmt: skip
    value = 0.0
    for label in labels:
        p = (current.get(label, 0) + SMOOTHING) / totals[0]
        q = (baseline.get(label, 0) + SMOOTHING) / totals[1]
        value += (p - q) * math.log(p / q)
    return value


def verdict_mix(rows: Sequence[TraceIndex], now: datetime) -> dict[str, Any]:
    current: Counter[str] = Counter()
    baseline: Counter[str] = Counter()
    for row in rows:
        if (row.user_id or "").startswith(EVAL_USER_PREFIX):
            continue
        finished = row.finished_at
        age = now - (finished if finished.tzinfo else finished.replace(tzinfo=UTC))
        if age < CURRENT_WINDOW:
            current.update(row.metrics["verdicts"])
        elif age < CURRENT_WINDOW + BASELINE_WINDOW:
            baseline.update(row.metrics["verdicts"])
    enough = min(sum(current.values()), sum(baseline.values())) >= MIN_VERDICTS
    return {
        "current_days": CURRENT_WINDOW.days,
        "baseline_days": BASELINE_WINDOW.days,
        "current": dict(current),
        "baseline": dict(baseline),
        "psi": psi(current, baseline) if enough else None,
        "note": None if enough else f"not enough data: {MIN_VERDICTS} verdicts per window needed",
    }


def build_report(
    stats: KbStats,
    rows: Sequence[TraceIndex],
    previous: Mapping[str, Any] | None,
    *,
    now: datetime,
    sha: str,
    fake: bool,
) -> dict[str, Any]:
    return {
        "kind": "drift",
        "git_sha": sha,
        "created_at": now.isoformat(),
        "fake": fake,
        "metrics": {
            "kb_version": stats.kb_version,
            "freshness": freshness(stats),
            "coverage": coverage(stats, previous),
            "verdict_mix": verdict_mix(rows, now),
        },
    }


async def kb_stats(client: ServiceClient) -> KbStats:
    async with client:
        stats: KbStats = await client.get("/stats", response_model=KbStats)
    return stats


def main(argv: Sequence[str] | None = None, kb: ServiceClient | None = None) -> int:
    """``kb``: the tests' way in; otherwise KB_URL names M6."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--out", type=Path, default=reports.RESULTS_DIR)
    parser.add_argument("--fake", action="store_true", help="the knowledge base is made up")
    args = parser.parse_args(argv)
    try:
        if kb is None:
            if not os.environ.get("KB_URL"):
                raise reports.OutputError("KB_URL is not set")
            kb = ServiceClient(os.environ["KB_URL"])
        now = datetime.now(UTC)
        name = f"drift-{now:%Y%m%dT%H%M%SZ}"
        reports.guard(args.out / f"{name}.json", args.fake)
        stats = asyncio.run(kb_stats(kb))
        index = reports.database()
        try:
            rows = index.all()
            previous = index.latest_report("drift")
        finally:
            index.dispose()
        report = build_report(
            stats, rows, previous.metrics if previous else None,
            now=now, sha=reports.git_sha(), fake=args.fake,
        )  # fmt: skip
        path = reports.write_report(args.out, name, report)
        if not args.fake:
            reports.store(report, path)
    except ServiceError as exc:
        print(f"error: the knowledge base is not available: {exc.summary}", file=sys.stderr)
        return 2
    except SQLAlchemyError as exc:
        print(f"error: the ops database is not available: {type(exc).__name__}", file=sys.stderr)
        return 2
    except reports.OutputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report["metrics"], indent=2, sort_keys=True))
    print(f"report: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
