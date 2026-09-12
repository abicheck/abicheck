# SPDX-License-Identifier: Apache-2.0
"""F-3/F-4 (plan §7): ``--depth source`` (L3-L5) regression corpus.

Unlike the crosscheck/pattern/preprocessor checks, source-ABI-replay (L4)
and source-graph (L5) analysis used to be shared between ``scan`` and
``compare`` (plan §3 rows #10/#11, classed MERGE, not COMPARE-STAGE) --
``scan --against``'s baseline path delegated to the same compare engine
``compare OLD NEW`` calls, so F-3 and F-4 were this harness's *green*
parity scenarios (equality with a live ``scan`` invocation, proven rather
than assumed). ``scan`` was deleted outright with ADR-068 Phase 6; the
finding sets/verdicts/exit codes it used to be checked against are now
pinned directly as this module's own compare-only regression corpus --
the same fixtures, the same expected identities/kinds/verdicts, asserted
against ``compare --depth source`` alone.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
if str(_REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(_REPO / "scripts"))
import example_catalog  # noqa: E402

from abicheck.buildsource.pack import BuildSourcePack  # noqa: E402
from abicheck.buildsource.source_abi import SourceAbiSurface, SourceEntity  # noqa: E402
from abicheck.model import AbiSnapshot  # noqa: E402

from .runner import (  # noqa: E402
    compare_finding_set,
    invoke_cli,
    kinds_of,
    without_surface_metrics,
    write_snapshot,
)


def test_f3_call_graph_break_finding_set() -> None:
    """case192: an L5 call-graph break (the plan's own "headline oneDAL-
    style scenario"). ``compare --depth source`` must resolve the exact
    symbol identity, not just the finding kind, and exit 4 (ABI breaking).
    """
    case_dir = example_catalog.case_dir("case192_call_graph_break_survives_suppression")
    old, new = case_dir / "old.abi.json", case_dir / "new.abi.json"
    assert old.is_file() and new.is_file()

    compare_findings = compare_finding_set(old, new, "--depth", "source")

    # (without_surface_metrics: see its own docstring -- a `compare`-only
    # ADR-027 roll-up here would otherwise pollute this exact-identity check.)
    compare_identities = {f.identity for f in without_surface_metrics(compare_findings)}
    assert compare_identities == {"_ZN4demo6detail13compute_avx2ERKNS_10DescriptorE"}

    exit_compare = invoke_cli(
        "compare", str(old), str(new), "--depth", "source"
    ).exit_code
    assert exit_compare == 4


def test_f3_non_reachable_counterexample_finding_set() -> None:
    """case193: the deliberate counter-example -- an ordinary exported
    function's call into a removed internal helper is NOT consumer-
    reachable, so ``compare`` must report a plain func_removed break with
    NO internal_symbol_required_by_public_api promotion."""
    case_dir = example_catalog.case_dir(
        "case193_ordinary_exported_fn_call_not_reachable"
    )
    old, new = case_dir / "old.abi.json", case_dir / "new.abi.json"

    compare_findings = compare_finding_set(old, new, "--depth", "source")

    # without_surface_metrics: see its own docstring in runner.py.
    assert kinds_of(without_surface_metrics(compare_findings)) == {"func_removed"}


def _inline_removal_snapshots(tmp_path: Path) -> tuple[Path, Path]:
    """A public inline function with no exported binary symbol, removed
    between versions -- an API-only change genuinely invisible in the
    binary (no ELF metadata at all on either side): only L4 source replay
    can see it. Hand-built evidence (no compiler), mirroring
    tests/test_l3l4l5_new_kinds.py::test_l4_inline_function_removed.
    """
    keeper = SourceEntity(
        id="keep",
        kind="function",
        qualified_name="keep",
        mangled_name="_Z4keepv",
        visibility="public_header",
    )
    old_surface = SourceAbiSurface(
        reachable_inline_bodies=[
            SourceEntity(
                id="clamp", kind="inline", qualified_name="clamp", body_hash="h1"
            )
        ],
        reachable_declarations=[keeper],
    )
    new_surface = SourceAbiSurface(reachable_declarations=[keeper])

    old_snap = AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        build_source=BuildSourcePack(root="", source_abi=old_surface),
    )
    new_snap = AbiSnapshot(
        library="libfoo.so",
        version="2.0",
        build_source=BuildSourcePack(root="", source_abi=new_surface),
    )
    return (
        write_snapshot(old_snap, tmp_path / "old.abi.json"),
        write_snapshot(new_snap, tmp_path / "new.abi.json"),
    )


def test_f4_source_only_change_classed_api_break_not_breaking(tmp_path: Path) -> None:
    """F-4: a source-only change invisible in the binary is classed
    API_BREAK, never BREAKING (the authority rule, ADR-028 D3/ADR-035 D1)."""
    old, new = _inline_removal_snapshots(tmp_path)

    compare_findings = compare_finding_set(old, new, "--depth", "source")
    assert kinds_of(compare_findings) == {"inline_function_removed"}

    from abicheck.checker_policy import API_BREAK_KINDS, BREAKING_KINDS, ChangeKind

    assert ChangeKind.INLINE_FUNCTION_REMOVED in API_BREAK_KINDS
    assert ChangeKind.INLINE_FUNCTION_REMOVED not in BREAKING_KINDS

    compare_report_verdict = invoke_cli(
        "compare", str(old), str(new), "--depth", "source", "-o", "json=-"
    )
    assert json.loads(compare_report_verdict.stdout)["verdict"] == "API_BREAK"
