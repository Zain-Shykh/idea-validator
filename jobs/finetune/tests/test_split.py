"""The split and its leakage assertions (spec Z9 A4 5). Fake rows and fake checks, in temporary
folders only."""

import json
from pathlib import Path

import pytest
from finetune_factories import (
    candidate,
    check,
    grid,
    write_checks,
    write_dataset,
    write_labelled,
)

from common.contracts.agents import FineTuneExample
from common.contracts.core import Dimension
from dataset import (
    DATA_DIR,
    DataError,
    Item,
    LabelledIdea,
    example_id,
    item_id,
    label_of,
    load_labelled,
    read_jsonl,
)
from split import PARTS, LeakageError, assert_no_leakage, main, split


def prepare(folder: Path, ideas: int = 40, checked_ideas: tuple[int, int] = (7, 4)) -> list:  # type: ignore[type-arg]
    """``checked_ideas`` whole ideas checked by Zain and by Ahmad."""
    rows = grid(ideas)
    write_dataset(folder, rows)
    zain, ahmad = (n * 6 for n in checked_ideas)
    write_checks(
        folder / "checked.csv",
        [check(r, "Zain", 1.5, "fix") for r in rows[:zain]]
        + [check(r, "Ahmad", 1.5, "fix") for r in rows[zain : zain + ahmad]],
    )
    write_labelled(folder / "labelled.jsonl", [("Unrelated labelled idea", "about volcanoes")])
    return rows


def _listed(folder: Path) -> list[LabelledIdea]:
    return load_labelled(folder / "labelled.jsonl")


def load(folder: Path) -> tuple[dict[str, list[FineTuneExample]], dict[str, Item]]:
    parts = {p: read_jsonl(folder / f"{p}.jsonl", FineTuneExample) for p in PARTS}
    items = {i.item_id: i for i in read_jsonl(folder / "items.jsonl", Item)}
    return parts, items


def test_split_by_idea_with_checked_rows_as_test(tmp_path: Path) -> None:
    rows = prepare(tmp_path, ideas=60)
    report = split(tmp_path, tmp_path / "labelled.jsonl")
    parts, items = load(tmp_path)
    ideas = {p: {items[example_id(r)].idea_id for r in parts[p]} for p in PARTS}
    assert not ideas["train"] & ideas["test"] and not ideas["train"] & ideas["val"]
    assert not ideas["val"] & ideas["test"]
    assert {example_id(r) for r in parts["test"]} == {r.item_id for r in rows[:66]}
    # the hand-fixed label, not the draft
    assert {label_of(r).score for r in parts["test"]} == {1.5}
    assert report["checked_by"] == {"Ahmad": 24, "Zain": 42} and report["fixed_by_hand"] == 66
    assert report["ideas"] == {"train": 44, "val": 5, "test": 11}
    assert sum(report["rows"].values()) == 360 and report["warnings"] == []
    assert_no_leakage(parts, items, [*_listed(tmp_path)])


def test_split_is_reproducible(tmp_path: Path) -> None:
    prepare(tmp_path)
    first = split(tmp_path, tmp_path / "labelled.jsonl")
    assert split(tmp_path, tmp_path / "labelled.jsonl") == first


def test_holdout_is_about_a_fifth(tmp_path: Path) -> None:
    prepare(tmp_path, ideas=100)
    report = split(tmp_path, tmp_path / "labelled.jsonl")
    assert report["ideas"] == {"train": 80, "val": 9, "test": 11}


def test_unchecked_rows_of_a_test_idea_are_used_nowhere(tmp_path: Path) -> None:
    rows = grid(20)
    write_dataset(tmp_path, rows)
    write_checks(tmp_path / "checked.csv", [check(r, "T") for r in rows[:2]])
    listed = write_labelled(tmp_path / "l.jsonl", [("x", "volcanoes")])
    report = split(tmp_path, listed, min_checked=2, quotas={})
    parts, _ = load(tmp_path)
    used = {example_id(r) for p in PARTS for r in parts[p]}
    assert report["unchecked_rows_of_test_ideas_unused"] == 4
    assert not used & {r.item_id for r in rows[2:6]}


def test_rejected_rows_are_left_out(tmp_path: Path) -> None:
    rows = prepare(tmp_path)
    write_checks(tmp_path / "checked.csv", [check(rows[-1], "Zain", action="reject")])
    report = split(tmp_path, tmp_path / "labelled.jsonl")
    parts, _ = load(tmp_path)
    assert report["rejected_rows"] == 1
    assert rows[-1].item_id not in {example_id(r) for p in PARTS for r in parts[p]}


