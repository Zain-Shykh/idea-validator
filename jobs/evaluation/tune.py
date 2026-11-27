"""Tune T_go, T_no, the floor and the weights against the human verdicts (EVAL-3/4, VE-10).

    python jobs/evaluation/tune.py --results eval/results/eval-<sha>.json

No model is called: every candidate rule set decides again, with the real M11 engine, on the
inputs M3 sent during the evaluation run. A candidate is ranked by (GO on a human NO-GO, wrong
verdicts, distance from the current rules), so a tie keeps what is there. k-fold cross-validation
shows how the tuned choice does on ideas it was not chosen on.

It writes a report. It never changes ``config/verdict.yaml``: a person does, with a version bump.
"""

import argparse
import hashlib
import statistics
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import permutations, product
from pathlib import Path
from typing import Any

from pydantic import ValidationError

import eval_metrics
import labelled
import reports
from common.contracts.core import Dimension, VerdictLabel, VerdictRequest
from verdict.config import VerdictConfig, load_config
from verdict.engine import label_only

T_GO = (60.0, 65.0, 70.0, 75.0, 80.0)
T_NO = (40.0, 45.0, 50.0, 55.0, 60.0)
FLOOR = (1.0, 1.5, 2.0)
WEIGHT_STEP = 5  # points moved from one dimension to another, in every goal preset
FOLDS = 5


class TuneError(Exception):
    """There is nothing sound to tune on."""


@dataclass(frozen=True)
class Candidate:
    changes: dict[str, Any]  # what differs from the current rules
    distance: tuple[int, float]
    config: VerdictConfig


def candidates(
    base: VerdictConfig,
    t_go: Sequence[float] = T_GO,
    t_no: Sequence[float] = T_NO,
    floor: Sequence[float] = FLOOR,
    weight_step: float = WEIGHT_STEP,
) -> list[Candidate]:
    """The current rules first, then every valid combination of the grid."""
    now = base.thresholds
    moves: list[tuple[Dimension, Dimension] | None] = [None]
    if weight_step:
        moves += list(permutations(Dimension, 2))
    found = [Candidate({}, (0, 0.0), base)]
    for go, no, low, move in product(
        sorted({*t_go, now.T_go}), sorted({*t_no, now.T_no}),
        sorted({*floor, now.floor}), moves,
    ):  # fmt: skip
        changes: dict[str, Any] = {
            name: value
            for name, value, current in (
                ("T_go", go, now.T_go),
                ("T_no", no, now.T_no),
                ("floor", low, now.floor),
            )
            if value != current
        }
        if move:
            changes["weights"] = f"{weight_step:g} points from {move[0]} to {move[1]}"
        if not changes:
            continue
        data = base.model_dump(mode="json")
        data["thresholds"] = {"T_go": go, "T_no": no, "floor": low}
        if move:
            for preset in data["weights"].values():
                preset[move[0].value] -= weight_step
                preset[move[1].value] += weight_step
        try:  # the same rules a committed file must meet
            config = VerdictConfig.model_validate(data)
        except ValidationError:
            continue
        size = abs(go - now.T_go) + abs(no - now.T_no) + 10 * abs(low - now.floor)
        size += weight_step if move else 0
        found.append(Candidate(changes, (len(changes), size), config))
    return found


def folds(human: Mapping[str, VerdictLabel], k: int) -> list[list[str]]:
    """Stratified by human verdict, and fixed: ids are dealt out in the order of their hash."""
    if k < 2 or len(human) < 2 * k:
        raise TuneError(f"{len(human)} labelled ideas are too few for {k} folds")
    ordered = sorted(human, key=lambda i: (human[i].value, hashlib.sha256(i.encode()).hexdigest()))
    return [ordered[n::k] for n in range(k)]


def best(
    table: Sequence[Mapping[str, VerdictLabel]],
    rules: Sequence[Candidate],
    human: Mapping[str, VerdictLabel],
    ids: Sequence[str],
) -> int:
    def rank(n: int) -> tuple[int, int, tuple[int, float], int]:
        system = table[n]
        false_go = len(eval_metrics.false_go_ids({i: human[i] for i in ids}, system))
        return false_go, sum(human[i] != system[i] for i in ids), rules[n].distance, n

    return min(range(len(rules)), key=rank)


