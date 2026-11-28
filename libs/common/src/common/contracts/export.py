"""Write the JSON Schema of every contract model (GEN-7).

``python -m common.contracts.export``          write ``libs/common/schemas/*.json``
``python -m common.contracts.export --check``  exit 1 if those files are stale (CI)
"""

import argparse
import inspect
import json
import sys
from pathlib import Path

from pydantic import BaseModel

from common import contracts
from common.contracts.base import ContractModel

# valid because the package is installed editable (repo checkout)
DEFAULT_DIR = Path(__file__).resolve().parents[3] / "schemas"


def models() -> dict[str, type[BaseModel]]:
    """``<file>.<Model>`` -> model class, for every model defined in a contract file."""
    found: dict[str, type[BaseModel]] = {}
    for module_name in contracts.__all__:
        module = getattr(contracts, module_name)
        for name, obj in inspect.getmembers(module, inspect.isclass):
            if (
                issubclass(obj, ContractModel)
                and obj is not ContractModel
                and obj.__module__ == module.__name__
            ):
                found[f"{module_name}.{name}"] = obj
    return found


def render() -> dict[str, str]:
    """File name -> file content."""
    return {
        f"{key}.json": json.dumps(model.model_json_schema(), indent=2, sort_keys=True) + "\n"
        for key, model in models().items()
    }


def stale(directory: Path) -> list[str]:
    """Names of files that are missing, outdated or left over."""
    expected = render()
    actual = {p.name: p.read_text(encoding="utf-8") for p in directory.glob("*.json")}
    return sorted(n for n in expected.keys() | actual.keys() if expected.get(n) != actual.get(n))


def write(directory: Path) -> int:
    directory.mkdir(parents=True, exist_ok=True)
    expected = render()
    for path in directory.glob("*.json"):
        if path.name not in expected:
            path.unlink()
    for name, content in expected.items():
        (directory / name).write_text(content, encoding="utf-8")
    return len(expected)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export contract JSON Schemas")
    parser.add_argument("--check", action="store_true", help="fail if files are stale")
    parser.add_argument("--out", type=Path, default=DEFAULT_DIR)
    args = parser.parse_args(argv)
    if args.check:
        names = stale(args.out)
        if names:
            print("stale schema files (run python -m common.contracts.export):", file=sys.stderr)
            print("\n".join(names), file=sys.stderr)
            return 1
        print("schemas up to date")
        return 0
    print(f"wrote {write(args.out)} schemas to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
