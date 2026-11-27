#!/usr/bin/env python
"""Fail when a rubric, prompt or rule file changed without a new version (AO-3, AVOID-2).

``python scripts/check_registry.py``           compare the files with ``config/registry.lock.json``
``python scripts/check_registry.py --update``  record new versions (refused without a bump)

The versions are part of the verdict cache key, so a silent change would serve stale verdicts.
"""

import argparse
import json
import sys
from pathlib import Path

import yaml

from common.cache_keys import digest
from common.registry import RegistryError, registry_entries

ROOT = Path(__file__).resolve().parents[1]
LOCK = "config/registry.lock.json"

Lock = dict[str, dict[str, str]]


def snapshot(root: Path) -> Lock:
    """``<kind>/<name>`` -> version and content hash of every versioned file."""
    entries = registry_entries(root / "rubrics", root / "prompts", root / "config")
    state = {f"{e.kind}/{e.name}": {"version": e.version, "sha256": e.sha256} for e in entries}
    rules = yaml.safe_load((root / "config/verdict.yaml").read_text(encoding="utf-8"))
    weights_version = rules.get("weights_version")
    if not isinstance(weights_version, str) or "weights" not in rules:
        raise RegistryError("verdict.yaml: needs weights and weights_version")
    # hashed as parsed values: only a real weight change needs a new weights_version
    state["config/verdict"] |= {
        "weights_version": weights_version,
        "weights_sha256": digest(rules["weights"]),
    }
    return state


def _number(version: str) -> int:
    return int(version[1:])  # v3, r2, w1


def _unbumped(key: str, old: dict[str, str], new: dict[str, str]) -> list[str]:
    """Changes that may never be recorded."""
    problems = []
    pairs = [("sha256", "version")]
    if "weights_sha256" in old:
        pairs.append(("weights_sha256", "weights_version"))
    for content, version in pairs:
        before, after = _number(old[version]), _number(new[version])
        if after < before:
            problems.append(f"{key}: {version} went back from {old[version]} to {new[version]}")
        elif old[content] != new[content] and after == before:
            what = "weights" if content == "weights_sha256" else "content"
            problems.append(f"{key}: {what} changed but {version} is still {new[version]}")
    return problems


def compare(lock: Lock, current: Lock) -> tuple[list[str], list[str]]:
    """(changes without a version bump, differences that ``--update`` may record)"""
    refused: list[str] = []
    pending: list[str] = []
    for key in sorted(lock.keys() | current.keys()):
        if key not in lock:
            pending.append(f"{key}: not in the lock yet")
        elif key not in current:
            pending.append(f"{key}: in the lock but the file is gone")
        elif lock[key] != current[key]:
            problems = _unbumped(key, lock[key], current[key])
            refused += problems
            if not problems:
                pending.append(f"{key}: new version not recorded yet")
    return refused, pending


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check rubric, prompt and rule versions")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--update", action="store_true", help="record bumped versions")
    args = parser.parse_args(argv)
    path = args.root / LOCK
    try:
        current = snapshot(args.root)
        if path.exists():
            lock: Lock = json.loads(path.read_text(encoding="utf-8"))
        elif args.update:
            lock = {}
        else:
            raise RegistryError(f"{LOCK} is missing (create it with --update)")
        refused, pending = compare(lock, current)
    except (RegistryError, OSError, ValueError, KeyError) as exc:
        print(f"registry check failed: {exc}", file=sys.stderr)
        return 1
    if refused:
        print("bump the version of every changed file:", *refused, sep="\n  ", file=sys.stderr)
        return 1
    if args.update:
        path.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"recorded {len(current)} entries in {LOCK}")
        return 0
    if pending:
        print("lock out of date (run with --update):", *pending, sep="\n  ", file=sys.stderr)
        return 1
    print(f"registry ok: {len(current)} entries")
    return 0


if __name__ == "__main__":
    sys.exit(main())
