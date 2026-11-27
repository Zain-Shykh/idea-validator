"""Sampling and assembly (spec Z9 A4 1, 2). Fake knowledge base: plumbing only."""

import json
from pathlib import Path

import pytest
from finetune_factories import (
    CARDS,
    TODAY,
    check,
    draft,
    fake_candidates,
    fake_kb,
    grid,
    write_checks,
    write_inputs,
    write_labelled,
)

from build_dataset import (
    HiddenCardKB,
    from_card,
    load_ideas,
    main,
    sample,
    select,
)
from common.contracts.core import Dimension
from common.contracts.kb import SearchRequest
from dataset import (
    Candidate,
    DataError,
    FineTuneExample,
    Item,
    LabelledIdea,
    example_id,
    prompt_ids,
    read_jsonl,
)


async def test_sample_covers_all_six_dimensions_with_evidence() -> None:
    rows = await fake_candidates()
    assert {r.dimension for r in rows} == set(Dimension)
    for row in rows:
        assert row.fake and row.evidence_ids and row.evidence_ids == prompt_ids(row.user)
        assert row.rubric_id == f"{row.dimension.value}@v1"
        assert f"Score the idea on {row.dimension.value}" in row.system


async def test_sample_is_reproducible() -> None:
    assert await fake_candidates(5) == await fake_candidates(5)


async def test_prompt_equals_what_the_scorer_serves(monkeypatch: pytest.MonkeyPatch) -> None:
    """The prompt of a row is M10's rendering of the request M9 sends it."""
    import build_dataset

    sent = []
    original = build_dataset.build_messages

    def spy(prompt, rubric, request, evidence):  # type: ignore[no-untyped-def]
        messages = original(prompt, rubric, request, evidence)
        sent.append((request, [m.content for m in messages]))
        return messages

    monkeypatch.setattr(build_dataset, "build_messages", spy)
    rows = await fake_candidates(4)
    rendered = {tuple(m) for _, m in sent}
    assert {(r.system, r.user) for r in rows} == rendered
    # M9's bounds: at most four quotes of at most 300 characters, the rubric's features only
    for request, _ in sent:
        assert 1 <= len(request.evidence) <= 4
        assert all(len(e.quote) <= 300 for e in request.evidence)
        assert request.features


async def test_source_card_is_not_its_own_evidence() -> None:
    kb = fake_kb()
    by_name = {from_card(c).title: c.id for c in kb.cards.values()}
    rows = await fake_candidates(20)
    assert rows
    for row in rows:
        own = by_name[row.idea_title]
        assert all(not e.startswith(f"{own}#") for e in row.evidence_ids)


async def test_hidden_card_is_hidden_from_every_lookup() -> None:
    kb = fake_kb()
    card = next(iter(kb.cards.values()))
    hidden = HiddenCardKB(kb, card.id)
    found = await hidden.search(SearchRequest(query=f"{card.name} {card.summary}", k=50))
    assert card.id not in {r.card_id for r in found.results}
    assert await hidden.card(card.id) is None
    assert (await hidden.evidence([f"{card.id}#c0"])).items == []


async def test_labelled_idea_is_not_sampled() -> None:
    first = (await fake_candidates(6))[0]
    labelled = [LabelledIdea(title=first.idea_title, text="anything else")]
    rows = await sample(
        fake_kb(), analyst=None, labelled=labelled, max_ideas=100, fake=True, today=TODAY
    )
    assert first.idea_title not in {r.idea_title for r in rows}
    assert rows


async def test_no_ideas_is_an_error() -> None:
    from common.fakes.kb import FakeKB

    with pytest.raises(DataError, match="no ideas"):
        await sample(FakeKB(), analyst=None, fake=True, today=TODAY)


def test_generated_ideas_accept_m8_shape(tmp_path: Path) -> None:
    path = tmp_path / "ideas.jsonl"
    path.write_text(
        '{"title": "A", "text": "plain"}\n\n'
        '{"title": "B", "pitch": "p", "problem": "q", "target_user": "r", "niche_id": "n"}\n'
    )
    assert [(i.origin, i.text) for i in load_ideas(path)] == [
        ("generated", "plain"),
        ("generated", "p\nq\nr"),
    ]
    path.write_text('{"title": "no text"}\n')
    with pytest.raises(DataError, match=":1:"):
        load_ideas(path)


