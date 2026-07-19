"""Targeted coverage for the dump --depth strict-contract helpers in
:mod:`abicheck.cli_dump_helpers`: ``evidence_depth_label``,
``check_requested_depth_satisfied``, ``_gated_source_label``,
``fold_dump_provenance_into_json``, ``_l4_source_abi_was_attempted``, and
``_dump_will_attempt_hybrid_l4_extraction``. Split out of
``test_cli_dump_helpers_coverage.py`` (CLAUDE.md file-size cap) -- that file
still covers the rest of the module (compile-db/debug-format resolution,
handle_non_elf_dump, perform_elf_dump plumbing).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.cli_dump_helpers import evidence_depth_label
from abicheck.model import AbiSnapshot


def _pack(build_evidence=None, source_abi=None, source_graph=None):
    from abicheck.buildsource.pack import BuildSourcePack

    return BuildSourcePack(
        root=Path("/nonexistent"),
        build_evidence=build_evidence,
        source_abi=source_abi,
        source_graph=source_graph,
    )


def test_evidence_depth_label_binary_when_no_headers_no_build_source() -> None:
    snap = AbiSnapshot(library="libfoo.so", version="1.0")
    assert evidence_depth_label(snap) == "binary"


def test_evidence_depth_label_headers_when_from_headers_set() -> None:
    snap = AbiSnapshot(library="libfoo.so", version="1.0", from_headers=True)
    assert evidence_depth_label(snap) == "headers"


def test_evidence_depth_label_build_when_build_evidence_has_facts() -> None:
    from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit

    snap = AbiSnapshot(library="libfoo.so", version="1.0", from_headers=True)
    snap.build_source = _pack(
        build_evidence=BuildEvidence(compile_units=[CompileUnit(id="cu1", source="a.c")])
    )
    assert evidence_depth_label(snap) == "build"


def test_evidence_depth_label_build_when_parsed_with_compile_db_context() -> None:
    """Codex review: `dump lib.so -H api.h -p build/` (ADR-020a/039
    -p/--compile-db build-context capture) has no BuildSourcePack of its
    own -- snap.build_source stays None -- but snap.parsed_with_build_context
    is still a legitimate "build" evidence signal, distinct from the
    BuildSourcePack machinery checked above."""
    snap = AbiSnapshot(
        library="libfoo.so", version="1.0", from_headers=True,
        parsed_with_build_context=True,
    )
    assert snap.build_source is None
    assert evidence_depth_label(snap) == "build"


def test_evidence_depth_label_source_when_source_abi_has_reachable_entities() -> None:
    from abicheck.buildsource.build_evidence import BuildEvidence
    from abicheck.buildsource.source_abi import SourceAbiSurface, SourceEntity

    snap = AbiSnapshot(library="libfoo.so", version="1.0", from_headers=True)
    snap.build_source = _pack(
        build_evidence=BuildEvidence(),
        source_abi=SourceAbiSurface(
            reachable_declarations=[SourceEntity(id="foo", kind="function")]
        ),
    )
    assert evidence_depth_label(snap) == "source"


def test_evidence_depth_label_source_when_source_graph_has_nodes() -> None:
    from abicheck.buildsource.source_graph import GraphNode, SourceGraphSummary

    snap = AbiSnapshot(library="libfoo.so", version="1.0", from_headers=True)
    snap.build_source = _pack(
        source_graph=SourceGraphSummary(nodes=[GraphNode(id="n1", kind="function")])
    )
    assert evidence_depth_label(snap) == "source"


def test_evidence_depth_label_does_not_overstate_empty_source_abi() -> None:
    # Regression (CodeRabbit review): source_abi/source_graph/build_evidence
    # can be present (non-None) but carry no real facts -- e.g.
    # _run_inline_source_abi returns an empty SourceAbiSurface() when clang is
    # unavailable after L3 was found. Presence alone must not overstate
    # "source"/"build" for a layer that ran but linked nothing.
    from abicheck.buildsource.build_evidence import BuildEvidence
    from abicheck.buildsource.source_abi import SourceAbiSurface
    from abicheck.buildsource.source_graph import SourceGraphSummary

    snap = AbiSnapshot(library="libfoo.so", version="1.0", from_headers=True)
    snap.build_source = _pack(
        build_evidence=BuildEvidence(),  # no targets/compile_units
        source_abi=SourceAbiSurface(),  # no reachable entities
        source_graph=SourceGraphSummary(),  # no nodes
    )
    # All three layers are present but empty -- falls all the way back to
    # "headers" (from_headers=True), not "source" or "build".
    assert evidence_depth_label(snap) == "headers"


# ── check_requested_depth_satisfied (CLI-audit P1 strict depth contract) ────


def test_check_requested_depth_satisfied_noop_when_depth_not_requested() -> None:
    """The bare default (no --depth) never hard-fails -- degrading silently
    is the whole point of leaving --depth unspecified."""
    from abicheck.cli_dump_helpers import check_requested_depth_satisfied

    snap = AbiSnapshot(library="libfoo.so", version="1.0")
    check_requested_depth_satisfied(None, snap)  # must not raise


def test_check_requested_depth_satisfied_binary_always_passes() -> None:
    from abicheck.cli_dump_helpers import check_requested_depth_satisfied

    snap = AbiSnapshot(library="libfoo.so", version="1.0")
    check_requested_depth_satisfied("binary", snap)  # must not raise


def test_check_requested_depth_satisfied_unknown_depth_is_noop() -> None:
    """Defensive branch: a depth value outside _DEPTH_RANK (should never
    happen past CLI parsing, which restricts --depth to the four known
    rungs) is treated as unconstrained rather than raising."""
    from abicheck.cli_dump_helpers import check_requested_depth_satisfied

    snap = AbiSnapshot(library="libfoo.so", version="1.0")
    check_requested_depth_satisfied("not-a-real-depth", snap)  # must not raise


def test_gated_source_label_without_a_pack_falls_back_to_headers_or_binary() -> None:
    from abicheck.cli_dump_helpers import _gated_source_label

    assert _gated_source_label(None, AbiSnapshot(library="libfoo.so", version="1.0")) == "binary"
    headers_snap = AbiSnapshot(library="libfoo.so", version="1.0", from_headers=True)
    assert _gated_source_label(None, headers_snap) == "headers"


def test_gated_source_label_without_a_pack_but_with_compile_db_context_is_build() -> None:
    """Codex review: -p/--compile-db build context (no BuildSourcePack) must
    still gate as "build", not fall through to "headers"."""
    from abicheck.cli_dump_helpers import _gated_source_label

    snap = AbiSnapshot(
        library="libfoo.so", version="1.0", from_headers=True,
        parsed_with_build_context=True,
    )
    assert _gated_source_label(None, snap) == "build"


def test_dump_will_attempt_hybrid_l4_extraction_false_for_prebuilt_pack(tmp_path) -> None:
    from abicheck.buildsource.pack import BuildSourcePack
    from abicheck.cli_dump_helpers import _dump_will_attempt_hybrid_l4_extraction

    pack_dir = tmp_path / "pack"
    BuildSourcePack.empty(pack_dir).write()
    assert _dump_will_attempt_hybrid_l4_extraction(pack_dir) is False


def test_dump_will_attempt_hybrid_l4_extraction_true_for_raw_source_tree(tmp_path) -> None:
    from abicheck.cli_dump_helpers import _dump_will_attempt_hybrid_l4_extraction

    tree = tmp_path / "src"
    tree.mkdir()
    assert _dump_will_attempt_hybrid_l4_extraction(tree) is True


def test_dump_will_attempt_hybrid_l4_extraction_false_without_any_input() -> None:
    """Codex review (third finding): no --sources at all means
    collect_inline_pack never runs regardless of frontend -- rejecting here
    would point the user at a fix (switch frontends) that would not
    actually satisfy --depth source; the real problem is reported
    downstream by check_requested_depth_satisfied instead."""
    from abicheck.cli_dump_helpers import _dump_will_attempt_hybrid_l4_extraction

    assert _dump_will_attempt_hybrid_l4_extraction(None) is False


def test_check_requested_depth_satisfied_headers_without_header_ast_fails() -> None:
    from abicheck.cli_dump_helpers import (
        DumpDepthNotSatisfiedError,
        check_requested_depth_satisfied,
    )

    snap = AbiSnapshot(library="libfoo.so", version="1.0")  # from_headers=False
    with pytest.raises(DumpDepthNotSatisfiedError, match="--depth headers"):
        check_requested_depth_satisfied("headers", snap)


def test_check_requested_depth_satisfied_headers_with_header_ast_passes() -> None:
    from abicheck.cli_dump_helpers import check_requested_depth_satisfied

    snap = AbiSnapshot(library="libfoo.so", version="1.0", from_headers=True)
    check_requested_depth_satisfied("headers", snap)  # must not raise


def test_check_requested_depth_satisfied_build_without_build_facts_fails() -> None:
    from abicheck.cli_dump_helpers import (
        DumpDepthNotSatisfiedError,
        check_requested_depth_satisfied,
    )

    snap = AbiSnapshot(library="libfoo.so", version="1.0", from_headers=True)
    with pytest.raises(DumpDepthNotSatisfiedError, match="--depth build"):
        check_requested_depth_satisfied("build", snap)


def test_check_requested_depth_satisfied_build_with_compile_db_context_passes() -> None:
    """Codex review: `dump lib.so -H api.h -p build/ --depth build` must be
    accepted -- the strict gate's own error message already documents "build
    via --build-info/a compile database" as a valid remedy, but the gate
    itself only checked snap.build_source (the newer BuildSourcePack
    machinery), never snap.parsed_with_build_context (the older -p/
    --compile-db ADR-020a/039 signal perform_elf_dump sets, with no
    BuildSourcePack of its own)."""
    from abicheck.cli_dump_helpers import (
        DumpDepthNotSatisfiedError,
        check_requested_depth_satisfied,
    )

    snap = AbiSnapshot(
        library="libfoo.so", version="1.0", from_headers=True,
        parsed_with_build_context=True,
    )
    check_requested_depth_satisfied("build", snap)  # must not raise
    # A compile-database build context is still not source-tier evidence --
    # --depth source must still fail for the same snapshot.
    with pytest.raises(DumpDepthNotSatisfiedError, match="--depth source"):
        check_requested_depth_satisfied("source", snap)


def test_check_requested_depth_satisfied_source_with_source_facts_passes() -> None:
    from abicheck.buildsource.build_evidence import BuildEvidence
    from abicheck.buildsource.source_abi import SourceAbiSurface, SourceEntity
    from abicheck.cli_dump_helpers import check_requested_depth_satisfied

    snap = AbiSnapshot(library="libfoo.so", version="1.0", from_headers=True)
    snap.build_source = _pack(
        build_evidence=BuildEvidence(),
        source_abi=SourceAbiSurface(
            reachable_declarations=[SourceEntity(id="foo", kind="function")]
        ),
    )
    check_requested_depth_satisfied("source", snap)  # must not raise


def test_check_requested_depth_satisfied_source_with_empty_payload_fails() -> None:
    """Mirrors test_evidence_depth_label_does_not_overstate_empty_source_abi:
    a coverage row can be present while the payload links no facts -- the
    strict check must see through that the same way evidence_depth_label does."""
    from abicheck.buildsource.build_evidence import BuildEvidence
    from abicheck.buildsource.source_abi import SourceAbiSurface
    from abicheck.buildsource.source_graph import SourceGraphSummary
    from abicheck.cli_dump_helpers import (
        DumpDepthNotSatisfiedError,
        check_requested_depth_satisfied,
    )

    snap = AbiSnapshot(library="libfoo.so", version="1.0", from_headers=True)
    snap.build_source = _pack(
        build_evidence=BuildEvidence(),
        source_abi=SourceAbiSurface(),
        source_graph=SourceGraphSummary(),
    )
    with pytest.raises(DumpDepthNotSatisfiedError, match="--depth source"):
        check_requested_depth_satisfied("source", snap)


def test_check_requested_depth_satisfied_header_graph_only_does_not_satisfy_source() -> None:
    """Codex review: service._attach_header_graph (--header-graph without
    --sources/--build-info) builds a pack whose L5 source_graph is genuinely
    non-empty while L3/L4 coverage rows are explicitly NOT_COLLECTED -- no
    build or L4 source-ABI replay ever ran. evidence_depth_label's honest L5
    check alone would read this as "source", letting --header-graph silently
    satisfy an explicit --depth source with zero real source/build evidence.
    The strict check must see through that and still fail."""
    from abicheck.buildsource.model import CoverageStatus, DataLayer, LayerCoverage
    from abicheck.buildsource.pack import BuildSourcePack
    from abicheck.buildsource.source_graph import GraphNode, SourceGraphSummary
    from abicheck.cli_dump_helpers import (
        DumpDepthNotSatisfiedError,
        check_requested_depth_satisfied,
    )

    snap = AbiSnapshot(library="libfoo.so", version="1.0", from_headers=True)
    pack = BuildSourcePack(
        root=Path(""),
        source_graph=SourceGraphSummary(nodes=[GraphNode(id="n1", kind="function")]),
    )
    pack.manifest.coverage = [
        LayerCoverage(layer=DataLayer.L3_BUILD.value, status=CoverageStatus.NOT_COLLECTED),
        LayerCoverage(layer=DataLayer.L4_SOURCE_ABI.value, status=CoverageStatus.NOT_COLLECTED),
        LayerCoverage(layer=DataLayer.L5_SOURCE_GRAPH.value, status=CoverageStatus.PRESENT),
    ]
    snap.build_source = pack

    # Sanity check: evidence_depth_label alone (the honesty-reporting
    # function) does read this as "source" -- that's the exact gap the
    # strict check must independently close, not a bug in that function.
    assert evidence_depth_label(snap) == "source"

    with pytest.raises(DumpDepthNotSatisfiedError, match="--depth source"):
        check_requested_depth_satisfied("source", snap)


def test_check_requested_depth_satisfied_l3_plus_backfilled_graph_does_not_satisfy_source() -> None:
    """Codex review (second finding): cli_buildsource.embed_build_source's
    header-only-graph backfill can graft a --header-graph L5 pack onto an
    otherwise-real, L3-only --build-info pack -- so "L3 present, L4 absent,
    L5 present" is NOT a reliable "genuine source-tier" signature either; it
    is exactly what a real `--build-info <L3-pack> --header-graph` run
    produces with zero L4/L5 source-tier replay. The strict gate must
    require real L4 facts, not just a non-empty L3."""
    from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
    from abicheck.buildsource.model import CoverageStatus, DataLayer, LayerCoverage
    from abicheck.buildsource.pack import BuildSourcePack
    from abicheck.buildsource.source_graph import GraphNode, SourceGraphSummary
    from abicheck.cli_dump_helpers import (
        DumpDepthNotSatisfiedError,
        check_requested_depth_satisfied,
    )

    snap = AbiSnapshot(library="libfoo.so", version="1.0", from_headers=True)
    backfilled_pack = BuildSourcePack(
        root=Path(""),
        build_evidence=BuildEvidence(compile_units=[CompileUnit(id="cu1", source="a.c")]),
        source_graph=SourceGraphSummary(nodes=[GraphNode(id="n1", kind="function")]),
    )
    backfilled_pack.manifest.coverage = [
        LayerCoverage(layer=DataLayer.L3_BUILD.value, status=CoverageStatus.PRESENT),
        LayerCoverage(layer=DataLayer.L4_SOURCE_ABI.value, status=CoverageStatus.NOT_COLLECTED),
        LayerCoverage(layer=DataLayer.L5_SOURCE_GRAPH.value, status=CoverageStatus.PRESENT),
    ]
    snap.build_source = backfilled_pack

    assert evidence_depth_label(snap) == "source"
    with pytest.raises(DumpDepthNotSatisfiedError, match="--depth source"):
        check_requested_depth_satisfied("source", snap)
    # The failure message should honestly name "build" (real L3), not
    # "binary" or "headers", since L3 genuinely is present.
    try:
        check_requested_depth_satisfied("source", snap)
    except DumpDepthNotSatisfiedError as exc:
        assert "reached 'build'" in str(exc)

    # A --depth build request against the same pack is genuinely satisfied
    # -- L3 is real here, only the "source" rung is gated on L4.
    check_requested_depth_satisfied("build", snap)  # must not raise


def test_check_requested_depth_satisfied_source_with_real_l4_facts_passes() -> None:
    """The gate is satisfied whenever L4 (source_abi) genuinely has facts,
    regardless of whether L3/L5 are also present -- this is what a real
    --sources/--build-info replay with L4 reachable declarations looks like."""
    from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
    from abicheck.buildsource.pack import BuildSourcePack
    from abicheck.buildsource.source_abi import SourceAbiSurface, SourceEntity
    from abicheck.buildsource.source_graph import GraphNode, SourceGraphSummary
    from abicheck.cli_dump_helpers import check_requested_depth_satisfied

    snap = AbiSnapshot(library="libfoo.so", version="1.0", from_headers=True)
    real_pack = BuildSourcePack(
        root=Path(""),
        build_evidence=BuildEvidence(compile_units=[CompileUnit(id="cu1", source="a.c")]),
        source_abi=SourceAbiSurface(
            reachable_declarations=[SourceEntity(id="foo", kind="function")]
        ),
        source_graph=SourceGraphSummary(nodes=[GraphNode(id="n1", kind="function")]),
    )
    snap.build_source = real_pack
    check_requested_depth_satisfied("source", snap)  # must not raise


def test_check_requested_depth_satisfied_source_zero_match_no_graph_passes() -> None:
    """CodeRabbit review: evidence_depth_label's own L4-or-L5 payload-emptiness
    rule requires *either* L4 *or* L5 to be non-empty -- a zero-match
    source-only dump that parsed TUs but linked no declarations (no binary to
    link against) AND folded no L5 graph leaves *both* empty, so
    evidence_depth_label reports "build" directly. The old code only called
    _gated_source_label when evidence_depth_label already said "source",
    so this genuinely-attempted, zero-match case skipped the gated recompute
    entirely and was wrongly rejected -- exactly the "unseeded --depth source
    that selected 0 TUs" scenario _write_snapshot_output's own G21.7 warning
    describes as expected, warn-only behavior, not a hard failure. Unlike
    test_check_requested_depth_satisfied_source_with_real_l4_facts_passes
    above, this pack deliberately carries NO source_graph, so it only passes
    once _gated_source_label runs unconditionally."""
    from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
    from abicheck.buildsource.pack import BuildSourcePack
    from abicheck.buildsource.source_abi import SourceAbiSurface
    from abicheck.cli_dump_helpers import check_requested_depth_satisfied

    snap = AbiSnapshot(library="libfoo.so", version="1.0", from_headers=True)
    surface = SourceAbiSurface()
    surface.coverage["compile_units_selected"] = 1
    surface.coverage["compile_units_parsed"] = 1
    pack = BuildSourcePack(
        root=Path(""),
        build_evidence=BuildEvidence(compile_units=[CompileUnit(id="cu1", source="a.c")]),
        source_abi=surface,
    )
    snap.build_source = pack

    assert evidence_depth_label(snap) == "build"
    check_requested_depth_satisfied("source", snap)  # must not raise


def test_fold_dump_provenance_uses_gated_label_for_zero_match_source_case() -> None:
    """Codex review: fold_dump_provenance_into_json previously computed
    effective_depth via the plain evidence_depth_label, which -- for this
    exact zero-match source-only case -- reports "build" even though
    check_requested_depth_satisfied('source', snap) just accepted it via
    _gated_source_label. A --depth source dump that the strict gate had
    just satisfied moments earlier would then serialize
    effective_depth: "build", degraded: true -- self-contradictory. Must
    use the same gated label the strict check used."""
    import json

    from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
    from abicheck.buildsource.pack import BuildSourcePack
    from abicheck.buildsource.source_abi import SourceAbiSurface
    from abicheck.cli_dump_helpers import fold_dump_provenance_into_json

    snap = AbiSnapshot(library="libfoo.so", version="1.0", from_headers=True)
    surface = SourceAbiSurface()
    surface.coverage["compile_units_selected"] = 1
    surface.coverage["compile_units_parsed"] = 1
    pack = BuildSourcePack(
        root=Path(""),
        build_evidence=BuildEvidence(compile_units=[CompileUnit(id="cu1", source="a.c")]),
        source_abi=surface,
    )
    snap.build_source = pack

    text, resolved_label = fold_dump_provenance_into_json("{}", "source", snap)
    provenance = json.loads(text)["dump_provenance"]
    assert provenance["effective_depth"] == "source"
    assert provenance["degraded"] is False
    # External review: cli.dump_cmd's "Resolved evidence depth: ..." stderr
    # echo reuses this returned label verbatim instead of recomputing via
    # the plain evidence_depth_label -- pin that the two can never disagree
    # for this exact case (JSON: "source" vs. a stale stderr "build").
    assert resolved_label == provenance["effective_depth"] == "source"


def test_fold_dump_provenance_malformed_json_returns_gated_label() -> None:
    """The pre-json.loads early-return path: if `text` isn't valid JSON at
    all, fold_dump_provenance_into_json must still return the same
    (text, effective_depth) tuple shape the caller relies on for its stderr
    echo -- effective computed via the same _gated_source_label used on the
    happy path, not left unset or recomputed differently."""
    from abicheck.cli_dump_helpers import fold_dump_provenance_into_json

    snap = AbiSnapshot(library="libfoo.so", version="1.0")

    text, resolved_label = fold_dump_provenance_into_json(
        "not valid json {{{", "binary", snap,
    )

    assert text == "not valid json {{{"
    assert resolved_label == "binary"


def test_fold_dump_provenance_falls_back_to_l4_extractor_when_ast_producer_absent() -> None:
    """Codex review: a symbol-only ELF dump (no -H headers) returns from
    dumper._build_symbol_only_snapshot before the L2 header-AST pipeline ever
    runs, so ast_producer stays None even when embed_build_source went on to
    run a real L4 source_abi:<extractor> replay over --sources. Without a
    fallback, dump_provenance.frontend silently loses the extractor identity
    for the (common) --sources/--build-info --depth build|source run with no
    -H. The pack's extractor ledger (buildsource.inline._run_inline_source_abi
    always records source_abi:<extractor>, success or not) is the correct
    fallback source."""
    import json

    from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
    from abicheck.buildsource.model import ExtractorRecord
    from abicheck.buildsource.pack import BuildSourcePack
    from abicheck.buildsource.source_abi import SourceAbiSurface
    from abicheck.cli_dump_helpers import fold_dump_provenance_into_json

    snap = AbiSnapshot(library="libfoo.so", version="1.0", from_headers=False)
    assert snap.ast_producer is None
    surface = SourceAbiSurface()
    surface.coverage["compile_units_selected"] = 1
    surface.coverage["compile_units_parsed"] = 1
    pack = BuildSourcePack(
        root=Path(""),
        build_evidence=BuildEvidence(compile_units=[CompileUnit(id="cu1", source="a.c")]),
        source_abi=surface,
    )
    pack.manifest.extractors = [
        ExtractorRecord(name="compile_commands", status="ok"),
        ExtractorRecord(
            name="source_abi:clang", status="ok", detail="scope=target, 1/1 TUs parsed"
        ),
    ]
    snap.build_source = pack

    text, _ = fold_dump_provenance_into_json("{}", "source", snap)
    provenance = json.loads(text)["dump_provenance"]
    assert provenance["frontend"] == "clang"


def test_fold_dump_provenance_prefers_l4_frontend_over_ast_producer_at_source_depth() -> None:
    """CodeRabbit review: a header snapshot parsed with one backend (e.g.
    castxml/hybrid, recorded as ast_producer) combined with a prebuilt L4
    pack from a *different* extractor (e.g. clang) must record the L4
    extractor as dump_provenance.frontend once the effective depth is
    "source" -- ast_producer names the unrelated L2 header-AST backend, not
    what actually produced the source-depth evidence. Below "source",
    ast_producer stays authoritative (covered by the sibling "falls back to
    l4 extractor when ast_producer absent" test above)."""
    import json

    from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
    from abicheck.buildsource.model import ExtractorRecord
    from abicheck.buildsource.pack import BuildSourcePack
    from abicheck.buildsource.source_abi import SourceAbiSurface
    from abicheck.cli_dump_helpers import fold_dump_provenance_into_json

    snap = AbiSnapshot(
        library="libfoo.so", version="1.0", from_headers=True, ast_producer="castxml",
    )
    surface = SourceAbiSurface()
    surface.coverage["compile_units_selected"] = 1
    surface.coverage["compile_units_parsed"] = 1
    pack = BuildSourcePack(
        root=Path(""),
        build_evidence=BuildEvidence(compile_units=[CompileUnit(id="cu1", source="a.c")]),
        source_abi=surface,
    )
    pack.manifest.extractors = [
        ExtractorRecord(name="source_abi:clang", status="ok"),
    ]
    snap.build_source = pack

    text, _ = fold_dump_provenance_into_json("{}", "source", snap)
    provenance = json.loads(text)["dump_provenance"]
    assert provenance["effective_depth"] == "source"
    assert provenance["frontend"] == "clang"


def test_fold_dump_provenance_keeps_ast_producer_below_source_depth() -> None:
    """Below "source" depth, ast_producer is the only frontend that ran at
    all (no L4 replay contributed to the effective depth), so it stays
    authoritative even if a build_source pack happens to carry an unrelated
    L4 extractor record from a prior/partial run."""
    import json

    from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
    from abicheck.buildsource.model import ExtractorRecord
    from abicheck.buildsource.pack import BuildSourcePack
    from abicheck.cli_dump_helpers import fold_dump_provenance_into_json

    snap = AbiSnapshot(
        library="libfoo.so", version="1.0", from_headers=True, ast_producer="castxml",
    )
    pack = BuildSourcePack(
        root=Path(""),
        build_evidence=BuildEvidence(compile_units=[CompileUnit(id="cu1", source="a.c")]),
    )
    pack.manifest.extractors = [ExtractorRecord(name="source_abi:clang", status="failed")]
    snap.build_source = pack

    text, _ = fold_dump_provenance_into_json("{}", "build", snap)
    provenance = json.loads(text)["dump_provenance"]
    assert provenance["effective_depth"] == "build"
    assert provenance["frontend"] == "castxml"


def test_fold_dump_provenance_frontend_none_when_no_l4_replay_and_no_ast_producer() -> None:
    """No header AST and no L4 extractor record at all (e.g. a bare --build-info
    dump with only L3 evidence) -- frontend has no source to fall back to and
    must stay None rather than fabricating a value."""
    import json

    from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
    from abicheck.buildsource.model import ExtractorRecord
    from abicheck.buildsource.pack import BuildSourcePack
    from abicheck.cli_dump_helpers import fold_dump_provenance_into_json

    snap = AbiSnapshot(library="libfoo.so", version="1.0", from_headers=False)
    pack = BuildSourcePack(
        root=Path(""),
        build_evidence=BuildEvidence(compile_units=[CompileUnit(id="cu1", source="a.c")]),
    )
    pack.manifest.extractors = [ExtractorRecord(name="compile_commands", status="ok")]
    snap.build_source = pack

    text, _ = fold_dump_provenance_into_json("{}", "build", snap)
    provenance = json.loads(text)["dump_provenance"]
    assert provenance["frontend"] is None
    assert provenance["source_scope"] is None


def test_fold_dump_provenance_source_scope_none_without_source_abi() -> None:
    """External review: dump_provenance.source_scope previously hardcoded
    "target" unconditionally, even for a build_source pack with no L4
    source_abi surface at all (e.g. a --depth build dump, L3 only) -- must
    report None rather than fabricating a scope no replay ever ran at."""
    import json

    from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
    from abicheck.buildsource.pack import BuildSourcePack
    from abicheck.cli_dump_helpers import fold_dump_provenance_into_json

    snap = AbiSnapshot(library="libfoo.so", version="1.0")
    pack = BuildSourcePack(
        root=Path(""),
        build_evidence=BuildEvidence(compile_units=[CompileUnit(id="cu1", source="a.c")]),
    )
    snap.build_source = pack

    text, _ = fold_dump_provenance_into_json("{}", "build", snap)
    provenance = json.loads(text)["dump_provenance"]
    assert provenance["source_scope"] is None


def test_fold_dump_provenance_source_scope_reads_prebuilt_pack_replay_scope() -> None:
    """External review: dump also accepts a *prebuilt* --build-info pack,
    which can have been collected at any replay scope (e.g. "changed"/"full"
    from a `collect --depth source --since ...`/`graph-full` run, not just
    dump's own inline-embed "target"). Hardcoding "target" unconditionally
    misreported that pack's actual scope; must read
    source_abi.coverage["replay_scope"] instead of assuming."""
    import json

    from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
    from abicheck.buildsource.pack import BuildSourcePack
    from abicheck.buildsource.source_abi import SourceAbiSurface
    from abicheck.cli_dump_helpers import fold_dump_provenance_into_json

    snap = AbiSnapshot(library="libfoo.so", version="1.0")
    surface = SourceAbiSurface()
    surface.coverage["compile_units_selected"] = 1
    surface.coverage["compile_units_parsed"] = 1
    surface.coverage["replay_scope"] = "changed"
    pack = BuildSourcePack(
        root=Path(""),
        build_evidence=BuildEvidence(compile_units=[CompileUnit(id="cu1", source="a.c")]),
        source_abi=surface,
    )
    snap.build_source = pack

    text, _ = fold_dump_provenance_into_json("{}", "source", snap)
    provenance = json.loads(text)["dump_provenance"]
    assert provenance["source_scope"] == "changed"


def test_l4_source_abi_was_attempted_false_for_unavailable_extractor() -> None:
    """Codex review (fifth finding): _run_inline_source_abi returns the same
    empty-surface, PARTIAL-coverage shape both when a source-only dump
    legitimately links zero declarations (no binary to match against) AND
    when the selected extractor is missing from PATH -- coverage *status*
    alone cannot tell them apart. The gate must key off
    SourceAbiSurface.coverage['compile_units_parsed'] (set only when replay
    actually executed) rather than the coverage row's PRESENT/PARTIAL status,
    so a categorically-failed extraction does not silently satisfy an
    explicit --depth source."""
    from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
    from abicheck.buildsource.model import CoverageStatus, DataLayer, LayerCoverage
    from abicheck.buildsource.pack import BuildSourcePack
    from abicheck.buildsource.source_abi import SourceAbiSurface
    from abicheck.cli_dump_helpers import _l4_source_abi_was_attempted

    pack = BuildSourcePack(
        root=Path(""),
        build_evidence=BuildEvidence(compile_units=[CompileUnit(id="cu1", source="a.c")]),
        # The exact shape _run_inline_source_abi returns when impl.available()
        # is False: a bare SourceAbiSurface() with no coverage dict populated
        # (replay never ran to set compile_units_parsed).
        source_abi=SourceAbiSurface(),
    )
    pack.manifest.coverage = [
        LayerCoverage(layer=DataLayer.L3_BUILD.value, status=CoverageStatus.PRESENT),
        LayerCoverage(layer=DataLayer.L4_SOURCE_ABI.value, status=CoverageStatus.PARTIAL),
        LayerCoverage(layer=DataLayer.L5_SOURCE_GRAPH.value, status=CoverageStatus.NOT_COLLECTED),
    ]

    assert _l4_source_abi_was_attempted(pack) is False


def test_l4_source_abi_was_attempted_true_for_zero_linked_but_parsed_tus() -> None:
    """The counterpart to the unavailable-extractor case above: replay
    genuinely ran and parsed TUs (compile_units_parsed > 0) but linked zero
    declarations -- the expected, warn-only outcome for a source-only `dump
    --sources` with no binary to link against. This must still count as
    "attempted", so it can still satisfy an explicit --depth source (matching
    the existing G21.7 warn-not-error behavior for the same scenario)."""
    from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
    from abicheck.buildsource.model import CoverageStatus, DataLayer, LayerCoverage
    from abicheck.buildsource.pack import BuildSourcePack
    from abicheck.buildsource.source_abi import SourceAbiSurface
    from abicheck.cli_dump_helpers import _l4_source_abi_was_attempted

    surface = SourceAbiSurface()
    surface.coverage["compile_units_selected"] = 1
    surface.coverage["compile_units_parsed"] = 1
    pack = BuildSourcePack(
        root=Path(""),
        build_evidence=BuildEvidence(compile_units=[CompileUnit(id="cu1", source="a.c")]),
        source_abi=surface,
    )
    pack.manifest.coverage = [
        LayerCoverage(layer=DataLayer.L3_BUILD.value, status=CoverageStatus.PRESENT),
        LayerCoverage(layer=DataLayer.L4_SOURCE_ABI.value, status=CoverageStatus.PARTIAL),
        LayerCoverage(layer=DataLayer.L5_SOURCE_GRAPH.value, status=CoverageStatus.NOT_COLLECTED),
    ]

    assert _l4_source_abi_was_attempted(pack) is True
