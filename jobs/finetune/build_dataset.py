"""Step 1 of the fine-tuning data (FT-2, architecture §14.1).

``sample``    turns knowledge-base cards (and generated ideas) into unlabelled prompts.
``assemble``  picks rows from the drafted ones, spread over dimensions and score levels.

The prompts are not rebuilt here: M9's scorers run against the knowledge base with a stand-in for
M10 that records each request, and M10's prompt code renders it. So the model is trained on the
evidence, features and text it is served.
"""

import argparse
import asyncio
import hashlib
import io
import json
import logging
import math
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, cast

from common.contracts.agents import ScoreRequest, ScorerRequest
from common.contracts.core import (
    Dimension,
    DimensionScore,
    IdeaCard,
    IdeaInput,
    StudentProfile,
)
from common.contracts.kb import EvidenceResponse, SearchRequest, SearchResponse
from common.fakes.kb import FakeKB
from common.http import ServiceClient, ServiceError
from common.idea_rules import is_complaint
from common.registry import load_prompts, load_rubrics
from common.trace import TraceClient
from dataset import (
    DATA_DIR,
    LABELLED,
    ROOT,
    Candidate,
    DataError,
    Draft,
    Item,
    LabelledIdea,
    guard_output,
    is_labelled,
    item_id,
    keyed,
    load_labelled,
    make_example,
    prompt_ids,
    read_checks,
    read_jsonl,
    stable_order,
    write_jsonl,
    write_text,
)
from scorer.services.prompt import build_messages, select_evidence
from validators.scorers import SCORERS
from validators.services.effort import EffortEstimator
from validators.services.engine import Engine, KbUnavailableError
from validators.services.tools import Analyst, HttpAnalyst, HttpKnowledgeBase, KnowledgeBase
from validators.settings import ValidatorSettings

log = logging.getLogger("finetune")

# M6 has no "list cards" endpoint: cards are found by searching for these topics
TOPICS = (
    "study tool for students", "flashcards and revision", "lecture notes", "course planning",
    "developer tools for code review", "testing and debugging", "deployment and devops",
    "API integration", "database management", "open source maintenance", "health tracking",
    "fitness and workouts", "mental health", "medical symptoms", "nutrition and recipes",
    "personal finance and budgeting", "small business invoicing", "online shop and payments",
    "marketing and social media", "job search and resumes", "hiring and interviews",
    "team collaboration chat", "project management tasks", "meeting notes and calendar",
    "AI assistant with a language model", "document search", "data analysis dashboard",
    "web scraping data", "browser extension", "mobile app", "travel planning",
    "food delivery and restaurants", "housing and rent", "transport and commuting",
    "accessibility for disabled users", "language learning", "music and video",
    "games and multiplayer", "smart home sensors", "security and privacy",
)  # fmt: skip

# feasibility and learning depend on the student, so ideas take turns over these
PROFILES = tuple(
    StudentProfile(
        user_id=f"ft_{n}", skills=skills, hours_per_week=hours, weeks_available=weeks,
        team_size=team, compute=compute, goal=goal, interests=interests,
    )
    for n, (skills, hours, weeks, team, compute, goal, interests) in enumerate(
        (
            (["Python", "React"], 15, 16, 2, "laptop", "course_project", ["education"]),
            (["Java"], 5, 8, 1, "laptop", "portfolio", ["games"]),
            (["Python", "machine learning", "databases"], 25, 20, 2, "free_gpu", "startup",
             ["health", "machine learning"]),
            (["JavaScript", "TypeScript", "web development"], 10, 12, 3, "laptop", "portfolio",
             ["developer tools", "productivity"]),
        )
    )
)  # fmt: skip


@dataclass(frozen=True)
class Idea:
    origin: str
    title: str
    text: str
    hidden_card: str | None = None

    @property
    def id(self) -> str:
        return "idea_" + hashlib.sha256(f"{self.title}\n{self.text}".encode()).hexdigest()[:12]


def from_card(card: IdeaCard) -> Idea:
    lines = [card.summary]
    lines += [f"Problem: {card.problem}"] if card.problem else []
    lines += [f"For: {card.customer}"] if card.customer else []
    return Idea("card", card.name[:200], "\n".join(lines)[:5000], hidden_card=card.id)


