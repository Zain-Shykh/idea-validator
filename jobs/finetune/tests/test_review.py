"""The review tool (spec Z9 A4 4). The keystrokes here are scripted by the test and the checks
live in temporary folders only: no real row is ever marked as checked by a test."""

import io
from pathlib import Path

import pytest
from finetune_factories import NOW, candidate, grid, write_dataset

from common.contracts.core import Dimension
from dataset import DATA_DIR, DataError, Item, item_id, read_checks
from review import load_dataset, main, progress, queue, review


def keys(*answers: str):  # type: ignore[no-untyped-def]
    pending = list(answers)

    def ask(_: str) -> str:
        if not pending:
            raise EOFError
        return pending.pop(0)

    return ask


def run(folder: Path, *answers: str, name: str = "Tester") -> tuple[int, str]:
    out = io.StringIO()
    done = review(folder, name, ask=keys(*answers), out=out, now=lambda: NOW)
    return done, out.getvalue()


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    write_dataset(tmp_path, grid(3))
    return tmp_path


def test_rows_come_idea_by_idea(folder: Path) -> None:
    _, items = load_dataset(folder)
    order = [items[k].idea_id for k in queue(items, {})]
    assert all(len(set(order[i : i + 6])) == 1 for i in (0, 6, 12))
    assert len(set(order)) == 3


def test_accept_fix_reject_and_resume(folder: Path) -> None:
    _, items = load_dataset(folder)
    first, second, third, fourth = queue(items, {})[:4]
    done, _ = run(folder, "a", "f", "4.5", "0.9", "", "FAKE better reason", "r", "y", "s", "q")
    assert done == 3
    checks = read_checks(folder / "checked.csv")
    assert [checks[k].action for k in (first, second, third)] == ["accept", "fix", "reject"]
    assert checks[second].label and checks[second].label.score == 4.5
    assert checks[second].label.reason == "FAKE better reason"
    assert checks[third].label is None and fourth not in checks
    assert all(c.checked_by == "Tester" and c.checked_at == NOW for c in checks.values())
    assert progress(folder) == "checked rows: 2 (Tester 2); rejected: 1; ideas with a check: 1 of 3"
    # the skipped row is offered again, in the idea already started; a second person appends
    assert queue(items, checks)[0] == fourth
    run(folder, "a", name="Other")
    assert read_checks(folder / "checked.csv")[fourth].checked_by == "Other"


@pytest.mark.parametrize(
    "fix",
    [
        ("7", "", "", ""),
        ("3.2", "", "", ""),
        ("", "2", "", ""),
        ("abc", "", "", ""),
        ("", "", "card_invented#c0", ""),
        ("", "", "", "see card_other#c1"),
    ],
)
def test_bad_fix_is_refused(folder: Path, fix: tuple[str, ...]) -> None:
    done, _ = run(folder, "f", *fix, "q")
    assert done == 0 and not (folder / "checked.csv").exists()


def test_a_fix_that_changes_nothing_is_an_accept(folder: Path) -> None:
    run(folder, "f", "", "", "", "", "q")
    assert [c.action for c in read_checks(folder / "checked.csv").values()] == ["accept"]


def test_unknown_keys_and_unconfirmed_reject_record_nothing(folder: Path) -> None:
    assert run(folder, "", "yes", "r", "n", "q")[0] == 0
    # end of input quits
    assert run(folder)[0] == 0


def test_limit(folder: Path) -> None:
    out = io.StringIO()
    assert review(folder, "T", ask=keys("a", "a", "a"), out=out, limit=2) == 2


def test_terminal_escapes_are_not_printed(tmp_path: Path) -> None:
    rows = grid(1)
    rows = [r.model_copy(update={"user": r.user + "\x1b[2J‮hidden"}) for r in rows]
    rows = [r.model_copy(update={"item_id": item_id(r.system, r.user)}) for r in rows]
    write_dataset(tmp_path, rows)
    _, shown = run(tmp_path, "q")
    assert "\x1b" not in shown and "‮" not in shown and "hidden" in shown


def test_mismatched_files_are_refused(folder: Path) -> None:
    lines = (folder / "items.jsonl").read_text().splitlines(keepends=True)
    (folder / "items.jsonl").write_text("".join(lines[1:]))
    with pytest.raises(DataError, match="assemble again"):
        load_dataset(folder)


def test_command_refuses_piped_input(
    folder: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("a\na\n"))
    assert main(["--name", "Tester", "--data", str(folder)]) == 1
    assert "person at a terminal" in capsys.readouterr().err
    assert not (folder / "checked.csv").exists()


def test_fake_rows_cannot_be_checked_in_the_real_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    import review as module

    row = candidate(1, Dimension.RISK)
    keep = set(Item.model_fields) - {"teacher_model", "fake"}
    items = {row.item_id: Item(**row.model_dump(include=keep), teacher_model="f", fake=True)}
    monkeypatch.setattr(module, "load_dataset", lambda _: ({}, items))
    with pytest.raises(DataError, match="fake"):
        run(DATA_DIR)
