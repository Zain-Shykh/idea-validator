"""Rules about idea text that more than one service applies: M9's scorers and M3's Discover
filters must agree on what a duplicate is and how big a build is (§6.4, §12.4). Pure.
"""

import re

from common.contracts.core import IdeaCard, StudentProfile

COMPLAINT_TAG = "complaint"
_STOP = frozenset(
    {"a", "an", "and", "are", "as", "at", "be", "before", "by", "for", "from", "in", "into"}
    | {"is", "it", "of", "on", "or", "that", "the", "their", "to", "with", "who"}
)


def words(text: str) -> set[str]:
    """Lower-case words without stop words and plural ``s``."""
    found = re.findall(r"[a-z0-9]+", text.lower())
    return {w[:-1] if len(w) > 3 and w.endswith("s") else w for w in found if w not in _STOP}


def search(pattern: str, text: str) -> re.Match[str] | None:
    return re.search(rf"\b(?:{pattern})\b", text, re.IGNORECASE)


def is_complaint(card: IdeaCard) -> bool:
    return COMPLAINT_TAG in card.niche_tags


def same_customer(customer: str, idea_text: str) -> bool:
    wanted = words(customer)
    return bool(wanted) and wanted <= words(idea_text)


# --- build effort ------------------------------------------------------------------------------

# ponytail: keyword tables, tuned on the labelled set in Z10
# each match makes the build bigger; the count picks the band an estimate is clamped to
_SCOPE = (
    "mobile app|android|ios app",
    "real[- ]time|live (?:chat|sync|collaboration)|multiplayer",
    "machine learning|ml model|classifier|recommendation",
    "fine[- ]?tun\\w*|train\\w* (?:a |our |the )?(?:\\w+ )?model",
    "computer vision|image recognition|ocr|video processing",
    "hardware|sensors?|arduino|raspberry pi|iot|wearables?",
    "payments?|checkout|billing",
    "marketplace|social network",
    "integrat\\w+ with|sync\\w* with",
    "offline",
)
EFFORT_BANDS: tuple[tuple[int, int], ...] = ((40, 160), (100, 320), (200, 700))  # hours


def effort_band(idea_text: str) -> tuple[int, int]:
    scope = sum(bool(search(pattern, idea_text)) for pattern in _SCOPE)
    return EFFORT_BANDS[0 if scope <= 1 else 1 if scope <= 3 else 2]


def available_hours(profile: StudentProfile) -> int:
    return profile.hours_per_week * profile.weeks_available * profile.team_size
