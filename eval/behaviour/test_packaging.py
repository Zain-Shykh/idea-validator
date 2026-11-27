"""Static checks on how Zain's services are packaged (DOCK-1..3, K8S-1, K8S-5, K8S-11).
Building the images and deploying them are manual checks (spec Z1, C3).
"""

import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SERVICES = ("orchestrator", "ideation", "validators", "scorer", "verdict", "critic", "agentops")


def docs(path: str) -> list[dict[str, Any]]:
    return [d for d in yaml.safe_load_all((ROOT / path).read_text()) if d]


def pins(path: Path) -> list[str]:
    return [line for line in path.read_text().splitlines() if line and not line.startswith("#")]


@pytest.mark.parametrize("service", SERVICES)
def test_dockerfile_is_pinned_multistage_nonroot(service: str) -> None:
    text = (ROOT / f"services/{service}/Dockerfile").read_text()
    bases = re.findall(r"^FROM (\S+)", text, re.MULTILINE)
    assert len(bases) == 2 and all(re.search(r":3\.12-slim@sha256:[0-9a-f]{64}$", b) for b in bases)
    assert re.findall(r"^USER (\S+)", text, re.MULTILINE) == ["10001"]
    assert f'"{service}.main:app"' in text and "localhost" not in text


@pytest.mark.parametrize("service", SERVICES)
def test_requirements_pins_match_dev_requirements(service: str) -> None:
    dev = {
        line.replace("uvicorn[standard]", "uvicorn") for line in pins(ROOT / "requirements-dev.txt")
    }
    own = pins(ROOT / f"services/{service}/requirements.txt")
    assert own and set(own) <= dev


@pytest.mark.parametrize("service", SERVICES)
def test_compose_fragment_is_included_and_has_its_port(service: str) -> None:
    root = docs("docker-compose.yml")[0]
    assert f"services/{service}/compose.yaml" in root["include"]
    spec = docs(f"services/{service}/compose.yaml")[0]["services"][service]
    ports = {"orchestrator": 8003, "ideation": 8008, "validators": 8009, "scorer": 8010}
    ports |= {"verdict": 8011, "critic": 8012, "agentops": 8013}
    assert spec["ports"] == [f"127.0.0.1:{ports[service]}:8000"]
    assert spec["image"].startswith(f"idea-validator-{service}:")


@pytest.mark.parametrize(
    "path",
    [f"k8s/base/{s}.yaml" for s in SERVICES]
    + ["k8s/overlays/dev/redis.yaml", "k8s/overlays/dev/postgres.yaml"],
)
def test_manifest_workload_standards(path: str) -> None:
    extra = ("PersistentVolumeClaim", "Job")
    deployment, service = [d for d in docs(path) if d["kind"] not in extra]
    assert (deployment["kind"], service["kind"]) == ("Deployment", "Service")
    pod = deployment["spec"]["template"]["spec"]
    assert pod["securityContext"]["runAsNonRoot"] is True
    assert pod["enableServiceLinks"] is False
    for container in pod["containers"]:
        assert_hardened(container)
    container, *sidecars = pod["containers"]
    assert sidecars == [] or deployment["metadata"]["name"] == "scorer"
    if "base" in path:
        assert container["readinessProbe"]["httpGet"]["path"] == "/ready"
        assert container["livenessProbe"]["httpGet"]["path"] == "/health"
        assert deployment["spec"]["strategy"]["rollingUpdate"]["maxUnavailable"] == 0
        assert deployment["metadata"]["labels"]["tier"] == "backend"
        assert {"configMapRef": {"name": "app-config"}} in container["envFrom"]


def assert_hardened(container: dict[str, Any], *, probes: bool = True) -> None:
    assert not container["image"].endswith(":latest") and ":" in container["image"]
    assert container["securityContext"]["allowPrivilegeEscalation"] is False
    for kind in ("requests", "limits"):
        assert set(container["resources"][kind]) == {"cpu", "memory"}
    assert not probes or ("readinessProbe" in container and "livenessProbe" in container)


def test_kustomization_lists_every_service_and_tags_every_image() -> None:
    base = docs("k8s/base/kustomization.yaml")[0]
    assert {f"{s}.yaml" for s in SERVICES} <= set(base["resources"])
    images = docs("k8s/overlays/dev/kustomization.yaml")[0]["images"]
    assert {i["name"].rsplit("-", 1)[1] for i in images} == set(SERVICES)
    assert all(re.fullmatch(r"[0-9a-f]{7,40}", i["newTag"]) for i in images)


