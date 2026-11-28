import shutil
from pathlib import Path

import pytest
import yaml

from common.contracts.core import Dimension, Workflow
from common.registry import (
    DATA_RULE,
    RegistryError,
    load_budgets,
    load_prompt,
    load_prompts,
    load_rubrics,
    registry_entries,
)

ROOT = Path(__file__).resolve().parents[3]
PROMPTS = (
    "intake",
    "ideator",
    "gap_analyst",
    "skeptic",
    "concretizer",
    "critic_check",
    "scorer",
    "effort",
)


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    for name in ("rubrics", "prompts", "config"):
        shutil.copytree(ROOT / name, tmp_path / name)
    return tmp_path


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text
    path.write_text(text.replace(old, new, 1))


def test_repo_rubrics_are_complete() -> None:
    rubric_set, rubrics = load_rubrics(ROOT / "rubrics")
    assert rubric_set == "r1" and set(rubrics) == set(Dimension)
    for rubric in rubrics.values():
        assert set(rubric.anchors) == set(range(6))
        assert {e.expected["score"] for e in rubric.examples} >= set(range(6))
        assert rubric.features


def test_repo_prompts() -> None:
    prompts = load_prompts(ROOT / "prompts")
    assert set(prompts) == set(PROMPTS)
    for name, prompt in prompts.items():
        version = 2 if name in ("critic_check", "gap_analyst") else 1
        assert prompt.id == f"{name}@v{version}" and (ROOT / f"prompts/{prompt.id}.md").is_file()
        assert DATA_RULE in prompt.body
    for name in ("ideator", "gap_analyst", "skeptic", "critic_check", "scorer"):
        assert "{{evidence}}" in prompts[name].body and "<evidence id=" in prompts[name].body


def test_repo_budgets() -> None:
    budgets = load_budgets(ROOT / "config/budgets.yaml")
    assert set(budgets.workflows) == set(Workflow)
    assert {"llm_call", "deep_search"} <= set(budgets.actions)


def test_registry_lists_every_versioned_file() -> None:
    entries = registry_entries(ROOT / "rubrics", ROOT / "prompts", ROOT / "config")
    assert len(entries) == 7 + 8 + 2
    assert all((ROOT / e.path).is_file() for e in entries)


def test_missing_example_level_is_rejected(tree: Path) -> None:
    path = tree / "rubrics/risk.yaml"
    data = yaml.safe_load(path.read_text())
    data["examples"] = [e for e in data["examples"] if e["expected"]["score"] != 2]
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(RegistryError, match=r"level\(s\) \[2\]"):
        load_rubrics(tree / "rubrics")


def test_example_citing_an_unknown_id_is_rejected(tree: Path) -> None:
    edit(tree / "rubrics/risk.yaml", "[card_e501#c0] (github)", "[card_zzz#c0] (github)")
    with pytest.raises(RegistryError, match="not in its input"):
        load_rubrics(tree / "rubrics")


def test_missing_anchor_is_rejected(tree: Path) -> None:
    path = tree / "rubrics/risk.yaml"
    data = yaml.safe_load(path.read_text())
    del data["anchors"][4]
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(RegistryError, match="anchor"):
        load_rubrics(tree / "rubrics")


def test_manifest_must_match_rubric_versions(tree: Path) -> None:
    edit(tree / "rubrics/risk.yaml", "version: 1", "version: 2")
    with pytest.raises(RegistryError, match="manifest"):
        load_rubrics(tree / "rubrics")


def test_two_versions_of_a_prompt_are_rejected(tree: Path) -> None:
    copy = tree / "prompts/intake@v2.md"
    shutil.copy(tree / "prompts/intake@v1.md", copy)
    edit(copy, "version: 1", "version: 2")
    with pytest.raises(RegistryError, match="two versions"):
        load_prompts(tree / "prompts")


def test_prompt_file_name_must_match_front_matter(tree: Path) -> None:
    edit(tree / "prompts/intake@v1.md", "version: 1", "version: 2")
    with pytest.raises(RegistryError, match="does not match"):
        load_prompt(tree / "prompts/intake@v1.md")


def test_mounted_prompt_without_version_in_its_name_loads(tree: Path) -> None:
    mounted = (tree / "prompts/intake@v1.md").rename(tree / "prompts/intake.md")
    assert load_prompt(mounted).id == "intake@v1"


@pytest.mark.parametrize("text", ["", "no front matter", "---\nname: intake\n---\nbody"])
def test_broken_prompt_is_rejected(tmp_path: Path, text: str) -> None:
    path = tmp_path / "intake.md"
    path.write_text(text)
    with pytest.raises(RegistryError):
        load_prompt(path)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("max_steps: 40}", "max_steps: 0}"),
        ("  discover:", "  other:"),
        ("version: v1", "version: 1"),
    ],
)
def test_bad_budgets_are_rejected(tree: Path, old: str, new: str) -> None:
    edit(tree / "config/budgets.yaml", old, new)
    with pytest.raises(RegistryError):
        load_budgets(tree / "config/budgets.yaml")


def test_missing_directory_is_a_registry_error(tmp_path: Path) -> None:
    with pytest.raises(RegistryError):
        load_rubrics(tmp_path)
    with pytest.raises(RegistryError):
        load_prompts(tmp_path)
