"""M8's agents (§12.3): each is a versioned prompt, an output schema and one model call.

Grounding is done by code after the call: only ids the agent was shown survive, and a claim,
objection or idea left without one is dropped (no evidence, no claim). Flagged evidence is never
shown. No agent scores an idea or says a verdict; the schemas have no field for either (VE-1).
"""

import asyncio
import html
import json
import logging
import re
from collections.abc import Iterable, Sequence

from pydantic import BaseModel, Field, ValidationError

from common.contracts.agents import (
    ConcretizeRequest,
    ConcretizeResponse,
    EnrichedIdea,
    GapAnalyzeRequest,
    GapAnalyzeResponse,
    IdeateRequest,
    IdeateResponse,
    IntakeRequest,
    IntakeResponse,
    SkepticRequest,
    SkepticResponse,
)
from common.contracts.base import ContractModel, NonEmptyStr
from common.contracts.core import (
    Claim,
    Dimension,
    EvidenceItem,
    GeneratedIdea,
    Objection,
    TraceKind,
)
from common.contracts.kb import SearchRequest
from common.contracts.tools import ChatMessage
from common.idea_text import shorten
from common.injection import scan_text
from common.llm import LLMClient
from common.metrics import FABRICATED_IDS
from common.registry import Prompt, RegistryError
from common.trace import TraceClient
from ideation.services.kb import KnowledgeBase

log = logging.getLogger("ideation")

MAX_IDEAS = 6
MAX_OBJECTIONS = 10
MAX_CLAIMS = 5
MAX_GAPS = 12
MAX_EVIDENCE = 24
MAX_IDEA_EVIDENCE = 8
MAX_QUOTE_CHARS = 600
MAX_QUERY_CHARS = 2000

# prompt name -> the placeholders it must have, each exactly once or more
PROMPTS: dict[str, frozenset[str]] = {
    "intake": frozenset({"idea", "profile"}),
    "ideator": frozenset({"round", "profile", "niches", "evidence", "objections"}),
    "gap_analyst": frozenset({"ideas", "evidence"}),
    "skeptic": frozenset({"idea", "evidence"}),
    "concretizer": frozenset({"idea", "changes"}),
}
_SLOT = re.compile(r"\{\{(\w+)\}\}")


# --- what a model may send: ids are optional here so that one bad entry does not lose the rest --


class DraftIdea(ContractModel):
    title: str
    text: str
    niche_id: str
    target_user: str
    gap_evidence_ids: list[str] = Field(default_factory=list)
    rationale: str


class IdeaDrafts(ContractModel):
    ideas: list[DraftIdea]


class DraftClaim(ContractModel):
    claim_id: str = ""
    text: NonEmptyStr
    evidence_ids: list[str] = Field(default_factory=list)


class DraftEnrichment(ContractModel):
    idea_index: int
    complaints: list[DraftClaim] = Field(default_factory=list)
    missing_features: list[DraftClaim] = Field(default_factory=list)


class EnrichmentDrafts(ContractModel):
    ideas: list[DraftEnrichment]


class DraftObjection(ContractModel):
    objection_id: str = ""
    dimension: Dimension
    text: NonEmptyStr
    evidence_ids: list[str] = Field(default_factory=list)
    fatal: bool = False


class ObjectionDrafts(ContractModel):
    objections: list[DraftObjection]


# --- prompt text -------------------------------------------------------------------------------


def _text(value: str) -> str:
    """Inserted text can neither close a block nor open one."""
    return html.escape(value, quote=False)


def _json(value: object) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", by_alias=True)
    return _text(json.dumps(value, ensure_ascii=False))


def clean(items: Iterable[EvidenceItem], limit: int) -> list[EvidenceItem]:
    """Unique, unflagged items (M12-2, ETH-5)."""
    unique: dict[str, EvidenceItem] = {}
    for item in items:
        if not scan_text(item.quote, item.source):
            unique.setdefault(item.evidence_id, item)
    return list(unique.values())[:limit]


def blocks(items: Sequence[EvidenceItem]) -> str:
    return "\n".join(
        f'<evidence id="{i.evidence_id}" source="{html.escape(i.source[:80])}">'
        f"{_text(i.quote[:MAX_QUOTE_CHARS])}</evidence>"
        for i in items
    )


def grounded(cited: Sequence[str], shown: set[str]) -> list[str]:
    """The cited ids that were shown; the rest are made up (§12.2)."""
    unique = list(dict.fromkeys(cited))
    kept = [i for i in unique if i in shown]
    if len(kept) < len(unique):
        FABRICATED_IDS.inc(len(unique) - len(kept))
    return kept


