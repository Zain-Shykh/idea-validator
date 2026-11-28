from scorer_factories import ROOT

from common.contracts.agents import ScoreRequest
from common.contracts.core import IdeaInput
from common.idea_text import CHANGES, append
from common.registry import load_prompts, load_rubrics
from scorer.services.prompt import MAX_IDEA_CHARS, build_messages


def test_an_appended_change_survives_the_prompt_bound() -> None:
    _, rubrics = load_rubrics(ROOT / "rubrics")
    rubric = next(iter(rubrics.values()))
    text = append("<" * 2990, CHANGES, ["serve nursing students only"], 3000)
    request = ScoreRequest(
        rubric_id=f"{rubric.dimension.value}@v{rubric.version}",
        idea=IdeaInput(title="t" * 200, text=text),
        evidence=[],
    )
    user = build_messages(load_prompts(ROOT / "prompts")["scorer"], rubric, request, [])[1].content
    idea = user.split("<idea>\n")[1].split("\n</idea>")[0]
    assert len(idea) <= MAX_IDEA_CHARS and "serve nursing students only" in idea