def _score(
    system: Mapping[str, VerdictLabel], human: Mapping[str, VerdictLabel], ids: Sequence[str]
) -> tuple[int, int]:
    """(right, false GO on NO-GO) over ``ids``."""
    wrong_go = eval_metrics.false_go_ids({i: human[i] for i in ids}, system)
    return sum(human[i] == system[i] for i in ids), len(wrong_go)


def cross_validate(
    table: Sequence[Mapping[str, VerdictLabel]],
    rules: Sequence[Candidate],
    human: Mapping[str, VerdictLabel],
    k: int,
) -> dict[str, Any]:
    """Choose on k-1 folds, measure on the one left out."""
    rows: list[dict[str, Any]] = []
    for number, held_out in enumerate(folds(human, k), 1):
        train = [i for i in human if i not in held_out]
        chosen = best(table, rules, human, train)
        row: dict[str, Any] = {"fold": number, "n": len(held_out), "chosen": rules[chosen].changes}
        row["train_agreement"] = _score(table[chosen], human, train)[0] / len(train)
        for name, system in (("tuned", table[chosen]), ("current", table[0])):
            right, false_go = _score(system, human, held_out)
            row[f"{name}_agreement"] = right / len(held_out)
            row[f"{name}_right"], row[f"{name}_false_go"] = right, false_go
        rows.append(row)
    summary: dict[str, Any] = {"k": k, "folds": rows}
    for name in ("tuned", "current"):
        values = [r[f"{name}_agreement"] for r in rows]
        summary[name] = {
            "held_out_agreement": sum(r[f"{name}_right"] for r in rows) / len(human),
            "fold_mean": statistics.fmean(values),
            "fold_stdev": statistics.stdev(values),
            "false_go_on_no_go": sum(r[f"{name}_false_go"] for r in rows),
        }
    train_mean = statistics.fmean(r["train_agreement"] for r in rows)
    summary["train_agreement_mean"] = train_mean
    summary["train_test_gap"] = train_mean - summary["tuned"]["fold_mean"]
    return summary


def tune(
    requests: Mapping[str, VerdictRequest],
    human: Mapping[str, VerdictLabel],
    rules: Sequence[Candidate],
    k: int = FOLDS,
) -> dict[str, Any]:
    """``rules[0]`` must be the current rules; every idea of ``requests`` needs a verdict."""
    mine = {i: human[i] for i in sorted(requests)}
    table = [{i: label_only(requests[i], c.config) for i in mine} for c in rules]
    validation = cross_validate(table, rules, mine, k)
    final = best(table, rules, mine, list(mine))
    tuned, current = validation["tuned"], validation["current"]
    # adopt only what also wins on ideas it was not chosen on, without more false GOs
    adopt = bool(
        rules[final].changes
        and tuned["held_out_agreement"] > current["held_out_agreement"]
        and tuned["false_go_on_no_go"] <= current["false_go_on_no_go"]
    )
    chosen = rules[final].config
    return {
        "n_ideas": len(mine),
        "n_candidates": len(rules),
        "recommendation": "adopt" if adopt else "keep",
        "best_on_all": {
            "changes": rules[final].changes,
            "thresholds": chosen.thresholds.model_dump(),
            "weights": chosen.model_dump(mode="json")["weights"],
        },
        "before": eval_metrics.verdict_metrics(mine, table[0]),
        "after": eval_metrics.verdict_metrics(mine, table[final]),
        "cross_validation": validation,
        "current_labels": {i: table[0][i].value for i in mine},
    }


def _matrix(confusion: Mapping[str, Mapping[str, int]]) -> list[str]:
    names = [label.value for label in VerdictLabel]
    lines = ["| human \\ system | " + " | ".join(names) + " |", "|---" * (len(names) + 1) + "|"]
    return lines + [
        f"| {h} | " + " | ".join(str(confusion[h][s]) for s in names) + " |" for h in names
    ]


