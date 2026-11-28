"""No weights in git and the switch to the tuned model (spec Z9 A4 8, 9; FT-7, M10-1, M10-5).
Building the model image with a stand-in file is a manual check (spec Z9 C3)."""

import json
import shutil
import subprocess
from typing import Any

import httpx
import pytest
import yaml
from scorer_factories import Replies, Rig, reply, request

from dataset import ROOT

WEIGHT_SUFFIXES = (".gguf", ".safetensors", ".bin", ".pt", ".pth", ".onnx", ".ckpt", ".h5")
MAX_BYTES = 5 * 1024 * 1024
COMPONENT = ROOT / "jobs/finetune/k8s-lora"


def test_no_weights_in_git() -> None:
    listed = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout
    files = [ROOT / name for name in listed.split("\0") if name]
    assert len(files) > 100
    bad = []
    for path in (f for f in files if f.is_file()):
        with path.open("rb") as handle:
            magic = handle.read(4)
        if path.suffix.lower() in WEIGHT_SUFFIXES or magic == b"GGUF":
            bad.append(f"{path.name}: a weight file")
        if path.stat().st_size > MAX_BYTES:
            bad.append(f"{path.name}: {path.stat().st_size} bytes")
    assert not bad


@pytest.mark.parametrize("name", ["model.gguf", "jobs/finetune/outputs/merged/model.safetensors"])
def test_weight_files_are_ignored(name: str) -> None:
    found = subprocess.run(["git", "check-ignore", "-q", name], cwd=ROOT, check=False)
    assert found.returncode == 0


def render(folder: str) -> dict[tuple[str, str], dict[str, Any]]:
    if shutil.which("kubectl") is None:
        pytest.skip("kubectl is not installed")
    out = subprocess.run(
        ["kubectl", "kustomize", str(ROOT / folder)], capture_output=True, text=True, check=True
    ).stdout
    return {(d["kind"], d["metadata"]["name"]): d for d in yaml.safe_load_all(out) if d}


def containers(workload: dict[str, Any], key: str = "containers") -> dict[str, dict[str, Any]]:
    return {c["name"]: c for c in workload["spec"]["template"]["spec"][key]}


def env(container: dict[str, Any]) -> dict[str, str]:
    return {e["name"]: e.get("value", "") for e in container["env"]}


def test_lora_component() -> None:
    base, lora = render("k8s/overlays/dev"), render("jobs/finetune/k8s-lora/dev")
    jobs = [name for kind, name in lora if kind == "Job"]
    assert jobs == ["model-import-lora-v1"]
    job = containers(lora["Job", jobs[0]])["model-import"]
    old = containers(base["Job", "model-import-base-v1"])["model-import"]
    assert job["image"] == "ghcr.io/zain-shykh/idea-validator-scorer-model:v1"
    # only the file name differs from the tested base script
    assert job["command"][-1] == old["command"][-1].replace("scorer-base-v1", "scorer-lora-v1")
    # apart from name, image and script the Job is the base one (volumes, security, resources)
    new_job, old_job = lora["Job", jobs[0]], base["Job", "model-import-base-v1"]
    assert {**job, "image": None, "command": None} == {**old, "image": None, "command": None}
    pod, old_pod = new_job["spec"]["template"]["spec"], old_job["spec"]["template"]["spec"]
    assert {**pod, "containers": None} == {**old_pod, "containers": None}
    scorer = lora["Deployment", "scorer"]
    before = base["Deployment", "scorer"]
    for key, name in (("containers", "llama"), ("initContainers", "wait-for-model")):
        new, previous = containers(scorer, key)[name], containers(before, key)[name]
        field = "args" if name == "llama" else "command"
        assert new[field] == [
            x.replace("scorer-base-v1", "scorer-lora-v1") for x in previous[field]
        ]
        assert {**new, field: None} == {**previous, field: None}
    wrapper = env(containers(scorer)["scorer"])
    assert wrapper == env(containers(before)["scorer"]) | {"SCORER_MODEL_ID": "scorer-lora@v1"}
    # M3 records the same id, or verdicts and cache keys would name the wrong model
    orchestrator = env(containers(lora["Deployment", "orchestrator"])["orchestrator"])
    assert orchestrator["SCORER_MODEL_ID"] == wrapper["SCORER_MODEL_ID"]
    # nothing else changes
    changed = {("Deployment", "scorer"), ("Deployment", "orchestrator")}
    same = {k: v for k, v in base.items() if k[0] != "Job" and k not in changed}
    assert same == {k: v for k, v in lora.items() if k[0] != "Job" and k not in changed}


def test_base_still_serves_the_base_model() -> None:
    """The base switches only through the component: there is no tuned model yet."""
    text = (ROOT / "k8s/base/scorer.yaml").read_text() + json.dumps(
        yaml.safe_load((ROOT / "k8s/overlays/dev/kustomization.yaml").read_text())
    )
    assert "lora" not in text and COMPONENT.is_dir()


@pytest.mark.parametrize(
    ("local", "model"),
    [
        ((reply(),), "scorer-lora@v1"),
        (("not json", "still not json"), "scorer-hosted"),
        ((httpx.ConnectError("refused"),), "scorer-hosted"),
    ],
    ids=["local", "invalid_twice", "unreachable"],
)
async def test_fallback_with_lora_model_id(local: tuple[Any, ...], model: str) -> None:
    rig = Rig(Replies(*local), [reply()], model_id="scorer-lora@v1")
    result = await rig.scorer.score(request())
    assert (result.model, result.score) == (model, 3.5)
