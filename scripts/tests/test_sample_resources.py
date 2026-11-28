"""The resource sampler: parsing ``kubectl top``, the percentiles and the proposals."""

import contextlib
import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path

import pytest

import sample_resources as sampler

TOP = """\
scorer-656cd968cc-67sh2   llama      1998m   1180Mi
scorer-656cd968cc-67sh2   scorer     7m      46Mi
verdict-aaa-1             verdict    2m      40Mi
verdict-aaa-2             verdict    4m      44Mi
"""


def test_top_output_is_parsed_and_lines_without_figures_are_left_out() -> None:
    odd = "new-pod  x  <unknown>  <unknown>\nshort line\n\n"
    rows = sampler.parse_top(TOP + odd)
    assert rows[0] == ("scorer-656cd968cc-67sh2", "llama", 1998.0, 1180.0)
    assert len(rows) == 4


@pytest.mark.parametrize(("text", "value"), [("250m", 250), ("2", 2000), ("0m", 0)])
def test_cpu_units(text: str, value: float) -> None:
    assert sampler.millicores(text) == value


@pytest.mark.parametrize(("text", "value"), [("512Ki", 0.5), ("46Mi", 46), ("1.5Gi", 1536)])
def test_memory_units(text: str, value: float) -> None:
    assert sampler.mebibytes(text) == value


def test_percentiles_are_measured_values() -> None:
    values = [float(n) for n in range(1, 101)]
    assert (sampler.percentile(values, 0.5), sampler.percentile(values, 0.95)) == (50, 95)
    assert sampler.percentile([7.0], 0.95) == 7
    with pytest.raises(ValueError):
        sampler.percentile([], 0.5)


def test_requests_and_limits_follow_the_rule_and_replicas_count_together() -> None:
    summary = sampler.summarise(sampler.parse_top(TOP))
    llama = summary["llama"]
    assert llama["cpu_m"]["request"] == 2400  # 1998 * 1.2, up to the next 10m
    assert llama["cpu_m"]["limit"] == 3000  # 1998 * 1.5
    assert llama["memory_mi"]["request"] == 1424  # 1180 * 1.2 = 1416, up to the next 16Mi
    assert llama["memory_mi"]["limit"] == 1776
    assert summary["verdict"]["samples"] == 2 and summary["verdict"]["cpu_m"]["max"] == 4
    # an idle container still gets a request
    assert sampler.round_up(0, 10) == 10
    # an exact multiple stays: 100 * 1.2 is not rounded up by float noise
    assert sampler.round_up(100 * 1.2, 10) == 120


def test_cgroup_counters() -> None:
    out = "104857600\nusage_usec 5\nnr_periods 40\nnr_throttled 10\n"
    assert sampler.cgroup("p", "c", lambda _: out) == {
        "memory_peak_mi": 100.0,
        "cpu_periods": 40,
        "cpu_throttled_periods": 10,
    }
    assert sampler.cgroup("p", "c", lambda _: None) is None
    assert sampler.cgroup("p", "c", lambda _: "max\n") is None


def test_failed_calls_are_counted_and_sampling_goes_on() -> None:
    replies = iter([None, "", TOP, TOP])
    left = iter([True, True, True, False])
    samples, skipped = sampler.collect(
        0, lambda: next(left), lambda _: next(replies), lambda _: None
    )
    assert (len(samples), skipped) == (8, 2)


def fake_kubectl(args: Sequence[str]) -> str | None:
    return TOP if args[0] == "top" else "1048576\nnr_periods 2\nnr_throttled 1\n"


