# SPDX-License-Identifier: Apache-2.0
"""Call-site proof that the three scan-only analysis engines have exactly
one production caller: ``scan_engine.py``.

ADR-068 §1 states the capability-loss defect by call site, not by import:

    Verified by call site, not by import: the only production callers of
    ``buildsource.cross_source_checks.run_crosschecks``, ``buildsource.pattern_facts.
    find_pattern_facts`` and ``buildsource.preprocessor_facts.collect_preprocessor_facts``
    anywhere under ``abicheck/`` are ``scan_engine.py``... Check by call
    site, not import: ``workflows/extraction.py`` imports two of the three
    and calls neither.

This module makes that claim executable instead of only a prose citation --
a real AST scan over every ``abicheck/**/*.py`` module, not a grep that a
comment or an unrelated identifier could fool. It is the parity harness's
proof, for ``pattern_scan``/``preprocessor_scan`` (and a second angle on
``crosscheck``, alongside ``test_cross_source_checks_parity.py``'s behavioral one),
that ``compare``'s pipeline *cannot* reach them today -- not merely that it
happens not to in the fixtures exercised elsewhere.

Once Phase 2a/2b (docs/contribute/plans/one-comparison-product.md §6) gives
one of these functions a second caller reachable from `compare`, this test
starts failing -- which is the point: it is the signal to delete the
corresponding tests/parity/gaps.py entry in that same PR, *once the whole
group of checks a primitive backs has actually reached parity through
compare's real, user-facing entry point*.

**Documented, now-complete exception (ADR-068 D3/D4/D5 / plan P2):**
``abicheck/workflows/cross_source_evolution.py`` is a second, legitimate
caller of ``run_crosschecks`` -- the migration folds all eleven
``cross_source_checks.ALL_CHECKS`` entries into evolution-stated findings via
``compare()``'s own ``cross_source_checks`` keyword (``unversioned_exported_
symbol``/``private_header_leak`` first, then ``exported_not_public``/
``public_not_exported``/``rtti_for_internal_type``/``public_to_internal_
dependency``, then the remaining five). That keyword defaults to ``True``
and is reached automatically by every real front end (CLI, typed API,
Action) with no opt-in flag of any kind (D5 rejects "a flag that merely
enables useful analysis") -- which is exactly why none of the eleven checks
is registered in ``tests/parity/gaps.py`` any more; see
``test_cross_source_checks_parity.py``'s own positive coverage for all eleven.
``run_crosschecks`` therefore backs no remaining scan-only gap at all (its
entry below carries no ``gap_key``). Only the raw structural claim this
module checks -- "nothing but scan_engine.py (and, for `run_crosschecks`,
`cross_source_evolution.py`) calls the primitive at all" -- needed updating
to admit that one caller.

**``find_pattern_facts``/``collect_preprocessor_facts`` closed outright (Phase 2b,
plan §3 #6/#8):** unlike ``run_crosschecks``, neither primitive backs more
than one gap key, and this slice migrates the whole capability -- both are
now called from ``abicheck/workflows/pattern_preprocessor_scan.py``
(``compare()``'s own ``pattern_preprocessor_scan`` keyword, default
``True``, no opt-in flag), so neither is tracked in this module's
``_ENGINE_PRIMITIVES`` table any more -- there is no remaining scan-only
row either backs. Their `tests/parity/gaps.py` entries are deleted in the
same PR that adds this caller (this module's own docstring's rule, applied
for real this time rather than only demonstrated for `run_crosschecks`'s
partial case). ``EXPECTED_GAPS`` is now empty: every one of the originally
listed fifteen scan-only capabilities is closed.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from .gaps import EXPECTED_GAPS

_ABICHECK_ROOT = Path(__file__).resolve().parent.parent.parent / "abicheck"

#: function name -> (defining module, expected caller module(s), gap key).
#: `run_crosschecks`' own caller set used to also include `scan_engine.py`
#: (the "one production caller" this module's own docstring describes) --
#: deleted with the `scan` command itself (ADR-068 Phase 6), leaving
#: `workflows/cross_source_evolution.py` (the documented ADR-068 D3
#: `compare`-side caller) as the sole remaining one. Its gap key stays
#: `None` since every crosscheck-backed gap is closed (nothing left in
#: `EXPECTED_GAPS` for it to name).
_ENGINE_PRIMITIVES: dict[str, tuple[str, tuple[str, ...], str | None]] = {
    "run_crosschecks": (
        "buildsource/cross_source_checks.py",
        ("workflows/cross_source_evolution.py",),
        None,  # fully migrated -- no scan-only crosscheck gap remains
    ),
}


def _posix(path: Path) -> str:
    """POSIX-style repo-relative spelling, independent of host OS separator
    (Windows CI reports ``abicheck\\scan_engine.py`` from ``str(path)``)."""
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


def _call_sites(function_name: str, defining_module: str) -> dict[Path, int]:
    """Repo-relative module -> number of *call expressions* resolving to
    ``function_name`` from *defining_module* (a repo-relative path, e.g.
    ``buildsource/cross_source_checks.py``) -- ``foo()`` after a direct import,
    ``mod.foo()``/``alias.foo()`` after a module import, an aliased import
    of either shape, or a relative import. Not a bare textual mention (a
    docstring, a comment, an unrelated ``foo`` in a different module)."""
    target_module = f"abicheck.{defining_module[:-3].replace('/', '.')}"
    hits: dict[Path, int] = {}
    for path in sorted(_ABICHECK_ROOT.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError):
            continue
        current_module = _module_dotted_name(path)
        direct, module_aliases = _bindings_for(
            tree, current_module, target_module, function_name
        )
        count = 0
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name) and func.id in direct:
                count += 1
            elif (
                isinstance(func, ast.Attribute)
                and func.attr == function_name
                and isinstance(func.value, ast.Name)
                and func.value.id in module_aliases
            ):
                count += 1
        if count:
            hits[path.relative_to(_ABICHECK_ROOT.parent)] = count
    return hits


@pytest.mark.parametrize("function_name", sorted(_ENGINE_PRIMITIVES))
def test_only_scan_engine_calls_it(function_name: str) -> None:
    defining_module, expected_callers, gap_key = _ENGINE_PRIMITIVES[function_name]
    if gap_key is not None:
        assert gap_key in EXPECTED_GAPS, f"{gap_key!r} must be a registered parity gap"
    expected = {f"abicheck/{m}" for m in expected_callers}

    sites = _call_sites(function_name, defining_module)
    # Drop the defining module itself (recursive helpers / the module's own
    # internal dispatch don't count as an external caller).
    sites = {p: n for p, n in sites.items() if p != Path("abicheck") / defining_module}

    callers = {_posix(p) for p in sites}
    if callers - expected:
        phase_note = (
            f"({EXPECTED_GAPS[gap_key].plan_phase}), delete the "
            f"{gap_key!r} entry from tests/parity/gaps.py in the same PR "
            "instead of leaving this assertion to rot."
            if gap_key is not None
            else "-- a genuinely new caller, or a gap this test needs updating for."
        )
        raise AssertionError(
            f"{function_name}() gained a caller outside {sorted(expected)}: "
            f"{sorted(callers)}. If this is Phase 2a/2b landing {phase_note}"
        )
    assert callers == expected, (
        f"{function_name}() has no production caller at all under abicheck/ "
        f"-- expected exactly {sorted(expected)} (ADR-068 §1)"
    )


#: The two primitives Phase 2b closes outright (plan §3 #6/#8): function
#: name -> (defining module, the exact caller set now expected). Neither
#: backs a remaining scan-only gap key, so unlike `_ENGINE_PRIMITIVES`
#: above these carry no `tests/parity/gaps.py` cross-reference -- this
#: table's own job is narrower: pin the caller set so a *third* caller
#: (or the loss of either expected one) is still caught structurally.
_CLOSED_ENGINE_PRIMITIVES: dict[str, tuple[str, tuple[str, ...]]] = {
    "find_pattern_facts": (
        "buildsource/pattern_facts.py",
        ("workflows/pattern_preprocessor_scan.py",),
    ),
    "collect_preprocessor_facts": (
        "buildsource/preprocessor_facts.py",
        ("workflows/pattern_preprocessor_scan.py",),
    ),
}


@pytest.mark.parametrize("function_name", sorted(_CLOSED_ENGINE_PRIMITIVES))
def test_closed_primitive_has_exactly_the_expected_callers(function_name: str) -> None:
    """Phase 2b (plan §3 #6/#8): `compare()` reaches both primitives via
    `workflows/pattern_preprocessor_scan.py` -- pins the exact set so a
    regression (losing that caller) or a surprise second caller both fail
    loudly. Used to also allow `scan_engine.py` as a caller; deleted with
    the `scan` command itself (ADR-068 Phase 6)."""
    defining_module, expected_callers = _CLOSED_ENGINE_PRIMITIVES[function_name]
    expected = {f"abicheck/{m}" for m in expected_callers}

    sites = _call_sites(function_name, defining_module)
    sites = {p: n for p, n in sites.items() if p != Path("abicheck") / defining_module}
    callers = {_posix(p) for p in sites}
    assert callers == expected, (
        f"{function_name}() caller set changed -- expected exactly "
        f"{sorted(expected)}, found {sorted(callers)}"
    )


def test_pattern_scan_and_preprocessor_scan_are_no_longer_registered_gaps() -> None:
    assert "pattern_scan" not in EXPECTED_GAPS
    assert "preprocessor_scan" not in EXPECTED_GAPS


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
        # exactly the shape scan_engine.py itself uses (it lives at
        # abicheck/scan_engine.py, one level above the buildsource/ package)
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
    ``abicheck`` itself (the relative-import-into-a-subpackage case, matching
    ``scan_engine.py``'s own real import site) and every other shape are both
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
