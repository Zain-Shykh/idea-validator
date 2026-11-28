"""No fake or stand-in is used outside tests (spec Z11, A4 #9). Static: the sources, images,
compose files and manifests that make up the running system."""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def deployed_files() -> list[Path]:
    found = [ROOT / "docker-compose.yml"]
    for folder in ("k8s", "config"):
        found += sorted((ROOT / folder).rglob("*.yaml"))
    found += sorted(ROOT.glob("services/*/compose.yaml"))
    found += sorted(ROOT.glob("services/*/Dockerfile*"))
    found += sorted(ROOT.glob("jobs/*/Dockerfile*"))
    # the tuned-model component of M10t
    found += sorted(ROOT.glob("jobs/finetune/k8s-lora/**/*.yaml"))
    return found


def runtime_sources() -> list[Path]:
    found = sorted(ROOT.glob("services/*/src/**/*.py"))
    found += sorted(ROOT.glob("jobs/evaluation/*.py"))
    found += [
        p
        for p in sorted(ROOT.glob("libs/common/src/common/**/*.py"))
        if "fakes" not in p.relative_to(ROOT).parts
    ]
    return found


def test_the_checks_look_at_something() -> None:
    assert len(deployed_files()) > 30 and len(runtime_sources()) > 50


@pytest.mark.parametrize("path", runtime_sources(), ids=lambda p: str(p.relative_to(ROOT)))
def test_runtime_code_does_not_import_a_fake(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert not re.search(r"^\s*(from|import)\s+(common\.fakes|standin)\b", text, re.MULTILINE)
    assert not re.search(r"^\s*from\s+common\s+import\s+.*\bfakes\b", text, re.MULTILINE)


@pytest.mark.parametrize("path", deployed_files(), ids=lambda p: str(p.relative_to(ROOT)))
def test_deployment_files_do_not_use_a_fake(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert not re.search(r"stand-?in:|standins?\b[/.-]|scripts/standins|common\.fakes", text, re.I)
    assert not re.search(r"LLM_BACKEND[\"']?\s*[:=]\s*[\"']?(fake|direct)\b", text)
    for document in yaml.safe_load_all(text) if path.suffix in {".yaml", ".yml"} else []:
        assert "idea-validator/stand-in" not in str(document)
        assert not fake_backend_env(document)


def fake_backend_env(node: object) -> bool:
    """The ``- name: LLM_BACKEND`` / ``value: fake`` form, which no one-line pattern sees."""
    if isinstance(node, list):
        return any(fake_backend_env(item) for item in node)
    if not isinstance(node, dict):
        return False
    if node.get("name") == "LLM_BACKEND" and node.get("value") in ("fake", "direct"):
        return True
    return any(fake_backend_env(item) for item in node.values())


def test_the_env_entry_form_of_a_fake_backend_is_seen() -> None:
    planted = "spec:\n  env:\n    - name: LLM_BACKEND\n      value: fake\n"
    assert fake_backend_env(yaml.safe_load(planted))


def test_the_cluster_calls_models_through_the_gateway_only() -> None:
    base = yaml.safe_load((ROOT / "k8s/base/kustomization.yaml").read_text())
    literals = next(g["literals"] for g in base["configMapGenerator"] if g["name"] == "app-config")
    assert "LLM_BACKEND=gateway" in literals
