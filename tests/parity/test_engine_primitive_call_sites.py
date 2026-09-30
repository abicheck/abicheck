# SPDX-License-Identifier: Apache-2.0
"""Call-site pin for the three analysis primitives ``compare()`` runs
automatically.

ADR-068 §1 stated the original defect by call site, not by import: the only
production caller of ``buildsource.cross_source_checks.run_crosschecks``,
``buildsource.pattern_facts.find_pattern_facts`` and
``buildsource.preprocessor_facts.collect_preprocessor_facts`` was
``scan_engine.py``, so ``compare`` could not reach any of them. Phase 2a/2b
gave each a ``compare``-side caller, and Phase 6 deleted ``scan_engine.py``.

What this module pins now is the resulting caller set, by a real AST scan
over every ``abicheck/**/*.py`` module (not a grep a comment could fool):
``run_crosschecks`` is called only from ``workflows/cross_source_evolution.py``
and the two lexical/preprocessor primitives only from
``workflows/pattern_preprocessor_scan.py``. Losing that caller would make
``compare`` silently stop running the check, and a surprise second caller is
a second pipeline; both fail here by name. The behavioral half lives in
``test_cross_source_checks_parity.py``, ``test_pattern_facts_behavior.py``
and ``test_preprocessor_facts_behavior.py``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_ABICHECK_ROOT = Path(__file__).resolve().parent.parent.parent / "abicheck"

#: function name -> (defining module, the exact production caller set).
_ENGINE_PRIMITIVES: dict[str, tuple[str, tuple[str, ...]]] = {
    "run_crosschecks": (
        "buildsource/cross_source_checks.py",
        ("workflows/cross_source_evolution.py",),
    ),
    "find_pattern_facts": (
        "buildsource/pattern_facts.py",
        ("workflows/pattern_preprocessor_scan.py",),
    ),
    "collect_preprocessor_facts": (
        "buildsource/preprocessor_facts.py",
        ("workflows/pattern_preprocessor_scan.py",),
    ),
}


def _posix(path: Path) -> str:
    """POSIX-style repo-relative spelling, independent of host OS separator
    (Windows CI reports ``abicheck\\checker.py`` from ``str(path)``)."""
    return path.as_posix()


def _module_dotted_name(path: Path) -> str:
    """Dotted module name for a file under the repo root, e.g.
    ``abicheck/buildsource/cross_source_checks.py`` -> ``abicheck.buildsource.cross_source_checks``."""
    parts = list(path.relative_to(_ABICHECK_ROOT.parent).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _resolve_from_import(current_module: str, node: ast.ImportFrom) -> str:
    """Absolute dotted module a ``from X import Y`` targets, X's own
    relativity (``node.level``) resolved against *current_module*."""
    if node.level == 0:
        return node.module or ""
    package_parts = current_module.split(".")[:-1]  # current module's own package
    # level=1 -> that package itself; level=2 -> its parent; etc.
    trim = node.level - 1
    base_parts = package_parts[: len(package_parts) - trim] if trim else package_parts
    base = ".".join(base_parts)
    return f"{base}.{node.module}" if node.module else base


def _bindings_for(
    tree: ast.AST, current_module: str, target_module: str, function_name: str
) -> tuple[set[str], set[str]]:
    """Local names in *tree* that resolve to ``target_module.function_name``.

    Returns ``(direct, module_aliases)``: ``direct`` names are bound to the
    function itself (``from target import function_name [as alias]``);
    ``module_aliases`` are bound to the *module* (``import target [as
    alias]``, ``from target's package import target [as alias]``), so a
    later ``alias.function_name(...)`` attribute call is still counted.
    Resolves relative imports (``from . import x`` / ``from .x import y``)
    against *current_module*'s own package -- a plain string/substring match
    would miss exactly the aliasing/qualified-call shapes this exists to
    catch (Codex review).
    """
    direct: set[str] = set()
    module_aliases: set[str] = set()
    target_parent, _, target_leaf = target_module.rpartition(".")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == target_module and alias.asname:
                    module_aliases.add(alias.asname)
        elif isinstance(node, ast.ImportFrom):
            resolved = _resolve_from_import(current_module, node)
            if resolved == target_module:
                for alias in node.names:
                    if alias.name == function_name:
                        direct.add(alias.asname or alias.name)
            elif resolved == target_parent:
                for alias in node.names:
                    if alias.name == target_leaf:
                        module_aliases.add(alias.asname or alias.name)
    return direct, module_aliases


def _targets() -> dict[str, str]:
    """Every ``(function_name -> defining module)`` pair the tables below
    audit, resolved in one place so the scan can answer all of them from a
    single traversal."""
    targets = {fn: mod for fn, (mod, _cs) in _ENGINE_PRIMITIVES.items()}
    return targets


#: Memoized result of the one repo traversal, keyed by nothing: the scan reads
#: `abicheck/**/*.py`, which no test in this process mutates.
_CALL_SITE_CACHE: dict[str, dict[Path, int]] | None = None


def _scan_every_target() -> dict[str, dict[Path, int]]:
    """One pass over ``abicheck/**/*.py``, resolving *every* audited primitive.

    Previously `_call_sites` re-read and re-parsed all ~790 modules of the
    package once per target function -- three full traversals for three
    parametrized tests asking about three names, at ~3.6s each. The scan is
    the same AST resolution as before (direct import, module-qualified call,
    either aliased, relative imports resolved); only the loop nesting is
    inverted, so each file is read and parsed once and every target is
    answered from that one tree.

    Deliberately **not** a retained repository AST: each tree is discarded as
    soon as its file has been scanned, so peak memory is one module rather
    than the whole package (measured at ~795 MiB if held, per worker).
    """
    targets = _targets()
    resolved = {
        fn: f"abicheck.{mod[:-3].replace('/', '.')}" for fn, mod in targets.items()
    }
    hits: dict[str, dict[Path, int]] = {fn: {} for fn in targets}

    for path in sorted(_ABICHECK_ROOT.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError):
            continue
        current_module = _module_dotted_name(path)
        bindings = {
            fn: _bindings_for(tree, current_module, target_module, fn)
            for fn, target_module in resolved.items()
        }
        if not any(direct or aliases for direct, aliases in bindings.values()):
            continue
        counts = {fn: 0 for fn in targets}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name):
                for fn, (direct, _aliases) in bindings.items():
                    if func.id in direct:
                        counts[fn] += 1
            elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                direct_aliases = bindings.get(func.attr)
                if direct_aliases is not None and func.value.id in direct_aliases[1]:
                    counts[func.attr] += 1
        rel = path.relative_to(_ABICHECK_ROOT.parent)
        for fn, count in counts.items():
            if count:
                hits[fn][rel] = count
    return hits


def _call_sites(function_name: str, defining_module: str) -> dict[Path, int]:
    """Repo-relative module -> number of *call expressions* resolving to
    ``function_name`` from *defining_module* (a repo-relative path, e.g.
    ``buildsource/cross_source_checks.py``) -- ``foo()`` after a direct import,
    ``mod.foo()``/``alias.foo()`` after a module import, an aliased import
    of either shape, or a relative import. Not a bare textual mention (a
    docstring, a comment, an unrelated ``foo`` in a different module).

    Answered from the one shared traversal above."""
    global _CALL_SITE_CACHE
    if _CALL_SITE_CACHE is None:
        _CALL_SITE_CACHE = _scan_every_target()
    assert _targets()[function_name] == defining_module, (
        f"{function_name} is audited as defined in {_targets()[function_name]!r}, "
        f"not {defining_module!r}"
    )
    return dict(_CALL_SITE_CACHE[function_name])


@pytest.mark.repo_scan
@pytest.mark.parametrize("function_name", sorted(_ENGINE_PRIMITIVES))
def test_primitive_has_exactly_the_expected_callers(function_name: str) -> None:
    """Phase 2b (plan §3 #6/#8): `compare()` reaches both primitives via
    `workflows/pattern_preprocessor_scan.py` -- pins the exact set so a
    regression (losing that caller) or a surprise second caller both fail
    loudly. Used to also allow `scan_engine.py` as a caller; deleted with
    the `scan` command itself (ADR-068 Phase 6)."""
    defining_module, expected_callers = _ENGINE_PRIMITIVES[function_name]
    expected = {f"abicheck/{m}" for m in expected_callers}

    sites = _call_sites(function_name, defining_module)
    sites = {p: n for p, n in sites.items() if p != Path("abicheck") / defining_module}
    callers = {_posix(p) for p in sites}
    assert callers == expected, (
        f"{function_name}() caller set changed -- expected exactly "
        f"{sorted(expected)}, found {sorted(callers)}"
    )


@pytest.mark.parametrize(
    "source",
    [
        "from abicheck.buildsource.cross_source_checks import run_crosschecks\n"
        "run_crosschecks(snap)\n",
        "from abicheck.buildsource.cross_source_checks import run_crosschecks as check\n"
        "check(snap)\n",
        "from abicheck.buildsource import cross_source_checks\n"
        "cross_source_checks.run_crosschecks(snap)\n",
        "from abicheck.buildsource import cross_source_checks as cc\ncc.run_crosschecks(snap)\n",
        "import abicheck.buildsource.cross_source_checks as cc\ncc.run_crosschecks(snap)\n",
        "from .buildsource.cross_source_checks import run_crosschecks\n"  # relative,
        # the shape a top-level abicheck/ module uses one level above the
        # buildsource/ package (as the retired scan_engine.py did)
        "run_crosschecks(snap)\n",
    ],
)
def test_bindings_for_resolves_every_aliasing_and_qualified_call_shape(
    source: str,
) -> None:
    """Codex review: a bare ``ast.Name`` match alone misses ``cross_source_checks.
    run_crosschecks()`` and an aliased import -- both real Python a new
    caller could legitimately write. Exercises every shape ``_bindings_for``
    claims to resolve, each as its own module so a caller written from
    ``abicheck`` itself (the relative-import-into-a-subpackage case, as the retired
    ``scan_engine.py`` did) and every other shape are both
    covered."""
    tree = ast.parse(source)
    current_module = "abicheck.scan_engine"
    direct, module_aliases = _bindings_for(
        tree,
        current_module,
        "abicheck.buildsource.cross_source_checks",
        "run_crosschecks",
    )
    calls = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in direct:
            calls += 1
        elif (
            isinstance(func, ast.Attribute)
            and func.attr == "run_crosschecks"
            and isinstance(func.value, ast.Name)
            and func.value.id in module_aliases
        ):
            calls += 1
    assert calls == 1, source