def markdown(report: Mapping[str, Any]) -> str:
    result = report["result"]
    lines = [f"# Verdict tuning ({report['git_sha']})", ""]
    if report["fake"]:
        lines += ["**FAKE input: these numbers are not results.**", ""]
    lines += [
        f"{result['n_ideas']} ideas, {result['n_candidates']} candidate rule sets. "
        f"Recommendation: **{result['recommendation']}**. Best on all ideas: "
        f"`{result['best_on_all']['changes'] or 'the current rules'}`.",
        "",
    ]
    for name, title in (("before", "Before (current rules)"), ("after", "After (best on all)")):
        found = result[name]
        kappa = "not defined" if found["kappa"] is None else f"{found['kappa']:.2f}"
        lines += [
            f"## {title}",
            "",
            f"Agreement {found['agreement']:.1%}, kappa {kappa}, **GO on a human NO-GO: "
            f"{found['false_go_on_no_go']} of {found['human_no_go']}** "
            f"{found['false_go_on_no_go_ids']}",
            "",
            *_matrix(found["confusion"]),
            "",
        ]
    check = result["cross_validation"]
    lines += [
        f"## {check['k']}-fold cross-validation",
        "",
        "| fold | n | chosen on the other folds | train | held out (tuned) | held out (current) |",
        "|---|---|---|---|---|---|",
        *(
            f"| {r['fold']} | {r['n']} | {r['chosen'] or 'current'} | "
            f"{r['train_agreement']:.1%} | {r['tuned_agreement']:.1%} | "
            f"{r['current_agreement']:.1%} |"
            for r in check["folds"]
        ),
        "",
    ]
    for name in ("tuned", "current"):
        row = check[name]
        lines.append(
            f"- {name}: held-out agreement {row['held_out_agreement']:.1%} (fold stdev "
            f"{row['fold_stdev']:.2f}), false GO {row['false_go_on_no_go']}"
        )
    lines.append(f"- train minus held-out gap of the tuned choice: {check['train_test_gap']:+.2f}")
    lines += ["", *(f"> Warning: {w}" for w in report["warnings"])]
    return "\n".join(lines) + "\n"


def load_requests(report: Mapping[str, Any]) -> tuple[dict[str, VerdictRequest], list[str]]:
    """The engine inputs of an evaluation report, and the ideas that have none."""
    if report.get("kind") != "eval" or not isinstance(report.get("ideas"), list):
        raise TuneError("not an evaluation report with its ideas")
    found, without = {}, []
    for entry in report["ideas"]:
        try:
            if entry.get("request") is None:
                without.append(str(entry["idea_id"]))
            else:
                found[str(entry["idea_id"])] = VerdictRequest.model_validate(entry["request"])
        except (KeyError, AttributeError, ValidationError) as exc:
            raise TuneError("an idea in the report cannot be read") from exc
    return found, without


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--results", type=Path, required=True, help="a run_eval.py report")
    parser.add_argument("--labels", type=Path, default=labelled.RESOLVED)
    parser.add_argument("--config", type=Path, default=reports.ROOT / "config/verdict.yaml")
    parser.add_argument("--out", type=Path, default=reports.RESULTS_DIR)
    parser.add_argument("--folds", type=int, default=FOLDS)
    args = parser.parse_args(argv)
    try:
        evaluation = reports.read_report(args.results)
        requests, without = load_requests(evaluation)
        known = [str(e["idea_id"]) for e in evaluation["ideas"]]
        human = labelled.load_resolved(args.labels, set(known))
        if missing := sorted(set(requests) - human.keys()):
            raise TuneError(f"no human verdict for {', '.join(missing)}")
        fake = (
            evaluation.get("fake") is not False
            or reports.made_up(args.labels)
            or reports.made_up(args.results)
        )
        reports.guard(args.out / "tune.json", fake)
        base = load_config(args.config)
        result = tune(requests, human, candidates(base), args.folds)
        warnings = []
        if without:
            warnings.append(f"ideas without a verdict are not tuned on: {', '.join(without)}")
        decided = {str(e["idea_id"]): e.get("verdict") for e in evaluation["ideas"]}
        if differ := [i for i, v in result["current_labels"].items() if decided[i] != v]:
            warnings.append(
                f"{args.config.name} does not reproduce the stored verdict of "
                f"{', '.join(differ)}: the run used other rules"
            )
        report = {
            "kind": "tune",
            "git_sha": reports.git_sha(),
            "created_at": datetime.now(UTC).isoformat(),
            "fake": fake,
            "from_results": args.results.name,
            "labels_sha256": hashlib.sha256(args.labels.read_bytes()).hexdigest(),
            "rules_version": base.version,
            "warnings": warnings,
            "result": result,
        }
        path = reports.write_report(args.out, "tune", report)
        reports.write_text(path.with_suffix(".md"), markdown(report))
    except (
        TuneError, labelled.LabelError, reports.OutputError, OSError, ValueError,
    ) as exc:  # fmt: skip
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(markdown(report))
    print(f"report: {path} (and .md). config/verdict.yaml is unchanged.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