@pytest.mark.parametrize("service", SERVICES)
def test_dockerfile_ships_its_own_service_and_checks_readiness(service: str) -> None:
    text = (ROOT / f"services/{service}/Dockerfile").read_text()
    assert set(re.findall(r"services/(\w+)/(?:src|requirements\.txt) ", text)) == {service}
    assert "HEALTHCHECK" in text and "http://127.0.0.1:8000/ready" in text
    spec = docs(f"services/{service}/compose.yaml")[0]["services"][service]
    assert spec["build"] == {"context": "../..", "dockerfile": f"services/{service}/Dockerfile"}


def test_configured_urls_of_deployed_services_resolve() -> None:
    """Each edge Z1 uses must point at a Service that is rendered, on its port."""
    base = docs("k8s/base/kustomization.yaml")[0]
    config = dict(item.split("=", 1) for item in base["configMapGenerator"][0]["literals"])
    manifests = [f"k8s/base/{s}.yaml" for s in SERVICES] + ["k8s/overlays/dev/redis.yaml"]
    ports = {
        d["metadata"]["name"]: d["spec"]["ports"][0]["port"]
        for path in manifests
        for d in docs(path)
        if d["kind"] == "Service"
    }
    for service in SERVICES:
        assert config[f"{service.upper()}_URL"] == f"http://{service}:{ports[service]}"
    assert (config["REDIS_HOST"], int(config["REDIS_PORT"])) == ("redis", ports["redis"])
    env = docs("services/orchestrator/compose.yaml")[0]["services"]["orchestrator"]["environment"]
    assert env["VERDICT_URL"] == "http://verdict:8000"
    assert env["AGENTOPS_URL"] == "http://agentops:8000"
    assert env["REDIS_HOST"] == "redis"


def test_verdict_rules_come_from_a_generated_configmap() -> None:
    """M11-3, K8S-4: one rules file, mounted in the cluster and in compose, not baked in."""
    assert "../../config" in docs("k8s/base/kustomization.yaml")[0]["resources"]
    generators = docs("config/kustomization.yaml")[0]["configMapGenerator"]
    assert {"name": "verdict-config", "files": ["verdict.yaml"]} in generators

    deployment = docs("k8s/base/verdict.yaml")[0]["spec"]
    assert deployment["replicas"] == 2
    pod = deployment["template"]["spec"]
    (mount,) = pod["containers"][0]["volumeMounts"]
    assert (mount["mountPath"], mount["readOnly"]) == ("/etc/verdict", True)
    assert pod["volumes"] == [{"name": mount["name"], "configMap": {"name": "verdict-config"}}]

    dockerfile = (ROOT / "services/verdict/Dockerfile").read_text()
    path = "/etc/verdict/verdict.yaml"
    assert f"VERDICT_CONFIG_PATH={path}" in dockerfile and "COPY config" not in dockerfile
    spec = docs("services/verdict/compose.yaml")[0]["services"]["verdict"]
    assert spec["volumes"] == [f"../../config/verdict.yaml:{path}:ro"]


def test_registry_files_reach_agentops_through_configmaps() -> None:
    """K8S-4, K8S-12: every rubric and prompt is in a generated ConfigMap; traces go to a PVC."""
    base = docs("k8s/base/kustomization.yaml")[0]["resources"]
    assert {"../../config", "../../rubrics", "../../prompts"} <= set(base)
    (rubrics,) = docs("rubrics/kustomization.yaml")[0]["configMapGenerator"]
    on_disk = {p.name for p in (ROOT / "rubrics").glob("*.yaml")} - {"kustomization.yaml"}
    assert set(rubrics["files"]) == on_disk
    (prompts,) = docs("prompts/kustomization.yaml")[0]["configMapGenerator"]
    expected = {f"{p.name.split('@')[0]}.md={p.name}" for p in (ROOT / "prompts").glob("*.md")}
    assert set(prompts["files"]) == expected
    generators = docs("config/kustomization.yaml")[0]["configMapGenerator"]
    assert {"name": "budgets", "files": ["budgets.yaml"]} in generators

    deployment, claim, _ = docs("k8s/base/agentops.yaml")
    assert (claim["kind"], claim["metadata"]["name"]) == ("PersistentVolumeClaim", "traces")
    pod = deployment["spec"]["template"]["spec"]
    (init,) = pod["initContainers"]
    (container,) = pod["containers"]
    assert init["command"] == ["python", "-m", "agentops.migrate"]
    assert init["image"] == container["image"] and init["resources"] and init["securityContext"]
    mounts = {m["name"]: m["mountPath"] for m in container["volumeMounts"]}
    dockerfile = (ROOT / "services/agentops/Dockerfile").read_text()
    for volume, variable in (
        ("traces", "TRACES"),
        ("rubrics", "RUBRICS"),
        ("prompts", "PROMPTS"),
        ("rules", "CONFIG"),
    ):
        assert f"AGENTOPS_{variable}_DIR={mounts[volume]}" in dockerfile
    assert all("value" in e or "secretKeyRef" in e["valueFrom"] for e in container["env"])


