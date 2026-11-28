"""M11-1, VE-1: the engine is plain code. No model, no network, no clock, no randomness, no I/O."""

import ast
import sys
from pathlib import Path

import verdict.engine

ALLOWED = ("common.contracts.", "verdict.config", "collections.abc", "dataclasses")


def imported_modules(path: Path) -> set[str]:
    found = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            found.add(node.module or "." * node.level)
    return found


def test_engine_imports_no_llm_http_or_io_library() -> None:
    modules = imported_modules(Path(verdict.engine.__file__))
    assert modules and all(m.startswith(ALLOWED) for m in modules), sorted(modules)


def test_engine_uses_no_io_builtins() -> None:
    tree = ast.parse(Path(verdict.engine.__file__).read_text())
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert not names & {"open", "print", "input", "eval", "exec", "__import__"}
    assert "verdict.engine" in sys.modules
