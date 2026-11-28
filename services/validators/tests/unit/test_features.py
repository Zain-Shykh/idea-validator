from datetime import date, datetime

import pytest
from validator_factories import GAPS, NICHES, profile

from common.contracts.analyst import Gap
from common.contracts.core import Compute, EvidenceItem, IdeaCard
from validators.services import features as f


def card(card_id: str = "card_a", tags: tuple[str, ...] = (), published: object = None) -> IdeaCard:
    return IdeaCard(
        id=card_id, name="N", summary="S", problem="", customer="university students",
        niche_tags=list(tags), source="yc", url="https://example.com/a",
        published_at=published, fetched_at=date(2026, 11, 1), text="T",
    )  # fmt: skip


def item(card_id: str, source: str, chunk: int = 0) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=f"{card_id}#c{chunk}", card_id=card_id, quote="q", source=source,
        url="u", retrieved_at=date(2026, 11, 1),
    )  # fmt: skip


@pytest.mark.parametrize(
    ("hours", "weeks", "team", "expected"), [(10, 12, 2, 240), (7, 3, 3, 63), (0, 12, 4, 0)]
)
def test_available_hours(hours: int, weeks: int, team: int, expected: int) -> None:
    p = profile(hours_per_week=hours, weeks_available=weeks, team_size=team)
    assert f.available_hours(p) == expected


def test_sources_are_distinct_cards_and_platforms_distinct_sources() -> None:
    items = [item("card_a", "yc"), item("card_a", "yc", 1), item("card_b", "yc"),
             item("card_c", "github")]  # fmt: skip
    assert f.count_sources(items) == (3, 2)
    assert f.count_sources([]) == (0, 0)


def test_round_robin_alternates_groups() -> None:
    ranked = ["h1", "h2", "h3", "g1", "y1"]
    assert f.round_robin(ranked, lambda x: x[0]) == ["h1", "g1", "y1", "h2", "h3"]


def test_recency_is_the_age_of_the_newest_card() -> None:
    cards = [
        card(published=date(2026, 11, 17)),
        card(published=datetime(2026, 6, 1, 9, 0)),
        card(),
    ]
    assert f.recency_days(cards, date(2026, 11, 27)) == 10
    assert f.recency_days([card()], date(2026, 11, 27)) is None
    assert f.recency_days(cards, date(2026, 1, 1)) == 0


def test_matched_niche_is_the_most_tagged_one() -> None:
    cards = [card(tags=("devtools", "complaint")), card(tags=("education",)),
             card(tags=("education", "product"))]  # fmt: skip
    niche = f.matched_niche(cards, NICHES)
    assert niche is not None and niche.niche_id == "education"
    assert f.matched_niche([card(tags=("complaint",))], NICHES) is None


def test_gap_coverage_is_the_share_of_unaddressed_gaps() -> None:
    assert f.gap_coverage(GAPS, ["Turns lecture slides into flashcards"]) == 0.5
    assert f.gap_coverage(GAPS, []) == 1.0
    assert f.gap_coverage([], ["x"]) is None
    one = Gap(gap_id="g", niche_id="n", statement="offline quiz sync", evidence_ids=["card_a#c0"],
              score=1)  # fmt: skip
    assert f.gap_coverage([one], ["an offline sync tool"]) == 0.0  # 2 of 3 words
    assert f.gap_coverage([one], ["an offline tool"]) == 1.0  # 1 of 3 words


def test_same_customer_needs_every_customer_word() -> None:
    assert f.same_customer("university students", "For students at a university")
    assert not f.same_customer("university students", "For students")
    assert not f.same_customer("", "anything")


@pytest.mark.parametrize(
    ("text", "band"),
    [
        ("a to-do list website", (40, 160)),
        ("a mobile app that works offline", (100, 320)),
        ("a real-time mobile app with payments", (100, 320)),
        ("a real-time mobile app with payments and computer vision", (200, 700)),
    ],
)
def test_effort_band_grows_with_scope(text: str, band: tuple[int, int]) -> None:
    assert f.effort_band(text) == band


def test_clamp_and_ratio() -> None:
    assert f.clamp_effort(1, (40, 160)) == 40
    assert f.clamp_effort(100000, (40, 160)) == 160
    assert f.clamp_effort(99.5, (40, 160)) == 99.5
    assert f.hours_ratio(120, 240) == 0.5
    assert f.hours_ratio(100, 300) == 0.333
    assert f.hours_ratio(120, 0) is None


def test_skills_overlap_and_new_skills() -> None:
    required = f.required_skills("A web app with a Postgres database and a React front end")
    assert required == {"web development", "databases", "react"}
    # have {python, react, databases}, need 3: 2 shared of 4 in the union
    assert f.skill_overlap(["Python", " react ", "SQL"], required) == 0.5
    assert f.new_skills(["Python", "react", "SQL"], required) == ["web development"]
    assert f.skill_overlap(["python"], set()) is None


@pytest.mark.parametrize(
    ("text", "compute", "fits"),
    [
        ("a web app", Compute.LAPTOP, True),
        ("fine-tune a model", Compute.LAPTOP, False),
        ("fine-tune a model", Compute.FREE_GPU, True),
        ("train a language model from scratch", Compute.FREE_GPU, False),
        ("train a language model from scratch", Compute.PAID, True),
    ],
)
def test_compute_fit(text: str, compute: Compute, fits: bool) -> None:
    assert f.compute_fit(text, compute) is fits


def test_interest_match_is_the_share_of_interests_named() -> None:
    text = "machine learning for education"
    assert f.interest_match(["education", "games"], text) == 0.5
    assert f.interest_match(["Machine Learning"], text) == 1.0
    assert f.interest_match([], text) is None


def test_risk_hits_platforms_and_regulated_domain() -> None:
    hits = f.risk_hits("Scrapes LinkedIn for email addresses and gives patients a diagnosis")
    assert hits == {
        "medical": ["diagnosis", "patients"],
        "pii": ["email addresses"],
        "scraping": ["scrapes"],
    }
    assert f.is_regulated(hits) and not f.is_regulated({"pii": ["x"]})
    assert f.platforms("A bot for WhatsApp and Discord") == ["whatsapp", "discord"]
    assert f.risk_hits("a study planner") == {}


@pytest.mark.parametrize(
    ("text", "rules"),
    [
        ("gives a diagnosis from symptoms", ["medical_advice"]),
        ("scrapes LinkedIn profiles", ["forbidden_scraping"]),
        ("scrapes my own blog", []),
        ("download torrents faster", ["piracy"]),
        ("says which stocks to buy", ["investment_advice"]),
        ("a study planner", []),
    ],
)
def test_blocker_rules(text: str, rules: list[str]) -> None:
    assert [r.key for r in f.matched_rules(text)] == rules