def test_agentops_compose_mounts_match_the_image_and_migrates_first() -> None:
    """LOC-2: the same folders as in the cluster; the service waits for the migration."""
    services = docs("services/agentops/compose.yaml")[0]["services"]
    dockerfile = (ROOT / "services/agentops/Dockerfile").read_text()
    mounts = dict(v.split(":")[:2] for v in services["agentops"]["volumes"])
    for source, variable in (
        ("traces", "TRACES"),
        ("../../rubrics", "RUBRICS"),
        ("../../prompts", "PROMPTS"),
        ("../../config", "CONFIG"),
    ):
        assert f"AGENTOPS_{variable}_DIR={mounts[source]}" in dockerfile
    assert services["agentops-migrate"]["command"] == ["python", "-m", "agentops.migrate"]
    waits = services["agentops"]["depends_on"]["agentops-migrate"]["condition"]
    assert waits == "service_completed_successfully"


def test_database_password_comes_only_from_a_hand_made_secret() -> None:
    """GEN-4: no Secret is committed and no manifest holds a literal password."""
    assert "postgres.yaml" in docs("k8s/overlays/dev/kustomization.yaml")[0]["resources"]
    assert not [p for p in (ROOT / "k8s").rglob("*.yaml") for d in yaml.safe_load_all(p.read_text())
                if d and d.get("kind") == "Secret"]  # fmt: skip
    for path in ("k8s/base/agentops.yaml", "k8s/overlays/dev/postgres.yaml"):
        pod = docs(path)[0]["spec"]["template"]["spec"]
        for container in pod.get("initContainers", []) + pod["containers"]:
            env = {e["name"]: e for e in container["env"]}
            reference = env["POSTGRES_PASSWORD"]["valueFrom"]["secretKeyRef"]
            assert reference == {"name": "db", "key": "POSTGRES_PASSWORD"}
    claim = docs("k8s/base/agentops.yaml")[1]["spec"]
    assert claim["accessModes"] == ["ReadWriteOnce"]
    assert claim["resources"]["requests"]["storage"] == "1Gi"


def test_scorer_pod_serves_the_model_from_the_pvc() -> None:
    """FT-7, K8S-1: the Job fills the PVC the sidecar reads; the wrapper talks to it over the
    pod's loopback; llama.cpp is pinned by digest and not reachable from outside the pod."""
    deployment, _, claim, job = docs("k8s/base/scorer.yaml")
    assert (claim["kind"], claim["metadata"]["name"], job["kind"]) == (
        "PersistentVolumeClaim", "models", "Job"
    )  # fmt: skip
    pod = deployment["spec"]["template"]["spec"]
    wrapper, llama = pod["containers"]
    (wait,) = pod["initContainers"]
    (importer,) = job["spec"]["template"]["spec"]["containers"]
    for container in (wait, importer):
        assert_hardened(container, probes=False)
    assert "startupProbe" in llama and wait["image"] == llama["image"]
    assert re.fullmatch(
        r"ghcr\.io/ggml-org/llama\.cpp:server-\S+@sha256:[0-9a-f]{64}", llama["image"]
    )

    args = dict(zip(llama["args"][::2], llama["args"][1::2], strict=True))
    model_file = args["--model"]
    assert model_file in importer["command"][-1] and model_file in wait["command"][-1]
    env = {e["name"]: e["value"] for e in wrapper["env"]}
    assert env["SCORER_BACKEND"] == "llamacpp"
    assert env["SCORER_MODEL_URL"] == f"http://{args['--host']}:{args['--port']}"
    assert args["--host"] == "127.0.0.1" and "ports" not in llama
    assert int(args["--threads"]) == int(llama["resources"]["limits"]["cpu"])
    for spec in (pod, job["spec"]["template"]["spec"]):
        claims = [v for v in spec["volumes"] if "persistentVolumeClaim" in v]
        assert claims == [{"name": "models", "persistentVolumeClaim": {"claimName": "models"}}]
    version = model_file.rsplit("-", 2)[-2] + "-" + model_file.rsplit("-", 1)[-1].split(".")[0]
    assert importer["image"].endswith(f"-scorer-model:{version}")
    assert job["metadata"]["name"] == f"model-import-{version}"
    # the Service must not select the Job's pods
    assert job["spec"]["template"]["metadata"]["labels"]["app"] != "scorer"

    mounts = {m["name"]: m["mountPath"] for m in wrapper["volumeMounts"]}
    dockerfile = (ROOT / "services/scorer/Dockerfile").read_text()
    for volume, variable in (("rubrics", "RUBRICS"), ("prompts", "PROMPTS")):
        assert f"SCORER_{variable}_DIR={mounts[volume]}" in dockerfile


