"""``scripts/standins/run.sh`` against a recording kubectl: it names its cluster, never takes
over a real Service and removes only what is labelled as the stand-in."""

import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

SCRIPT = Path(__file__).resolve().parents[1] / "standins/run.sh"
KUBECTL = """#!/usr/bin/env bash
echo "$*" >> "$KUBECTL_LOG"
case "$*" in
  *"get service $REAL "*) printf '%s' "$REAL_ANSWER"; exit "${GET_EXIT:-0}" ;;
  *"create configmap"*) echo "kind: ConfigMap" ;;
  *"apply -f -"*) cat >> "$KUBECTL_LOG.applied" ;;
esac
"""


def run(tmp_path: Path, *args: str, **env: str) -> tuple[subprocess.CompletedProcess[str], str]:
    kubectl = tmp_path / "kubectl"
    kubectl.write_text(KUBECTL)
    kubectl.chmod(0o755)
    log = tmp_path / "calls.log"
    base = {k: v for k, v in os.environ.items() if k not in ("KUBE_CONTEXT", "NAMESPACE")}
    defaults = {"KUBE_CONTEXT": "idea-validator", "REAL": "-", "REAL_ANSWER": ""}
    done = subprocess.run(
        ["bash", str(SCRIPT), *args],
        env=base
        | defaults
        | {"PATH": f"{tmp_path}:{os.environ['PATH']}", "KUBECTL_LOG": str(log)}
        | env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return done, log.read_text() if log.exists() else ""


@pytest.mark.parametrize("args", [("up", "abc1234"), ("down",)])
def test_without_a_named_cluster_nothing_is_called(tmp_path: Path, args: tuple[str, ...]) -> None:
    done, calls = run(tmp_path, *args, KUBE_CONTEXT="")
    assert done.returncode != 0 and calls == ""


@pytest.mark.parametrize("name", ["profile", "kb", "tools"])
@pytest.mark.parametrize("label", ["", "false"])
def test_a_real_service_is_never_replaced(tmp_path: Path, name: str, label: str) -> None:
    done, calls = run(tmp_path, "up", "abc1234", REAL=name, REAL_ANSWER=f"{name}:{label}")
    assert done.returncode == 1 and "will not replace" in done.stderr
    assert not any(word in calls for word in ("apply", "create", "label ", "restart", "delete"))


def test_when_the_services_cannot_be_read_nothing_is_changed(tmp_path: Path) -> None:
    done, calls = run(tmp_path, "up", "abc1234", REAL="profile", GET_EXIT="1")
    assert done.returncode != 0 and "apply" not in calls


@pytest.mark.parametrize("answer", ["", "kb:true"])
def test_up_applies_with_the_tag_and_every_call_names_the_cluster(
    tmp_path: Path, answer: str
) -> None:
    done, calls = run(tmp_path, "up", "abc1234", REAL="kb", REAL_ANSWER=answer)
    assert done.returncode == 0, done.stderr
    assert all(
        line.startswith("--context idea-validator -n idea-validator ")
        for line in calls.splitlines()
    )
    applied = (tmp_path / "calls.log.applied").read_text()
    assert "idea-validator-orchestrator:abc1234" in applied and "set-by-run-sh" not in applied


def test_down_deletes_by_the_stand_in_label_only(tmp_path: Path) -> None:
    done, calls = run(tmp_path, "down")
    assert done.returncode == 0
    assert calls.splitlines() == [
        "--context idea-validator -n idea-validator delete deployment,service,configmap "
        "-l idea-validator/stand-in=true --ignore-not-found"
    ]


@pytest.mark.parametrize("args", [(), ("up",), ("restart",)])
def test_wrong_arguments_change_nothing(tmp_path: Path, args: tuple[str, ...]) -> None:
    done, calls = run(tmp_path, *args)
    assert done.returncode != 0 and calls == ""


def test_every_service_of_the_manifest_is_guarded() -> None:
    objects = yaml.safe_load_all((SCRIPT.parent / "standin.yaml").read_text())
    services = {o["metadata"]["name"] for o in objects if o["kind"] == "Service"}
    (loop,) = re.findall(r"for name in (.+); do", SCRIPT.read_text())
    assert set(loop.split()) == services
