"""Boundary lengths of ``common.idea_text`` (spec Z8, A4 #15)."""

import pytest

from common.idea_text import CHANGES, CLARIFICATIONS, append, shorten

LONG = append(
    append("b" * 4000, CLARIFICATIONS, ["first answer " * 20, "second"], 5000),
    CHANGES,
    ["a change " * 50],
    5000,
)


@pytest.mark.parametrize("text", ["", "plain", "p" * 300, LONG, "x" + "\n\nChanges:\n- " * 5])
def test_no_limit_is_ever_exceeded(text: str) -> None:
    for limit in [*range(0, 130), 199, 200, 201, 1199, 1200, 1201, 1999, 2000, 4999, 5000]:
        assert len(shorten(text, limit)) <= limit, limit
        assert len(append(text, CHANGES, ["new entry"], limit)) <= limit, limit


def test_the_exact_length_is_kept_and_one_less_is_cut_from_the_idea() -> None:
    assert shorten(LONG, len(LONG)) == LONG
    cut = shorten(LONG, len(LONG) - 1)
    assert len(cut) == len(LONG) - 1 and cut.endswith(LONG[-300:])


def test_many_changes_in_a_row_stay_within_the_limit_and_keep_the_newest() -> None:
    text = "i" * 5000
    for n in range(12):
        text = append(text, CHANGES, [f"change{n:02d} " + "z" * 480], 5000)
    assert len(text) <= 5000 and text.startswith("i" * 2000)
    assert all(f"change{n:02d}" in shorten(text, 1200) for n in range(12))


# M3 appends "<question, at most 200 characters>\n  <answer>"; M9 sends M10 1200 characters
def test_answers_to_long_questions_reach_the_scoring_model() -> None:
    pairs = [f"{'Q' * 200}\n  ANSWER{n}" for n in range(3)]
    text = append("i" * 4000, CLARIFICATIONS, pairs, 5000)
    text = append(text, CHANGES, ["narrow it " * 50], 5000)
    cut = shorten(text, 1200)
    assert [f"ANSWER{n}" in cut for n in range(3)] == [True, True, True]
