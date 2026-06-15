# SPDX-License-Identifier: Apache-2.0
"""G20 Phase 2 — cross-source corroboration scenarios (ADR-035 D4).

The "1 + 1 > 2" thesis: a finding invisible or ambiguous to any single evidence
source, resolved only by crosschecking two. Each scenario pairs a **positive**
(the divergence is present) with a **clean negative** (the healthy build) so the
corpus proves no false positive on the well-formed case — the FP-rate gate's 0/0
contract in test form.

Pure-Python synthetic ``AbiSnapshot``s (no compiler/castxml); runs in the default
lane. Mirrors the ``_snap``/``_findings_of``/``_coverage`` pattern of
``tests/test_crosscheck.py``.
"""

from __future__ import annotations

from abicheck.buildsource.build_evidence import BuildEvidence, BuildOption
from abicheck.buildsource.crosscheck import (
    CHECK_HEADER_BUILD_CONTEXT_MISMATCH,
    CHECK_ODR_TYPE_VARIANT,
    CHECK_PRIVATE_HEADER_LEAK,
    PROVIDER_PUBLIC_HEADER_AST,
    PROVIDER_SOURCE_INDEX,
    run_crosschecks,
)
from abicheck.buildsource.pack import BuildSourcePack
from abicheck.buildsource.source_abi import SourceAbiSurface, SourceEntity
from abicheck.buildsource.source_graph import SourceGraphSummary
from abicheck.checker_policy import ChangeKind, Confidence
from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.model import AbiSnapshot, Function, RecordType, ScopeOrigin


def _snap(**kw) -> AbiSnapshot:
    kw.setdefault("library", "libfoo.so")
    kw.setdefault("version", "1.0")
    kw.setdefault("from_headers", True)
    return AbiSnapshot(**kw)


def _findings(res, kind: ChangeKind):
    return [c for c in res.findings if c.kind == kind]


def _coverage(res, check: str) -> dict:
    return next(r for r in res.coverage if r["layer"] == f"crosscheck:{check}")


# --------------------------------------------------------------------------- #
# header_build_context_mismatch — L2 macro context vs L3 build flags
# --------------------------------------------------------------------------- #
def _build_pack(*abi_flags: str) -> BuildSourcePack:
    be = BuildEvidence(
        build_options=[
            BuildOption(key=k, value="1", abi_relevant=True) for k in abi_flags
        ]
    )
    return BuildSourcePack(root="", build_evidence=be)


def test_header_build_mismatch_needs_both_macro_and_flag_sources():
    # Positive: headers parsed WITHOUT the build's ABI flags -> recorded layout
    # is untrustworthy. Only L2 (parsed-without-context) ↔ L3 (flags present)
    # together expose it. Neither source alone reaches the finding.
    snap = _snap(
        build_source=_build_pack("glibcxx_use_cxx11_abi", "define:BIG_BUFFERS")
    )
    snap.parsed_with_build_context = False
    res = run_crosschecks(snap)
    hits = _findings(res, ChangeKind.HEADER_BUILD_CONTEXT_MISMATCH)
    assert len(hits) == 1
    assert _coverage(res, CHECK_HEADER_BUILD_CONTEXT_MISMATCH)["status"] == "present"


def test_header_build_mismatch_clean_when_parsed_with_context():
    # Negative (FP guard): the healthy build parsed headers WITH the flags.
    snap = _snap(build_source=_build_pack("glibcxx_use_cxx11_abi"))
    snap.parsed_with_build_context = True
    res = run_crosschecks(snap)
    assert _findings(res, ChangeKind.HEADER_BUILD_CONTEXT_MISMATCH) == []
    assert _coverage(res, CHECK_HEADER_BUILD_CONTEXT_MISMATCH)["status"] == "present"


# --------------------------------------------------------------------------- #
# odr_type_variant — per-TU layout vs layout (L4 only)
# --------------------------------------------------------------------------- #
def test_odr_variant_needs_per_tu_surface():
    # Positive: two TUs disagree on one public type's layout — recorded only on
    # the L4 source-replay surface; no artifact layer sees it.
    surface = SourceAbiSurface(
        odr_conflicts=[
            {
                "qualified_name": "geometry::Vec3",
                "header": "vec3.h",
                "old_type_hash": "a",
                "new_type_hash": "b",
            }
        ]
    )
    snap = _snap(build_source=BuildSourcePack(root="", source_abi=surface))
    res = run_crosschecks(snap)
    hits = _findings(res, ChangeKind.ODR_TYPE_VARIANT)
    assert [c.caused_by_type for c in hits] == ["geometry::Vec3"]


