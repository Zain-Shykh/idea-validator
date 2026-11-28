"""What the four scorers share: traced retrieval, evidence bounds, the M10 call and grounding.

Nothing the scoring model writes leaves this module as text: only its score, confidence and the
ids it picked from the evidence it was sent. Reasons are written by code, so a product name can
only come from a knowledge-base card (M9-4, AVOID-4).
"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx

from common.contracts.agents import Rubric, ScoreRequest, ScorerRequest
from common.contracts.analyst import Gap, Niche
from common.contracts.core import (
    Dimension,
    DimensionScore,
    EvidenceItem,
    IdeaCard,
    IdeaInput,
    TraceKind,
)
from common.contracts.kb import SearchRequest, SearchResponse
from common.http import ServiceClient, ServiceError
from common.trace import TraceClient
from validators.services.effort import AGENT, EffortEstimator
from validators.services.tools import Analyst, KnowledgeBase
from validators.settings import ValidatorSettings

log = logging.getLogger("validators")

NO_MODEL = "none"
FAILED_PREFIX = "scoring failed"
MAX_QUERY_CHARS = 2000
MAX_REQUEST_EVIDENCE = 20
MAX_GAPS = 20
_MAX_NAME_CHARS = 80


class KbUnavailableError(Exception):
    """Retrieval failed. That is not the same as finding nothing."""


@dataclass(frozen=True)
class Hit:
    """One knowledge-base card and its best evidence chunk."""

    item: EvidenceItem
    card: IdeaCard
    similarity: float | None  # None: evidence from the request, or no 0..1 score


def idea_text(idea: IdeaInput) -> str:
    return f"{idea.title}\n{idea.text}"


def card_label(card: IdeaCard) -> str:
    return f"{card.name[:_MAX_NAME_CHARS]} [{card.id}]"


class Engine:
    def __init__(
        self,
        settings: ValidatorSettings,
        *,
        kb: KnowledgeBase,
        analyst: Analyst | None,
        scorer: ServiceClient,
        effort: EffortEstimator,
        rubrics: dict[Dimension, Rubric],
        tracer: TraceClient,
        today: Callable[[], date] = date.today,
    ) -> None:
        self.settings = settings
        self.kb = kb
        self.analyst = analyst
        self.scorer = scorer
        self.effort = effort
        self.rubrics = rubrics
        self.tracer = tracer
        self.today = today
        # one per process: calls wait here, so the HTTP timeout covers only the scoring
        self._slots = asyncio.Semaphore(settings.scorer_concurrency)

    def deadline(self) -> float:
        return asyncio.get_running_loop().time() + self.settings.deadline_s

    async def _tool[T](
        self,
        run_id: str,
        tool: str,
        input: str,
        call: Awaitable[T],
        summary: Callable[[T], object],
    ) -> T:
        with self.tracer.step(
            AGENT, TraceKind.TOOL_CALL, tool=tool, input=input, run_id=run_id
        ) as step:
            result = await call
            step.output = json.dumps(summary(result))
            return result

    # --- retrieval -----------------------------------------------------------------------------

    async def retrieve(self, request: ScorerRequest, query: str) -> list[Hit]:
        """One search of its own plus the request's evidence as M6 knows it; one hit per card,
        the most similar first."""
        run_id = request.run_id
        try:
            search = SearchRequest(query=query[:MAX_QUERY_CHARS], k=self.settings.search_k)
            found: SearchResponse = await self._tool(
                run_id, "search_kb", search.model_dump_json(), self.kb.search(search),
                lambda r: [x.evidence_id for x in r.results],
            )  # fmt: skip
            candidates: list[tuple[EvidenceItem, float | None]] = []
            for result in sorted(found.results, key=lambda r: -r.score):
                if result.score < self.settings.min_similarity:
                    continue
                item = EvidenceItem(**result.model_dump(exclude={"score", "card_name"}))
                # a score outside 0..1 is not a similarity: never a competitor or duplicate
                candidates.append((item, result.score if result.score <= 1 else None))
            wanted = [e.evidence_id for e in request.evidence][:MAX_REQUEST_EVIDENCE]
            candidates += [(item, None) for item in await self.resolve(run_id, wanted)]

            best: dict[str, tuple[EvidenceItem, float | None]] = {}
            for item, similarity in candidates:
                best.setdefault(item.card_id, (item, similarity))
            cards = await self._tool(
                run_id, "get_cards", json.dumps(sorted(best)),
                asyncio.gather(*(self.kb.card(card_id) for card_id in best)),
                lambda found: [c.id for c in found if c is not None],
            )  # fmt: skip
        except ServiceError as exc:
            raise KbUnavailableError(exc.code) from exc
        return [
            Hit(item, card, similarity)
            for (item, similarity), card in zip(best.values(), cards, strict=True)
            if card is not None and card.id == item.card_id
        ]

    async def resolve(self, run_id: str, ids: Sequence[str]) -> list[EvidenceItem]:
        """Evidence as M6 stores it; ids it does not know are dropped."""
        ids = list(dict.fromkeys(ids))
        if not ids:
            return []
        try:
            response = await self._tool(
                run_id, "kb_evidence", json.dumps(ids), self.kb.evidence(ids),
                lambda r: [i.evidence_id for i in r.items],
            )  # fmt: skip
        except ServiceError as exc:
            raise KbUnavailableError(exc.code) from exc
        asked = set(ids)
        return [i for i in response.items if i.evidence_id in asked]

    # --- analyst (optional: its features become null when it is down) ---------------------------

    async def niches(self, run_id: str) -> list[Niche]:
        if self.analyst is None:
            return []
        try:
            response = await self._tool(
                run_id, "niches", "", self.analyst.niches(),
                lambda r: [n.niche_id for n in r.niches],
            )  # fmt: skip
        except ServiceError as exc:
            log.warning("analyst unavailable: %s", exc.code)
            return []
        return response.niches

    async def gaps(self, run_id: str, niche_id: str) -> list[Gap]:
        if self.analyst is None:
            return []
        try:
            response = await self._tool(
                run_id, "gaps", niche_id, self.analyst.gaps(niche_id),
                lambda r: [g.gap_id for g in r.gaps],
            )  # fmt: skip
        except ServiceError as exc:
            log.warning("analyst unavailable: %s", exc.code)
            return []
        return response.gaps[:MAX_GAPS]

    # --- scoring -------------------------------------------------------------------------------

    def rubric_id(self, dimension: Dimension) -> str:
        return f"{dimension.value}@v{self.rubrics[dimension].version}"

    def bounded(self, evidence: Iterable[EvidenceItem]) -> list[EvidenceItem]:
        """What is sent to the model: few, short items (CPU latency, spec Z4 C4)."""
        unique: dict[str, EvidenceItem] = {}
        for item in evidence:
            unique.setdefault(item.evidence_id, item)
        return [
            item.model_copy(update={"quote": item.quote[: self.settings.max_quote_chars]})
            for item in list(unique.values())[: self.settings.max_evidence]
        ]

    def abstain(
        self, dimension: Dimension, reason: str, features: dict[str, Any]
    ) -> DimensionScore:
        return DimensionScore(
            dimension=dimension,
            score=None,
            confidence=0,
            evidence_ids=[],
            features=features,
            reason=reason,
            rubric_version=self.rubric_id(dimension),
            model=NO_MODEL,
        )

    async def score(
        self,
        request: ScorerRequest,
        dimension: Dimension,
        evidence: Iterable[EvidenceItem],
        features: dict[str, Any],
        facts: str,
        deadline: float,
    ) -> DimensionScore:
        """One dimension from M10. ``facts`` is the code-written summary used in the reason."""
        sent = self.bounded(evidence)
        if not sent:
            return self.abstain(dimension, "no evidence", features)
        rubric = self.rubrics[dimension]
        body = ScoreRequest(
            rubric_id=self.rubric_id(dimension),
            idea=IdeaInput(
                title=request.idea.title, text=request.idea.text[: self.settings.max_idea_chars]
            ),
            evidence=sent,
            features={name: features[name] for name in rubric.features if name in features},
        )
        try:
            async with asyncio.timeout_at(deadline), self._slots:
                reply = await self._call_scorer(request.run_id, body)
        except TimeoutError:
            log.warning("deadline passed before %s was scored", dimension.value)
            reason = f"{FAILED_PREFIX}: validator deadline passed"
            return self.abstain(dimension, reason, features)
        except ServiceError as exc:
            log.warning("scorer call for %s failed: %s", dimension.value, exc.code)
            return self.abstain(dimension, f"{FAILED_PREFIX}: scorer {exc.code}", features)

        if reply.dimension is not dimension or reply.rubric_version != body.rubric_id:
            reason = f"{FAILED_PREFIX}: scorer answered for another rubric"
            return self.abstain(dimension, reason, features)
        if reply.score is None:
            if reply.model == NO_MODEL:
                reason = f"{FAILED_PREFIX}: no scoring model gave a usable reply"
            else:
                reason = "the scoring model cited none of the evidence"
            return self.abstain(dimension, reason, features).model_copy(
                update={"model": reply.model}
            )
        allowed = {item.evidence_id for item in sent}
        ids = [i for i in dict.fromkeys(reply.evidence_ids) if i in allowed]
        if not ids:
            reason = "the scorer cited none of the retrieved evidence"
            return self.abstain(dimension, reason, features)
        return DimensionScore(
            dimension=dimension,
            score=reply.score,
            confidence=reply.confidence,
            evidence_ids=ids,
            features=features,
            reason=f"{dimension.value} {reply.score:g}/5 from {reply.model}. {facts} "
            f"Cites {', '.join(ids)}.",
            rubric_version=body.rubric_id,
            model=reply.model,
        )

    async def _call_scorer(self, run_id: str, body: ScoreRequest) -> DimensionScore:
        with self.tracer.step(
            AGENT, TraceKind.TOOL_CALL, tool="score_dimension",
            input=body.model_dump_json(include={"rubric_id", "features"}), run_id=run_id,
        ) as step:  # fmt: skip
            # no retry: after a timeout the first job still occupies the model, and M10 has its
            # own retry and fallback
            reply: DimensionScore = await self.scorer.request(
                "POST",
                "/score",
                body=body,
                response_model=DimensionScore,
                retry=False,
                timeout=httpx.Timeout(self.settings.scorer_timeout_s, connect=3.0),
            )
            step.output = reply.model_dump_json(include={"score", "confidence", "model"})
            return reply