def test_the_command_is_sampled_and_its_exit_code_kept(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "samples.json"
    command = [sys.executable, "-c", "raise SystemExit(3)"]
    assert (
        sampler.main(["--interval", "0.01", "--out", str(out), "--", *command], fake_kubectl) == 3
    )
    report = json.loads(out.read_text())
    assert report["command_exit"] == 3 and report["containers"]["llama"]["samples"] >= 1
    assert len(report["containers"]["verdict"]["cgroup"]) == 2
    assert "| llama |" in capsys.readouterr().out


def test_no_sample_is_exit_2(tmp_path: Path) -> None:
    out = tmp_path / "samples.json"
    assert sampler.main(["--duration", "0", "--out", str(out)], lambda _: None) == 2
    assert not out.exists()


def test_a_command_that_cannot_start_is_exit_2(tmp_path: Path) -> None:
    assert sampler.main(["--", str(tmp_path / "missing")], fake_kubectl) == 2


def test_the_context_is_passed_to_every_call(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    class Done:
        returncode = 0
        stdout = ""

    def run(command: list[str], **_: object) -> Done:
        seen.append(command)
        return Done()

    monkeypatch.setattr(sampler.subprocess, "run", run)
    monkeypatch.setenv("KUBE_CONTEXT", "idea-validator")
    sampler.kubectl(["top", "pods"])
    assert seen[0][:5] == ["kubectl", "--context", "idea-validator", "-n", "idea-validator"]


# --- edge cases (spec Z11, A7) ---


@pytest.mark.parametrize(
    "line",
    [
        "error: Metrics API not available",
        "NAME  CONTAINER  CPU(cores)  MEMORY(bytes)",
        "p c 1m 5MB",
        "p c 1x 5Mi",
        "p c 1m ..Mi",
        "p c 1m 5Mi extra",
    ],
)
def test_odd_kubectl_lines_are_skipped(line: str) -> None:
    assert sampler.parse_top(line) == []


def test_output_without_a_figure_counts_as_a_skipped_call() -> None:
    samples, skipped = sampler.collect(0, lambda: False, lambda _: "No resources found\n")
    assert (samples, skipped) == ([], 1)


def test_containers_that_appear_or_vanish_mid_run_keep_their_own_sample_count() -> None:
    replies = iter(["a x 1m 1Mi\n", "a x 3m 3Mi\nb y 5m 5Mi\n", "b y 7m 7Mi\n"])
    left = iter([True, True, False])
    samples, skipped = sampler.collect(
        0, lambda: next(left), lambda _: next(replies), lambda _: None
    )
    summary = sampler.summarise(samples)
    assert skipped == 0 and {n: e["samples"] for n, e in summary.items()} == {"x": 2, "y": 2}


def test_a_pod_that_is_gone_has_no_kernel_counters_but_stays_in_the_table(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def gone(args: Sequence[str]) -> str | None:
        return TOP if args[0] == "top" else None

    assert sampler.main(["--duration", "0"], gone) == 0
    assert "| 1180 / 1180 / 1180 | - | - |" in capsys.readouterr().out


@pytest.mark.parametrize("out", ["", "\n", "12\nnr_periods x\n"])
def test_unreadable_kernel_counters_are_left_out(out: str) -> None:
    assert sampler.cgroup("p", "c", lambda _: out) is None


@pytest.mark.parametrize("outcome", ["missing", "hang", "failed"])
def test_a_kubectl_that_fails_gives_nothing(monkeypatch: pytest.MonkeyPatch, outcome: str) -> None:
    def run(command: list[str], **options: object) -> object:
        assert options["timeout"]
        if outcome == "missing":
            raise FileNotFoundError("kubectl")
        if outcome == "hang":
            raise sampler.subprocess.TimeoutExpired(command, 30)
        return sampler.subprocess.CompletedProcess(command, 1, "partial", "boom")

    monkeypatch.setattr(sampler.subprocess, "run", run)
    assert sampler.kubectl(["top", "pods"]) is None


def test_two_values_percentiles_and_rounding_boundaries() -> None:
    assert sampler.percentile([2.0, 1.0], 0.5) == 1 and sampler.percentile([2.0, 1.0], 0.95) == 2
    assert sampler.round_up(160.0, 16) == 160 and sampler.round_up(160.1, 16) == 176


def test_ctrl_c_stops_the_wrapped_command(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[subprocess.Popen[bytes]] = []
    real = subprocess.Popen

    def popen(command: list[str]) -> subprocess.Popen[bytes]:
        started.append(real(command))
        return started[-1]

    def interrupted(_: Sequence[str]) -> str | None:
        raise KeyboardInterrupt

    monkeypatch.setattr(sampler.subprocess, "Popen", popen)
    command = [sys.executable, "-c", "import time; time.sleep(30)"]
    with pytest.raises(KeyboardInterrupt):
        sampler.main(["--", *command], interrupted)
    assert started[0].wait(timeout=5) != 0


# --- BUGS (left failing on purpose, see the Z11 testing report) ---


def test_an_unwritable_out_still_shows_the_table_and_keeps_the_exit_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "missing-folder" / "samples.json"
    with contextlib.suppress(OSError):
        sampler.main(["--duration", "0", "--out", str(out)], fake_kubectl)
    assert "| llama |" in capsys.readouterr().out


def test_a_negative_interval_is_refused_before_the_command_starts(tmp_path: Path) -> None:
    """Today: the command starts, time.sleep raises, and the running command is killed."""
    command = [sys.executable, "-c", "import time; time.sleep(30)"]
    with pytest.raises(SystemExit) as stopped:
        sampler.main(["--interval", "-1", "--", *command], fake_kubectl)
    assert stopped.value.code == 2


def test_sigterm_does_not_leave_the_wrapped_command_running(tmp_path: Path) -> None:
    kubectl = tmp_path / "kubectl"
    kubectl.write_text("#!/bin/sh\necho 'p c 1m 1Mi'\n")
    kubectl.chmod(0o755)
    pidfile = tmp_path / "pid"
    child = (
        "import os, pathlib, time; "
        f"pathlib.Path({str(pidfile)!r}).write_text(str(os.getpid())); time.sleep(20)"
    )
    done = subprocess.Popen(
        [sys.executable, sampler.__file__, "--interval", "0.05", "--", sys.executable, "-c", child],
        env=os.environ | {"PATH": f"{tmp_path}:{os.environ['PATH']}"},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    pid = 0
    try:
        deadline = time.monotonic() + 10
        while not (pidfile.exists() and pidfile.read_text()) and time.monotonic() < deadline:
            time.sleep(0.05)
        pid = int(pidfile.read_text())
        done.send_signal(signal.SIGTERM)
        done.wait(timeout=10)
        deadline = time.monotonic() + 3
        while Path(f"/proc/{pid}").exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not Path(f"/proc/{pid}").exists(), "the load keeps running without its sampler"
    finally:
        done.kill()
        if pid and Path(f"/proc/{pid}").exists():
            os.kill(pid, signal.SIGKILL)
