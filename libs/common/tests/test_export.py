from pathlib import Path

from common.contracts import export


def test_committed_schemas_are_up_to_date() -> None:
    assert export.main(["--check"]) == 0


def test_check_fails_on_missing_stale_and_extra_files(tmp_path: Path) -> None:
    assert export.main(["--out", str(tmp_path)]) == 0
    assert export.main(["--check", "--out", str(tmp_path)]) == 0

    stale_file = tmp_path / "core.Verdict.json"
    stale_file.write_text("{}")
    (tmp_path / "core.IdeaCard.json").unlink()
    (tmp_path / "core.Removed.json").write_text("{}")

    assert export.stale(tmp_path) == [
        "core.IdeaCard.json",
        "core.Removed.json",
        "core.Verdict.json",
    ]
    assert export.main(["--check", "--out", str(tmp_path)]) == 1

    export.write(tmp_path)  # rewrites and removes the leftover
    assert export.stale(tmp_path) == []
