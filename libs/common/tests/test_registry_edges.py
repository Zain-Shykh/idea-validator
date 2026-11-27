"""Spec Z3 A7: bad registry and budget files, and ways around the version check."""

import json
import shutil
from pathlib import Path

import pytest
import yaml

from common.registry import RegistryError, load_budgets, load_prompts, registry_entries

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    for name in ("rubrics", "prompts", "config"):
        shutil.copytree(ROOT / name, tmp_path / name)
    return tmp_path


def entries(tree: Path) -> object:
    return registry_entries(tree / "rubrics", tree / "prompts", tree / "config")


@pytest.mark.parametrize(
    "name",
    ["rubrics/risk.yaml", "rubrics/manifest.yaml", "config/budgets.yaml", "config/verdict.yaml"],
)
@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"\xff\xfe\x00",
        b"[1, 2]",
        b"!!python/object/apply:os.getcwd []",
        # PyYAML raises a plain ValueError here, not a YAMLError
        b"when: 2026-13-45\n",
    ],
    ids=["empty", "not-utf8", "list", "python-tag", "impossible-date"],
)
def test_any_bad_file_is_a_registry_error(tree: Path, name: str, content: bytes) -> None:
    (tree / name).write_bytes(content)
    with pytest.raises(RegistryError):
        entries(tree)


def test_impossible_date_in_prompt_front_matter_is_a_registry_error(tree: Path) -> None:
    (tree / "prompts/intake@v1.md").write_text("---\nname: intake\nversion: 2026-13-45\n---\nx\n")
    with pytest.raises(RegistryError):
        load_prompts(tree / "prompts")


@pytest.mark.parametrize(
    "name", ["rubrics/risk.yaml", "config/budgets.yaml", "config/verdict.yaml"]
)
def test_missing_file_or_directory_in_its_place_is_a_registry_error(tree: Path, name: str) -> None:
    (tree / name).unlink()
    with pytest.raises(RegistryError):
        entries(tree)
    (tree / name).mkdir()
    with pytest.raises(RegistryError):
        entries(tree)


def test_prompt_without_the_data_rule_or_body_is_rejected(tree: Path) -> None:
    path = tree / "prompts/intake@v1.md"
    for body, message in (("Do the thing.", "data-not-instructions"), ("  ", "empty prompt")):
        path.write_text(f"---\nname: intake\nversion: 1\n---\n{body}\n")
        with pytest.raises(RegistryError, match=message):
            load_prompts(tree / "prompts")


def test_rubric_under_the_wrong_file_name_or_output_schema_is_rejected(tree: Path) -> None:
    path = tree / "rubrics/risk.yaml"
    data = yaml.safe_load(path.read_text())
    path.write_text(yaml.safe_dump(data | {"output_schema": "score"}))
    with pytest.raises(RegistryError, match="output_schema"):
        entries(tree)
    path.write_text(yaml.safe_dump(data))
    path.rename(tree / "rubrics/danger.yaml")
    with pytest.raises(RegistryError, match="file name"):
        entries(tree)


@pytest.mark.parametrize("workflow", ["discover", "validate"])
@pytest.mark.parametrize("cap", ["max_tokens", "max_cost_usd", "max_pages", "max_steps"])
@pytest.mark.parametrize("value", [None, -1, 0, float("nan"), "lots", "absent"])
def test_every_workflow_cap_is_required_and_positive(
    tree: Path, workflow: str, cap: str, value: object
) -> None:
    path = tree / "config/budgets.yaml"
    data = yaml.safe_load(path.read_text())
    if value == "absent":
        del data["workflows"][workflow][cap]
    else:
        data["workflows"][workflow][cap] = value
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(RegistryError):
        load_budgets(path)


@pytest.mark.parametrize("action", [{}, {"max_tokens": None}, {"max_pages": -1}, {"max_x": 1}])
def test_bad_action_caps_are_rejected(tree: Path, action: dict[str, object]) -> None:
    path = tree / "config/budgets.yaml"
    data = yaml.safe_load(path.read_text())
    data["actions"]["llm_call"] = action
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(RegistryError):
        load_budgets(path)


def test_registry_paths_and_lock_agree_with_the_files() -> None:
    lock = json.loads((ROOT / "config/registry.lock.json").read_text())
    listed = registry_entries(ROOT / "rubrics", ROOT / "prompts", ROOT / "config")
    assert sorted(f"{e.kind}/{e.name}" for e in listed) == sorted(lock)
