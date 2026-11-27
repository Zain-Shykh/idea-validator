"""Step 6: base vs fine-tuned vs hosted on the hand-checked test rows (FT-8, M10-4).

    python jobs/finetune/evaluate.py --base-url http://127.0.0.1:8081 --tuned-url http://127.0.0.1:8082

Every model gets each test prompt once: no repair, no fallback, so the table shows the model and
not the serving code around it. ``--base-url`` and ``--tuned-url`` are OpenAI-compatible servers
(llama-server or Ollama); the hosted model is the alias ``scorer-hosted`` with the usual LLM
settings. Why the fine-tune did not help in the picked case is for a person to write.
"""

import argparse
import asyncio
import hashlib
import json
import os
import statistics
import sys
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from common.contracts.agents import FineTuneExample
from common.contracts.tools import ChatMessage
from common.llm import LLMClient, LLMError, LLMResult, describe_errors, strip_fence
from common.settings import LlmSettings
from common.trace import TraceClient
from dataset import (
    DATA_DIR,
    RESULTS_DIR,
    DataError,
    Item,
    clean,
    example_id,
    guard_output,
    keyed,
    label_of,
    prompt_ids,
    read_checks,
    read_jsonl,
    write_text,
)
from scorer.services.backend import LocalModel, LocalModelError, reply_schema
from scorer.services.scoring import HOSTED_ALIAS, MAX_TOKENS, ModelReply

MODELS = ("base", "tuned", "hosted")
AGENT = "finetune_eval"
REPORT = "finetune-comparison"
TODO = "TODO: a person explains why fine-tuning did not help here."
_RAW_CHARS = 2000


@dataclass(frozen=True)
class Reply:
    content: str
    model: str
    tokens_in: int
    tokens_out: int
    cost_usd: float = 0.0
    cache_hit: bool = False


# item id, messages, number of evidence items
Ask = Callable[[str, list[ChatMessage], int], Awaitable[Reply]]


@dataclass(frozen=True)
class Row:
    item_id: str
    dimension: str
    messages: list[ChatMessage]
    allowed: list[str]
    human: float


def load_test_set(folder: Path) -> tuple[list[Row], dict[str, Any]]:
    """``test.jsonl``, refused unless each label is the one a person put in ``checked.csv``."""
    path = folder / "test.jsonl"
    examples = read_jsonl(path, FineTuneExample)
    if not examples:
        raise DataError(f"{path}: no test rows")
    items = keyed(read_jsonl(folder / "items.jsonl", Item), folder / "items.jsonl")
    checks = read_checks(folder / "checked.csv")
    rows: dict[str, Row] = {}
    for example in examples:
        key = example_id(example)
        check = checks.get(key)
        if check is None or check.label != label_of(example):
            raise DataError(f"{path}: row {key} is not the hand-checked label: run split.py again")
        if key not in items or key in rows:
            raise DataError(f"{path}: row {key} is unknown to items.jsonl or appears twice")
        rows[key] = Row(
            item_id=key,
            dimension=items[key].dimension.value,
            messages=[ChatMessage(role=m.role, content=m.content) for m in example.messages[:2]],
            allowed=prompt_ids(example.messages[1].content),
            human=label_of(example).score,
        )
    used = [checks[k] for k in rows]
    info = {
        "rows": len(rows),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "checked_by": {
            n: sum(c.checked_by == n for c in used) for n in sorted({c.checked_by for c in used})
        },
        "fixed_by_hand": sum(c.action == "fix" for c in used),
        "teacher_models": sorted({items[k].teacher_model for k in rows}),
        "fake": any(items[k].fake for k in rows),
    }
    return list(rows.values()), info


