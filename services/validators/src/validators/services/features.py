"""Computed features and code rules (§12.4, WF-V4). Pure: no I/O, no clock, no model."""

import re
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from itertools import zip_longest

from common.contracts.analyst import Gap, Niche
from common.contracts.core import BlockerType, Compute, EvidenceItem, IdeaCard, StudentProfile
from common.idea_rules import (
    COMPLAINT_TAG,
    EFFORT_BANDS,
    available_hours,
    effort_band,
    is_complaint,
    same_customer,
    words,
)
from common.idea_rules import search as _search

# the rules M3's Discover filters share live in common.idea_rules
__all__ = [
    "EFFORT_BANDS",
    "available_hours",
    "effort_band",
    "is_complaint",
    "same_customer",
    "words",
]

_KIND_TAGS = frozenset({COMPLAINT_TAG, "product"})

# --- evidence ----------------------------------------------------------------------------------


def count_sources(items: Iterable[EvidenceItem]) -> tuple[int, int]:
    """VE-4a: independent sources are distinct cards, platforms are distinct ``source`` values."""
    items = list(items)
    return len({i.card_id for i in items}), len({i.source for i in items})


def round_robin[T](ranked: Sequence[T], key: Callable[[T], str]) -> list[T]:
    """Take the best of each group (platform) in turn, so a short selection still shows several."""
    groups: dict[str, list[T]] = {}
    for entry in ranked:
        groups.setdefault(key(entry), []).append(entry)
    return [e for turn in zip_longest(*groups.values()) for e in turn if e is not None]


def recency_days(cards: Iterable[IdeaCard], today: date) -> int | None:
    """Age of the newest publication; None when no card has a date."""
    dates = [d.date() if isinstance(d, datetime) else d for c in cards if (d := c.published_at)]
    return max(0, (today - max(dates)).days) if dates else None


# --- niches and gaps ---------------------------------------------------------------------------


def matched_niche(cards: Iterable[IdeaCard], niches: Sequence[Niche]) -> Niche | None:
    """The niche most of the retrieved cards are tagged with. It depends on the evidence only,
    never on the profile."""
    counts = Counter(
        tag.casefold() for c in cards for tag in set(c.niche_tags) if tag not in _KIND_TAGS
    )
    for tag, _ in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0])):
        for niche in niches:
            if tag in (niche.niche_id.casefold(), niche.name.casefold()):
                return niche
    return None


def gap_coverage(gaps: Sequence[Gap], product_texts: Sequence[str]) -> float | None:
    """Share of the gaps no product addresses: none of the texts has half of the gap's words."""
    if not gaps:
        return None
    products = [words(text) for text in product_texts]
    unaddressed = 0
    for gap in gaps:
        wanted = words(gap.statement)
        if not any(wanted and len(wanted & have) * 2 >= len(wanted) for have in products):
            unaddressed += 1
    return round(unaddressed / len(gaps), 3)


# --- feasibility and learning ------------------------------------------------------------------

# ponytail: keyword tables, tuned on the labelled set in Z10
_SKILLS = {
    "python": "python",
    "javascript": "javascript|node(?:\\.js)?",
    "typescript": "typescript",
    "react": "react",
    "web development": "web ?(?:app|site)s?|website|dashboard|browser extension",
    "mobile development": "mobile app|android|ios app|flutter|react native",
    "databases": "database|sql|postgres",
    "machine learning": "machine learning|ml model|classifier|recommendation|recommends?",
    "llm integration": "llm|gpt|chatbot|language model|ai[- ](?:assistant|powered|tutor)",
    "model training": "fine[- ]?tun\\w*|train\\w* (?:a |our |the )?(?:\\w+ )?model",
    "computer vision": "computer vision|image recognition|ocr|photos? of",
    "devops": "docker|kubernetes|ci/cd|ci pipelines?",
    "payments": "payments?|checkout|billing",
    "real-time systems": "real[- ]time|live (?:chat|sync|collaboration)|multiplayer",
    "hardware": "hardware|sensors?|arduino|raspberry pi|iot|wearables?",
}
_SKILL_ALIASES = {"js": "javascript", "ts": "typescript", "ml": "machine learning",
                  "sql": "databases", "reactjs": "react", "web": "web development"}  # fmt: skip

_HEAVY_COMPUTE = "from scratch|foundation model|train\\w* (?:a |our |an )?(?:llm|language model)"
_GPU_COMPUTE = (
    "fine[- ]?tun\\w*|train\\w* (?:a |our |the )?(?:\\w+ )?model|computer vision|"
    "image recognition|video generation"
)
_COMPUTE_RANK = {Compute.LAPTOP: 0, Compute.FREE_GPU: 1, Compute.PAID: 2}


def clamp_effort(hours: float, band: tuple[int, int]) -> float:
    return float(min(max(hours, band[0]), band[1]))


def hours_ratio(estimate: float, available: int) -> float | None:
    return round(estimate / available, 3) if available else None


def required_skills(idea_text: str) -> set[str]:
    return {skill for skill, pattern in _SKILLS.items() if _search(pattern, idea_text)}


def _skill_names(skills: Iterable[str]) -> set[str]:
    names = (s.strip().casefold() for s in skills)
    return {_SKILL_ALIASES.get(name, name) for name in names if name}