def load_ideas(path: Path) -> list[Idea]:
    """Generated ideas: ``{"title", "text"}`` per line, or M8's ``GeneratedIdea`` shape
    (``title``, ``pitch``, ``problem``, ``target_user``)."""
    ideas = []
    for number, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
            text = data.get("text") or "\n".join(
                str(data[k]) for k in ("pitch", "problem", "target_user") if data.get(k)
            )
            checked = IdeaInput(title=data["title"], text=text)
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise DataError(f"{path}:{number}: needs a title and a text or pitch") from exc
        ideas.append(Idea("generated", checked.title, checked.text))
    return ideas


class HiddenCardKB:
    """The knowledge base without one card: an idea made from a card must not cite itself."""

    def __init__(self, kb: KnowledgeBase, hidden: str | None) -> None:
        self.kb = kb
        self.hidden = hidden
        self.index_versions: set[str] = set()

    async def search(self, request: SearchRequest) -> SearchResponse:
        wider = request.model_copy(update={"k": min(50, request.k + 5)})
        found = await self.kb.search(wider)
        self.index_versions.add(found.index_version)
        results = [r for r in found.results if r.card_id != self.hidden][: request.k]
        return SearchResponse(results=results, index_version=found.index_version)

    async def card(self, card_id: str) -> IdeaCard | None:
        return None if card_id == self.hidden else await self.kb.card(card_id)

    async def evidence(self, ids: Sequence[str]) -> EvidenceResponse:
        return await self.kb.evidence([i for i in ids if i.partition("#")[0] != self.hidden])


class RecordingScorer:
    """Stands where M10 stands in M9: keeps each request and scores nothing."""

    def __init__(self) -> None:
        self.requests: list[ScoreRequest] = []

    async def request(
        self, method: str, path: str, *, body: ScoreRequest, **_: Any
    ) -> DimensionScore:
        self.requests.append(body)
        return DimensionScore(
            dimension=Dimension(body.rubric_id.partition("@")[0]),
            score=None,
            confidence=0,
            evidence_ids=[],
            reason="recorded for the fine-tuning dataset",
            rubric_version=body.rubric_id,
            model="none",
        )


async def find_cards(kb: KnowledgeBase, topics: Sequence[str]) -> list[IdeaCard]:
    ids: dict[str, None] = {}
    for topic in topics:
        found = await kb.search(SearchRequest(query=topic, k=50))
        ids.update(dict.fromkeys(r.card_id for r in found.results))
    cards = [await kb.card(card_id) for card_id in sorted(ids, key=stable_order)]
    return [c for c in cards if c is not None]


async def sample(
    kb: KnowledgeBase,
    *,
    analyst: Analyst | None,
    topics: Sequence[str] = TOPICS,
    generated: Sequence[Idea] = (),
    labelled: Sequence[LabelledIdea] = (),
    max_ideas: int = 100,
    fake: bool,
    today: date,
) -> list[Candidate]:
    """``max_ideas`` bounds the ideas made from cards; every generated idea is used."""
    _, rubrics = load_rubrics(ROOT / "rubrics")
    prompts = load_prompts(ROOT / "prompts")
    from_cards = [from_card(c) for c in await find_cards(kb, topics) if not is_complaint(c)]
    pool = {
        idea.id: idea
        for idea in [*generated, *from_cards]
        if not is_labelled(idea.title, idea.text, labelled)
    }
    chosen = [i for i in pool.values() if i.origin == "generated"]
    chosen += [i for i in pool.values() if i.origin == "card"][:max_ideas]
    if not chosen:
        raise DataError("no ideas: the knowledge base returned no product cards for the topics")

    candidates: dict[str, Candidate] = {}
    for index, idea in enumerate(sorted(chosen, key=lambda i: stable_order(i.id))):
        recorder = RecordingScorer()
        hidden = HiddenCardKB(kb, idea.hidden_card)
        engine = Engine(
            ValidatorSettings(),
            kb=hidden,
            analyst=analyst,
            scorer=cast(ServiceClient, recorder),
            # no model: M9's band midpoint, so sampling costs no model quota
            effort=EffortEstimator(prompts["effort"], None, 1.0),
            rubrics=rubrics,
            tracer=TraceClient(stream=io.StringIO()),
            today=lambda: today,
        )
        request = ScorerRequest(
            run_id=f"run_ft_{idea.id}",
            idea=IdeaInput(title=idea.title, text=idea.text),
            profile=PROFILES[index % len(PROFILES)],
            evidence=[],
        )
        try:
            for run in SCORERS.values():
                await run(engine, request)
        except KbUnavailableError as exc:
            raise DataError(f"knowledge base unavailable: {exc}") from exc
        for sent in recorder.requests:
            dimension = Dimension(sent.rubric_id.partition("@")[0])
            evidence = select_evidence(sent.evidence)
            system, user = (
                m.content
                for m in build_messages(prompts["scorer"], rubrics[dimension], sent, evidence)
            )
            key = item_id(system, user)
            candidates[key] = Candidate(
                item_id=key,
                idea_id=idea.id,
                origin="card" if idea.origin == "card" else "generated",
                idea_title=idea.title,
                idea_text=idea.text,
                dimension=dimension,
                rubric_id=sent.rubric_id,
                evidence_ids=prompt_ids(user),
                system=system,
                user=user,
                kb_version=",".join(sorted(hidden.index_versions)) or "unknown",
                built_on=today,
                fake=fake,
            )
    return sorted(candidates.values(), key=lambda c: c.item_id)