class Agents:
    def __init__(
        self,
        prompts: dict[str, Prompt],
        llm: LLMClient,
        kb: KnowledgeBase,
        tracer: TraceClient,
        *,
        search_k: int = 8,
        llm_timeout_s: float = 50.0,
    ) -> None:
        """Raises ``RegistryError`` when a prompt is missing or has other placeholders."""
        for name, slots in PROMPTS.items():
            if name not in prompts:
                raise RegistryError(f"missing prompt: {name}")
            if set(_SLOT.findall(prompts[name].body)) != slots:
                raise RegistryError(f"{prompts[name].id}: placeholders must be {sorted(slots)}")
        self.prompts = prompts
        self.llm = llm
        self.kb = kb
        self.tracer = tracer
        self.search_k = search_k
        self.llm_timeout_s = llm_timeout_s

    async def _ask[T: ContractModel](
        self,
        agent: str,
        prompt: str,
        schema: type[T],
        values: dict[str, str],
        run_id: str,
        *,
        alias: str = "agent-default",
        temperature: float = 0.0,
        max_tokens: int = 1500,
    ) -> T:
        found = self.prompts[prompt]
        # one pass: a "{{evidence}}" inside inserted text stays literal
        content = _SLOT.sub(lambda m: values[m.group(1)], found.body)
        async with asyncio.timeout(self.llm_timeout_s):
            result = await self.llm.chat(
                alias,
                [ChatMessage(role="user", content=content)],
                schema,
                agent=agent,
                run_id=run_id,
                max_tokens=max_tokens,
                temperature=temperature,
                prompt_version=found.id,
            )
        assert result.data is not None  # chat() raises when the schema is not met
        return result.data

    # --- the five agents -----------------------------------------------------------------------

    async def intake(self, request: IntakeRequest) -> IntakeResponse:
        values = {
            "idea": _text(f"{request.idea.title}\n{request.idea.text}"),
            "profile": _json(request.profile.model_dump(mode="json", exclude={"user_id"})),
        }
        return await self._ask(
            "intake", "intake", IntakeResponse, values, request.run_id, alias="agent-cheap"
        )

    async def ideate(self, request: IdeateRequest) -> IdeateResponse:
        """Ideas that cite gap evidence they were shown; the others are not returned (M8-3)."""
        parts: list[str] = []
        niche_of: dict[str, str] = {}
        for gap in request.gaps[:MAX_GAPS]:
            items = clean(
                (i for i in gap.evidence if i.evidence_id not in niche_of),
                MAX_EVIDENCE - len(niche_of),
            )
            if items:
                niche_of |= {i.evidence_id: gap.niche_id for i in items}
                parts.append(
                    f"Gap in niche {_text(gap.niche_id)}: {_text(gap.statement)}\n{blocks(items)}"
                )
        if not parts:  # no evidence, no idea; nothing to ask the model
            return IdeateResponse(ideas=[])
        values = {
            "round": str(request.round),
            # the model needs what the student can do, not who the student is
            "profile": _json(request.profile.model_dump(mode="json", exclude={"user_id"})),
            "niches": _json([n.model_dump(mode="json") for n in request.niches]),
            "evidence": "\n".join(parts),
            "objections": _json(
                [{"dimension": o.dimension, "text": o.text} for o in request.prior_objections[:20]]
            ),
        }
        drafts = await self._ask(
            "ideator", "ideator", IdeaDrafts, values, request.run_id,
            temperature=0.7, max_tokens=3000,
        )  # fmt: skip
        known_niches = set(niche_of.values())
        ideas: list[GeneratedIdea] = []
        for draft in drafts.ideas:
            ids = grounded(draft.gap_evidence_ids, set(niche_of))
            if not ids:
                continue
            niche = draft.niche_id if draft.niche_id in known_niches else niche_of[ids[0]]
            try:
                ideas.append(
                    GeneratedIdea(
                        title=draft.title.strip()[:200],
                        text=shorten(draft.text.strip(), 5000),
                        niche_id=niche,
                        target_user=draft.target_user.strip()[:200],
                        gap_evidence_ids=ids,
                        rationale=draft.rationale.strip()[:1000],
                    )
                )
            except ValidationError:
                log.warning("an idea with an empty field was dropped")
        return IdeateResponse(ideas=ideas[:MAX_IDEAS])

    async def _idea_evidence(self, idea: GeneratedIdea, run_id: str) -> list[EvidenceItem]:
        """The idea's gap evidence and what M6 finds for the idea. Raises ``ServiceError``."""
        cited = await self.kb.evidence(idea.gap_evidence_ids)
        query = shorten(f"{idea.title}\n{idea.text}", MAX_QUERY_CHARS)
        search = SearchRequest(query=query, k=self.search_k)
        with self.tracer.step("gap_analyst", TraceKind.TOOL_CALL, tool="search_kb",
                              input=search.model_dump_json(), run_id=run_id) as step:  # fmt: skip
            found = await self.kb.search(search)
            step.output = json.dumps([r.evidence_id for r in found.results])
        results = (
            EvidenceItem(**r.model_dump(exclude={"score", "card_name"})) for r in found.results
        )
        return clean([*cited.items, *results], MAX_IDEA_EVIDENCE)

    async def gap_analyze(self, request: GapAnalyzeRequest) -> GapAnalyzeResponse:
        """Complaints and missing features per idea, from the knowledge base only (WF-D4)."""
        ideas = request.ideas[:MAX_IDEAS]
        if not ideas:
            return GapAnalyzeResponse(ideas=[])
        evidence = await asyncio.gather(
            *(self._idea_evidence(idea, request.run_id) for idea in ideas)
        )
        shown = {i.evidence_id: i for items in evidence for i in items}
        enriched = [EnrichedIdea(idea=idea) for idea in ideas]
        if not shown:
            return GapAnalyzeResponse(ideas=enriched)
        values = {
            "ideas": _json(
                [
                    {
                        "idea_index": n,
                        "title": idea.title,
                        "text": idea.text,
                        "evidence_ids": [i.evidence_id for i in evidence[n]],
                    }
                    for n, idea in enumerate(ideas)
                ]
            ),
            "evidence": blocks(list(shown.values())),
        }
        drafts = await self._ask(
            "gap_analyst", "gap_analyst", EnrichmentDrafts, values, request.run_id,
            max_tokens=2500,
        )  # fmt: skip
        done: set[int] = set()
        for draft in drafts.ideas:
            n = draft.idea_index
            if not 0 <= n < len(ideas) or n in done:
                continue
            done.add(n)
            own = {i.evidence_id for i in evidence[n]}

            def claims(
                drafted: list[DraftClaim], kind: str, n: int = n, own: set[str] = own
            ) -> list[Claim]:
                kept = [(c.text, ids) for c in drafted if (ids := grounded(c.evidence_ids, own))][
                    :MAX_CLAIMS
                ]
                return [
                    Claim(claim_id=f"{kind}_{n}_{m}", text=text[:500], evidence_ids=ids)
                    for m, (text, ids) in enumerate(kept)
                ]

            enriched[n] = EnrichedIdea(
                idea=ideas[n],
                complaints=claims(draft.complaints, "complaint"),
                missing_features=claims(draft.missing_features, "missing"),
            )
        return GapAnalyzeResponse(ideas=enriched)

    async def skeptic(self, request: SkepticRequest) -> SkepticResponse:
        """Evidence-backed reasons the idea would fail. M12 checks them before they count."""
        evidence = clean(request.evidence, MAX_EVIDENCE)
        if not evidence:
            return SkepticResponse(objections=[])
        values = {
            "idea": _text(f"{request.idea.title}\n{request.idea.text}"),
            "evidence": blocks(evidence),
        }
        drafts = await self._ask("skeptic", "skeptic", ObjectionDrafts, values, request.run_id)
        shown = {i.evidence_id for i in evidence}
        kept = [(o, ids) for o in drafts.objections if (ids := grounded(o.evidence_ids, shown))]
        return SkepticResponse(
            objections=[
                Objection(
                    objection_id=f"obj_{n + 1}",
                    idea_id=request.idea_id,
                    dimension=o.dimension,
                    text=o.text[:1000],
                    evidence_ids=ids,
                    fatal=o.fatal,
                )
                for n, (o, ids) in enumerate(kept[:MAX_OBJECTIONS])
            ]
        )

    async def concretize(self, request: ConcretizeRequest) -> ConcretizeResponse:
        """Words for the engine's candidates; one for each dimension that was asked for."""
        if not request.changes:
            return ConcretizeResponse(changes=[])
        values = {
            "idea": _text(f"{request.idea.title}\n{request.idea.text}"),
            "changes": _json([c.model_dump(mode="json") for c in request.changes]),
        }
        reply = await self._ask(
            "concretizer", "concretizer", ConcretizeResponse, values, request.run_id,
            alias="agent-cheap",
        )  # fmt: skip
        left = {c.dimension for c in request.changes}
        changes = []
        for change in reply.changes:
            if change.dimension in left:
                left.remove(change.dimension)
                changes.append(change)
        return ConcretizeResponse(changes=changes)
