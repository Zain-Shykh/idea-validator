from pathlib import Path

from common.contracts.analyst import Gap, Niche
from common.contracts.kb import SearchFilters, SearchRequest
from common.fakes import FakeAnalyst, FakeKB

CARDS = Path(__file__).resolve().parents[3] / "eval/fixtures/cards_small.jsonl"


async def test_fake_kb_ranks_filters_and_resolves_evidence() -> None:
    kb = FakeKB.from_jsonl(CARDS)
    query = "A tool that turns lecture slides into spaced-repetition flashcards"
    found = (await kb.search(SearchRequest(query=query, k=5))).results
    assert found[0].card_name == "SlideCards" and len(found) <= 5
    assert all(0 < r.score <= 1 for r in found)
    only = SearchRequest(query=query, filters=SearchFilters(sources=["github"]))
    assert {r.source for r in (await kb.search(only)).results} == {"github"}

    resolved = await kb.evidence([found[0].evidence_id, "card_nope#c0", f"{found[0].card_id}#c9"])
    assert [i.evidence_id for i in resolved.items] == [found[0].evidence_id]
    assert len(resolved.missing) == 2
    assert await kb.card("card_nope") is None


async def test_fake_kb_returns_nothing_for_unrelated_text() -> None:
    kb = FakeKB.from_jsonl(CARDS)
    assert (await kb.search(SearchRequest(query="zzzz qqqq"))).results == []


async def test_fake_analyst_filters_gaps_by_niche() -> None:
    niche = Niche(niche_id="education", name="education", size=3, trend_slope=0.1,
                  trend_label="rising")  # fmt: skip
    gap = Gap(gap_id="g1", niche_id="education", statement="s", evidence_ids=["card_a#c0"], score=1)
    analyst = FakeAnalyst([niche], [gap])
    assert (await analyst.niches()).niches == [niche]
    assert (await analyst.gaps("education")).gaps == [gap]
    assert (await analyst.gaps("devtools")).gaps == []
