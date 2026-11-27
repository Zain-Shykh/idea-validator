"""M6 stand-in over a list of cards: ``search``, ``card`` and ``evidence``.

Similarity is the share of a card's name, summary and customer words that appear in the query.
It is predictable on fixtures and says nothing about how well the real retrieval works.
"""

import json
import re
from collections.abc import Iterable, Sequence
from pathlib import Path

from common.contracts.core import EvidenceItem, IdeaCard
from common.contracts.kb import EvidenceResponse, SearchRequest, SearchResponse, SearchResult

INDEX_VERSION = "fake-kb"
_STOP = frozenset(
    {"a", "an", "and", "are", "as", "at", "be", "before", "by", "for", "from", "in", "into"}
    | {"is", "it", "of", "on", "or", "that", "the", "to", "with"}
)


def _words(text: str) -> set[str]:
    found = re.findall(r"[a-z0-9]+", text.lower())
    return {w[:-1] if len(w) > 3 and w.endswith("s") else w for w in found if w not in _STOP}


def _evidence(card: IdeaCard) -> EvidenceItem:
    """Each card has one chunk, ``#c0``, holding its text."""
    return EvidenceItem(
        evidence_id=f"{card.id}#c0",
        card_id=card.id,
        quote=card.text,
        source=card.source,
        url=card.url,
        retrieved_at=card.fetched_at,
    )


class FakeKB:
    def __init__(self, cards: Iterable[IdeaCard] = ()) -> None:
        self.cards = {card.id: card for card in cards}
        self.queries: list[SearchRequest] = []

    @classmethod
    def from_jsonl(cls, *paths: str | Path) -> "FakeKB":
        return cls(
            IdeaCard.model_validate(json.loads(line))
            for path in paths
            for line in Path(path).read_text(encoding="utf-8").splitlines()
            if line.strip()
        )

    async def search(self, request: SearchRequest) -> SearchResponse:
        self.queries.append(request)
        query = _words(request.query)
        results = []
        for card in self.cards.values():
            filters = request.filters
            if filters.sources is not None and card.source not in filters.sources:
                continue
            if filters.niches is not None and not set(filters.niches) & set(card.niche_tags):
                continue
            own = _words(f"{card.name} {card.summary} {card.customer}")
            score = len(own & query) / len(own) if own else 0.0
            if score > 0:
                item = _evidence(card)
                results.append(
                    SearchResult(**item.model_dump(), score=round(score, 4), card_name=card.name)
                )
        results.sort(key=lambda r: (-r.score, r.card_id))
        return SearchResponse(results=results[: request.k], index_version=INDEX_VERSION)

    async def card(self, card_id: str) -> IdeaCard | None:
        return self.cards.get(card_id)

    async def evidence(self, ids: Sequence[str]) -> EvidenceResponse:
        items, missing = [], []
        for evidence_id in ids:
            card_id, _, chunk = evidence_id.partition("#")
            card = self.cards.get(card_id)
            if card is None or chunk != "c0":
                missing.append(evidence_id)
            else:
                items.append(_evidence(card))
        return EvidenceResponse(items=items, missing=missing)
