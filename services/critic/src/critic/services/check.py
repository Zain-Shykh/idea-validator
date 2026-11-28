"""``/check`` (M12-1, §12.5): code first, then the model judge. The first rule that applies wins;
only a claim that passes every code rule reaches the model, and only the model can say
``supported``. No verdict on an idea is decided or changed here (VE-1)."""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Sequence

from common.contracts.agents import CheckRequest, CheckResponse, ClaimResult
from common.contracts.core import Claim, EvidenceItem, TraceKind
from common.http import ServiceError
from common.injection import scan_text
from common.metrics import FABRICATED_IDS
from common.trace import TraceClient
from critic.services import audit
from critic.services.judge import Judge, JudgeUnavailableError, Verdict
from critic.services.kb import KnowledgeBase
from critic.services.names import CARD_ID, missing_names, names_in, normalise

log = logging.getLogger("critic")

MAX_IDS_PER_CLAIM = 20
MAX_CLAIM_CHARS = 2000
MAX_SHOWN = 5
UNAVAILABLE_PREFIX = "claim check unavailable"


class KbUnavailableError(Exception):
    """M6 failed. That is not the same as finding that a claim is unsupported."""


def _result(claim: Claim, verdict: Verdict, reason: str) -> ClaimResult:
    return ClaimResult(
        claim_id=claim.claim_id,
        supported=verdict == "supported",
        verdict=verdict,
        reason=reason,
    )


def _precheck(claim: Claim) -> str | None:
    """Why the claim is unsupported before anything is looked up."""
    if not claim.evidence_ids:
        return "the claim cites no evidence"
    if len(set(claim.evidence_ids)) > MAX_IDS_PER_CLAIM:
        return f"the claim cites more than {MAX_IDS_PER_CLAIM} evidence ids"
    if len(claim.text) > MAX_CLAIM_CHARS:
        return f"the claim is longer than {MAX_CLAIM_CHARS} characters"
    return None


def lexical_overlap(claim: str, quotes: Sequence[str]) -> float:
    """Share of the claim's words found in the quotes. Reported only: it never decides."""
    words = set(normalise(claim).split())
    seen = set(normalise(" ".join(quotes)).split())
    return len(words & seen) / len(words) if words else 0.0


