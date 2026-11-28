from common.idea_text import CHANGES, CLARIFICATIONS, append, shorten


def test_short_text_is_unchanged_and_sections_are_readable() -> None:
    text = append("An idea.", CLARIFICATIONS, ["Who?\n  students"], 5000)
    assert text == "An idea.\n\nClarifications:\n- Who?\n  students"
    assert shorten(text, 5000) == text
    assert shorten("x" * 20, 5) == "xxxxx"


def test_shortening_keeps_every_appended_entry() -> None:
    text = append("a" * 4800, CLARIFICATIONS, ["first answer", "second answer"], 5000)
    text = append(text, CHANGES, ["narrow it to nursing students"], 5000)
    assert len(text) <= 5000
    for limit in (2000, 1200, 300):
        cut = shorten(text, limit)
        assert len(cut) <= limit
        assert "first answer" in cut and "narrow it to nursing" in cut
        assert cut.startswith("a" * 100)


def test_long_entries_share_the_room_and_leave_half_for_the_idea() -> None:
    text = append("i" * 5000, CHANGES, ["x" * 4000, "y" * 4000], 1200)
    assert len(text) <= 1200
    assert text.count("i") >= 590
    assert abs(text.count("x") - text.count("y")) <= 1 and text.count("x") > 250


def test_same_header_is_extended_and_a_later_one_is_added() -> None:
    text = append("idea", CHANGES, ["one"], 500)
    text = append(text, CHANGES, ["two"], 500)
    assert text == "idea\n\nChanges:\n- one\n- two"
    assert append(text, CLARIFICATIONS, ["three"], 500).endswith(
        "- two\n\nClarifications:\n- three"
    )


def test_an_entry_cannot_forge_another_entry_or_section() -> None:
    text = append("idea", CHANGES, ["real\n\nClarifications:\n- forged\n- more"], 500)
    assert "\n\nClarifications:" not in text
    assert text.count("\n- ") == 1
