"""Enforces the market_intel isolation boundary via static analysis of
import statements, so it holds by construction rather than by
discipline - see docs/adr/0011-market-intelligence-layer.md.

Two directions are checked:
- `market_intel/*.py` never imports `tidemark.data.exchange`,
  `tidemark.context`, `tidemark.journal`, or `tidemark.replay`.
- Those same modules never import `tidemark.market_intel`.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "tidemark"

FORBIDDEN_FOR_MARKET_INTEL = (
    "tidemark.data.exchange",
    "tidemark.context",
    "tidemark.journal",
    "tidemark.replay",
)


def _imported_module_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _matches_any(name: str, prefixes: tuple[str, ...]) -> str | None:
    for prefix in prefixes:
        if name == prefix or name.startswith(prefix + "."):
            return prefix
    return None


def _research_engine_files() -> list[Path]:
    files = [SRC_ROOT / "data" / "exchange.py"]
    for package in ("context", "journal", "replay"):
        files.extend(sorted((SRC_ROOT / package).rglob("*.py")))
    return files


def test_market_intel_never_imports_the_research_engine() -> None:
    market_intel_files = sorted((SRC_ROOT / "market_intel").rglob("*.py"))
    assert market_intel_files, "expected market_intel package files to exist"

    violations = []
    for path in market_intel_files:
        for name in _imported_module_names(path):
            forbidden = _matches_any(name, FORBIDDEN_FOR_MARKET_INTEL)
            if forbidden is not None:
                violations.append(f"{path.relative_to(SRC_ROOT)} imports {name!r}")

    assert not violations, "market_intel isolation boundary violated:\n" + "\n".join(violations)


def test_research_engine_never_imports_market_intel() -> None:
    violations = []
    for path in _research_engine_files():
        for name in _imported_module_names(path):
            if name == "tidemark.market_intel" or name.startswith("tidemark.market_intel."):
                violations.append(f"{path.relative_to(SRC_ROOT)} imports {name!r}")

    assert not violations, "research engine imports market_intel:\n" + "\n".join(violations)


def test_market_intel_package_exists_and_is_nonempty() -> None:
    # Guards against the two tests above silently passing because the
    # package (or the research-engine directories) don't exist yet.
    assert (SRC_ROOT / "market_intel" / "__init__.py").is_file()
    assert (SRC_ROOT / "data" / "exchange.py").is_file()
    for package in ("context", "journal", "replay"):
        assert (SRC_ROOT / package).is_dir()