class Checker:
    def __init__(
        self, kb: KnowledgeBase, judge: Judge, tracer: TraceClient, deadline_s: float
    ) -> None:
        self.kb = kb
        self.judge = judge
        self.tracer = tracer
        self.deadline_s = deadline_s

    async def check(self, request: CheckRequest, run_id: str) -> CheckResponse:
        deadline = asyncio.get_running_loop().time() + self.deadline_s
        early: list[ClaimResult | None] = []
        blocked = 0
        for claim in request.claims:
            reason = _precheck(claim)
            if reason is None and scan_text(claim.text):
                blocked += 1
                reason = "the claim text looks like an instruction to a model"
            early.append(None if reason is None else _result(claim, "unsupported", reason))

        wanted = list(
            dict.fromkeys(
                i
                for claim, done in zip(request.claims, early, strict=True)
                if done is None
                for i in claim.evidence_ids
            )
        )
        known = await self._evidence(run_id, wanted)
        flagged = {i for i, item in known.items() if scan_text(item.quote, item.source)}
        blocked += len(flagged)

        results: list[ClaimResult] = []
        outage: str | None = None
        cards: dict[str, str | None] = {}
        for claim, done in zip(request.claims, early, strict=True):
            if done is None:
                done, outage = await self._claim(
                    claim, known, flagged, cards, request.strict, run_id, deadline, outage
                )
            results.append(done)

        audit.injections_blocked(self.tracer, run_id, blocked)
        audit.decision(
            self.tracer,
            run_id,
            "check",
            {
                "strict": request.strict,
                "results": [[audit.safe(r.claim_id), r.verdict] for r in results],
                "flagged_evidence": sorted(flagged),
            },
        )
        return CheckResponse(results=results)

    async def _claim(
        self,
        claim: Claim,
        known: dict[str, EvidenceItem],
        flagged: set[str],
        cards: dict[str, str | None],
        strict: bool,
        run_id: str,
        deadline: float,
        outage: str | None,
    ) -> tuple[ClaimResult, str | None]:
        """The result and, once the model has failed for good, why."""
        cited = list(dict.fromkeys(claim.evidence_ids))
        unknown = [i for i in cited if i not in known]
        if unknown:
            FABRICATED_IDS.inc(len(unknown))
            listed = ", ".join(unknown[:MAX_SHOWN])
            reason = f"cites evidence that does not exist: {listed}"
            return _result(claim, "unsupported", reason), outage

        clean = [known[i] for i in cited if i not in flagged]
        withheld = len(cited) - len(clean)
        if not clean or (strict and withheld):
            reason = (
                f"{withheld} of {len(cited)} cited passages look like instructions to a model "
                "and were withheld"
            )
            return _result(claim, "unverifiable", reason), outage

        card_ids = {item.card_id for item in clean}
        uncited = [c for c in dict.fromkeys(CARD_ID.findall(claim.text)) if c not in card_ids]
        if uncited:
            reason = f"names cards it does not cite: {', '.join(uncited[:MAX_SHOWN])}"
            return _result(claim, "unsupported", reason), outage

        names = missing_names(
            names_in(CARD_ID.sub(" ", claim.text)),
            [text for item in clean for text in (item.quote, item.source)],
        )
        if names:
            names = missing_names(names, await self._card_names(run_id, sorted(card_ids), cards))
        if names:
            listed = ", ".join(n[:80] for n in names[:MAX_SHOWN])
            reason = f"the cited evidence does not mention: {listed}"
            return _result(claim, "unsupported", reason), outage

        ids = ", ".join(item.evidence_id for item in clean)
        if outage is None:
            try:
                verdict = await self.judge.judge(
                    claim.text, clean, strict=strict, run_id=run_id, deadline=deadline
                )
            except JudgeUnavailableError as exc:
                why = exc.why
                if exc.outage:
                    outage = why
            else:
                said = {
                    "supported": "the cited evidence supports the claim",
                    "unsupported": "the cited evidence does not support the claim",
                    "unverifiable": "the cited evidence is not enough to decide",
                }[verdict]
                return _result(claim, verdict, f"{said} (checked: {ids})"), outage
        else:
            why = outage
        share = lexical_overlap(claim.text, [item.quote for item in clean])
        reason = (
            f"{UNAVAILABLE_PREFIX}: {why}; {share:.0%} of the claim's words appear in the "
            f"cited evidence ({ids})"
        )
        return _result(claim, "unverifiable", reason), outage

    # --- tools ---------------------------------------------------------------------------------

    async def _tool[T](
        self,
        run_id: str,
        tool: str,
        ids: Sequence[str],
        call: Awaitable[T],
        summary: Callable[[T], object],
    ) -> T:
        try:
            with self.tracer.step(
                audit.AGENT, TraceKind.TOOL_CALL, tool=tool, input=json.dumps(list(ids)),
                run_id=run_id,
            ) as step:  # fmt: skip
                result = await call
                step.output = json.dumps(summary(result))
                return result
        except ServiceError as exc:
            log.warning("knowledge base call %s failed: %s", tool, exc.code)
            raise KbUnavailableError(exc.code) from exc

    async def _evidence(self, run_id: str, ids: list[str]) -> dict[str, EvidenceItem]:
        """The asked ids that M6 knows. Anything else it returns is ignored."""
        if not ids:
            return {}
        response = await self._tool(
            run_id, "kb_evidence", ids, self.kb.evidence(ids),
            lambda r: [i.evidence_id for i in r.items],
        )  # fmt: skip
        asked = set(ids) - set(response.missing)
        return {i.evidence_id: i for i in response.items if i.evidence_id in asked}

    async def _card_names(
        self, run_id: str, ids: list[str], cache: dict[str, str | None]
    ) -> list[str]:
        new = [i for i in ids if i not in cache]
        if new:
            found = await self._tool(
                run_id, "get_cards", new, asyncio.gather(*(self.kb.card(i) for i in new)),
                lambda cards: [c.id for c in cards if c is not None],
            )  # fmt: skip
            for card_id, card in zip(new, found, strict=True):
                cache[card_id] = card.name if card is not None and card.id == card_id else None
        return [name for i in ids if (name := cache[i]) is not None]
