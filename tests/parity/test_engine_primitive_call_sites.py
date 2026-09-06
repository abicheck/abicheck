# SPDX-License-Identifier: Apache-2.0
"""Call-site proof that the three scan-only analysis engines have exactly
one production caller: ``scan_engine.py``.

ADR-068 §1 states the capability-loss defect by call site, not by import:

    Verified by call site, not by import: the only production callers of
    ``buildsource.crosscheck.run_crosschecks``, ``buildsource.pattern_scan.
    scan_files`` and ``buildsource.preprocessor_scan.run_preprocessor_scan``
    anywhere under ``abicheck/`` are ``scan_engine.py``... Check by call
    site, not import: ``workflows/extraction.py`` imports two of the three
    and calls neither.

This module makes that claim executable instead of only a prose citation --
a real AST scan over every ``abicheck/**/*.py`` module, not a grep that a
comment or an unrelated identifier could fool. It is the parity harness's
proof, for ``pattern_scan``/``preprocessor_scan`` (and a second angle on
``crosscheck``, alongside ``test_crosscheck_parity.py``'s behavioral one),
that ``compare``'s pipeline *cannot* reach them today -- not merely that it
happens not to in the fixtures exercised elsewhere.

Once Phase 2a/2b (docs/contribute/plans/one-comparison-product.md §6) gives
one of these functions a second caller reachable from `compare`, this test
starts failing -- which is the point: it is the signal to delete the
corresponding tests/parity/gaps.py entry in that same PR.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from .gaps import EXPECTED_GAPS

_ABICHECK_ROOT = Path(__file__).resolve().parent.parent.parent / "abicheck"

#: function name -> (defining module, expected sole-caller module, gap key)
_ENGINE_PRIMITIVES: dict[str, tuple[str, str, str]] = {
    "run_crosschecks": (
        "buildsource/crosscheck.py",
        "scan_engine.py",
        "private_header_leak",  # any of the 11 crosscheck gap keys will do
    ),
    "scan_files": ("buildsource/pattern_scan.py", "scan_engine.py", "pattern_scan"),
    "run_preprocessor_scan": (
        "buildsource/preprocessor_scan.py",
        "scan_engine.py",
        "preprocessor_scan",
    ),
}


def _call_sites(function_name: str) -> dict[Path, int]:
    """Repo-relative module -> number of *call expressions* naming
    ``function_name`` as the called function (``foo()``, not ``x.foo()``,
    an import, a re-export, or a bare mention in a docstring/comment)."""
    hits: dict[Path, int] = {}
    for path in sorted(_ABICHECK_ROOT.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError):
            continue
        count = 0
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == function_name
            ):
                count += 1
        if count:
            hits[path.relative_to(_ABICHECK_ROOT.parent)] = count
    return hits


def _posix(path: Path) -> str:
    """POSIX-style repo-relative spelling, independent of host OS separator
    (Windows CI reports ``abicheck\\scan_engine.py`` from ``str(path)``)."""
    return path.as_posix()


@pytest.mark.parametrize("function_name", sorted(_ENGINE_PRIMITIVES))
def test_only_scan_engine_calls_it(function_name: str) -> None:
    defining_module, expected_caller, gap_key = _ENGINE_PRIMITIVES[function_name]
    assert gap_key in EXPECTED_GAPS, f"{gap_key!r} must be a registered parity gap"

    sites = _call_sites(function_name)
    # Drop the defining module itself (recursive helpers / the module's own
    # internal dispatch don't count as an external caller).
    sites = {p: n for p, n in sites.items() if p != Path("abicheck") / defining_module}

    callers = {_posix(p) for p in sites}
    if callers - {f"abicheck/{expected_caller}"}:
        raise AssertionError(
            f"{function_name}() gained a caller outside {expected_caller!r}: "
            f"{sorted(callers)}. If this is Phase 2a/2b landing "
            f"({EXPECTED_GAPS[gap_key].plan_phase}), delete the "
            f"{gap_key!r} entry from tests/parity/gaps.py in the same PR "
            "instead of leaving this assertion to rot."
        )
    assert callers == {f"abicheck/{expected_caller}"}, (
        f"{function_name}() has no production caller at all under abicheck/ "
        f"-- expected exactly {expected_caller!r} (ADR-068 §1)"
    )