async def attempt(ask: Ask, row: Row, clock: Callable[[], float]) -> dict[str, Any]:
    """One call, judged as it came."""
    started = clock()
    try:
        reply = await ask(row.item_id, row.messages, len(row.allowed))
    except (LocalModelError, LLMError, TimeoutError) as exc:
        return {"error": (str(exc) or type(exc).__name__)[:300]}
    result: dict[str, Any] = {
        "model": reply.model,
        "latency_s": clock() - started,
        "tokens_in": reply.tokens_in,
        "tokens_out": reply.tokens_out,
        "cost_usd": reply.cost_usd,
        "cache_hit": reply.cache_hit,
        "valid": False,
        "raw": reply.content[:_RAW_CHARS],
    }
    try:
        # the serving code's own idea of a well-formed reply
        parsed = ModelReply.model_validate_json(strip_fence(reply.content))
    except ValidationError as exc:
        result["problem"] = describe_errors(exc, 3)[:300]
        return result
    # as M10 reads ids: "[card_x#c0]" is how the prompt brackets them
    cited = list(dict.fromkeys(i.strip(" []") for i in parsed.evidence_ids))
    result |= {
        "valid": True,
        "score": parsed.score,
        "ids_cited": len(cited),
        "ids_real": sum(i in row.allowed for i in cited),
    }
    return result


def _ratio(part: float, whole: float) -> float | None:
    return part / whole if whole else None


def _mae(rows: Sequence[Row], results: Mapping[str, dict[str, Any]]) -> float | None:
    errors = [abs(results[r.item_id]["score"] - r.human) for r in rows]
    return _ratio(sum(errors), len(errors))


def metrics(
    rows: Sequence[Row], results: Mapping[str, dict[str, Any]], common: Sequence[Row]
) -> dict[str, Any]:
    replies = [x for x in results.values() if "error" not in x]
    valid = [r for r in rows if results[r.item_id].get("valid")]
    # a cached reply says nothing about the model's speed
    latencies = [x["latency_s"] for x in replies if not x["cache_hit"]]
    return {
        "replies": len(replies),
        "errors": len(rows) - len(replies),
        "valid": len(valid),
        "mae": _mae(valid, results),
        "mae_on_rows_all_models_scored": _mae(common, results),
        "within_1": _ratio(
            sum(abs(results[r.item_id]["score"] - r.human) <= 1 for r in valid), len(valid)
        ),
        "valid_json_rate": _ratio(len(valid), len(replies)),
        "real_evidence_id_rate": _ratio(
            sum(results[r.item_id]["ids_real"] for r in valid),
            sum(results[r.item_id]["ids_cited"] for r in valid),
        ),
        "latency_p50_s": statistics.median(latencies) if latencies else None,
        "latency_mean_s": statistics.fmean(latencies) if latencies else None,
        "cache_hits": sum(x["cache_hit"] for x in replies),
        "cost_per_score_usd": _ratio(sum(x["cost_usd"] for x in replies), len(replies)),
        "tokens_in_per_score": _ratio(sum(x["tokens_in"] for x in replies), len(replies)),
        "tokens_out_per_score": _ratio(sum(x["tokens_out"] for x in replies), len(replies)),
    }


def did_not_help(
    rows: Sequence[Row], results: Mapping[str, Mapping[str, dict[str, Any]]]
) -> dict[str, Any] | None:
    """The row where the fine-tune lost most to the base model; else one where it only tied it
    and was still wrong. A reply that is not valid counts as worse than any score."""

    def error(model: str, row: Row) -> float | None:
        result = results[model][row.item_id]
        if "error" in result:
            return None  # a failed call says nothing about the model
        return abs(result["score"] - row.human) if result["valid"] else 6.0

    found: list[tuple[float, float, str, Row]] = []
    for row in rows:
        base, tuned = error("base", row), error("tuned", row)
        if base is not None and tuned is not None and tuned > 0 and tuned >= base:
            found.append((tuned - base, tuned, row.item_id, row))
    if not found:
        return None
    gap, _, key, row = max(found, key=lambda f: f[:3])
    return {
        "item_id": key,
        "dimension": row.dimension,
        "kind": "worse than base" if gap > 0 else "no better than base",
        "human_score": row.human,
        "prompt": row.messages[1].content,
        "replies": {m: results[m][key] for m in MODELS},
    }


