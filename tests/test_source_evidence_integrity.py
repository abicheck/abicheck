# SPDX-License-Identifier: Apache-2.0
"""G20 Phase 3 — source-evidence integrity guard (ADR-035 D4).

The "a failed L4 link is never silently green" invariant — the oneDAL field
failure shape: the L4 replay parses many TUs but **zero** of its public source
declarations link to the binary's exported symbols. That degraded coverage must
be reported as such (every export unmatched), never folded in as a clean L4 pass.

Two surfaces carry the honest signal without any engine plumbing:
  * the ``link_source_abi`` boundary report (``coverage.matched_symbols`` /
    ``unmatched.symbols_without_decl``);
  * the cross-check coverage rows, which mark an empty L4 surface *skipped*, not
    *present* (so an ODR audit is never credited on a surface with no facts).

Pure-Python; default lane.
"""

from __future__ import annotations

from abicheck.buildsource.crosscheck import CHECK_ODR_TYPE_VARIANT, run_crosschecks
from abicheck.buildsource.pack import BuildSourcePack
from abicheck.buildsource.source_abi import (
    SourceAbiSurface,
    SourceAbiTu,
    SourceEntity,
    SourceLocation,
)
from abicheck.buildsource.source_link import link_source_abi
from abicheck.checker_policy import ChangeKind
from abicheck.model import AbiSnapshot
from abicheck.service_scan import _layers_from_coverage


def _public_fn(name: str, mangled: str) -> SourceEntity:
    return SourceEntity(
        id=f"decl://{name}",
        kind="function",
        qualified_name=name,
        mangled_name=mangled,
        source_location=SourceLocation(
            path="include/api.h", line=3, origin="PUBLIC_HEADER"
        ),
        visibility="public_header",
    )


def test_unlinked_source_evidence_reports_zero_matched_not_clean():
    # oneDAL shape: a TU is parsed (a public decl exists) but NONE of the binary's
    # exports resolve to a source declaration — the link is degraded.
    tu = SourceAbiTu(
        tu_id="cu://src/foo.cpp",
        source="src/foo.cpp",
        public_header_roots=["include/api.h"],
        functions=[_public_fn("foo", "_Z3foov")],
    )
    exports = ["_Z3barv", "_Z3bazv", "_Z3quxv"]
    surface = link_source_abi(
        tu_iter := [tu], exported_symbols=exports, library="libfoo.so"
    )

    # Parsed work happened ...
    assert surface.coverage["reachable_declarations"] == 1
    assert len(tu_iter) == 1
    # ... yet zero exports matched a source decl: the boundary is degraded.
    assert surface.coverage["exported_symbols"] == 3
    assert surface.coverage["matched_symbols"] == 0
    # Every export is reported unmatched — the failure is *named*, not hidden.
    assert surface.unmatched["symbols_without_decl"] == sorted(exports)
    # The parsed decl that resolved to nothing is also surfaced.
    assert "foo" in surface.unmatched["decls_without_symbol"]


def test_partial_link_records_only_the_unmatched_remainder():
    # A healthy-ish link where one export matches: the report distinguishes the
    # matched symbol from the still-unmatched remainder (no all-or-nothing lie).
    tu = SourceAbiTu(
        tu_id="cu://src/foo.cpp",
        source="src/foo.cpp",
        public_header_roots=["include/api.h"],
        functions=[_public_fn("foo", "_Z3foov"), _public_fn("bar", "_Z3barv")],
    )
    surface = link_source_abi([tu], exported_symbols=["_Z3foov", "_Z3missingv"])
    assert surface.coverage["matched_symbols"] == 1
    assert surface.unmatched["symbols_without_decl"] == ["_Z3missingv"]


def test_empty_l4_surface_is_skipped_not_credited_as_clean():
    # An ODR audit on an L4 surface with no facts must read *skipped*, never a
    # clean "present" pass — coverage honesty (the integrity contract's other half).
    snap = AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        from_headers=True,
        build_source=BuildSourcePack(root="", source_abi=SourceAbiSurface()),
    )
    res = run_crosschecks(snap)
    row = next(
        r for r in res.coverage if r["layer"] == f"crosscheck:{CHECK_ODR_TYPE_VARIANT}"
    )
    assert row["status"] == "skipped"
    assert "empty" in row["detail"]
    assert [c for c in res.findings if c.kind == ChangeKind.ODR_TYPE_VARIANT] == []


