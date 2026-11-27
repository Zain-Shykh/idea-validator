"""The labelled set (EVAL-1): candidate ideas and the verdicts people gave them.

Label files are written by people. Nothing here creates or changes one: this module reads them,
refuses what cannot be used, and lists where the two labellers disagree.

    python jobs/evaluation/labelled.py            # check whatever label files exist
"""

import argparse
import csv
import json
import sys
from collections.abc import Collection, Sequence
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError

from common.contracts.base import ContractModel, NonEmptyStr
from common.contracts.core import IdeaInput, StudentProfile, VerdictLabel
from common.llm import describe_errors
from eval_metrics import kappa

ROOT = Path(__file__).resolve().parents[2]
LABELLED_DIR = ROOT / "eval/labelled"
IDEAS = LABELLED_DIR / "ideas.jsonl"
RESOLVED = LABELLED_DIR / "resolved.csv"
LABELLERS = ("zain", "ahmad")
LABEL_COLUMNS = ("idea_id", "verdict", "note")
RESOLVED_COLUMNS = ("idea_id", "verdict", "labelled_by", "resolution")


class LabelError(Exception):
    """A file of the labelled set cannot be used. The message says which and why."""


class LabelledIdea(ContractModel):
    idea_id: NonEmptyStr
    title: NonEmptyStr = Field(max_length=200)
    text: NonEmptyStr = Field(max_length=5000)
    profile: dict[str, object]
    drafted_by: NonEmptyStr
    owner_review: Literal["pending", "accepted", "edited"]

    def student(self, user_id: str) -> StudentProfile:
        return StudentProfile.model_validate({**self.profile, "user_id": user_id})

    def idea(self) -> IdeaInput:
        return IdeaInput(title=self.title, text=self.text)


def _read(path: Path) -> str:
    try:
        # utf-8-sig: spreadsheets put a byte order mark in front of a CSV
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise LabelError(f"cannot read {path}: {exc}") from exc


def load_ideas(path: Path = IDEAS) -> list[LabelledIdea]:
    ideas: list[LabelledIdea] = []
    for number, line in enumerate(_read(path).split("\n"), 1):
        if not line.strip():
            continue
        try:
            idea = LabelledIdea.model_validate(json.loads(line))
            idea.student("u_check")
        except ValidationError as exc:
            raise LabelError(f"{path}:{number}: {describe_errors(exc)}") from exc
        except ValueError as exc:
            raise LabelError(f"{path}:{number}: not JSON") from exc
        ideas.append(idea)
    if not ideas:
        raise LabelError(f"{path}: no ideas")
    if len({i.idea_id for i in ideas}) != len(ideas):
        raise LabelError(f"{path}: an idea_id appears twice")
    return ideas


def _rows(path: Path, columns: Sequence[str], known: Collection[str]) -> dict[str, dict[str, str]]:
    reader = csv.DictReader(_read(path).splitlines())
    if tuple(reader.fieldnames or ()) != tuple(columns):
        raise LabelError(f"{path}: the first line must be {','.join(columns)}")
    rows: dict[str, dict[str, str]] = {}
    for row in reader:
        where = f"{path}:{reader.line_num}"
        if None in row or any(value is None for value in row.values()):
            raise LabelError(f"{where}: needs exactly {len(columns)} values")
        idea_id = row["idea_id"].strip()
        if idea_id not in known:
            raise LabelError(f"{where}: unknown idea_id {idea_id!r}")
        if idea_id in rows:
            raise LabelError(f"{where}: {idea_id} appears twice")
        if row["verdict"].strip() not in set(VerdictLabel):
            allowed = ", ".join(VerdictLabel)
            raise LabelError(f"{where}: verdict must be one of {allowed}")
        rows[idea_id] = {name: value.strip() for name, value in row.items()}
    return rows


def load_labels(path: Path, known: Collection[str]) -> dict[str, VerdictLabel]:
    """One person's independent labels."""
    return {i: VerdictLabel(r["verdict"]) for i, r in _rows(path, LABEL_COLUMNS, known).items()}


def load_resolved(path: Path, known: Collection[str]) -> dict[str, VerdictLabel]:
    """The agreed verdicts. Each row says who labelled it."""
    rows = _rows(path, RESOLVED_COLUMNS, known)
    for idea_id, row in rows.items():
        if not row["labelled_by"]:
            raise LabelError(f"{path}: {idea_id} does not say who labelled it")
    return {i: VerdictLabel(r["verdict"]) for i, r in rows.items()}


def check(folder: Path, ideas_path: Path) -> tuple[list[str], list[str]]:
    """What can be said about the label files, and what is still wrong or missing."""
    ids = [i.idea_id for i in load_ideas(ideas_path)]
    report, problems = [f"{len(ids)} ideas"], []
    people: dict[str, dict[str, VerdictLabel]] = {}
    for name in LABELLERS:
        path = folder / f"labels_{name}.csv"
        if not path.exists():
            problems.append(f"{path.name} does not exist yet")
            continue
        people[name] = load_labels(path, set(ids))
        report.append(f"{name}: {len(people[name])} labels")
        if missing := [i for i in ids if i not in people[name]]:
            problems.append(f"{name} has not labelled: {', '.join(missing)}")
    disagreed: list[str] = []
    if len(people) == len(LABELLERS):
        first, second = (people[name] for name in LABELLERS)
        both = [i for i in ids if i in first and i in second]
        disagreed = [i for i in both if first[i] != second[i]]
        value = kappa([(first[i], second[i]) for i in both])
        shown = "not defined" if value is None else f"{value:.2f}"
        report.append(f"agree on {len(both) - len(disagreed)} of {len(both)}, kappa {shown}")
        report += [f"disagree {i}: {first[i]} / {second[i]}" for i in disagreed]
    resolved_path = folder / "resolved.csv"
    if not resolved_path.exists():
        problems.append("resolved.csv does not exist yet")
        return report, problems
    rows = _rows(resolved_path, RESOLVED_COLUMNS, set(ids))
    final = load_resolved(resolved_path, set(ids))
    if missing := [i for i in ids if i not in final]:
        problems.append(f"resolved.csv lacks: {', '.join(missing)}")
    for idea_id, verdict in final.items():
        given = {labels[idea_id] for labels in people.values() if idea_id in labels}
        # a disagreement, or a verdict neither person gave, needs a written reason
        if (idea_id in disagreed or (given and verdict not in given)) and not rows[idea_id][
            "resolution"
        ]:
            problems.append(f"resolved.csv: {idea_id} needs a resolution note")
    return report, problems


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--dir", type=Path, default=LABELLED_DIR)
    parser.add_argument("--ideas", type=Path, default=IDEAS)
    args = parser.parse_args(argv)
    try:
        report, problems = check(args.dir, args.ideas)
    except LabelError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print("\n".join(report))
    for problem in problems:
        print(f"open: {problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
