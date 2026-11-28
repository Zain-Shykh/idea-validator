from typing import Any

import pytest

from common.cache_keys import retrieval_key, step_key, verdict_key
from common.contracts.kb import SearchFilters

BASE: dict[str, Any] = {
    "idea_text": "A study planner",
    "user_id": "u_1",
    "profile_version": 1,
    "rubric_set": "r1",
    "engine_version": "m11@v1",
    "weights_version": "w1",
    "kb_version": "ing_1",
    "prompt_versions": {"skeptic": "skeptic@v1"},
    "scorer_model": "scorer-base@v1",
}


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("idea_text", "A meal planner"),
        ("user_id", "u_2"),
        ("profile_version", 2),
        ("rubric_set", "r2"),
        ("engine_version", "m11@v2"),
        ("weights_version", "w2"),
        ("kb_version", "ing_2"),
        ("prompt_versions", {"skeptic": "skeptic@v2"}),
        ("scorer_model", "scorer-lora@v1"),
    ],
)
def test_verdict_key_changes_with_each_input(name: str, value: object) -> None:
    assert verdict_key(**{**BASE, name: value}) != verdict_key(**BASE)


def test_verdict_key_is_stable_and_ignores_whitespace() -> None:
    assert verdict_key(**BASE) == verdict_key(**{**BASE, "idea_text": "  A study\n planner "})
    assert verdict_key(**BASE).startswith("cache:verdict:")


def test_verdict_key_requires_every_version() -> None:
    with pytest.raises(TypeError):
        verdict_key(**{k: v for k, v in BASE.items() if k != "kb_version"})


def test_step_key_ignores_dict_order_and_rejects_unstable_values() -> None:
    assert step_key("score", {"a": 1, "b": 2}) == step_key("score", {"b": 2, "a": 1})
    assert step_key("score", {"a": 1}) != step_key("risk", {"a": 1})
    with pytest.raises(TypeError):
        step_key("score", {"a": {1, 2}})


def test_retrieval_key_ignores_filter_order_but_not_index_version() -> None:
    def key(sources: list[str], index: str = "i1") -> str:
        return retrieval_key(
            query="q",
            filters=SearchFilters(sources=sources),
            k=5,
            rerank=True,
            index_version=index,
        )

    assert key(["yc", "github"]) == key(["github", "yc"])
    assert key(["yc"]) != key(["yc"], "i2")
    assert key(["yc"]) == retrieval_key(
        query="q", filters={"sources": ["yc"]}, k=5, rerank=True, index_version="i1"
    )
