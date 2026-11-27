"""Step 4: split by idea into train, validation and test (FT-4).

Ideas with a hand-checked row are the test set, and only their checked rows are in it, with the
checked label. Further ideas are held out for validation loss, so that training is never steered
by the test set. The assertions look at the rows as written, not at how they were chosen.
"""

import argparse
import hashlib
import json
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from common.contracts.agents import FineTuneExample
from dataset import (
    DATA_DIR,
    GROUP_SIMILARITY,
    LABELLED,
    Check,
    DataError,
    Item,
    LabelledIdea,
    example_id,
    grounded_label,
    groups,
    guard_output,
    idea_block,
    is_labelled,
    load_labelled,
    make_example,
    read_checks,
    similarity,
    stable_order,
    write_jsonl,
    write_text,
)
from review import load_dataset

PARTS = ("train", "val", "test")
HOLDOUT_SHARE = 0.2  # FT-4: about 80/20 by idea
MIN_VAL_SHARE = 0.1  # of the ideas that are not in test
MIN_CHECKED = 60  # FT-3
QUOTAS = {"Zain": 40, "Ahmad": 20}  # work-distribution §6


class LeakageError(DataError):
    """An idea is where it must not be."""


def assert_no_leakage(
    parts: Mapping[str, Sequence[FineTuneExample]],
    items: Mapping[str, Item],
    labelled: Sequence[LabelledIdea],
) -> None:
    """No row or idea (also not a near copy) in two parts, and no labelled-set idea at all."""
    if not labelled:
        raise LeakageError("no labelled-set ideas to check against")
    seen: dict[str, str] = {}
    ids: dict[str, str] = {}
    blocks: dict[str, set[str]] = {name: set() for name in parts}
    for name, rows in parts.items():
        for row in rows:
            key = example_id(row)
            item = items.get(key)
            if item is None:
                raise LeakageError(f"{name}: row {key} has no entry in items.jsonl")
            if key in seen:
                raise LeakageError(f"row {key} is in {seen[key]} and in {name}")
            seen[key] = name
            if ids.setdefault(item.idea_id, name) != name:
                raise LeakageError(f"idea {item.idea_id} is in {ids[item.idea_id]} and in {name}")
            block = idea_block(row.messages[1].content)
            blocks[name].add(block)
            title, _, text = block.partition("\n")
            if is_labelled(item.idea_title, item.idea_text, labelled) or is_labelled(
                title, text or title, labelled
            ):
                raise LeakageError(f"{name}: idea {item.idea_id} is in the labelled set (EVAL-1)")
    names = list(parts)
    for index, first in enumerate(names):
        for second in names[index + 1 :]:
            for a in blocks[first]:
                if any(similarity(a, b) >= GROUP_SIMILARITY for b in blocks[second]):
                    raise LeakageError(f"near-identical ideas in {first} and {second}: {a[:80]!r}")


def _checked(checks: Mapping[str, Check], items: Mapping[str, Item]) -> dict[str, Check]:
    """The usable checks. A hand-edited ``checked.csv`` meets the same rules as the tool."""
    if unknown := sorted(set(checks) - set(items)):
        raise DataError(f"checked.csv names {len(unknown)} unknown rows, e.g. {unknown[0]}")
    kept = {}
    for key, check in checks.items():
        if check.label is None:
            continue
        try:
            grounded_label(check.label.model_dump_json(), items[key].evidence_ids)
        except DataError as exc:
            raise DataError(f"checked.csv, row {key}: {exc}") from exc
        kept[key] = check
    return kept


