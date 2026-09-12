#!/usr/bin/env python3
"""mypy-override-targets gate: every ``[[tool.mypy.overrides]]`` target resolves.

A leaf module imported by ``check_ai_readiness.py``, which registers
:func:`check_mypy_override_targets` as the ``mypy-override-targets`` check.
Split out rather than added inline because ``check_ai_readiness.py`` is already
past the 2000-line hard cap (same reasoning as ``adr_status_sync.py``'s own
docstring records).

**Why this exists.** A per-module mypy override names a module by dotted path,
and mypy silently ignores an override whose target does not exist. So when a
module is deleted or moved, its override outlives it with no signal anywhere:
the gate passes, ``mypy abicheck/`` passes, and the only cost is an
accumulating list of stale suppressions plus the *comments* attached to them,
which then describe modules a reader cannot find. A 2026-09 cleanup audit
found eleven such entries at once -- eight ``cli_*`` modules, plus
``cli_scan``/``cli_scan_baseline`` and ``workflows.scan_config``, the last
three left behind when ADR-068 Phase 6 deleted the ``scan`` command and its
scan-only modules.

Two failure modes, because an override can be stale in two different ways:

1. An **exact** target (no ``*``) naming neither a module file nor a package.
2. A **wildcard** target (``abicheck.foo.*``) matching nothing at all -- which
   is the same defect one indirection further out, and the reason this check
   does not simply skip every pattern it cannot resolve literally.

Third-party targets are deliberately out of scope: an override for ``yaml`` or
``elftools`` is about a dependency that may or may not be installed in the
environment running mypy, so "does not resolve here" proves nothing. Only
first-party (``abicheck.*``) targets are checked, since those resolve from the
tree itself.

Pure-stdlib, like its caller, so it can run as the first CI step before
``pip install``. ``tomllib`` is stdlib only from Python 3.11; the canonical
CI lane for this gate pins 3.13 (AGENTS.md), and an older interpreter gets a
warning rather than a hard failure -- the safe direction, matching
``check_mutation_score.py``'s own tomllib note.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Protocol

ROOT = Path(__file__).resolve().parent.parent

#: The one first-party import root whose override targets are checked. Kept as
#: a local literal to keep this module a leaf; the package directory is the
#: source of truth for what resolves, so there is nothing to keep in sync.
FIRST_PARTY_TOP_LEVEL = "abicheck"


class Findings(Protocol):
    def err(self, check: str, msg: str) -> None: ...
    def warn(self, check: str, msg: str) -> None: ...


def _module_names(package_root: Path, top_level: str) -> frozenset[str]:
    """Every dotted module and package name importable under *package_root*.

    Both are collected, because an override may legally target either a module
    (``abicheck.cli_project``) or a package (``abicheck.workflows``).
    """
    names: set[str] = {top_level}
    for path in package_root.rglob("*.py"):
        rel = path.relative_to(package_root)
        parts = list(rel.parts)
        if parts[-1] == "__init__.py":
            parts.pop()
        else:
            parts[-1] = parts[-1][: -len(".py")]
        if any(part.startswith(".") for part in parts):
            continue
        # Record the module itself and every package above it, so a target
        # naming an intermediate package resolves even if that package holds
        # no `__init__.py` of its own.
        for cut in range(len(parts) + 1):
            names.add(".".join([top_level, *parts[:cut]]).rstrip("."))
    return frozenset(names)


def _override_targets(doc: dict) -> list[str]:
    """Flatten every ``[[tool.mypy.overrides]]`` ``module`` value to a list.

    A ``module`` key is either a single string or a list of strings; both
    spellings appear in this repo's own ``pyproject.toml``.
    """
    overrides = doc.get("tool", {}).get("mypy", {}).get("overrides", [])
    targets: list[str] = []
    for override in overrides:
        module = override.get("module")
        if isinstance(module, str):
            targets.append(module)
        elif isinstance(module, list):
            targets.extend(m for m in module if isinstance(m, str))
    return targets


def stale_override_targets(
    pyproject: Path, package_root: Path, top_level: str | None = None
) -> list[tuple[str, str]]:
    """Return ``(target, reason)`` for each first-party override that resolves
    to nothing.

    Separated from :func:`check_mypy_override_targets` so a test can assert the
    resolution rules directly, against a synthetic tree, rather than only
    through this repo's own (expected-clean) ``pyproject.toml``.

    *top_level* defaults to :data:`FIRST_PARTY_TOP_LEVEL` read at *call* time,
    not bound as a def-time default: the caller below derives *package_root*
    from the same constant, and a def-time default would let the two disagree
    (the path following an override of the constant while the dotted prefix
    kept the original) -- which is exactly the split
    ``tests/test_mypy_override_targets.py`` found when it first exercised the
    check function against a synthetic tree.
    """
    import tomllib

    if top_level is None:
        top_level = FIRST_PARTY_TOP_LEVEL

    with pyproject.open("rb") as handle:
        doc = tomllib.load(handle)

    known = _module_names(package_root, top_level)
    prefix = f"{top_level}."
    stale: list[tuple[str, str]] = []
    for target in _override_targets(doc):
        if target != top_level and not target.startswith(prefix):
            continue  # third-party; see this module's docstring
        if "*" in target:
            if not any(fnmatch.fnmatchcase(name, target) for name in known):
                stale.append((target, "wildcard matches no module"))
        elif target not in known:
            stale.append((target, "no such module or package"))
    return stale


def check_mypy_override_targets(f: Findings) -> None:
    """Fail on a ``[[tool.mypy.overrides]]`` target that no longer exists."""
    pyproject = ROOT / "pyproject.toml"
    if not pyproject.exists():
        f.err("mypy-override-targets", "pyproject.toml not found")
        return
    try:
        stale = stale_override_targets(pyproject, ROOT / FIRST_PARTY_TOP_LEVEL)
    except ModuleNotFoundError:  # pragma: no cover - pre-3.11 interpreter
        f.warn(
            "mypy-override-targets",
            "tomllib unavailable (Python < 3.11); override targets unchecked",
        )
        return
    for target, reason in stale:
        f.err(
            "mypy-override-targets",
            f"[[tool.mypy.overrides]] targets {target!r} but {reason} -- "
            "delete the override (and its comment) along with the module",
        )
