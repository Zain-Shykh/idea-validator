"""Where reports go, and the guard that keeps made-up input from passing as a result."""

import json
import os
import re
import subprocess
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

import eval_paths  # noqa: F401  (must come before the service imports)
from agentops.repositories.index import IndexRepository
from agentops.settings import AgentOpsSettings
from common.contracts.ops import Report

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


def database() -> IndexRepository:
    """The ``ops`` database, from the same environment M13 reads."""
    url = AgentOpsSettings().db_url()
    if url is None:
        raise OutputError("no database configured (POSTGRES_HOST or AGENTOPS_DATABASE_URL)")
    return IndexRepository(url)


def store(report: Mapping[str, Any], path: Path) -> None:
    """Into ``eval_runs``, where M13 reads the latest one. Fake reports are never stored."""
    if report["fake"]:
        raise OutputError("a fake report is not stored in eval_runs")
    index = database()
    try:
        index.add_report(
            Report(
                kind=report["kind"],
                git_sha=report["git_sha"],
                created_at=datetime.fromisoformat(report["created_at"]),
                metrics=report["metrics"],
                path=path.name,
            )
        )
    except SQLAlchemyError as exc:
        # the type only: database errors quote the values they were given
        raise OutputError(f"cannot store the report: {type(exc).__name__}") from exc
    finally:
        index.dispose()