def test_odr_variant_clean_when_surface_consistent():
    # Negative (FP guard): a real L4 surface with a reachable type but no ODR
    # conflict is genuinely clean -> present, zero findings.
    surface = SourceAbiSurface(
        reachable_types=[
            SourceEntity(id="t1", kind="record", qualified_name="geometry::Vec3")
        ]
    )
    snap = _snap(build_source=BuildSourcePack(root="", source_abi=surface))
    res = run_crosschecks(snap)
    assert _findings(res, ChangeKind.ODR_TYPE_VARIANT) == []
    assert _coverage(res, CHECK_ODR_TYPE_VARIANT)["status"] == "present"


# --------------------------------------------------------------------------- #
# exported_not_public / public_not_exported — bidirectional L0↔L2 pair
# --------------------------------------------------------------------------- #
def test_export_decl_pair_trips_both_directions():
    snap = _snap(
        elf=ElfMetadata(symbols=[ElfSymbol(name="_Z8internalv", is_default=True)])
    )
    snap.functions = [
        Function(
            name="internal",
            mangled="_Z8internalv",
            return_type="void",
            origin=ScopeOrigin.EXPORT_ONLY,
        ),
        Function(
            name="public_api",
            mangled="_Z10public_apiv",
            return_type="void",
            origin=ScopeOrigin.PUBLIC_HEADER,
        ),
    ]
    res = run_crosschecks(snap)
    assert [c.symbol for c in _findings(res, ChangeKind.EXPORTED_NOT_PUBLIC)] == [
        "_Z8internalv"
    ]
    assert [c.symbol for c in _findings(res, ChangeKind.PUBLIC_NOT_EXPORTED)] == [
        "_Z10public_apiv"
    ]


def test_export_decl_pair_clean_when_contract_holds():
    # Negative (FP guard): every export is declared and every decl is exported.
    snap = _snap(
        elf=ElfMetadata(symbols=[ElfSymbol(name="_Z10public_apiv", is_default=True)])
    )
    snap.functions = [
        Function(
            name="public_api",
            mangled="_Z10public_apiv",
            return_type="void",
            origin=ScopeOrigin.PUBLIC_HEADER,
        ),
    ]
    res = run_crosschecks(snap)
    assert _findings(res, ChangeKind.EXPORTED_NOT_PUBLIC) == []
    assert _findings(res, ChangeKind.PUBLIC_NOT_EXPORTED) == []


# --------------------------------------------------------------------------- #
# provider-agreement matrix (§6.8) — corroboration grows with the evidence
# --------------------------------------------------------------------------- #
def _leak_snap(*, with_graph: bool) -> AbiSnapshot:
    snap = _snap(elf=ElfMetadata(symbols=[ElfSymbol(name="_Z3usev")]))
    snap.functions = [
        Function(
            name="use",
            mangled="_Z3usev",
            return_type="detail::Impl *",
            origin=ScopeOrigin.PUBLIC_HEADER,
        ),
    ]
    snap.types = [
        RecordType(
            name="detail::Impl", kind="struct", origin=ScopeOrigin.PRIVATE_HEADER
        ),
    ]
    if with_graph:
        snap.build_source = BuildSourcePack(root="", source_graph=SourceGraphSummary())
    return snap


def test_provider_matrix_records_more_providers_with_more_evidence():
    rich = run_crosschecks(_leak_snap(with_graph=True))
    thin = run_crosschecks(_leak_snap(with_graph=False))
    # Same finding in both — the divergence is the provider list, not the verdict.
    assert _findings(rich, ChangeKind.PRIVATE_HEADER_LEAK)
    assert _findings(thin, ChangeKind.PRIVATE_HEADER_LEAK)
    rich_p = rich.providers[CHECK_PRIVATE_HEADER_LEAK]
    thin_p = thin.providers[CHECK_PRIVATE_HEADER_LEAK]
    assert thin_p == [PROVIDER_PUBLIC_HEADER_AST]
    assert rich_p == [PROVIDER_PUBLIC_HEADER_AST, PROVIDER_SOURCE_INDEX]
    assert set(thin_p) < set(rich_p)


def test_provider_matrix_finding_confidence_is_unchanged_by_provider_count():
    # Scope guard (plan §5): the engine records the provider LIST per check but
    # stamps the finding's Confidence independent of provider count — deriving a
    # confidence tag from the count is a separate, out-of-scope enhancement.
    rich = run_crosschecks(_leak_snap(with_graph=True))
    thin = run_crosschecks(_leak_snap(with_graph=False))
    cr = _findings(rich, ChangeKind.PRIVATE_HEADER_LEAK)[0]
    ct = _findings(thin, ChangeKind.PRIVATE_HEADER_LEAK)[0]
    assert cr.confidence == ct.confidence == Confidence.MEDIUM