# --- assemble ------------------------------------------------------------------------------------


def level(score: float) -> int:
    """Rounded down: level 5 means a real 5, not a 4.5."""
    return math.floor(score)


def select(
    usable: dict[str, tuple[Candidate, Draft]], target: int, pinned: Sequence[str] = ()
) -> list[str]:
    """Round-robin over (dimension, level) cells, so rare cells are filled first. ``pinned``
    (hand-checked rows) are always kept."""
    chosen = dict.fromkeys(i for i in pinned if i in usable)
    cells: dict[tuple[str, int], list[str]] = {}
    for key in sorted(usable, key=stable_order):
        candidate, draft = usable[key]
        assert draft.label is not None
        if key not in chosen:
            cells.setdefault((candidate.dimension.value, level(draft.label.score)), []).append(key)
    queues = [cells[cell] for cell in sorted(cells)]
    depth = 0
    while len(chosen) < target and any(depth < len(q) for q in queues):
        for queue in queues:
            if depth < len(queue) and len(chosen) < target:
                chosen[queue[depth]] = None
        depth += 1
    return sorted(chosen, key=stable_order)


def assemble(
    candidates_path: Path,
    drafts_path: Path,
    out: Path,
    *,
    labelled: Sequence[LabelledIdea],
    target: int,
    min_rows: int,
) -> tuple[bool, dict[str, Any]]:
    candidates = keyed(read_jsonl(candidates_path, Candidate), candidates_path)
    drafts = keyed(read_jsonl(drafts_path, Draft), drafts_path)
    usable = {
        key: (candidate, drafts[key])
        for key, candidate in candidates.items()
        if key in drafts
        and drafts[key].label is not None
        and not is_labelled(candidate.idea_title, candidate.idea_text, labelled)
    }
    checks = read_checks(out / "checked.csv")
    if lost := sorted(set(checks) - set(usable)):
        raise DataError(
            f"{len(lost)} hand-checked rows are not usable any more (e.g. {lost[0]}): use the "
            "candidates and drafts they came from, or remove their ideas from checked.csv"
        )
    keys = select(usable, target, pinned=list(checks))
    fake = any(usable[k][0].fake or usable[k][1].fake for k in keys)
    guard_output(out / "all.jsonl", fake)

    rows, items = [], []
    for key in keys:
        candidate, draft = usable[key]
        assert draft.label is not None
        rows.append(make_example(candidate.system, candidate.user, draft.label))
        items.append(
            Item(
                **candidate.model_dump(include=set(Item.model_fields) - {"teacher_model", "fake"}),
                teacher_model=draft.model,
                fake=candidate.fake or draft.fake,
            )
        )
    by_dimension = Counter(i.dimension.value for i in items)
    by_level = Counter(level(usable[k][1].label.score) for k in keys)  # type: ignore[union-attr]
    report: dict[str, Any] = {
        "rows": len(rows),
        "ideas": len({i.idea_id for i in items}),
        "candidates": len(candidates),
        "drafts": len(drafts),
        "usable": len(usable),
        "by_dimension": {d.value: by_dimension[d.value] for d in Dimension},
        "by_level": {str(n): by_level[n] for n in range(6)},
        "by_origin": dict(sorted(Counter(i.origin for i in items).items())),
        "teacher_models": sorted({i.teacher_model for i in items}),
        "fake": fake,
    }
    problems = [f"{len(rows)} rows, need {min_rows}"] if len(rows) < min_rows else []
    problems += [f"no row for {d}" for d, n in report["by_dimension"].items() if not n]
    problems += [
        f"no row at score level {n}" for n, count in report["by_level"].items() if not count
    ]
    report["problems"] = problems
    guard_output(out / "report.json", fake)
    write_text(out / "report.json", json.dumps(report, indent=2) + "\n")
    # an incomplete set is not written: it must not be mistaken for the dataset
    if not problems:
        write_jsonl(out / "all.jsonl", rows)
        write_jsonl(out / "items.jsonl", items)
    return not problems, report


