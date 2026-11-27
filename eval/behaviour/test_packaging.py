"""Static checks on how Zain's services are packaged (DOCK-1..3, K8S-1, K8S-5, K8S-11).
Building the images and deploying them are manual checks (spec Z1, C3).
"""

import re
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
    deployment, service = [d for d in docs(path) if d["kind"] != "PersistentVolumeClaim"]
    assert (deployment["kind"], service["kind"]) == ("Deployment", "Service")
    pod = deployment["spec"]["template"]["spec"]
    assert pod["securityContext"]["runAsNonRoot"] is True
    assert pod["enableServiceLinks"] is False
    (container,) = pod["containers"]
    assert not container["image"].endswith(":latest") and ":" in container["image"]
    assert container["securityContext"]["allowPrivilegeEscalation"] is False
    for kind in ("requests", "limits"):
        assert set(container["resources"][kind]) == {"cpu", "memory"}
    assert "readinessProbe" in container and "livenessProbe" in container
    if "base" in path:
        assert container["readinessProbe"]["httpGet"]["path"] == "/ready"
        assert container["livenessProbe"]["httpGet"]["path"] == "/health"
        assert deployment["spec"]["strategy"]["rollingUpdate"]["maxUnavailable"] == 0
        assert deployment["metadata"]["labels"]["tier"] == "backend"
        assert {"configMapRef": {"name": "app-config"}} in container["envFrom"]


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
