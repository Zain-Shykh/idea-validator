"""File formats and shared rules of the fine-tuning pipeline (FT-2..5, DATA-7).

``all.jsonl``, ``train.jsonl``, ``val.jsonl`` and ``test.jsonl`` hold pure DATA-7 lines. What is
known about a row lives beside them: ``items.jsonl`` (provenance) and ``checked.csv`` (hand
checks), both keyed by ``item_id``, a hash of the system and user messages.
"""

import csv
import hashlib
import json
import os
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError

ROOT = Path(__file__).resolve().parents[2]
# the dataset is built with the code that serves it (M9, M10)
for _service in ("scorer", "validators"):
    if (_src := str(ROOT / "services" / _service / "src")) not in sys.path:
        sys.path.append(_src)

from common.contracts.agents import FineTuneExample, FineTuneMessage, ScorerOutput  # noqa: E402
from common.contracts.base import ContractModel, EvidenceId, NonEmptyStr  # noqa: E402
from common.contracts.core import Dimension  # noqa: E402
from common.idea_rules import words  # noqa: E402
from common.llm import describe_errors  # noqa: E402
from scorer.services.scoring import ModelReply  # noqa: E402

DATA_DIR = ROOT / "jobs/finetune/data"
RESULTS_DIR = ROOT / "eval/results"
LABELLED = ROOT / "eval/labelled/ideas.jsonl"

# word-set (Jaccard) similarity: near copies share a split group; the labelled-set rule is
# stricter, because excluding one idea too many only costs a row
GROUP_SIMILARITY = 0.8
LABELLED_SIMILARITY = 0.6

CHECK_COLUMNS = (
    "item_id", "checked_by", "checked_at", "action", "score", "confidence", "evidence_ids",
    "reason",
)  # fmt: skip
# the pattern of a real id: the template's own '<evidence id="...">' must not count
_EVIDENCE_TAG = re.compile(r'<evidence id="(card_[A-Za-z0-9]+#c\d+)"')
_IDEA_BLOCK = re.compile(r"<idea>\n(.*?)\n</idea>", re.DOTALL)
_CARD_IN_TEXT = re.compile(r"\bcard_[a-z0-9]+(?:#c\d+)?", re.IGNORECASE)

Origin = Literal["card", "generated"]
Action = Literal["accept", "fix", "reject"]


class DataError(Exception):
    """A file or a row cannot be used. The message says which and why."""


class Candidate(ContractModel):
    """One unlabelled (idea, evidence, dimension) prompt."""

    item_id: NonEmptyStr
    idea_id: NonEmptyStr
    origin: Origin
    idea_title: NonEmptyStr
    idea_text: NonEmptyStr
    dimension: Dimension
    rubric_id: NonEmptyStr
    evidence_ids: list[EvidenceId]
    system: NonEmptyStr
    user: NonEmptyStr
    kb_version: NonEmptyStr
    built_on: date
    fake: bool


class Draft(ContractModel):
    """The teacher's reply to one candidate; ``label`` is None when it was rejected."""

    item_id: NonEmptyStr
    label: ScorerOutput | None
    problem: str | None = None
    model: NonEmptyStr
    drafted_at: datetime
    fake: bool


class Item(ContractModel):
    """What is known about one row of ``all.jsonl``."""

    item_id: NonEmptyStr
    idea_id: NonEmptyStr
    origin: Origin
    idea_title: NonEmptyStr
    idea_text: NonEmptyStr
    dimension: Dimension
    rubric_id: NonEmptyStr
    evidence_ids: list[EvidenceId]
    teacher_model: NonEmptyStr
    fake: bool


class Check(ContractModel):
    """One hand check. ``label`` is the label after the check; None for a rejected row."""

    item_id: NonEmptyStr
    checked_by: NonEmptyStr
    checked_at: datetime
    action: Action
    label: ScorerOutput | None


def item_id(system: str, user: str) -> str:
    return hashlib.sha256(f"{system}\x00{user}".encode()).hexdigest()[:16]


def stable_order(key: str) -> str:
    """A fixed, content-independent order (no random seed to record)."""
    return hashlib.sha256(key.encode()).hexdigest()


def example_id(example: FineTuneExample) -> str:
    return item_id(example.messages[0].content, example.messages[1].content)


def make_example(system: str, user: str, label: ScorerOutput) -> FineTuneExample:
    return FineTuneExample(
        messages=[
            FineTuneMessage(role="system", content=system),
            FineTuneMessage(role="user", content=user),
            FineTuneMessage(role="assistant", content=label.model_dump_json()),
        ]
    )


def label_of(example: FineTuneExample) -> ScorerOutput:
    return ScorerOutput.model_validate_json(example.messages[2].content)


def prompt_ids(user: str) -> list[str]:
    """The evidence ids of a prompt. Outside text is escaped, so it cannot forge a tag."""
    return _EVIDENCE_TAG.findall(user)


def idea_block(user: str) -> str:
    """The idea as the model sees it."""
    match = _IDEA_BLOCK.search(user)
    if match is None:
        raise DataError("a prompt without an <idea> block")
    return match.group(1)