# --- command line --------------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    first = commands.add_parser("sample", help="build unlabelled prompts from the knowledge base")
    source = first.add_mutually_exclusive_group(required=True)
    source.add_argument("--kb-url", help="the real knowledge base (M6)")
    source.add_argument("--fake-cards", type=Path, nargs="+", help="FakeKB, for trying it out")
    first.add_argument("--analyst-url", help="M7; without it trend and gap features are null")
    first.add_argument("--ideas", type=Path, help="generated ideas, one JSON object per line")
    first.add_argument("--topics", type=Path, help="search topics, one per line")
    first.add_argument("--max-ideas", type=int, default=100, help="ideas made from cards")
    first.add_argument("--labelled", type=Path, default=LABELLED)
    first.add_argument("--out", type=Path, default=DATA_DIR / "candidates.jsonl")
    first.add_argument("--force", action="store_true", help="replace an existing candidates file")
    second = commands.add_parser("assemble", help="pick the dataset from drafted candidates")
    second.add_argument("--candidates", type=Path, default=DATA_DIR / "candidates.jsonl")
    second.add_argument("--drafts", type=Path, default=DATA_DIR / "drafts.jsonl")
    second.add_argument("--labelled", type=Path, default=LABELLED)
    second.add_argument("--out", type=Path, default=DATA_DIR, help="folder")
    second.add_argument("--target", type=int, default=360)
    second.add_argument("--min-rows", type=int, default=300)
    return parser


async def _sample(args: argparse.Namespace) -> None:
    fake = args.fake_cards is not None
    guard_output(args.out, fake)
    if args.out.exists() and not args.force:
        # prompts hold dates and search results: a new sample would orphan existing drafts
        raise DataError(f"{args.out} exists; --force replaces it (drafts of old rows are lost)")
    kb: KnowledgeBase = (
        FakeKB.from_jsonl(*args.fake_cards) if fake else HttpKnowledgeBase(args.kb_url, 30.0)
    )
    labelled = load_labelled(args.labelled)
    if not labelled:
        log.warning("%s is missing or empty: no labelled-set idea can be excluded", args.labelled)
    topics = (
        [t for t in args.topics.read_text(encoding="utf-8").splitlines() if t.strip()]
        if args.topics
        else TOPICS
    )
    try:
        candidates = await sample(
            kb,
            analyst=HttpAnalyst(args.analyst_url, 30.0) if args.analyst_url else None,
            topics=topics,
            generated=load_ideas(args.ideas) if args.ideas else (),
            labelled=labelled,
            max_ideas=args.max_ideas,
            fake=fake,
            today=date.today(),
        )
    except ServiceError as exc:
        raise DataError(f"knowledge base: {exc.summary}") from exc
    write_jsonl(args.out, candidates)
    counts = Counter(c.dimension.value for c in candidates)
    print(
        f"{len(candidates)} candidates from {len({c.idea_id for c in candidates})} ideas: "
        + ", ".join(f"{d.value}={counts[d.value]}" for d in Dimension)
    )


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    args = _parser().parse_args(argv)
    try:
        if args.command == "sample":
            asyncio.run(_sample(args))
            return 0
        passed, report = assemble(
            args.candidates,
            args.drafts,
            args.out,
            labelled=load_labelled(args.labelled),
            target=args.target,
            min_rows=args.min_rows,
        )
        print(json.dumps(report, indent=2))
        return 0 if passed else 1
    except DataError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
