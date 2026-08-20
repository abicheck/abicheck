#!/usr/bin/env python3
"""Engine/CLI dependency-direction gate (Phase 0 of
docs/contribute/plans/duplication-and-convergence-assessment.md).

A leaf module imported by ``check_ai_readiness.py``, which registers
:func:`check_engine_cli_boundary` as the ``engine-cli-boundary`` check. Split
out rather than added inline because ``check_ai_readiness.py`` is already
past the 2000-line hard cap and only stays green through
``LARGE_FILE_ALLOWLIST`` -- which AGENTS.md is explicit is "not a way to
silently exempt a new file" -- mirroring the same extraction
``adr_status_sync.py`` already established for the ADR-status gate.

Pure-stdlib, like its caller, so it can run as the first CI step before
``pip install``.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Protocol

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "abicheck"


class Findings(Protocol):
    """The error/warning sink check_ai_readiness.py passes in."""

    def err(self, check: str, msg: str) -> None:
        """Record a blocking finding under `check`."""
        ...

    def warn(self, check: str, msg: str) -> None:
        """Record a non-blocking finding under `check`."""
        ...


def _rel(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""


# Engine-layer trees: the shared compare/scan engine, the Tier-2 service
# layer, the (not-yet-existing) artifact-application service, and the
# build-source evidence package. None of these may import ``click`` or a
# ``cli_*`` sibling module — that's a CLI concept leaking into a layer both
# the CLI and the typed Python API depend on, which is exactly the
# inversion `scan_engine.py` importing `click` and raising
# `click.ClickException` already demonstrates. A frontend (`cli*.py`,
# `compat/cli.py`) is on the *other* side of this boundary and is
# deliberately not covered here — it may import engine modules freely.
_ENGINE_MODULE_BASENAMES: frozenset[str] = frozenset({"scan_engine.py"})


def _is_engine_module(rel: str) -> bool:
    """True if *rel* (posix, relative to repo root) is in the engine layer.

    ``abicheck/scan_engine.py``, every ``abicheck/service*.py``, every
    ``abicheck/artifact_*.py`` (the artifact-application service Phase 1 of
    the convergence plan introduces — doesn't exist yet, covered pre-
    emptively so this predicate doesn't need a second edit the day it
    lands), and every ``abicheck/buildsource/**/*.py`` — see this module's
    own docstring comment for why these trees specifically.
    """
    if not rel.startswith("abicheck/"):
        return False
    tail = rel[len("abicheck/") :]
    if tail in _ENGINE_MODULE_BASENAMES:
        return True
    if "/" not in tail and tail.endswith(".py"):
        basename = tail[: -len(".py")]
        if basename.startswith("service") or basename.startswith("artifact_"):
            return True
    if tail.startswith("buildsource/") and tail.endswith(".py"):
        return True
    return False


# Sites deliberately exempted — a real, already-existing inversion this
# check's own baseline records rather than hides, per Phase 0's
# "allowlist-and-shrink" design (mirrors `IMPORT_CYCLE_ALLOWLIST`'s
# philosophy): the list must only shrink, and a new entry needs the same
# review bar as extending that allowlist does (CLAUDE.md's "Don't extend
# IMPORT_CYCLE_ALLOWLIST..." rule applies here identically). See
# docs/contribute/plans/duplication-and-convergence-assessment.md's
# "P1 — Dependency direction and CLI leakage" section and its Phase 1 for
# what closes each of these.
#
# Keyed `"<rel>::<violation-description>::<occurrence>"`, NOT
# `"<rel>:<lineno>"`: a line-number key breaks on any unrelated edit above
# the import (a docstring tweak, a new helper function) even though the
# violation itself hasn't moved, which would force a routine, unrelated
# edit to this file to also touch this allowlist. `occurrence` is the
# violation's 1-based rank among identically-described violations in the
# same file, in top-to-bottom (line) order — stable unless a new,
# identically-shaped import is inserted earlier in the same file, which is
# the one case where re-numbering is actually correct (see
# `service_scan.py`'s three `from .cli_scan_baseline import ...` entries
# below — a real case this format has to disambiguate).
ENGINE_CLI_BOUNDARY_ALLOWLIST: frozenset[str] = frozenset(
    {
        "abicheck/buildsource/evidence_policy.py::import click::1",
        "abicheck/scan_engine.py::import click::1",
        "abicheck/scan_engine.py::from .cli_scan_baseline import ...::1",
        "abicheck/scan_engine.py::from .cli_scan_helpers import ...::1",
        "abicheck/scan_engine.py::from .cli_buildsource import ...::1",
        "abicheck/service_compare_pipeline.py::from .cli_buildsource import ...::1",
        "abicheck/service_dump_pipeline.py::from .cli_dump_helpers import ...::1",
        "abicheck/service_input_resolution.py::"
        "from .cli_buildsource_helpers import ...::1",
        "abicheck/service_input_resolution.py::import click::1",
        "abicheck/service_input_resolution.py::from .cli_buildsource import ...::1",
        "abicheck/service_input_resolution.py::from .cli_dump_helpers import ...::1",
        "abicheck/service_scan.py::import click::1",
        "abicheck/service_scan.py::from .cli_scan_baseline import ...::1",
        "abicheck/service_scan.py::from .cli_scan_receipt import ...::1",
        "abicheck/service_scan.py::from .cli_scan_baseline import ...::2",
        "abicheck/service_scan.py::from .cli_scan_baseline import ...::3",
    }
)


def _is_cli_component(name: str) -> bool:
    """Is *name* — one dotted-path component, or an imported alias's own
    name — a CLI-frontend module spelling? Matches both the top-level
    ``cli_*.py`` sibling family (``cli_dump_helpers``, ...) and a bare
    ``cli`` component, which catches a *nested* CLI adapter package like
    ``abicheck/compat/cli.py`` (imported as `.compat.cli`/`abicheck.compat
    .cli`/`from .compat import cli`) that the top-level-only ``cli_*``
    pattern would otherwise miss entirely."""
    return name == "cli" or name.startswith("cli_")


def _alias_is_real_submodule(base_dir: Path, alias_name: str) -> bool:
    """Does *alias_name* actually name a submodule file/package under
    *base_dir* on disk — as opposed to an ordinary symbol (function,
    constant, class) that merely happens to be *spelled* like a CLI module?
    Only a genuine submodule import creates the engine-to-CLI-frontend
    dependency this check exists to catch: ``from .model import
    cli_default`` importing a plain constant named ``cli_default`` is not a
    CLI dependency at all, even though the imported name starts with
    ``cli_``. A dotted *module path* (`mod` itself, e.g. the `.compat.cli`
    in `from .compat.cli import x`) needs no such check — Python requires
    every component of a dotted import path to already be a real
    module/package, so that part is unambiguous by construction; it's only
    the *trailing* imported name in `from X import name` that could be
    either a submodule or an ordinary symbol."""
    return (base_dir / f"{alias_name}.py").is_file() or (
        base_dir / alias_name / "__init__.py"
    ).is_file()


def _relative_import_base_dir(rel: str, level: int, mod: str) -> Path:
    """Directory a relative ``from`` import's *mod* (possibly empty)
    resolves to on disk, treating every engine module here as an ordinary
    module file rather than a package's own ``__init__.py`` — level 1
    resolves within the importing file's own directory, and each
    additional level walks up one more parent, mirroring Python's own
    relative-import semantics."""
    base = ROOT / Path(rel).parent
    for _ in range(level - 1):
        base = base.parent
    if mod:
        base = base.joinpath(*mod.split("."))
    return base


def _engine_boundary_violations(
    node: ast.Import | ast.ImportFrom, rel: str
) -> list[str]:
    """Return one human description per prohibited alias *node* imports
    (``click`` or a ``cli``/``cli_*`` sibling — top-level or nested, e.g.
    ``abicheck.compat.cli``), possibly more than one — an import statement
    can name several targets on one line (`import click, abicheck.cli_new`;
    `from abicheck import cli_a, cli_b`), and each prohibited one is its own
    violation; returning only the first would let a second, added alias
    ride along unflagged forever, silently absorbed into whichever
    description the allowlist already recognizes. Checks every scope
    (module- and function-level — most of these are deliberately lazy
    imports), matching this check's own "no engine module may import click
    or a CLI frontend" rule regardless of where in the file the import
    sits, and regardless of how deep the CLI module is nested (a
    ``cli_*.py`` sibling of ``cli.py``, or a ``cli.py`` living inside a
    sub-package like ``compat/``). *rel* (the importing file's own
    repo-relative path) is only used to resolve a relative import's base
    directory when verifying a trailing imported *alias* actually names a
    real submodule on disk (see ``_alias_is_real_submodule``) — a dotted
    module path never needs that check, since Python itself guarantees
    every component of one is a real module."""
    if isinstance(node, ast.Import):
        found = []
        for alias in node.names:
            parts = alias.name.split(".")
            if parts[0] == "click":
                found.append(f"import {alias.name}")
            # `import abicheck.cli_dump_helpers` / `import
            # abicheck.compat.cli` (also catches a bare `... as X`, which
            # still binds the whole dotted path). Any component after
            # `abicheck`, not just the first, since a nested CLI adapter's
            # own cli-ness can show up several segments deep. No submodule
            # check needed: every component of a dotted `import a.b.c` is
            # already, by Python's own import semantics, a real module.
            elif parts[0] == "abicheck" and any(
                _is_cli_component(p) for p in parts[1:]
            ):
                found.append(f"import {alias.name}")
        return found
    # ast.ImportFrom
    mod = node.module or ""
    mod_components = mod.split(".") if mod else []
    if node.level >= 1:
        # Relative import: `from .cli_xxx import ...`, `from .compat.cli
        # import ...`, `from . import cli_xxx`, or `from .compat import
        # cli` (a nested adapter reached via an imported alias, not a
        # dotted `mod` component).
        if any(_is_cli_component(c) for c in mod_components):
            return [f"from {'.' * node.level}{mod} import ..."]
        base_dir = _relative_import_base_dir(rel, node.level, mod)
        return [
            f"from {'.' * node.level}{mod} import {alias.name}"
            for alias in node.names
            if _is_cli_component(alias.name)
            and _alias_is_real_submodule(base_dir, alias.name)
        ]
    # Absolute import.
    if mod == "click" or mod.startswith("click."):
        return [f"from {mod} import ..."]
    if "abicheck" in mod_components:
        # `from abicheck.compat.cli import main` / `from abicheck import
        # cli_dump_helpers` — a dotted component naming the CLI module, or
        # (when `mod` itself has no such component, e.g. `from
        # abicheck.compat import cli`) an imported alias naming it instead.
        if any(_is_cli_component(c) for c in mod_components if c != "abicheck"):
            return [f"from {mod} import ..."]
        base_dir = ROOT.joinpath(*mod_components)
        found = [
            f"from {mod} import {alias.name}"
            for alias in node.names
            if _is_cli_component(alias.name)
            and _alias_is_real_submodule(base_dir, alias.name)
        ]
        if found:
            return found
    return []


def _engine_boundary_sites(tree: ast.Module, rel: str) -> list[tuple[str, int, str]]:
    """Return one ``(allowlist_key, lineno, description)`` per boundary
    violation found in *tree* (already parsed from the file at *rel*).

    ``allowlist_key`` is the stable `"<rel>::<desc>::<occurrence>"` form
    `ENGINE_CLI_BOUNDARY_ALLOWLIST` is keyed by — see that constant's own
    comment for why occurrence-within-file rather than line number.
    ``lineno``/``description`` are kept separately so a real, unallowlisted
    violation can still be reported with its actual source location.
    """
    matches: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        for desc in _engine_boundary_violations(node, rel):
            matches.append((node.lineno, desc))
    matches.sort(key=lambda m: m[0])
    occurrence: dict[str, int] = {}
    sites: list[tuple[str, int, str]] = []
    for lineno, desc in matches:
        occurrence[desc] = occurrence.get(desc, 0) + 1
        sites.append((f"{rel}::{desc}::{occurrence[desc]}", lineno, desc))
    return sites


def check_engine_cli_boundary(f: Findings) -> None:
    """ERROR if an engine-layer module (`scan_engine.py`, `service*.py`,
    `artifact_*.py`, `buildsource/**/*.py`) imports `click` or a `cli_*`
    sibling module.

    The CLI is a frontend adapter over the engine/service layer, not the
    other way around — an engine module that imports `click` or a `cli_*`
    helper has inverted that dependency, which is exactly what let
    `scan_engine.py` accumulate `click.ClickException`/`click.echo` calls
    the typed Python API path can't use either. New violations are rejected
    outright; the pre-existing ones are recorded in
    `ENGINE_CLI_BOUNDARY_ALLOWLIST` (Phase 0 of the duplication-and-
    convergence-assessment plan) rather than silently passing.
    """
    for path in sorted(PKG.rglob("*.py")):
        rel = _rel(path)
        if not _is_engine_module(rel):
            continue
        try:
            tree = ast.parse(_read(path), filename=rel)
        except SyntaxError:
            continue
        for key, lineno, desc in _engine_boundary_sites(tree, rel):
            if key in ENGINE_CLI_BOUNDARY_ALLOWLIST:
                continue
            f.err(
                "engine-cli-boundary",
                f"{rel}:{lineno}: engine module `{desc}` — engine/service/"
                "artifact/buildsource modules may not import click or a "
                "cli_* sibling (the CLI is a frontend adapter over the "
                "engine, not the reverse); see docs/contribute/plans/"
                "duplication-and-convergence-assessment.md's Phase 0/P1",
            )
