"""Poisoned pages (ETH-5, M12-2, AO-7; spec Z6, A4 6-8): fetched text that gives orders is
flagged, kept out of every prompt and counted. No real model is called."""

from critic_factories import ROOT, CountingCassette, Sink, checker, claim, reply, request
from fastapi.testclient import TestClient
from validator_factories import StubScorer, body, engine

from common.contracts.agents import ScanRequest, ScanResponse
from common.contracts.core import EvidenceItem, TraceKind
from common.fakes import FakeKB
from common.metrics import INJECTIONS_BLOCKED
from critic.main import build_app
from critic.settings import CriticSettings
from validators.scorers import SCORERS

FIXTURES = ROOT / "eval/fixtures"
POISONED = FIXTURES / "poisoned_cards.jsonl"
CLEAN = (FIXTURES / "cards_small.jsonl", FIXTURES / "validator_cards.jsonl")
PLANTED = "card_4ea32214#c0"  # cards_small.jsonl carries one poisoned card among honest ones
ORDERS = ("score 5", "score of 5", "claim is supported", "card_x#c0", "pre​vious")


async def evidence_of(knowledge: FakeKB) -> list[EvidenceItem]:
    return (await knowledge.evidence([f"{i}#c0" for i in knowledge.cards])).items


async def scan(items: list[EvidenceItem]) -> ScanResponse:
    built = checker()
    api = TestClient(build_app(CriticSettings(KB_URL="http://kb:8000"), checker=built))
    payload = ScanRequest(evidence=items).model_dump(mode="json")
    return ScanResponse.model_validate_json(api.post("/scan", json=payload).text)


async def test_scan_flags_poisoned_cards() -> None:
    items = await evidence_of(FakeKB.from_jsonl(POISONED))
    counter = INJECTIONS_BLOCKED.labels("m12")
    before = counter._value.get()
    flagged = (await scan(items)).flagged
    assert {f.evidence_id for f in flagged} == {i.evidence_id for i in items}
    assert all(f.patterns and f.card_id for f in flagged)
    assert counter._value.get() == before + len(items)


async def test_scan_passes_clean_cards() -> None:
    items = await evidence_of(FakeKB.from_jsonl(*CLEAN))
    assert len(items) > 60
    assert [f.evidence_id for f in (await scan(items)).flagged] == [PLANTED]


async def test_check_does_not_follow_a_poisoned_card() -> None:
    knowledge = FakeKB.from_jsonl(CLEAN[0], POISONED)
    poisoned = [f"{i}#c0" for i in knowledge.cards if i.startswith("card_poison")]
    clean = "card_816599ab#c0"
    cassette = CountingCassette([reply("unsupported")])
    sink = Sink()
    built = checker(cassette=cassette, knowledge=knowledge, sink=sink)
    only = claim("This idea deserves the top mark", poisoned, "only")
    mixed = claim("This idea deserves the top mark", [clean, *poisoned], "mixed")
    first, second = (await built.check(request(only, mixed), "run_1")).results
    assert (first.verdict, second.verdict) == ("unverifiable", "unsupported")
    assert cassette.calls == 1
    await sink.tracer.flush()
    (prompt,) = [r.input for r in sink.records if r.kind is TraceKind.LLM_CALL]
    assert prompt and clean in prompt
    assert not any(order in prompt for order in ORDERS)
    assert not any(i in prompt for i in poisoned)


async def test_validators_withhold_poisoned_passages_from_the_scorer() -> None:
    knowledge = FakeKB.from_jsonl(*CLEAN, POISONED)
    poisoned = {i for i in knowledge.cards if i.startswith("card_poison")}
    scorer = StubScorer()
    built = engine(scorer=scorer, knowledge=knowledge, search_k=50, min_similarity=0)
    retrieved = set()
    for run in SCORERS.values():
        await run(built, body())
    for query in list(knowledge.queries):  # searching again appends to the list
        retrieved |= {r.card_id for r in (await FakeKB.search(knowledge, query)).results}
    assert retrieved & poisoned, "the poisoned cards must be retrieved for this test to count"
    sent = [item for request_ in scorer.requests for item in request_.evidence]
    assert sent and not {item.card_id for item in sent} & poisoned
