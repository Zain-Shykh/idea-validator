"""``scripts/check_eval.sh`` fails when a metric regresses past its tolerance (spec Z10, A4 #5).
Every number in ``fixtures/FAKE_*.json`` is invented."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/check_eval.sh"
FIXTURES = Path(__file__).parent / "fixtures"
BASELINE = FIXTURES / "FAKE_baseline.json"


def run(*args: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        env=os.environ | {"PYTHON": sys.executable} | env,
        capture_output=True, text=True, timeout=60,
    )  # fmt: skip


def test_an_equal_report_passes() -> None:
    done = run("--report", str(FIXTURES / "FAKE_report_same.json"), "--baseline", str(BASELINE))
    assert done.returncode == 0, done.stderr


def test_a_regressed_report_fails_and_names_the_metrics() -> None:
    report = str(FIXTURES / "FAKE_report_regressed.json")
    done = run("--report", report, "--baseline", str(BASELINE))
    assert done.returncode == 1
    assert "regression: agreement" in done.stdout
    assert "regression: false_go_on_no_go" in done.stdout


def test_perturbing_the_baseline_turns_a_pass_into_a_failure(tmp_path: Path) -> None:
    better = json.loads(BASELINE.read_text())
    better["metrics"]["agreement"] = 0.9
    perturbed = tmp_path / "FAKE_perturbed.json"
    perturbed.write_text(json.dumps(better))
    done = run("--report", str(FIXTURES / "FAKE_report_same.json"), "--baseline", str(perturbed))
    assert done.returncode == 1


def test_without_a_baseline_nothing_passes() -> None:
    done = run("--report", str(FIXTURES / "FAKE_report_same.json"), "--baseline", "nothing.json")
    assert done.returncode == 2 and "no baseline" in done.stderr


def test_fixtures_say_they_are_fake_and_no_real_baseline_is_faked() -> None:
    for path in FIXTURES.glob("*.json"):
        data = json.loads(path.read_text())
        assert path.name.startswith("FAKE_") and data["fake"] and "FAKE" in data["WARNING"]
    real = ROOT / "eval/results/baseline.json"
    if real.exists():
        assert json.loads(real.read_text())["fake"] is False


# a stand-in kubectl: records its calls and answers what the script asks
KUBECTL = """#!/usr/bin/env bash
echo "$*" >> "$KUBECTL_LOG"
case "$*" in
  *"create configmap"*) echo "kind: ConfigMap" ;;
  *"apply -f -"*) cat >> "$KUBECTL_LOG" ;;
  *"get job eval"*) echo "$JOB_STATE" ;;
  *"get pods"*) echo "$POD_EXIT" ;;
  *logs*) echo "the job's log"; echo 'REPORT_JSON {"kind": "eval"}' ;;
esac
"""


@pytest.mark.parametrize(
    "state, pod_exit, expected",
    [("Complete", "0", 0), ("Failed", "1", 1), ("Failed", "2", 2), ("Failed", "137", 2),
     ("", "", 2)],
)  # fmt: skip
def test_cluster_mode_passes_the_jobs_result_on(
    tmp_path: Path, state: str, pod_exit: str, expected: int
) -> None:
    kubectl = tmp_path / "kubectl"
    kubectl.write_text(KUBECTL)
    kubectl.chmod(0o755)
    log = tmp_path / "calls.log"
    done = run(
        "--baseline", str(BASELINE),
        PATH=f"{tmp_path}:{os.environ['PATH']}", KUBECTL_LOG=str(log), JOB_STATE=state,
        POD_EXIT=pod_exit, IMAGE_TAG="abc1234", EVAL_TIMEOUT_S="1", EVAL_POLL_S="1",
        EVAL_REPORT_OUT=str(tmp_path / "saved.json"),
    )  # fmt: skip
    assert done.returncode == expected, done.stderr
    if state:
        assert json.loads((tmp_path / "saved.json").read_text()) == {"kind": "eval"}
        assert "the job's log" in done.stdout and "REPORT_JSON" not in done.stdout
    calls = log.read_text()
    assert "idea-validator-evaluation:abc1234" in calls and "set-by-overlay" not in calls
    assert "delete job eval --ignore-not-found" in calls