def grounded_label(reply: str, allowed: Sequence[str]) -> ScorerOutput:
    """The label in ``reply`` if M10 would accept its shape and every id it cites, also inside
    the reason, was in the prompt. Nothing is repaired: a label is used as given or not at all."""
    try:
        parsed = ModelReply.model_validate_json(reply)
    except ValidationError as exc:
        raise DataError(f"not the scorer's reply format: {describe_errors(exc)}") from exc
    ids = list(dict.fromkeys(parsed.evidence_ids))
    if not ids:
        raise DataError("cites no evidence")
    if unknown := [i for i in ids if i not in allowed]:
        raise DataError(f"cites ids that are not in the prompt: {', '.join(unknown[:5])}")
    known = set(allowed) | {i.partition("#")[0] for i in allowed}
    if any(found not in known for found in _CARD_IN_TEXT.findall(parsed.reason)):
        raise DataError("the reason names a card that is not in the prompt")
    try:
        return ScorerOutput(
            score=parsed.score,
            confidence=parsed.confidence,
            evidence_ids=ids,
            reason=parsed.reason,
        )
    except ValidationError as exc:
        raise DataError(f"invalid label: {describe_errors(exc)}") from exc


# --- ideas: similarity, groups, the labelled set -------------------------------------------------


def similarity(first: str, second: str) -> float:
    a, b = words(first), words(second)
    return len(a & b) / len(a | b) if a | b else 1.0


def groups(ideas: Mapping[str, str], threshold: float = GROUP_SIMILARITY) -> dict[str, str]:
    """idea id -> group id. Ideas whose texts are near copies end up in one group."""
    parent = {key: key for key in ideas}

    def root(key: str) -> str:
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    # ponytail: all pairs, fine for a few hundred ideas
    keys = sorted(ideas)
    for index, first in enumerate(keys):
        for second in keys[index + 1 :]:
            if similarity(ideas[first], ideas[second]) >= threshold:
                low, high = sorted((root(first), root(second)))
                parent[high] = low
    return {key: root(key) for key in keys}


class LabelledIdea(BaseModel):
    """One line of the EVAL-1 idea list. Other keys (profile, verdicts) are ignored."""

    title: NonEmptyStr
    text: NonEmptyStr


def load_labelled(path: Path) -> list[LabelledIdea]:
    """Empty when the file does not exist: callers decide whether that is acceptable."""
    ideas = []
    for number, line in _lines(path) if path.exists() else []:
        try:
            data = json.loads(line)
            nested = data.get("idea") if isinstance(data, dict) else None
            ideas.append(LabelledIdea.model_validate(nested or data))
        except (ValueError, ValidationError) as exc:
            raise DataError(f"{path}:{number}: needs a title and a text") from exc
    return ideas


def is_labelled(title: str, text: str, labelled: Iterable[LabelledIdea]) -> bool:
    mine = f"{title}\n{text}"
    return any(
        words(title) == words(other.title)
        or similarity(mine, f"{other.title}\n{other.text}") >= LABELLED_SIMILARITY
        for other in labelled
    )


# --- files ---------------------------------------------------------------------------------------


def guard_output(path: Path, fake: bool) -> None:
    """Output of a fake knowledge base or model never lands where real data and results go."""
    resolved = path.resolve()
    if fake and any(resolved.is_relative_to(real) for real in (DATA_DIR, RESULTS_DIR)):
        raise DataError(f"{path}: made with fake data or a fake model; write it somewhere else")


def _lines(path: Path) -> list[tuple[int, str]]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise DataError(f"cannot read {path}: {exc}") from exc
    # split on "\n" only: JSON may hold U+2028 unescaped
    return [(n, line) for n, line in enumerate(text.split("\n"), 1) if line.strip()]


def read_jsonl[T: BaseModel](path: Path, model: type[T]) -> list[T]:
    rows = []
    for number, line in _lines(path):
        try:
            rows.append(model.model_validate_json(line))
        except ValidationError as exc:
            raise DataError(f"{path}:{number}: {describe_errors(exc)}") from exc
    return rows


def keyed[T: (Candidate, Draft, Item)](rows: Sequence[T], path: Path) -> dict[str, T]:
    found = {row.item_id: row for row in rows}
    if len(found) != len(rows):
        raise DataError(f"{path}: an item id appears twice")
    return found


def write_text(path: Path, text: str) -> None:
    """Whole file or nothing."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def write_jsonl(path: Path, rows: Iterable[BaseModel]) -> None:
    write_text(path, "".join(row.model_dump_json() + "\n" for row in rows))


def append_jsonl(path: Path, row: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(row.model_dump_json() + "\n")


def read_checks(path: Path) -> dict[str, Check]:
    """``checked.csv``; empty when there is none yet."""
    if not path.exists():
        return {}
    checks: dict[str, Check] = {}
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle, strict=True)
            if tuple(reader.fieldnames or ()) != CHECK_COLUMNS:
                raise DataError(f"{path}: columns must be {','.join(CHECK_COLUMNS)}")
            for row in reader:
                label = None
                if row["action"] != "reject":
                    label = ScorerOutput(
                        score=float(row["score"]),
                        confidence=float(row["confidence"]),
                        evidence_ids=row["evidence_ids"].split(),
                        reason=row["reason"],
                    )
                check = Check.model_validate(
                    {k: row[k] for k in CHECK_COLUMNS[:4]} | {"label": label}
                )
                if check.item_id in checks:
                    raise DataError(f"{path}: {check.item_id} is checked twice")
                checks[check.item_id] = check
    except (csv.Error, ValueError, TypeError) as exc:
        raise DataError(f"{path}: bad row after {len(checks)} good ones: {exc}") from exc
    return checks


def append_check(path: Path, check: Check) -> None:
    new = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        if new:
            writer.writerow(CHECK_COLUMNS)
        label = check.label
        writer.writerow(
            [check.item_id, check.checked_by, check.checked_at.isoformat(), check.action]
            + (
                ["", "", "", ""]
                if label is None
                else [label.score, label.confidence, " ".join(label.evidence_ids), label.reason]
            )
        )