def split(
    folder: Path,
    labelled_path: Path,
    *,
    min_checked: int = MIN_CHECKED,
    quotas: Mapping[str, int] = QUOTAS,
) -> dict[str, Any]:
    labelled = load_labelled(labelled_path)
    if not labelled:
        raise LeakageError(
            f"{labelled_path} is missing or empty: write the EVAL-1 ideas first, or nothing "
            "can prove they are not in training"
        )
    examples, items = load_dataset(folder)
    fake = any(i.fake for i in items.values())
    guard_output(folder / "train.jsonl", fake)
    checks = read_checks(folder / "checked.csv")
    kept = _checked(checks, items)
    by_name = Counter(c.checked_by.casefold() for c in kept.values())
    shortfalls = [
        f"{name} checked {by_name[name.casefold()]}, needs {needed}"
        for name, needed in quotas.items()
        if by_name[name.casefold()] < needed
    ]
    if len(kept) < min_checked:
        shortfalls.insert(0, f"{len(kept)} hand-checked rows, need {min_checked}")
    if shortfalls:
        raise DataError("not enough hand checks: " + "; ".join(shortfalls))

    usable = [k for k in examples if not (k in checks and checks[k].label is None)]
    # grouped on the idea as the model sees it, the same text the assertions compare
    blocks = {items[k].idea_id: idea_block(examples[k].messages[1].content) for k in usable}
    group_of = groups(blocks)
    every = sorted(set(group_of.values()), key=stable_order)
    test_groups = {group_of[items[k].idea_id] for k in kept}
    rest = [g for g in every if g not in test_groups]
    wanted = max(
        round(HOLDOUT_SHARE * len(every)) - len(test_groups),
        round(MIN_VAL_SHARE * len(rest)),
        1,
    )
    if len(rest) <= wanted:
        raise DataError(
            f"only {len(rest)} idea groups have no check: nothing would be left to train on. "
            "Check whole ideas (review.py does) or sample more ideas"
        )
    val_groups = set(rest[:wanted])

    parts: dict[str, list[FineTuneExample]] = {name: [] for name in PARTS}
    unused = 0
    for key in sorted(usable, key=stable_order):
        group = group_of[items[key].idea_id]
        if group not in test_groups:
            parts["val" if group in val_groups else "train"].append(examples[key])
        elif (label := kept[key].label if key in kept else None) is not None:
            system, user, _ = (m.content for m in examples[key].messages)
            parts["test"].append(make_example(system, user, label))
        else:
            unused += 1  # an unchecked row of a test idea is used nowhere
    assert_no_leakage(parts, items, labelled)

    ideas = {
        name: len({items[example_id(r)].idea_id for r in rows}) for name, rows in parts.items()
    }
    train_share = (len(every) - len(test_groups) - len(val_groups)) / len(every)
    tested = Counter(items[example_id(r)].dimension.value for r in parts["test"])
    warnings = []
    if train_share < 0.7:
        warnings.append(f"only {train_share:.0%} of the idea groups train (FT-4: about 80/20)")
    if len(tested) < 6:
        warnings.append(f"the test set covers {len(tested)} of 6 dimensions")
    report: dict[str, Any] = {
        "rows": {name: len(rows) for name, rows in parts.items()},
        "ideas": ideas,
        "idea_groups": len(every),
        "train_group_share": round(train_share, 3),
        "test_rows_by_dimension": dict(sorted(tested.items())),
        "unchecked_rows_of_test_ideas_unused": unused,
        "rejected_rows": len(checks) - len(kept),
        "checked": len(kept),
        "checked_by": dict(sorted(Counter(c.checked_by for c in kept.values()).items())),
        "fixed_by_hand": sum(c.action == "fix" for c in kept.values()),
        "thresholds": {"min_checked": min_checked, "quotas": dict(quotas)},
        "labelled_ideas_excluded": len(labelled),
        "labelled_sha256": hashlib.sha256(labelled_path.read_bytes()).hexdigest(),
        "warnings": warnings,
        "fake": fake,
    }
    for name, rows in parts.items():
        write_jsonl(folder / f"{name}.jsonl", rows)
    report["sha256"] = {
        name: hashlib.sha256((folder / f"{name}.jsonl").read_bytes()).hexdigest() for name in PARTS
    }
    write_text(folder / "split-report.json", json.dumps(report, indent=2) + "\n")
    return report


def _quota(value: str) -> tuple[str, int]:
    name, _, count = value.partition("=")
    if not name or not count.isdigit():
        raise argparse.ArgumentTypeError("expected NAME=COUNT")
    return name, int(count)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--data", type=Path, default=DATA_DIR)
    parser.add_argument("--labelled", type=Path, default=LABELLED)
    parser.add_argument("--min-checked", type=int, default=MIN_CHECKED)
    parser.add_argument(
        "--quota", type=_quota, action="append", help="NAME=COUNT, replaces the default quotas"
    )
    args = parser.parse_args(argv)
    # the required numbers can only be changed away from the real data folder
    relaxed = args.min_checked < MIN_CHECKED or args.quota is not None
    if relaxed and args.data.resolve().is_relative_to(DATA_DIR):
        print(
            "error: --min-checked and --quota are for try-outs, not the real data", file=sys.stderr
        )
        return 1
    try:
        report = split(
            args.data,
            args.labelled,
            min_checked=args.min_checked,
            quotas=dict(args.quota) if args.quota else QUOTAS,
        )
    except DataError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
