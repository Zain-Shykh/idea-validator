#!/usr/bin/env python
"""Sample CPU and memory per container while a load runs, and propose requests and limits (K8S-5).

    KUBE_CONTEXT=idea-validator python scripts/sample_resources.py --out samples.json \
        -- python scripts/load_validate.py ...        # sample as long as the command runs
    KUBE_CONTEXT=idea-validator python scripts/sample_resources.py --duration 300

``request ≈ p50 + 20 %`` and ``limit ≈ p95 x 1.5``. They are proposals: a CPU figure close to the
current limit was cut off by it, and ``memory.peak`` (the container's own high-water mark since it
started, page cache included) shows peaks between two samples.

Exit code 2 when not one sample was taken; otherwise the wrapped command's code.
"""

import argparse
import json
import math
import os
import re
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

HEADROOM = 1.2
LIMIT_FACTOR = 1.5
CPU_STEP_M = 10
MEMORY_STEP_MI = 16
_UNITS_MI = {"Ki": 1 / 1024, "Mi": 1.0, "Gi": 1024.0}
Sample = tuple[str, str, float, float]  # pod, container, millicores, MiB
Kubectl = Callable[[Sequence[str]], str | None]


def kubectl(args: Sequence[str]) -> str | None:
    """The output, or None when the call failed. Context and namespace come from the environment."""
    context = os.environ.get("KUBE_CONTEXT")
    command = ["kubectl", *(["--context", context] if context else [])]
    command += ["-n", os.environ.get("NAMESPACE", "idea-validator"), *args]
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout if done.returncode == 0 else None


def millicores(text: str) -> float:
    return float(text[:-1]) if text.endswith("m") else float(text) * 1000


def mebibytes(text: str) -> float:
    match = re.fullmatch(r"([0-9.]+)(Ki|Mi|Gi)", text)
    if not match:
        raise ValueError(f"not a memory figure: {text}")
    return float(match[1]) * _UNITS_MI[match[2]]


def parse_top(text: str) -> list[Sample]:
    """``kubectl top pods --containers --no-headers``; a line without figures is left out."""
    found: list[Sample] = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 4:
            continue
        try:
            found.append((parts[0], parts[1], millicores(parts[2]), mebibytes(parts[3])))
        except ValueError:
            continue
    return found


def percentile(values: Sequence[float], share: float) -> float:
    """Nearest rank: always one of the measured values."""
    if not values:
        raise ValueError("no values")
    ordered = sorted(values)
    return ordered[max(0, math.ceil(share * len(ordered)) - 1)]


def round_up(value: float, step: int) -> int:
    return max(step, math.ceil(value / step - 1e-9) * step)


def summarise(samples: Sequence[Sample]) -> dict[str, dict[str, Any]]:
    """Per container name; the replicas of one Deployment count together."""
    containers: dict[str, list[Sample]] = {}
    for sample in samples:
        containers.setdefault(sample[1], []).append(sample)
    summary = {}
    for name, rows in sorted(containers.items()):
        entry: dict[str, Any] = {"samples": len(rows)}
        for key, index, step in (("cpu_m", 2, CPU_STEP_M), ("memory_mi", 3, MEMORY_STEP_MI)):
            values = [float(row[index]) for row in rows]
            p50, p95 = percentile(values, 0.5), percentile(values, 0.95)
            entry[key] = {
                "p50": p50,
                "p95": p95,
                "max": max(values),
                "request": round_up(p50 * HEADROOM, step),
                "limit": round_up(p95 * LIMIT_FACTOR, step),
            }
        summary[name] = entry
    return summary