def test_no_model_weights_are_tracked_and_the_model_image_is_pinned() -> None:
    """M10-5: weights never enter git; the model image copies one GGUF and runs as non-root."""
    tracked = subprocess.run(
        ["git", "ls-files", "*.gguf", "*.safetensors", "*.bin"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    )  # fmt: skip
    assert tracked.stdout == ""
    text = (ROOT / "jobs/finetune/model-image/Dockerfile").read_text()
    bases = re.findall(r"^FROM (\S+)", text, re.MULTILINE)
    assert bases and all("@sha256:" in base for base in bases)
    assert re.findall(r"^USER (\S+)", text, re.MULTILINE) == ["10001"]
    # --chmod also applies to the folder COPY creates: without x, the Job cannot read the model
    modes = re.findall(r"--chmod=0?([0-7]{3})", text)
    assert modes and all(int(digit, 8) & 5 == 5 for mode in modes for digit in mode)


def test_validators_get_rubrics_and_prompts_and_one_scorer_slot() -> None:
    """K8S-4: rubrics and prompts are mounted where the image looks; one call to the CPU model."""
    deployment = docs("k8s/base/validators.yaml")[0]["spec"]
    pod = deployment["template"]["spec"]
    (container,) = pod["containers"]
    mounts = {m["name"]: m["mountPath"] for m in container["volumeMounts"]}
    volumes = {v["name"]: v["configMap"]["name"] for v in pod["volumes"]}
    dockerfile = (ROOT / "services/validators/Dockerfile").read_text()
    compose = docs("services/validators/compose.yaml")[0]["services"]["validators"]
    for name, variable in (("rubrics", "RUBRICS"), ("prompts", "PROMPTS")):
        assert volumes[name] == name
        assert f"VALIDATORS_{variable}_DIR={mounts[name]}" in dockerfile
        assert f"../../{name}:{mounts[name]}:ro" in compose["volumes"]
    assert deployment["replicas"] == 1
    assert {"name": "VALIDATORS_SCORER_CONCURRENCY", "value": "1"} in container["env"]


def test_critic_gets_its_prompt_and_budgets_from_configmaps() -> None:
    """K8S-4: the claim-check prompt and budget rules are mounted where the image looks."""
    pod = docs("k8s/base/critic.yaml")[0]["spec"]["template"]["spec"]
    (container,) = pod["containers"]
    mounts = {m["name"]: m["mountPath"] for m in container["volumeMounts"]}
    assert all(m["readOnly"] for m in container["volumeMounts"])
    volumes = {v["name"]: v["configMap"]["name"] for v in pod["volumes"]}
    assert volumes == {"prompts": "prompts", "budgets": "budgets"}
    dockerfile = (ROOT / "services/critic/Dockerfile").read_text()
    budgets = f"{mounts['budgets']}/budgets.yaml"
    assert f"CRITIC_PROMPTS_DIR={mounts['prompts']}" in dockerfile
    assert f"CRITIC_BUDGETS_PATH={budgets}" in dockerfile
    assert "COPY config" not in dockerfile and "COPY prompts" not in dockerfile
    compose = docs("services/critic/compose.yaml")[0]["services"]["critic"]
    assert compose["volumes"] == [
        f"../../prompts:{mounts['prompts']}:ro",
        f"../../config/budgets.yaml:{budgets}:ro",
    ]


def test_orchestrator_reads_its_rules_from_configmaps() -> None:
    """K8S-4: rubrics, prompts and budgets are mounted, in the cluster and in compose."""
    deployment = docs("k8s/base/orchestrator.yaml")[0]["spec"]
    assert deployment["replicas"] == 1  # run tasks and locks are in the process
    pod = deployment["template"]["spec"]
    mounts = {m["mountPath"] for m in pod["containers"][0]["volumeMounts"]}
    assert mounts == {"/etc/rubrics", "/etc/prompts", "/etc/budgets"}
    assert {v["configMap"]["name"] for v in pod["volumes"]} == {"rubrics", "prompts", "budgets"}
    dockerfile = (ROOT / "services/orchestrator/Dockerfile").read_text()
    for path in (*mounts, "/etc/budgets/budgets.yaml"):
        assert path in dockerfile
    spec = docs("services/orchestrator/compose.yaml")[0]["services"]["orchestrator"]
    assert {v.split(":")[1] for v in spec["volumes"]} == {
        "/etc/rubrics", "/etc/prompts", "/etc/budgets/budgets.yaml",
    }  # fmt: skip
    base = docs("k8s/base/kustomization.yaml")[0]
    config = {item.split("=", 1)[0] for item in base["configMapGenerator"][0]["literals"]}
    needed = {"PROFILE_URL", "VERDICT_URL", "VALIDATORS_URL", "KB_URL", "CRITIC_URL"}
    assert needed | {"IDEATION_URL", "SCOUTS_URL"} <= config
