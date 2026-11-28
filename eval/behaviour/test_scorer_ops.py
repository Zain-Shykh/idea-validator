"""M10 against its neighbours: M13 ingest rules, the check script and the model-import Job."""

import argparse
import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from scorer_factories import ROOT, Replies, Rig, reply, request

from agentops.services.events import parse_event
from agentops.services.ingest import parse_batch
from common.contracts.ops import ScorerFallbackEvent


@pytest.mark.parametrize("hosted", [[reply()], []], ids=["fallback", "abstain"])
async def test_m13_ingest_accepts_every_scorer_record(hosted: list[str]) -> None:
    rig = Rig(Replies(httpx.ConnectError("refused")), hosted)
    await rig.scorer.score(request())
    records = await rig.records()
    parsed = parse_batch(("[" + ",".join(r.model_dump_json() for r in records) + "]").encode())
    assert len(parsed) == len(records)
    events = [e for r in parsed if (e := parse_event(r)) is not None]
    assert events == [ScorerFallbackEvent(dimension=request_dimension())]


def request_dimension() -> Any:
    return request().rubric_id.split("@")[0]


# --- scripts/scorer_check.py -------------------------------------------------------------------

_SPEC = importlib.util.spec_from_file_location("scorer_check", ROOT / "scripts/scorer_check.py")
assert _SPEC and _SPEC.loader
scorer_check = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(scorer_check)


def abstention(body: dict[str, Any]) -> dict[str, Any]:
    return {
        "dimension": body["rubric_id"].split("@")[0],
        "score": None,
        "confidence": 0,
        "evidence_ids": [],
        "reason": "no evidence",
        "rubric_version": body["rubric_id"],
        "model": "none",
    }


async def run_check(monkeypatch: pytest.MonkeyPatch, failing: set[int]) -> int:
    seen: list[int] = []

    def serve(req: httpx.Request) -> httpx.Response:
        seen.append(1)
        if len(seen) - 1 in failing:
            return httpx.Response(500)
        return httpx.Response(200, json=abstention(json.loads(req.content)))

    real = httpx.AsyncClient
    monkeypatch.setattr(
        scorer_check.httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(serve), **kw),
    )
    return await scorer_check.run(argparse.Namespace(url="http://scorer", record=None))


async def test_check_script_passes_on_ten_valid_responses(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await run_check(monkeypatch, set()) == 0
    assert "valid responses 10/10, scored 0" in capsys.readouterr().out


async def test_check_script_counts_an_http_error_once(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Bug on 374cf24: one HTTP 500 and nine good answers print 8/10."""
    assert await run_check(monkeypatch, {0}) == 1
    assert "valid responses 9/10" in capsys.readouterr().out


def test_check_flags_foreign_ids_and_wrong_rubric() -> None:
    body = request()
    result = scorer_check.DimensionScore(
        dimension="risk",
        score=3.25,
        confidence=1,
        evidence_ids=["card_zz#c0"],
        reason="r",
        rubric_version="risk@v1",
        model="m",
    )
    assert len(scorer_check.check(body, result)) == 3


# --- model-import Job shell script, run against temp directories -------------------------------


def import_job(tmp_path: Path) -> subprocess.CompletedProcess[str]:
    docs = [d for d in yaml.safe_load_all((ROOT / "k8s/base/scorer.yaml").read_text()) if d]
    (container,) = docs[-1]["spec"]["template"]["spec"]["containers"]
    script = container["command"][-1].replace("/models/", f"{tmp_path}/pvc/")
    script = script.replace("/model/", f"{tmp_path}/image/")
    return subprocess.run(["sh", "-c", script], capture_output=True, text=True, check=False)


def image(tmp_path: Path, content: bytes, digest_of: bytes | None = None) -> Path:
    (tmp_path / "pvc").mkdir(exist_ok=True)
    (tmp_path / "image").mkdir(exist_ok=True)
    (tmp_path / "image/model.gguf").write_bytes(content)
    digest = hashlib.sha256(content if digest_of is None else digest_of).hexdigest()
    (tmp_path / "image/model.gguf.sha256").write_text(f"{digest}  model.gguf\n")
    return tmp_path / "pvc/scorer-base-v1.gguf"


def test_job_imports_then_does_nothing_and_repairs_a_corrupt_copy(tmp_path: Path) -> None:
    target = image(tmp_path, b"weights")
    first = import_job(tmp_path)
    assert first.returncode == 0, first.stderr
    assert target.read_bytes() == b"weights" and not Path(f"{target}.tmp").exists()
    assert "already imported" in import_job(tmp_path).stdout
    target.write_bytes(b"trunc")
    assert import_job(tmp_path).returncode == 0 and target.read_bytes() == b"weights"


def test_job_never_publishes_a_file_with_the_wrong_checksum(tmp_path: Path) -> None:
    target = image(tmp_path, b"weights", digest_of=b"other")
    assert import_job(tmp_path).returncode != 0
    assert not target.exists()


def test_job_fails_without_a_checksum_file(tmp_path: Path) -> None:
    target = image(tmp_path, b"weights")
    (tmp_path / "image/model.gguf.sha256").unlink()
    assert import_job(tmp_path).returncode != 0 and not target.exists()