async def evaluate(
    folder: Path,
    asks: Mapping[str, Ask],
    *,
    waits: Mapping[str, float] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], float] = time.perf_counter,
) -> dict[str, Any]:
    """``waits``: seconds between two calls of a model (rate limits), outside its latency."""
    rows, test = load_test_set(folder)
    results: dict[str, dict[str, dict[str, Any]]] = {}
    for name in MODELS:
        results[name] = {}
        for index, row in enumerate(rows):
            if index and (wait := (waits or {}).get(name)):
                await sleep(wait)
            results[name][row.item_id] = await attempt(asks[name], row, clock)
        if all("error" in x for x in results[name].values()):
            first = next(iter(results[name].values()))["error"]
            raise DataError(f"{name}: no reply to any row ({first}); no report written")
    common = [r for r in rows if all(results[m][r.item_id].get("valid") for m in MODELS)]
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "test_set": test,
        "rows_all_models_scored": len(common),
        "models": {
            name: {
                "answered_as": sorted({x["model"] for x in results[name].values() if "model" in x}),
                **metrics(rows, results[name], common),
            }
            for name in MODELS
        },
        "did_not_help": did_not_help(rows, results),
        "rows": [
            {
                "item_id": r.item_id,
                "dimension": r.dimension,
                "human_score": r.human,
                **{m: results[m][r.item_id] for m in MODELS},
            }
            for r in rows
        ],
        "fake": test["fake"],
    }


_TABLE = (
    ("MAE vs human", "mae", "{:.2f}"),
    ("MAE on rows all three scored", "mae_on_rows_all_models_scored", "{:.2f}"),
    ("Within 1 point", "within_1", "{:.0%}"),
    ("Valid-JSON rate", "valid_json_rate", "{:.0%}"),
    ("Real-evidence-id rate", "real_evidence_id_rate", "{:.0%}"),
    ("Latency p50 (s)", "latency_p50_s", "{:.1f}"),
    ("Latency mean (s)", "latency_mean_s", "{:.1f}"),
    ("API cost per score (USD)", "cost_per_score_usd", "{:.6f}"),
    ("Tokens in / score", "tokens_in_per_score", "{:.0f}"),
    ("Tokens out / score", "tokens_out_per_score", "{:.0f}"),
    ("Valid replies", "valid", "{}"),
    ("Failed calls", "errors", "{}"),
)


def markdown(report: Mapping[str, Any]) -> str:
    test, models = report["test_set"], report["models"]
    lines = ["# Fine-tuning comparison (FT-8)", ""]
    if report["fake"]:
        lines += ["**FAKE: made from fake data or a fake model. Not a result.**", ""]
    lines += [
        f"{test['rows']} hand-checked test rows (sha256 `{test['sha256'][:16]}`), "
        f"{test['fixed_by_hand']} of them corrected by hand. "
        f"Generated {report['generated_at']}.",
        "",
        "| Metric | " + " | ".join(models) + " |",
        "|---|" + "---|" * len(models),
    ]
    for title, key, form in _TABLE:
        cells = ["n/a" if m[key] is None else form.format(m[key]) for m in models.values()]
        lines.append(f"| {title} | " + " | ".join(cells) + " |")
    lines += [
        "| Model | " + " | ".join(", ".join(m["answered_as"]) for m in models.values()) + " |",
        "",
        f"MAE and within-1 use each model's valid replies; {report['rows_all_models_scored']} "
        "rows were scored by all three. One attempt per row, without repair or fallback. "
        f"Local decoding: {report.get('local_decoding', 'not recorded')}. API cost is the "
        "provider's list price; local models have none (their cost is the CPU time above).",
        "",
        "**Known bias:** the hand checks started from the hosted teacher's draft "
        f"({', '.join(test['teacher_models'])}), which favours the hosted model in the error "
        "metrics.",
        "",
        "## Where fine-tuning did not help",
        "",
    ]
    case = report["did_not_help"]
    if case is None:
        lines.append(
            "No such row: the fine-tuned model was never wrong where the base model did as "
            "well or better."
        )
    else:
        # model replies and outside text: indented, so they cannot add markdown or HTML
        def block(text: str) -> list[str]:
            return ["", *(f"    {line}" for line in clean(text).split("\n")), ""]

        lines += [
            f"Row `{case['item_id']}` ({case['dimension']}), {case['kind']}. Human score: "
            f"{case['human_score']:g}.",
            "",
            "Prompt:",
            *block(case["prompt"]),
        ]
        for name, reply in case["replies"].items():
            lines += [f"{name} replied:", *block(reply.get("raw") or reply.get("error", ""))]
        lines += ["**Explanation:** " + TODO]
    return "\n".join(lines) + "\n"


