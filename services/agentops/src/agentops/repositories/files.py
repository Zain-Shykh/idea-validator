"""Trace records on disk (the ``traces`` volume): ``<root>/YYYY-MM-DD/<run_id>.jsonl``.

``run_id`` must already be checked (``context.safe_id``): it becomes a file name.
"""

import logging
import os
import tempfile
from collections.abc import Sequence
from datetime import UTC
from pathlib import Path

from pydantic import ValidationError

from common.contracts.core import TraceRecord

log = logging.getLogger("agentops.files")


class TraceFiles:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def append(self, run_id: str, records: Sequence[TraceRecord]) -> None:
        """One file per run and UTC day of the record. ``ts`` must be timezone-aware."""
        by_day: dict[str, list[str]] = {}
        for record in records:
            day = record.ts.astimezone(UTC).date().isoformat()
            by_day.setdefault(day, []).append(record.model_dump_json() + "\n")
        for day, lines in by_day.items():
            folder = self.root / day
            folder.mkdir(parents=True, exist_ok=True)
            with (folder / f"{run_id}.jsonl").open("a+b") as file:
                # a crash or full disk may have left half a line: never glue a record onto it
                if file.seek(0, os.SEEK_END):
                    file.seek(-1, os.SEEK_END)
                    if file.read(1) != b"\n":
                        file.write(b"\n")
                file.write("".join(lines).encode())

    def read(self, run_id: str) -> list[TraceRecord]:
        """One record per ``step_id`` (a re-sent one replaces the first), ordered by
        ``(ts, step_id)`` (D-003)."""
        records: dict[int, TraceRecord] = {}
        for path in sorted(self.root.glob(f"*/{run_id}.jsonl")):
            for line in path.read_bytes().split(b"\n"):
                if not line:
                    continue
                try:
                    record = TraceRecord.model_validate_json(line)
                except ValidationError:
                    log.warning("skipped an unreadable trace line in %s", path)
                    continue
                records[record.step_id] = record
        return sorted(records.values(), key=lambda r: (r.ts, r.step_id))

    def writable(self) -> bool:
        self.root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=self.root) as probe:
            probe.write(b"ok")
        return True
