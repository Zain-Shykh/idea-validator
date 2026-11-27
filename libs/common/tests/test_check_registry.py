import importlib.util
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("check_registry", ROOT / "scripts/check_registry.py")
assert spec and spec.loader
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    for name in ("rubrics", "prompts", "config"):
        shutil.copytree(ROOT / name, tmp_path / name)
    return tmp_path


def run(tree: Path, *flags: str) -> int:
    return int(check.main(["--root", str(tree), *flags]))


def edit(tree: Path, name: str, old: str, new: str) -> None:
    path = tree / name
    text = path.read_text()
    assert old in text, old
    path.write_text(text.replace(old, new, 1))


def test_committed_lock_matches_the_repository() -> None:
    assert check.main([]) == 0


@pytest.mark.parametrize(
    ("name", "old", "new"),
    [
        ("rubrics/risk.yaml", "A moderate concern", "A medium concern"),
        ("rubrics/manifest.yaml", "# The rubric set", "# The set"),
        ("prompts/scorer@v1.md", "thin, old", "weak, old"),
        ("config/budgets.yaml", "max_pages: 20", "max_pages: 25"),
        # Z2 follow-up: the verdict rules too, comments included
        ("config/verdict.yaml", "T_go: 70", "T_go: 65"),
        ("config/verdict.yaml", "# Rules of the", "# The rules of the"),
    ],
)
def test_content_change_without_version_bump_fails(
    tree: Path, capsys: pytest.CaptureFixture[str], name: str, old: str, new: str
) -> None:
    assert run(tree) == 0
    edit(tree, name, old, new)
    assert run(tree) == 1
    assert "is still" in capsys.readouterr().err
    lock = (tree / check.LOCK).read_text()
    assert run(tree, "--update") == 1
    assert (tree / check.LOCK).read_text() == lock


def test_prompt_bump_passes_after_update(tree: Path) -> None:
    edit(tree, "prompts/scorer@v1.md", "thin, old", "weak, old")
    edit(tree, "prompts/scorer@v1.md", "version: 1", "version: 2")
    (tree / "prompts/scorer@v1.md").rename(tree / "prompts/scorer@v2.md")
    assert run(tree) == 1  # bumped but not recorded
    assert run(tree, "--update") == 0
    assert run(tree) == 0


def test_rubric_bump_also_needs_a_new_rubric_set(tree: Path) -> None:
    edit(tree, "rubrics/risk.yaml", "version: 1", "version: 2")
    edit(tree, "rubrics/manifest.yaml", "risk: 1", "risk: 2")
    assert run(tree, "--update") == 1
    edit(tree, "rubrics/manifest.yaml", "rubric_set: r1", "rubric_set: r2")
    assert run(tree, "--update") == 0
    assert run(tree) == 0


def test_weight_change_also_needs_a_new_weights_version(
    tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    name = "config/verdict.yaml"
    edit(tree, name, "feasibility: 15, risk: 15", "feasibility: 20, risk: 10")
    edit(tree, name, "version: v1 ", "version: v2 ")
    assert run(tree, "--update") == 1
    assert "weights_version is still w1" in capsys.readouterr().err
    edit(tree, name, "weights_version: w1", "weights_version: w2")
    assert run(tree, "--update") == 0


def test_version_may_not_go_back(tree: Path) -> None:
    edit(tree, "config/budgets.yaml", "version: v1", "version: v2")
    assert run(tree, "--update") == 0
    edit(tree, "config/budgets.yaml", "version: v2", "version: v1")
    assert run(tree, "--update") == 1


def test_missing_lock_and_invalid_file_fail(tree: Path) -> None:
    (tree / "rubrics/risk.yaml").write_text("dimension: [")
    assert run(tree) == 1
    shutil.copy(ROOT / "rubrics/risk.yaml", tree / "rubrics/risk.yaml")
    (tree / check.LOCK).unlink()
    assert run(tree) == 1
    assert run(tree, "--update") == 0
