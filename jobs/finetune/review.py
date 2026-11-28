"""Step 3: a person checks and fixes drafted labels (FT-3).

    python jobs/finetune/review.py --name Zain

Rows come one idea at a time, all its dimensions together: ideas with a checked row become the
test set, so scattering the checks would leave nothing to train on. Each decision is appended to
``checked.csv`` at once; stop any time and run again to continue.
"""

import argparse
import sys
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

from common.contracts.agents import FineTuneExample, ScorerOutput
from common.contracts.core import Dimension
from dataset import (
    DATA_DIR,
    Check,
    DataError,
    Item,
    append_check,
    clean,
    example_id,
    grounded_label,
    guard_output,
    keyed,
    label_of,
    read_checks,
    read_jsonl,
    stable_order,
)

Ask = Callable[[str], str]

_HELP = "[a]ccept  [f]ix  [r]eject (bad row, never used)  [s]kip  [q]uit > "


def load_dataset(folder: Path) -> tuple[dict[str, FineTuneExample], dict[str, Item]]:
    """``all.jsonl`` and ``items.jsonl``, checked to describe the same rows."""
    path = folder / "all.jsonl"
    rows = read_jsonl(path, FineTuneExample)
    examples = {example_id(e): e for e in rows}
    items = keyed(read_jsonl(folder / "items.jsonl", Item), folder / "items.jsonl")
    if len(examples) != len(rows) or examples.keys() != items.keys():
        raise DataError(f"{path} and items.jsonl do not describe the same rows: assemble again")
    return examples, items


def queue(items: Mapping[str, Item], checked: Mapping[str, Check]) -> list[str]:
    """Unchecked rows: started ideas first, then ideas in a fixed order, dimensions together."""
    started = {items[k].idea_id for k in checked if k in items}
    order = list(Dimension)
    return sorted(
        (k for k in items if k not in checked),
        key=lambda k: (
            items[k].idea_id not in started,
            stable_order(items[k].idea_id),
            order.index(items[k].dimension),
        ),
    )


def _fix(draft: ScorerOutput, allowed: Sequence[str], ask: Ask) -> ScorerOutput:
    """Each field in turn; Enter keeps it. The result passes the same rules as a draft."""
    score = ask(f"score 0-5, half steps [{draft.score:g}] > ").strip()
    confidence = ask(f"confidence 0-1 [{draft.confidence:g}] > ").strip()
    ids = ask(f"evidence ids, space separated [{' '.join(draft.evidence_ids)}] > ").split()
    reason = ask("reason [keep] > ").strip()
    try:
        edited = ScorerOutput(
            score=float(score) if score else draft.score,
            confidence=float(confidence) if confidence else draft.confidence,
            evidence_ids=ids or draft.evidence_ids,
            reason=reason or draft.reason,
        )
    except ValueError as exc:  # pydantic's error is a ValueError
        raise DataError(f"not a valid label: {exc}") from exc
    return grounded_label(edited.model_dump_json(), allowed)


def review(
    folder: Path,
    name: str,
    *,
    ask: Ask,
    out: TextIO,
    limit: int | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> int:
    """The number of rows decided in this session."""
    examples, items = load_dataset(folder)
    path = folder / "checked.csv"
    guard_output(path, any(i.fake for i in items.values()))
    todo = queue(items, read_checks(path))
    done = 0

    def say(text: str = "") -> None:
        print(text, file=out)

    for key in todo:
        if limit is not None and done >= limit:
            break
        item, example = items[key], examples[key]
        draft = label_of(example)
        say(f"\n{'=' * 78}\nrow {key} · {item.dimension.value} · idea {item.idea_id}")
        say(clean(example.messages[0].content))
        say()
        say(clean(example.messages[1].content))
        say(f"\nDRAFT by {clean(item.teacher_model)}:")
        say(clean(draft.model_dump_json(indent=2)))
        check: Check | None = None
        while check is None:
            try:
                command = ask(_HELP).strip().lower()
                label: ScorerOutput | None = draft
                if command == "q":
                    return done
                if command == "s":
                    break
                if command == "r":
                    if ask("leave this row out for good? [y/N] > ").strip().lower() != "y":
                        continue
                    label, action = None, "reject"
                elif command == "a":
                    action = "accept"
                elif command == "f":
                    label = _fix(draft, item.evidence_ids, ask)
                    action = "accept" if label == draft else "fix"
                else:
                    continue
            except DataError as exc:
                say(clean(str(exc)))
                continue
            except (EOFError, KeyboardInterrupt):
                return done
            check = Check.model_validate(
                {
                    "item_id": key,
                    "checked_by": name,
                    "checked_at": now(),
                    "action": action,
                    "label": label,
                }
            )
        if check is not None:
            append_check(path, check)
            done += 1
    return done


def progress(folder: Path) -> str:
    _, items = load_dataset(folder)
    checks = read_checks(folder / "checked.csv")
    kept = [c for c in checks.values() if c.action != "reject"]
    by_name = Counter(c.checked_by for c in kept)
    ideas = {items[c.item_id].idea_id for c in kept if c.item_id in items}
    names = ", ".join(f"{n} {count}" for n, count in sorted(by_name.items())) or "nobody yet"
    return (
        f"checked rows: {len(kept)} ({names}); rejected: {len(checks) - len(kept)}; "
        f"ideas with a check: {len(ideas)} of {len({i.idea_id for i in items.values()})}"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--name", required=True, help="your name, recorded as checked_by")
    parser.add_argument("--data", type=Path, default=DATA_DIR)
    parser.add_argument("--count", type=int, help="stop after this many rows")
    args = parser.parse_args(argv)
    name = clean(args.name).strip()
    if not name or "\n" in name:
        parser.error("--name must be one line of text")
    # checked_by says a person read the row, so nothing may be piped in
    if not sys.stdin.isatty():
        print("error: review needs a person at a terminal", file=sys.stderr)
        return 1
    try:
        done = review(args.data, name, ask=input, out=sys.stdout, limit=args.count)
        print(f"\ndecided now: {done}. {progress(args.data)}")
    except DataError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