def skill_overlap(have: Iterable[str], required: set[str]) -> float | None:
    """Jaccard of the student's skills and the skills the idea names; None if it names none."""
    own = _skill_names(have)
    return round(len(own & required) / len(own | required), 3) if required else None


def new_skills(have: Iterable[str], required: set[str]) -> list[str]:
    return sorted(required - _skill_names(have))


def compute_fit(idea_text: str, compute: Compute) -> bool:
    need = Compute.LAPTOP
    if _search(_HEAVY_COMPUTE, idea_text):
        need = Compute.PAID
    elif _search(_GPU_COMPUTE, idea_text):
        need = Compute.FREE_GPU
    return _COMPUTE_RANK[compute] >= _COMPUTE_RANK[need]


def interest_match(interests: Iterable[str], text: str) -> float | None:
    """Share of the student's interests whose words all appear in ``text``."""
    wanted = [w for interest in interests if (w := words(interest))]
    have = words(text)
    return round(sum(w <= have for w in wanted) / len(wanted), 3) if wanted else None


# --- risk --------------------------------------------------------------------------------------

# shared by the risk categories and the blocker rules, on the idea and on the evidence
_SCRAPING = "scrap(?:e|es|ed|ing|er|ers)|crawl\\w*"
_PAYWALL = "paywalls?"

_RISK = {
    "medical": "medical|medicine|medication|diagnos\\w*|patients?|symptoms?|prescri\\w*|dosages?|"
    "clinical|health advice|therap\\w*|disease",
    "legal": "legal advice|lawyers?|lawsuits?|contracts? review|immigration",
    "finance": "invest\\w*|trading|stocks?|crypto\\w*|loans?|credit score|bank accounts?|tax\\w*",
    "minors": "children|kids|minors|under[- ]1[38]|school ?children",
    "pii": "personal data|e-?mail addresses?|phone numbers?|location tracking|biometric\\w*|"
    "face recognition|health records?|passwords?",
    "scraping": f"{_SCRAPING}|terms of service",
}
_REGULATED = frozenset({"medical", "legal", "finance", "minors"})
_PLATFORMS = ("instagram", "whatsapp", "linkedin", "facebook", "tiktok", "twitter", "youtube",
              "discord", "telegram", "spotify", "reddit", "openai")  # fmt: skip
_CLOSED_PLATFORMS = "linkedin|instagram|facebook|tiktok|twitter|whatsapp"


@dataclass(frozen=True)
class BlockerRule:
    key: str
    type: BlockerType
    description: str
    idea: tuple[str, ...]  # all of these must match the idea
    evidence: str  # a card must match this before the blocker may be named


# ponytail: keyword rules cannot read negation ("not medical advice"). A rule never decides by
# itself: without a card on the same topic it only makes the risk dimension abstain.
BLOCKER_RULES = (
    BlockerRule(
        "medical_advice",
        BlockerType.SAFETY,
        "unlicensed medical advice: the idea offers diagnosis, treatment or dosage guidance",
        ("diagnos\\w*|prescri\\w*|dosages?|medical advice|treatment (?:advice|plans?)",),
        "medical|diagnos\\w*|health advice|patients?|prescri\\w*|dosages?|clinical",
    ),
    BlockerRule(
        "forbidden_scraping",
        BlockerType.LEGAL,
        "scraping a platform whose terms of service forbid it",
        (_SCRAPING, _CLOSED_PLATFORMS),
        f"{_SCRAPING}|terms of service",
    ),
    BlockerRule(
        "piracy",
        BlockerType.LEGAL,
        "copyright infringement: the idea distributes or unlocks protected content",
        (
            f"pirat\\w*|torrent\\w*|bypass\\w* (?:\\w+ )?{_PAYWALL}|"
            "crack\\w* (?:software|licen[cs]es?)",
        ),
        f"copyright|pira\\w*|dmca|{_PAYWALL}",
    ),
    BlockerRule(
        "investment_advice",
        BlockerType.LEGAL,
        "unlicensed investment advice: the idea tells users what to buy or sell",
        ("investment advice|trading signals?|which (?:stocks|coins|crypto\\w*) to (?:buy|sell)",),
        "investment advice|financial advice|securities|regulat\\w*|licen[cs]\\w*",
    ),
)


def risk_hits(idea_text: str) -> dict[str, list[str]]:
    """Category -> the distinct terms of the idea that matched it."""
    found = {
        category: sorted(
            {m.lower() for m in re.findall(rf"\b(?:{pattern})\b", idea_text, re.IGNORECASE)}
        )
        for category, pattern in _RISK.items()
    }
    return {category: terms for category, terms in found.items() if terms}


def mentions_risk(text: str, categories: Iterable[str]) -> bool:
    return any(_search(_RISK[category], text) for category in categories)


def platforms(idea_text: str) -> list[str]:
    """Third-party platforms the idea names (``platform_dependency``)."""
    return [name for name in _PLATFORMS if _search(name, idea_text)]


def is_regulated(hits: Iterable[str]) -> bool:
    return bool(_REGULATED & set(hits))


def matched_rules(idea_text: str) -> list[BlockerRule]:
    return [r for r in BLOCKER_RULES if all(_search(p, idea_text) for p in r.idea)]


def supports(rule: BlockerRule, quote: str) -> bool:
    return _search(rule.evidence, quote) is not None
