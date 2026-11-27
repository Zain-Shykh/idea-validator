"""Fake output stays out of the places for real data and results (spec Z9 A4 11)."""

from pathlib import Path

import pytest
from finetune_factories import CARDS

from build_dataset import main
from dataset import DATA_DIR, RESULTS_DIR, DataError, guard_output


@pytest.mark.parametrize("folder", [DATA_DIR, RESULTS_DIR])
def test_fake_output_is_refused_in_real_folders(folder: Path) -> None:
    with pytest.raises(DataError, match="fake"):
        guard_output(folder / "x.json", fake=True)
    with pytest.raises(DataError, match="fake"):
        guard_output(folder / ".." / folder.name / "deeper" / "x.json", fake=True)
    guard_output(folder / "x.json", fake=False)


def test_sample_with_fake_cards_refuses_the_data_folder(capsys: pytest.CaptureFixture[str]) -> None:
    target = DATA_DIR / "candidates.jsonl"
    existed = target.exists()
    assert main(["sample", "--fake-cards", *map(str, CARDS)]) == 1
    assert "fake" in capsys.readouterr().err and target.exists() is existed