# --- command line --------------------------------------------------------------------------------


def local_ask(server: LocalModel, name: str, timeout_s: float, constrained: bool) -> Ask:
    async def ask(_: str, messages: list[ChatMessage], n_evidence: int) -> Reply:
        async with asyncio.timeout(timeout_s):
            reply = await server.complete(
                messages,
                max_tokens=MAX_TOKENS,
                schema=reply_schema(n_evidence) if constrained else None,
            )
        return Reply(reply.content, name, reply.tokens_in, reply.tokens_out)

    return ask


def hosted_ask(llm: LLMClient) -> Ask:
    async def ask(item: str, messages: list[ChatMessage], _: int) -> Reply:
        # no schema: common.llm would add a repair retry
        reply: LLMResult[BaseModel] = await llm.chat(
            HOSTED_ALIAS, messages, agent=AGENT, run_id=f"run_ft_eval_{item}", max_tokens=400
        )
        return Reply(
            reply.content,
            reply.model,
            reply.tokens_in,
            reply.tokens_out,
            reply.cost_usd,
            reply.cache_hit,
        )

    return ask


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    settings = LlmSettings()
    fake = settings.backend == "fake" or load_test_set(args.data)[1]["fake"]
    for suffix in (".json", ".md"):
        target = args.out / f"{REPORT}{suffix}"
        guard_output(target, fake)
        if target.exists() and not args.force:
            raise DataError(f"{target} exists and may hold a written explanation; --force replaces")
    args.out.mkdir(parents=True, exist_ok=True)
    servers = {
        "base": (LocalModel(args.base_url, args.base_model, timeout_s=args.timeout_s)),
        "tuned": (LocalModel(args.tuned_url, args.tuned_model, timeout_s=args.timeout_s)),
    }
    names = {"base": args.base_model, "tuned": args.tuned_model}
    with (args.out / f"{REPORT}.trace.log").open("a", encoding="utf-8") as trace_log:
        tracer = TraceClient(os.environ.get("AGENTOPS_URL"), stream=trace_log)
        llm = LLMClient(settings, tracer=tracer)
        try:
            asks = {
                name: local_ask(server, names[name], args.timeout_s, args.constrained)
                for name, server in servers.items()
            }
            asks["hosted"] = hosted_ask(llm)
            report = await evaluate(args.data, asks, waits={"hosted": 60.0 / args.rpm})
        finally:
            for server in servers.values():
                await server.aclose()
            await tracer.aclose()
            await llm.aclose()
    report["fake"] = report["fake"] or fake
    report["local_decoding"] = (
        "constrained to the reply schema, as served" if args.constrained else "unconstrained"
    )
    write_text(args.out / f"{REPORT}.json", json.dumps(report, indent=2) + "\n")
    write_text(args.out / f"{REPORT}.md", markdown(report))
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--data", type=Path, default=DATA_DIR)
    parser.add_argument("--out", type=Path, default=RESULTS_DIR, help="folder")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--tuned-url", required=True)
    parser.add_argument("--base-model", default="qwen2.5-1.5b-instruct-q4_k_m")
    parser.add_argument("--tuned-model", default="scorer-lora-v1-q4_k_m")
    parser.add_argument("--constrained", action="store_true", help="local decoding as served")
    parser.add_argument("--timeout-s", type=float, default=180.0, help="per local reply")
    parser.add_argument("--rpm", type=float, default=10.0, help="hosted calls per minute")
    parser.add_argument("--force", action="store_true", help="replace an existing report")
    args = parser.parse_args(argv)
    if args.rpm <= 0 or args.timeout_s <= 0:
        parser.error("--rpm and --timeout-s must be positive")
    try:
        report = asyncio.run(_run(args))
    except (DataError, LLMError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(markdown(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