def cgroup(pod: str, container: str, run: Kubectl) -> dict[str, float] | None:
    """cgroup v2 counters since the container started."""
    out = run(
        ["exec", pod, "-c", container, "--", "cat",
         "/sys/fs/cgroup/memory.peak", "/sys/fs/cgroup/cpu.stat"]
    )  # fmt: skip
    if out is None:
        return None
    lines = out.split("\n")
    stat: Mapping[str, str] = dict(line.split(" ", 1) for line in lines[1:] if " " in line)
    try:
        return {
            "memory_peak_mi": round(int(lines[0]) / 1024**2, 1),
            "cpu_periods": int(stat.get("nr_periods", 0)),
            "cpu_throttled_periods": int(stat.get("nr_throttled", 0)),
        }
    except ValueError:
        return None


def collect(
    interval: float,
    running: Callable[[], bool],
    run: Kubectl = kubectl,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[list[Sample], int]:
    """Until ``running`` says no. Returns the samples and how many calls gave none."""
    samples: list[Sample] = []
    skipped = 0
    while True:
        out = run(["top", "pods", "--containers", "--no-headers"])
        found = parse_top(out) if out else []
        samples += found
        skipped += not found
        if not running():
            return samples, skipped
        sleep(interval)


def table(summary: Mapping[str, Mapping[str, Any]]) -> str:
    rows = [
        "| Container | n | CPU m p50 / p95 / max | Memory Mi p50 / p95 / max"
        " | memory.peak Mi | throttled periods |",
        "|---|---|---|---|---|---|",
    ]
    for name, e in summary.items():
        cpu, mem, kernel = e["cpu_m"], e["memory_mi"], e.get("cgroup") or []
        peak = max((k["memory_peak_mi"] for k in kernel), default="-")
        throttled = (
            f"{sum(k['cpu_throttled_periods'] for k in kernel)}"
            f" of {sum(k['cpu_periods'] for k in kernel)}"
            if kernel
            else "-"
        )
        rows.append(
            f"| {name} | {e['samples']} | {cpu['p50']:g} / {cpu['p95']:g} / {cpu['max']:g}"
            f" | {mem['p50']:g} / {mem['p95']:g} / {mem['max']:g} | {peak} | {throttled} |"
        )
    return "\n".join(rows)


def main(argv: Sequence[str] | None = None, run: Kubectl = kubectl) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--interval", type=float, default=5)
    parser.add_argument("--duration", type=float, default=60, help="without a command")
    parser.add_argument("--out", type=Path, help="the summary and the samples, as JSON")
    parser.add_argument("command", nargs="*", help="after --: sample while it runs")
    args = parser.parse_args(argv)
    if not (args.interval > 0 and args.duration >= 0):
        parser.error("--interval must be above 0 and --duration not below it")
    child = None
    if args.command:
        try:
            child = subprocess.Popen(args.command)
        except OSError as exc:
            print(f"error: cannot start the command: {exc}", file=sys.stderr)
            return 2
    end = time.monotonic() + args.duration

    def running() -> bool:
        return child.poll() is None if child else time.monotonic() < end

    def stop(number: int, _: object) -> None:
        raise SystemExit(128 + number)

    # SIGTERM by default skips ``finally``, and the load would go on without its sampler
    previous = signal.signal(signal.SIGTERM, stop)
    try:
        samples, skipped = collect(args.interval, running, run)
    finally:
        signal.signal(signal.SIGTERM, previous)
        if child and child.poll() is None:
            child.terminate()
    code = child.wait() if child else 0
    if not samples:
        print(f"error: no sample in {skipped} tries (is metrics-server up?)", file=sys.stderr)
        return 2
    summary = summarise(samples)
    for pod, container in sorted({(s[0], s[1]) for s in samples}):
        if counters := cgroup(pod, container, run):
            summary[container].setdefault("cgroup", []).append({"pod": pod, **counters})
    print(table(summary))
    print(f"calls without a sample: {skipped}")
    if args.out:
        report = {"interval_s": args.interval, "skipped": skipped, "command_exit": code,
                  "containers": summary, "samples": samples}  # fmt: skip
        try:
            args.out.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
        except OSError as exc:
            print(f"error: cannot write {args.out}: {exc}", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
