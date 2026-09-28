"""Enforces the market_intel isolation boundary via static analysis of
import statements, so it holds by construction rather than by
discipline - see docs/adr/0011-market-intelligence-layer.md.

Two directions are checked:
- `market_intel/*.py` never imports `tidemark.context`, `tidemark.journal`,
  or `tidemark.replay`, and - with exactly ONE explicit exception -
  never anything under `tidemark.data` either.
- Those same modules never import `tidemark.market_intel`.

THE EXCEPTIONS (Merge 3, see the ADR's "Addendum: Merge 3"; extended for
"intel distributions", see "Addendum: intel distributions"):
`tidemark.data.models` (a plain ORM data module with no imports of its
own back into the research engine) may be imported, for exactly two
read-only purposes: a lookup of BTC's stored Section 1 result
(`JournalEntry` - the table `tidemark run` actually writes; an earlier
version of this read `ContextRecord`/`context_records`, a table nothing
in production writes to, until an audit caught it) in `context_read.py`,
and a lookup of the latest universe snapshot's selected symbols
(`UniverseSnapshot`/`UniverseSnapshotRow`) in `universe_read.py`.
Nothing else under `tidemark.data` is permitted, and no other file may
import even `tidemark.data.models`, which is a deliberate TIGHTENING of
the boundary versus a plain "tidemark.data.exchange is forbidden" rule:
`tidemark.data.store`, for instance, is not named anywhere in CLAUDE.md's
forbidden list, but its `TidemarkStore` transitively imports
`tidemark.data.exchange` to type-hint candle fetching - importing it
here would smuggle a forbidden import in through the back door. An
allowlist of exactly one submodule, read by exactly two named files,
closes that gap and any other one like it, present or future.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "tidemark"

FORBIDDEN_MODULE_PREFIXES = (
    "tidemark.context",
    "tidemark.journal",
    "tidemark.replay",
)
ALLOWED_DATA_SUBMODULE = "tidemark.data.models"


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


def _is_allowed_data_import(name: str) -> bool:
    return name == ALLOWED_DATA_SUBMODULE or name.startswith(ALLOWED_DATA_SUBMODULE + ".")


def _boundary_violation(name: str) -> bool:
    if name == "tidemark.data" or name.startswith("tidemark.data."):
        return not _is_allowed_data_import(name)
    return any(
        name == prefix or name.startswith(prefix + ".") for prefix in FORBIDDEN_MODULE_PREFIXES
    )


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
            if _boundary_violation(name):
                violations.append(f"{path.relative_to(SRC_ROOT)} imports {name!r}")

    assert not violations, "market_intel isolation boundary violated:\n" + "\n".join(violations)


ALLOWED_DATA_IMPORTERS = {"context_read.py", "universe_read.py"}


def test_only_the_two_named_files_import_the_one_permitted_data_module() -> None:
    """The allowlist above permits `tidemark.data.models` package-wide,
    but in practice only `context_read.py` and `universe_read.py` should
    ever need it - this test keeps that true rather than merely possible.
    """
    market_intel_files = sorted((SRC_ROOT / "market_intel").rglob("*.py"))

    importers = {
        path.name
        for path in market_intel_files
        if any(_is_allowed_data_import(name) for name in _imported_module_names(path))
    }

    assert importers == ALLOWED_DATA_IMPORTERS, (
        f"expected only {ALLOWED_DATA_IMPORTERS} to import {ALLOWED_DATA_SUBMODULE!r}, "
        f"found: {importers}"
    )


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


def test_the_telegram_bot_files_are_covered_by_the_boundary_scan() -> None:
    # The boundary tests above scan `market_intel/*.py` via rglob, so
    # they already cover any new file added under the package - this
    # test exists only to make that coverage claim concrete for the
    # Merge 2 bot files specifically (per that merge's own instruction
    # to "extend the existing import-boundary test to cover it"), so the
    # claim can't silently go stale if one of these files is ever
    # renamed or removed.
    market_intel_dir = SRC_ROOT / "market_intel"
    for filename in ("bot.py", "telegram_client.py", "telegram_render.py", "bot_state.py"):
        assert (market_intel_dir / filename).is_file()


def test_the_context_read_exception_file_exists_and_never_imports_tidemark_context() -> None:
    # Same purpose as the test above, for the Merge 3 boundary exception
    # specifically: names the one file the exception applies to, and
    # double-checks (redundantly with the general scan, deliberately)
    # that it never imports the evaluation logic it's reading a stored
    # result of.
    path = SRC_ROOT / "market_intel" / "context_read.py"
    assert path.is_file()
    names = _imported_module_names(path)
    assert not any(
        name == "tidemark.context" or name.startswith("tidemark.context.") for name in names
    )
    assert not any(
        name == "tidemark.data.store" or name.startswith("tidemark.data.store.") for name in names
    )


def test_the_universe_read_exception_file_exists_and_never_imports_symbol_source() -> None:
    # Same purpose as the test above, for the "intel distributions"
    # boundary exception: names the file, and double-checks it never
    # imports `data.symbol_source` or `data.store` - it must read the
    # snapshot itself, never the TIDEMARK_SYMBOLS-fallback resolution
    # logic those modules carry.
    path = SRC_ROOT / "market_intel" / "universe_read.py"
    assert path.is_file()
    names = _imported_module_names(path)
    assert not any(
        name == "tidemark.data.store" or name.startswith("tidemark.data.store.") for name in names
    )
    assert not any(
        name == "tidemark.data.symbol_source" or name.startswith("tidemark.data.symbol_source.")
        for name in names
    )


def test_the_distributions_file_is_covered_by_the_boundary_scan() -> None:
    # Same purpose as the telegram-bot-files test above, for the "intel
    # distributions" files specifically.
    market_intel_dir = SRC_ROOT / "market_intel"
    for filename in ("distributions.py", "universe_read.py"):
        assert (market_intel_dir / filename).is_file()


def test_the_coin_context_cache_files_are_covered_by_the_boundary_scan() -> None:
    # Same purpose as the telegram-bot-files test above, for the "/coin
    # universe context" files (Phase 2) specifically. Neither imports
    # tidemark.data at all - the cache is market_intel's own table, like
    # evaluation_store.py's - so they need no new allowlist entry, only
    # confirmation the general scan actually covers them.
    market_intel_dir = SRC_ROOT / "market_intel"
    for filename in ("universe_context_store.py", "coin_universe_context.py"):
        assert (market_intel_dir / filename).is_file()
