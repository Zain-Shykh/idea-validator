import csv
import json
from collections import Counter
from pathlib import Path

import pytest

import labelled
from common.contracts.core import VerdictLabel
from dataset import load_labelled

IDEAS = labelled.IDEAS


def test_candidates_are_complete_marked_as_drafts_and_cover_the_four_cases() -> None:
    ideas = labelled.load_ideas()
    assert len(ideas) >= 40
    assert all(i.drafted_by.startswith("ai:") for i in ideas if i.owner_review == "pending")
    assert len({i.title.lower() for i in ideas}) == len(ideas)
    rows = list(csv.DictReader((IDEAS.parent / "case_types.csv").read_text().splitlines()))
    assert {r["idea_id"] for r in rows} == {i.idea_id for i in ideas}
    counts = Counter(r["intended_type"] for r in rows)
    assert set(counts) == {"clear_go", "clear_no_go", "borderline", "thin_evidence"}
    assert min(counts.values()) >= 8
    # what labellers read says nothing about the intended type
    assert "intended" not in IDEAS.read_text()


def test_the_finetune_split_reads_the_same_file() -> None:
    assert len(load_labelled(IDEAS)) == len(labelled.load_ideas())


def test_committed_label_files_are_valid_if_they_exist() -> None:
    ids = {i.idea_id for i in labelled.load_ideas()}
    for name in labelled.LABELLERS:
        if (path := IDEAS.parent / f"labels_{name}.csv").exists():
            labelled.load_labels(path, ids)
    if labelled.RESOLVED.exists():
        labelled.load_resolved(labelled.RESOLVED, ids)


def make_set(folder: Path, zain: str, ahmad: str, resolved: str | None) -> Path:
    ideas = folder / "ideas.jsonl"
    ideas.write_text(
        "".join(
            json.dumps(i.model_dump() | {"idea_id": f"x{n}"}) + "\n"
            for n, i in enumerate(labelled.load_ideas()[:2], 1)
        )
    )
    (folder / "labels_zain.csv").write_text("idea_id,verdict,note\n" + zain)
    (folder / "labels_ahmad.csv").write_text("idea_id,verdict,note\n" + ahmad)
    if resolved is not None:
        (folder / "resolved.csv").write_text("idea_id,verdict,labelled_by,resolution\n" + resolved)
    return ideas


def test_disagreements_need_a_note(tmp_path: Path) -> None:
    ideas = make_set(
        tmp_path, "x1,GO,\nx2,NO-GO,\n", "x1,GO,\nx2,PIVOT,\n", "x1,GO,both,\nx2,PIVOT,both,\n"
    )
    report, problems = labelled.check(tmp_path, ideas)
    assert "disagree x2: NO-GO / PIVOT" in report
    assert problems == ["resolved.csv: x2 needs a resolution note"]
    (tmp_path / "resolved.csv").write_text(
        "idea_id,verdict,labelled_by,resolution\nx1,GO,both,\nx2,PIVOT,both,narrower is viable\n"
    )
    assert labelled.main(["--dir", str(tmp_path), "--ideas", str(ideas)]) == 0


def test_missing_files_and_labels_are_open_and_nothing_is_written(tmp_path: Path) -> None:
    ideas = make_set(tmp_path, "x1,GO,\n", "x1,GO,\n", None)
    before = sorted(p.name for p in tmp_path.iterdir())
    _, problems = labelled.check(tmp_path, ideas)
    assert any("x2" in p for p in problems) and "resolved.csv does not exist yet" in problems
    assert sorted(p.name for p in tmp_path.iterdir()) == before


def test_a_verdict_nobody_gave_needs_a_note(tmp_path: Path) -> None:
    ideas = make_set(
        tmp_path, "x1,GO,\nx2,GO,\n", "x1,GO,\nx2,GO,\n", "x1,PIVOT,both,\nx2,GO,both,\n"
    )
    assert labelled.check(tmp_path, ideas)[1] == ["resolved.csv: x1 needs a resolution note"]


@pytest.mark.parametrize(
    "body, message",
    [
        ("idea_id,verdict\n", "first line"),
        ("idea_id,verdict,note\nx9,GO,\n", "unknown idea_id"),
        ("idea_id,verdict,note\nx1,GO,\nx1,GO,\n", "twice"),
        ("idea_id,verdict,note\nx1,MAYBE,\n", "verdict must be"),
        ("idea_id,verdict,note\nx1,GO\n", "exactly 3 values"),
    ],
)
def test_unusable_label_files_are_refused(tmp_path: Path, body: str, message: str) -> None:
    path = tmp_path / "labels.csv"
    path.write_text(body)
    with pytest.raises(labelled.LabelError, match=message):
        labelled.load_labels(path, {"x1"})


def test_resolved_must_say_who_labelled(tmp_path: Path) -> None:
    path = tmp_path / "resolved.csv"
    path.write_text("﻿idea_id,verdict,labelled_by,resolution\nx1,GO,,\n", encoding="utf-8")
    with pytest.raises(labelled.LabelError, match="who labelled"):
        labelled.load_resolved(path, {"x1"})
    path.write_text("﻿idea_id,verdict,labelled_by,resolution\nx1,GO,zain,\n")
    assert labelled.load_resolved(path, {"x1"}) == {"x1": VerdictLabel.GO}


# --- edge cases: label files (all input here is FAKE) ---


def test_a_label_file_the_csv_reader_rejects_is_a_label_error(tmp_path: Path) -> None:
    """An escaping ``csv.Error`` ends ``run_eval`` with exit 1, which means "regression"."""
    path = tmp_path / "FAKE_labels.csv"
    path.write_text('idea_id,verdict,note\nx1,GO,"' + "a" * 200_000 + '"\n')
    with pytest.raises(labelled.LabelError):
        labelled.load_labels(path, {"x1"})


@pytest.mark.parametrize(
    "content, message",
    [
        (b"", "no ideas"),
        (b"\xff\xfe\x00", "cannot read"),
        (b"{not json}\n", "not JSON"),
        (b"[1]\n", ":1:"),
        (b'{"idea_id": "a"}\n', ":1:"),
    ],
)
def test_unusable_idea_files_are_refused(tmp_path: Path, content: bytes, message: str) -> None:
    path = tmp_path / "ideas.jsonl"
    path.write_bytes(content)
    with pytest.raises(labelled.LabelError, match=message):
        labelled.load_ideas(path)


def test_a_repeated_idea_id_and_bad_files_give_exit_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    line = labelled.load_ideas()[0].model_dump_json() + "\n"
    path = tmp_path / "ideas.jsonl"
    path.write_text(line * 2)
    assert labelled.main(["--ideas", str(path), "--dir", str(tmp_path)]) == 2
    assert "twice" in capsys.readouterr().err
    path.write_text(line)
    # nothing labelled yet: open, not an error
    assert labelled.main(["--ideas", str(path), "--dir", str(tmp_path)]) == 1
    assert "labels_zain.csv does not exist yet" in capsys.readouterr().out
