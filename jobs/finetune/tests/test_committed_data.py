"""The files in ``jobs/finetune/data`` themselves (spec Z9 A4 6; FT-2..4). Skipped while there
is no data: nothing has been generated yet."""

import pytest

from build_dataset import level
from common.contracts.agents import FineTuneExample
from common.contracts.core import Dimension
from dataset import (
    DATA_DIR,
    LABELLED,
    Item,
    example_id,
    label_of,
    load_labelled,
    read_checks,
    read_jsonl,
)
from split import MIN_CHECKED, PARTS, QUOTAS, assert_no_leakage

pytestmark = pytest.mark.skipif(
    not (DATA_DIR / "train.jsonl").exists(), reason="no fine-tuning data committed yet"
)


def test_committed_dataset() -> None:
    rows = read_jsonl(DATA_DIR / "all.jsonl", FineTuneExample)
    items = {i.item_id: i for i in read_jsonl(DATA_DIR / "items.jsonl", Item)}
    assert len(rows) >= 300 and not any(i.fake for i in items.values())
    assert {i.dimension for i in items.values()} == set(Dimension)
    assert {level(label_of(r).score) for r in rows} == set(range(6))


def test_committed_split_has_no_leak_and_checked_rows_are_the_test_set() -> None:
    parts = {p: read_jsonl(DATA_DIR / f"{p}.jsonl", FineTuneExample) for p in PARTS}
    items = {i.item_id: i for i in read_jsonl(DATA_DIR / "items.jsonl", Item)}
    assert_no_leakage(parts, items, load_labelled(LABELLED))
    checks = {k: c for k, c in read_checks(DATA_DIR / "checked.csv").items() if c.label}
    assert len(checks) >= MIN_CHECKED
    assert {example_id(r) for r in parts["test"]} == set(checks)
    for name, needed in QUOTAS.items():
        assert sum(c.checked_by.casefold() == name.casefold() for c in checks.values()) >= needed