def test_near_copies_stay_together(tmp_path: Path) -> None:
    """Two ideas with different ids and almost the same words: one is checked."""
    rows = grid(20)
    words = "alpha bravo charlie delta echo foxtrot golf hotel india juliett kilo"
    twins = []
    for number, extra in ((50, "lima"), (51, "mike")):
        base = candidate(number, Dimension.RISK)
        user = base.user.replace(base.idea_title + "\n" + base.idea_text, f"{words}\n{extra}")
        twins.append(
            base.model_copy(
                update={
                    "user": user,
                    "item_id": item_id(base.system, user),
                    "idea_title": words,
                    "idea_text": extra,
                }
            )
        )
    write_dataset(tmp_path, [*rows, *twins])
    write_checks(tmp_path / "checked.csv", [check(twins[0], "T")])
    listed = write_labelled(tmp_path / "l.jsonl", [("x", "volcanoes")])
    split(tmp_path, listed, min_checked=1, quotas={})
    parts, _ = load(tmp_path)
    for part in ("train", "val"):
        assert twins[1].item_id not in {example_id(r) for r in parts[part]}


@pytest.mark.parametrize("case", ["same_idea", "same_row", "near_copy", "labelled", "no_list"])
def test_assertions_catch_leaks(tmp_path: Path, case: str) -> None:
    prepare(tmp_path)
    split(tmp_path, tmp_path / "labelled.jsonl")
    parts, items = load(tmp_path)
    labelled = list(_listed(tmp_path))
    moved = parts["test"][0]
    if case == "same_idea":
        key = example_id(parts["train"][0])
        items[key] = items[key].model_copy(update={"idea_id": items[example_id(moved)].idea_id})
    elif case == "same_row":
        parts["train"].append(moved)
    elif case == "near_copy":
        # another row id and idea id, the same idea text
        system = moved.messages[0].model_copy(update={"content": "changed"})
        copy = moved.model_copy(update={"messages": [system, *moved.messages[1:]]})
        items[example_id(copy)] = items[example_id(moved)].model_copy(
            update={"item_id": example_id(copy), "idea_id": "idea_other"}
        )
        parts["train"].append(copy)
    elif case == "labelled":
        item = items[example_id(parts["train"][0])]
        labelled.append(LabelledIdea(title=item.idea_title, text="reworded"))
    else:
        labelled = []
    with pytest.raises(LeakageError):
        assert_no_leakage(parts, items, labelled)


def test_labelled_idea_in_the_dataset_stops_the_split(tmp_path: Path) -> None:
    rows = prepare(tmp_path)
    listed = write_labelled(tmp_path / "l.jsonl", [(rows[-1].idea_title, rows[-1].idea_text)])
    with pytest.raises(LeakageError, match="labelled set"):
        split(tmp_path, listed)
    assert not (tmp_path / "train.jsonl").exists()


@pytest.mark.parametrize("content", [None, "", "\n"])
def test_missing_labelled_set_stops_the_split(tmp_path: Path, content: str | None) -> None:
    prepare(tmp_path)
    listed = tmp_path / "other.jsonl"
    if content is not None:
        listed.write_text(content)
    with pytest.raises(LeakageError, match="missing or empty"):
        split(tmp_path, listed)


def test_labelled_set_accepts_the_nested_shape(tmp_path: Path) -> None:
    path = tmp_path / "l.jsonl"
    path.write_text(json.dumps({"idea": {"title": "A", "text": "B"}, "verdict": "GO"}) + "\n")
    assert load_labelled(path)[0].title == "A"
    path.write_text('{"idea_title": "A"}\n')
    with pytest.raises(DataError, match=":1:"):
        load_labelled(path)


@pytest.mark.parametrize(
    ("checked", "problem"),
    [((5, 4), "need 60"), ((11, 0), "Ahmad checked 0, needs 20"), ((6, 5), "Zain checked 36")],
)
def test_too_few_checks(tmp_path: Path, checked: tuple[int, int], problem: str) -> None:
    prepare(tmp_path, checked_ideas=checked)
    with pytest.raises(DataError, match=problem):
        split(tmp_path, tmp_path / "labelled.jsonl")


def test_no_ideas_left_to_train_on(tmp_path: Path) -> None:
    prepare(tmp_path, ideas=12)
    with pytest.raises(DataError, match="nothing would be left"):
        split(tmp_path, tmp_path / "labelled.jsonl")


def test_hand_edited_check_with_a_foreign_id_is_refused(tmp_path: Path) -> None:
    prepare(tmp_path)
    path = tmp_path / "checked.csv"
    path.write_text(path.read_text().replace("card_fake0000#c0", "card_other#c0", 1))
    with pytest.raises(DataError, match="not in the prompt"):
        split(tmp_path, tmp_path / "labelled.jsonl")


def test_unknown_checked_row_is_refused(tmp_path: Path) -> None:
    prepare(tmp_path)
    write_checks(tmp_path / "checked.csv", [check(candidate(99, Dimension.RISK), "Zain")])
    with pytest.raises(DataError, match="unknown rows"):
        split(tmp_path, tmp_path / "labelled.jsonl")


def test_command_and_its_limits(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    prepare(tmp_path)
    args = ["--data", str(tmp_path), "--labelled", str(tmp_path / "labelled.jsonl")]
    assert main(args) == 0
    assert main([*args, "--quota", "Nobody=1"]) == 1
    capsys.readouterr()
    assert main(["--data", str(DATA_DIR), "--min-checked", "1"]) == 1
    assert "try-outs" in capsys.readouterr().err
