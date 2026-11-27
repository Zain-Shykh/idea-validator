"""Where reports go, and the guard that keeps made-up input from passing as a result."""

import json
import os
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = ROOT / "eval/results"
# input from one of these folders is made up by definition
_FAKE_FOLDERS = frozenset({"tests", "fixtures"})


class OutputError(Exception):
    """A report cannot be read or written."""


def git_sha() -> str:
    """The image sets GIT_SHA; a checkout asks git."""
    sha = os.environ.get("GIT_SHA", "")
    if not sha:
        try:
            sha = subprocess.run(
                ["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
                capture_output=True, text=True, timeout=10, check=True,
            ).stdout.strip()  # fmt: skip
        except (OSError, subprocess.SubprocessError):
            sha = ""
    # it becomes part of a file name
    return sha if re.fullmatch(r"[A-Za-z0-9._-]{1,64}", sha) else "unknown"


def made_up(path: Path) -> bool:
    resolved = path.resolve()
    inside = resolved.relative_to(ROOT).parts if resolved.is_relative_to(ROOT) else resolved.parts
    return bool(_FAKE_FOLDERS & set(inside))


def guard(path: Path, fake: bool) -> None:
    """A report made from fake input never lands where real results go."""
    if fake and path.resolve().is_relative_to(RESULTS_DIR):
        raise OutputError(f"{path}: made from fake input; write it somewhere else")


def write_report(folder: Path, name: str, report: Mapping[str, Any]) -> Path:
    """``<name>-<sha>.json``, with ``FAKE-`` in front of a fake one. Whole file or nothing."""
    fake = bool(report["fake"])
    path = folder / f"{'FAKE-' if fake else ''}{name}-{report['git_sha']}.json"
    guard(path, fake)
    write_text(path, json.dumps(report, indent=2, sort_keys=True) + "\n")
    return path


def write_text(path: Path, text: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    except OSError as exc:
        raise OutputError(f"cannot write {path}: {exc}") from exc


def read_report(path: Path) -> dict[str, Any]:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError) as exc:
        raise OutputError(f"cannot read the report {path}: {type(exc).__name__}") from exc
    if not isinstance(report, dict):
        raise OutputError(f"{path}: not a report")
    return report