def _degraded_surface_snapshot() -> AbiSnapshot:
    """oneDAL shape as a snapshot: a TU parses one public decl, but none of the
    three binary exports resolve to a source declaration (matched_symbols == 0)."""
    tu = SourceAbiTu(
        tu_id="cu://src/foo.cpp",
        source="src/foo.cpp",
        public_header_roots=["include/api.h"],
        functions=[_public_fn("foo", "_Z3foov")],
    )
    surface = link_source_abi(
        [tu], exported_symbols=["_Z3barv", "_Z3bazv", "_Z3quxv"], library="libfoo.so"
    )
    return AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        from_headers=True,
        build_source=BuildSourcePack(root="", source_abi=surface),
    )


def test_integrity_counters_surface_on_rendered_scan_layers():
    # The §3.3 plumbing: the L4 boundary integrity counters must reach the
    # rendered ScanResult layer (via _layers_from_coverage), not just the internal
    # source_link object — so a degraded link is named even when ODR runs clean.
    snap = _degraded_surface_snapshot()
    res = run_crosschecks(snap)
    layers = _layers_from_coverage(res.coverage)
    odr = next(
        layer
        for layer in layers
        if layer.layer == f"crosscheck:{CHECK_ODR_TYPE_VARIANT}"
    )
    # ODR itself ran clean (no conflicts) ...
    assert odr.status == "present"
    assert [c for c in res.findings if c.kind == ChangeKind.ODR_TYPE_VARIANT] == []
    # ... yet the rendered layer names the degraded boundary: zero exports matched
    # a source decl, all three unmatched.
    assert odr.counters["exported_symbols"] == 3
    assert odr.counters["matched_symbols"] == 0
    assert odr.counters["unmatched_symbols"] == 3
    assert odr.facts == 1  # one parsed declaration anchored the row
    # The counters round-trip through the serialized report payload.
    assert odr.to_dict()["counters"]["matched_symbols"] == 0


def test_healthy_link_records_matched_counters_not_zero():
    # The clean counterpart (FP-rate sanity): a fully-linked surface reports a
    # non-zero matched count on the same rendered layer — no false "degraded".
    tu = SourceAbiTu(
        tu_id="cu://src/foo.cpp",
        source="src/foo.cpp",
        public_header_roots=["include/api.h"],
        functions=[_public_fn("foo", "_Z3foov")],
    )
    surface = link_source_abi([tu], exported_symbols=["_Z3foov"], library="libfoo.so")
    snap = AbiSnapshot(
        library="libfoo.so", version="1.0", from_headers=True,
        build_source=BuildSourcePack(root="", source_abi=surface),
    )
    odr = next(
        layer
        for layer in _layers_from_coverage(run_crosschecks(snap).coverage)
        if layer.layer == f"crosscheck:{CHECK_ODR_TYPE_VARIANT}"
    )
    assert odr.counters["matched_symbols"] == 1
    assert odr.counters["unmatched_symbols"] == 0


def test_empty_l4_skip_row_still_names_the_degraded_boundary():
    # A zero-TU L4 run with exports on record is the worst degraded shape: the ODR
    # check *skips* (no parsed facts to audit), but the skipped row must still name
    # the boundary (exported>0, matched==0), never read as silently clean.
    surface = SourceAbiSurface(
        library="libfoo.so",
        coverage={"exported_symbols": 3, "matched_symbols": 0},
        unmatched={"symbols_without_decl": ["_Z3barv", "_Z3bazv", "_Z3quxv"]},
    )
    snap = AbiSnapshot(
        library="libfoo.so", version="1.0", from_headers=True,
        build_source=BuildSourcePack(root="", source_abi=surface),
    )
    res = run_crosschecks(snap)
    odr = next(
        layer
        for layer in _layers_from_coverage(res.coverage)
        if layer.layer == f"crosscheck:{CHECK_ODR_TYPE_VARIANT}"
    )
    assert odr.status == "skipped"  # no parsed TUs to audit ...
    assert odr.counters["exported_symbols"] == 3  # ... but the link is named
    assert odr.counters["matched_symbols"] == 0
    assert odr.counters["unmatched_symbols"] == 3


def test_layers_from_coverage_tolerates_non_numeric_counters():
    # A hand-edited / forward-compat coverage row with a non-numeric counter or
    # facts value must not abort the render — the bad value is dropped.
    rows = [
        {
            "layer": "crosscheck:odr_type_variant",
            "status": "present",
            "detail": "",
            "facts": "not-a-number",
            "counters": {"matched_symbols": "oops", "exported_symbols": 2},
        }
    ]
    layers = _layers_from_coverage(rows)
    assert len(layers) == 1
    assert layers[0].facts == 0  # bad facts coerced to 0
    assert layers[0].counters == {"exported_symbols": 2}  # bad entry dropped, good kept