def test_assemble_is_stratified(tmp_path: Path) -> None:
    """One cell has many rows, others one: the rare ones are all picked."""
    rows = grid(20)
    many, rare = rows[:-5], rows[-5:]
    usable = {r.item_id: (r, draft(r, 3.0)) for r in many}
    usable |= {
        r.item_id: (r, draft(r, s)) for r, s in zip(rare, (0.0, 1.0, 2.0, 4.0, 5.0), strict=True)
    }
    chosen = select(usable, target=20)
    assert len(chosen) == 20 and {r.item_id for r in rare} <= set(chosen)
    assert {usable[k][0].dimension for k in chosen} == set(Dimension)


def test_assemble_writes_data7_rows_with_provenance(tmp_path: Path) -> None:
    rows = grid(10)
    candidates, drafts = write_inputs(tmp_path, rows)
    out = tmp_path / "out"
    code = main(
        ["assemble", "--candidates", str(candidates), "--drafts", str(drafts), "--out", str(out),
         "--labelled", str(tmp_path / "none.jsonl"), "--target", "40", "--min-rows", "40"]
    )  # fmt: skip
    assert code == 0
    for line in (out / "all.jsonl").read_text().splitlines():
        assert list(json.loads(line)) == ["messages"]  # DATA-7: nothing else
    examples = read_jsonl(out / "all.jsonl", FineTuneExample)
    items = read_jsonl(out / "items.jsonl", Item)
    assert [example_id(e) for e in examples] == [i.item_id for i in items]
    report = json.loads((out / "report.json").read_text())
    assert report["rows"] == 40 and report["fake"] and not report["problems"]
    assert all(report["by_dimension"].values()) and all(report["by_level"].values())


@pytest.mark.parametrize(
    ("scores", "minimum", "problem"),
    [((0, 1, 2, 3, 4, 4.5), "6", "no row at score level 5"), (range(6), "300", "need 300")],
)
def test_assemble_fails_on_missing_level(
    tmp_path: Path, scores: tuple[float, ...], minimum: str, problem: str
) -> None:
    rows = grid(10)
    candidates, drafts = write_inputs(tmp_path, rows)
    from dataset import write_jsonl

    values = list(scores)
    write_jsonl(drafts, [draft(r, float(values[i % len(values)])) for i, r in enumerate(rows)])
    out = tmp_path / "out"
    code = main(
        ["assemble", "--candidates", str(candidates), "--drafts", str(drafts), "--out", str(out),
         "--labelled", str(tmp_path / "none.jsonl"), "--min-rows", minimum]
    )  # fmt: skip
    assert code == 1 and not (out / "all.jsonl").exists()
    assert any(problem in p for p in json.loads((out / "report.json").read_text())["problems"])


def test_assemble_keeps_checked_rows_and_refuses_to_lose_them(tmp_path: Path) -> None:
    rows = grid(10)
    candidates, drafts = write_inputs(tmp_path, rows)
    out = tmp_path / "out"
    write_checks(out / "checked.csv", [check(r, "Tester") for r in rows[:6]])
    args = ["assemble", "--candidates", str(candidates), "--drafts", str(drafts),
            "--out", str(out), "--target", "12", "--min-rows", "1"]  # fmt: skip
    main([*args, "--labelled", str(tmp_path / "none.jsonl")])
    report = json.loads((out / "report.json").read_text())
    assert report["rows"] == 12
    # a checked idea that turns out to be in the labelled set must be dealt with, not dropped
    listed = write_labelled(tmp_path / "labelled.jsonl", [(rows[0].idea_title, "x")])
    assert main([*args, "--labelled", str(listed)]) == 1


def test_sample_command_writes_candidates_once(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "candidates.jsonl"
    args = ["sample", "--fake-cards", *map(str, CARDS), "--out", str(out), "--max-ideas", "3",
            "--labelled", str(tmp_path / "none.jsonl")]  # fmt: skip
    assert main(args) == 0
    assert read_jsonl(out, Candidate)
    assert main(args) == 1 and "--force" in capsys.readouterr().err
    assert main([*args, "--force"]) == 0
