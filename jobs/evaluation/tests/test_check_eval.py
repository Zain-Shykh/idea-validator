"""``scripts/check_eval.sh`` fails when a metric regresses past its tolerance (spec Z10, A4 #5).
Every number in ``fixtures/FAKE_*.json`` is invented."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import reports
import run_eval

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


# --- exit 1 is reserved for a regression ---------------------------------------------------------


@pytest.mark.parametrize(
    "args",
    [("--report",), ("--baseline",), ("--report", ""), ("--nonsense",), ("stray",)],
)
def test_wrong_arguments_are_not_reported_as_a_regression(args: tuple[str, ...]) -> None:
    done = run(*args)
    assert done.returncode not in (0, 1), done.stderr


def test_a_report_file_that_does_not_exist_gives_2() -> None:
    done = run("--report", "no-such-FAKE.json", "--baseline", str(BASELINE))
    assert done.returncode == 2, done.stderr


@pytest.mark.parametrize("failing", ["create configmap", "delete job", "get job", "get pods"])
def test_a_kubectl_failure_is_not_reported_as_a_regression(tmp_path: Path, failing: str) -> None:
    """kubectl exits 1 on most errors; so does ``get pods`` when the deadline removed the pod."""
    kubectl = tmp_path / "kubectl"
    broken = f'case "$*" in *"{failing}"*) echo "error from server" >&2; exit 1 ;; esac\n'
    kubectl.write_text(KUBECTL.replace('case "$*" in', broken + 'case "$*" in', 1))
    kubectl.chmod(0o755)
    done = run(
        "--baseline", str(BASELINE),
        PATH=f"{tmp_path}:{os.environ['PATH']}", KUBECTL_LOG=str(tmp_path / "calls.log"),
        JOB_STATE="Failed", POD_EXIT="2", IMAGE_TAG="abc1234", EVAL_TIMEOUT_S="1",
        EVAL_POLL_S="1", EVAL_REPORT_OUT=str(tmp_path / "saved.json"),
    )  # fmt: skip
    assert done.returncode not in (0, 1), done.stderr


# --- edge cases: reports and the fake guard (all input here is FAKE) ---


def test_a_fake_report_cannot_reach_results_by_a_roundabout_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    link = tmp_path / "elsewhere"
    link.symlink_to(reports.RESULTS_DIR, target_is_directory=True)
    monkeypatch.chdir(reports.ROOT / "jobs")
    for folder in (link, Path("../eval/results"), reports.RESULTS_DIR / "sub/../sub"):
        with pytest.raises(reports.OutputError, match="fake"):
            reports.guard(folder / "x.json", True)
    with pytest.raises(reports.OutputError, match="not stored"):
        reports.store({"fake": True}, tmp_path / "x.json")


@pytest.mark.parametrize("sha", ["../../x", "a b", "a" * 65, "x\ny"])
def test_a_hostile_git_sha_never_becomes_a_file_name(
    monkeypatch: pytest.MonkeyPatch, sha: str
) -> None:
    monkeypatch.setenv("GIT_SHA", sha)
    assert reports.git_sha() == "unknown"


@pytest.mark.parametrize("content", [b"", b"[1]", b"\xff", b"[" * 100_000, b"null"])
def test_an_unreadable_report_or_baseline_gives_exit_2(tmp_path: Path, content: bytes) -> None:
    path = tmp_path / "FAKE_report.json"
    path.write_bytes(content)
    good = Path(__file__).parent / "fixtures/FAKE_report_same.json"
    assert run_eval.main(["--results", str(path), "--compare", str(good)]) == 2
    assert run_eval.main(["--results", str(good), "--compare", str(path)]) == 2


def test_a_fake_report_from_a_job_is_not_saved(tmp_path: Path) -> None:
    kubectl = tmp_path / "kubectl"
    kubectl.write_text(KUBECTL.replace('{"kind": "eval"}', '{"fake": true, "kind": "eval"}'))
    kubectl.chmod(0o755)
    saved = tmp_path / "saved.json"
    done = run(
        "--baseline", str(BASELINE),
        PATH=f"{tmp_path}:{os.environ['PATH']}", KUBECTL_LOG=str(tmp_path / "calls.log"),
        JOB_STATE="Complete", IMAGE_TAG="abc1234", EVAL_TIMEOUT_S="1", EVAL_POLL_S="1",
        EVAL_REPORT_OUT=str(saved),
    )  # fmt: skip
    assert done.returncode == 0 and "not saved" in done.stderr and not saved.exists()


def test_a_crash_of_the_evaluation_is_not_a_regression(monkeypatch: pytest.MonkeyPatch) -> None:
    def crash(*_: object) -> int:
        raise RuntimeError("idea text")

    monkeypatch.setattr(run_eval, "main", crash)
    assert run_eval.cli() == 2


def test_a_readable_but_unusable_baseline_stops_before_any_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "FAKE_empty.json"
    path.write_text("{}")
    assert run_eval.main(["--compare", str(path), "--out", str(tmp_path)]) == 2
    # the baseline is the complaint, not the missing service URLs
    assert "baseline" in capsys.readouterr().err
