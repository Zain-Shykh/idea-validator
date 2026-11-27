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
    "path", [f"k8s/base/{s}.yaml" for s in SERVICES] + ["k8s/overlays/dev/redis.yaml"]
)
def test_manifest_workload_standards(path: str) -> None:
    deployment, service = docs(path)
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
