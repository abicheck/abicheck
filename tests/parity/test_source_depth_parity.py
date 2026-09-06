# SPDX-License-Identifier: Apache-2.0
"""F-3/F-4 (plan §7): ``--depth source`` (L3-L5) parity.

Unlike the crosscheck/pattern/preprocessor checks, source-ABI-replay (L4)
and source-graph (L5) analysis is **already** shared between ``scan`` and
``compare`` (plan §3 rows #10/#11, classed MERGE, not COMPARE-STAGE) --
``scan --against``'s baseline path delegates to the same compare engine
``compare OLD NEW`` calls. F-3 and F-4 are therefore this harness's *green*
parity scenarios: they must show equality today, not a recorded gap. If
either regresses, ``assert_no_capability_loss`` fails the same way it would
for a real cross-source-check loss -- an empty ``EXPECTED_GAPS`` intersection
here means no loss is excusable.
"""

from __future__ import annotations

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
    assert_no_capability_loss,
    compare_finding_set,
    invoke_cli,
    kinds_of,
    scan_finding_set,
    write_snapshot,
)


def test_f3_call_graph_break_finding_set_parity() -> None:
    """case192: an L5 call-graph break (the plan's own "headline oneDAL-
    style scenario"). `scan --against` and `compare --depth source` must
    produce the same finding set -- kind AND resolved identity, not just
    matching counts."""
    case_dir = example_catalog.case_dir("case192_call_graph_break_survives_suppression")
    old, new = case_dir / "old.abi.json", case_dir / "new.abi.json"
    assert old.is_file() and new.is_file()

    scan_findings = scan_finding_set(new, "--against", str(old))
    compare_findings = compare_finding_set(old, new, "--depth", "source")

    assert_no_capability_loss(
        scan_findings=scan_findings,
        compare_findings=compare_findings,
        context="case192 (--depth source, F-3)",
    )
    # Identity, not just kind: both tools must resolve the same symbol.
    scan_identities = {f.identity for f in scan_findings}
    compare_identities = {f.identity for f in compare_findings}
    assert scan_identities == compare_identities
    assert scan_identities == {"_ZN4demo6detail13compute_avx2ERKNS_10DescriptorE"}

    exit_scan = invoke_cli("scan", str(new), "--against", str(old)).exit_code
    exit_compare = invoke_cli(
        "compare", str(old), str(new), "--depth", "source"
    ).exit_code
    assert exit_scan == exit_compare == 4


def test_f3_non_reachable_counterexample_finding_set_parity() -> None:
    """case193: the deliberate counter-example -- an ordinary exported
    function's call into a removed internal helper is NOT consumer-
    reachable, so both tools must agree on a plain func_removed break with
    NO internal_symbol_required_by_public_api promotion on either side."""
    case_dir = example_catalog.case_dir(
        "case193_ordinary_exported_fn_call_not_reachable"
    )
    old, new = case_dir / "old.abi.json", case_dir / "new.abi.json"

    scan_findings = scan_finding_set(new, "--against", str(old))
    compare_findings = compare_finding_set(old, new, "--depth", "source")

    assert_no_capability_loss(
        scan_findings=scan_findings,
        compare_findings=compare_findings,
        context="case193 (--depth source, F-3 counter-example)",
    )
    assert kinds_of(scan_findings) == kinds_of(compare_findings) == {"func_removed"}


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
    API_BREAK, never BREAKING, on BOTH tools (the authority rule, ADR-028
    D3/ADR-035 D1) -- and both tools actually agree on the finding, not
    just the verdict bucket."""
    old, new = _inline_removal_snapshots(tmp_path)

    scan_findings = scan_finding_set(new, "--against", str(old))
    compare_findings = compare_finding_set(old, new, "--depth", "source")

    assert (
        kinds_of(scan_findings)
        == kinds_of(compare_findings)
        == {"inline_function_removed"}
    )
    assert_no_capability_loss(
        scan_findings=scan_findings,
        compare_findings=compare_findings,
        context="inline_function_removed (F-4)",
    )

    from abicheck.checker_policy import API_BREAK_KINDS, BREAKING_KINDS, ChangeKind

    assert ChangeKind.INLINE_FUNCTION_REMOVED in API_BREAK_KINDS
    assert ChangeKind.INLINE_FUNCTION_REMOVED not in BREAKING_KINDS

    scan_report_verdict = invoke_cli(
        "scan", str(new), "--against", str(old), "--format", "json"
    )
    compare_report_verdict = invoke_cli(
        "compare", str(old), str(new), "--depth", "source", "--format", "json"
    )
    import json

    assert json.loads(scan_report_verdict.stdout)["verdict"] == "API_BREAK"
    assert json.loads(compare_report_verdict.stdout)["verdict"] == "API_BREAK"
