# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Tests for the five cross-source checks this PR migrates onto ADR-068 D3 /
plan P2's ``CrossSourceEvolution`` state: ``header_build_context_mismatch``,
``odr_type_variant``, ``identity_collision_detected``,
``compile_context_conflict``, and ``source_surface_dso_mismatch`` (plan §3
#3, closing out the row -- the sixth checks landed in
``test_cross_source_evolution.py``, split out here purely to keep that file
under the architecture gate's test-file line cap, same reasoning as
``test_call_graph_extra.py``'s own split from ``test_call_graph.py``).

Same 3x3 (OLD state, NEW state) evidence/finding matrix pattern per check as
the sibling module, plus a ``TestFiveMoreChecksNotEvaluatedCrux`` class
generalizing the crux (plan §7 F-8/F-9) across all five, and an identity
test for each of the three checks (``odr_type_variant``, ``identity_
collision_detected``, ``compile_context_conflict``) that needed the same
per-check identity generalization ``workflows.cross_source_evolution``
already applies to ``private_header_leak``/``rtti_for_internal_type``/
``public_to_internal_dependency`` -- see that module's own docstring.
"""

from __future__ import annotations

import pytest

from abicheck.buildsource.build_evidence import BuildEvidence, BuildOption, CompileUnit
from abicheck.buildsource.pack import BuildSourcePack
from abicheck.buildsource.source_abi import SourceAbiSurface, SourceEntity
from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind, CrossSourceEvolution
from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.model import AbiSnapshot
from abicheck.workflows.cross_source_evolution import compute_cross_source_evolution

# --------------------------------------------------------------------------- #
# The five checks this PR migrates onto compute_cross_source_evolution:
# header_build_context_mismatch, odr_type_variant, identity_collision_
# detected, compile_context_conflict, and source_surface_dso_mismatch
# (plan §3 #3, closing out the row). Same 3x3 matrix pattern as the checks
# above.
# --------------------------------------------------------------------------- #

_HBCM_NONE = "no_evidence"  # no L3 build evidence at all
_HBCM_CLEAN = "evidence_clean"  # headers parsed WITH the build context
_HBCM_FLAG = "evidence_flagged"


def _hbcm_snapshot(state: str) -> AbiSnapshot:
    """``header_build_context_mismatch``: ABI-relevant build flags recorded
    but the headers parsed context-free (mirrors ``test_crosscheck.py``'s
    own fixture shape)."""
    if state == _HBCM_NONE:
        return AbiSnapshot(library="libfoo.so", version="1.0", from_headers=True)
    if state not in (_HBCM_CLEAN, _HBCM_FLAG):
        raise ValueError(state)
    build_evidence = BuildEvidence(
        build_options=[BuildOption(key="std", value="c++17", abi_relevant=True)]
    )
    return AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        from_headers=True,
        parsed_with_build_context=(state == _HBCM_CLEAN),
        build_source=BuildSourcePack(root="", build_evidence=build_evidence),
    )


_HBCM_MATRIX: dict[tuple[str, str], CrossSourceEvolution | None] = {
    (_HBCM_NONE, _HBCM_NONE): None,
    (_HBCM_NONE, _HBCM_CLEAN): None,
    (_HBCM_NONE, _HBCM_FLAG): CrossSourceEvolution.NOT_EVALUATED,
    (_HBCM_CLEAN, _HBCM_NONE): None,
    (_HBCM_CLEAN, _HBCM_CLEAN): None,
    (_HBCM_CLEAN, _HBCM_FLAG): CrossSourceEvolution.INTRODUCED,
    (_HBCM_FLAG, _HBCM_NONE): CrossSourceEvolution.NOT_EVALUATED,
    (_HBCM_FLAG, _HBCM_CLEAN): CrossSourceEvolution.RESOLVED,
    (_HBCM_FLAG, _HBCM_FLAG): CrossSourceEvolution.PERSISTENT,
}


@pytest.mark.parametrize("old_state, new_state", sorted(_HBCM_MATRIX))
def test_header_build_context_mismatch_evolution_matrix(
    old_state: str, new_state: str
) -> None:
    old = _hbcm_snapshot(old_state)
    new = _hbcm_snapshot(new_state)
    changes = compute_cross_source_evolution(old, new)
    hits = [c for c in changes if c.kind == ChangeKind.HEADER_BUILD_CONTEXT_MISMATCH]
    expected = _HBCM_MATRIX[(old_state, new_state)]
    if expected is None:
        assert hits == [], f"unexpected finding for ({old_state}, {new_state})"
    else:
        assert len(hits) == 1
        assert hits[0].cross_source_evolution == expected


def test_header_build_context_mismatch_authority_unchanged() -> None:
    from abicheck.checker_policy import API_BREAK_KINDS

    assert ChangeKind.HEADER_BUILD_CONTEXT_MISMATCH in API_BREAK_KINDS
    old = _hbcm_snapshot(_HBCM_NONE)
    new = _hbcm_snapshot(_HBCM_FLAG)
    for c in compute_cross_source_evolution(old, new):
        if c.kind == ChangeKind.HEADER_BUILD_CONTEXT_MISMATCH:
            assert c.effective_verdict is None


def test_header_build_context_mismatch_wired_into_compare_by_default() -> None:
    old = _hbcm_snapshot(_HBCM_NONE)
    new = _hbcm_snapshot(_HBCM_FLAG)
    result = compare(old, new, scope_to_public_surface=False)
    hits = [
        c
        for c in result.changes
        if c.kind == ChangeKind.HEADER_BUILD_CONTEXT_MISMATCH
    ]
    assert len(hits) == 1
    assert hits[0].cross_source_evolution == CrossSourceEvolution.NOT_EVALUATED


_ODR_NONE = "no_evidence"  # no L4 source-ABI surface at all
_ODR_CLEAN = "evidence_clean"  # surface present, no recorded ODR conflicts
_ODR_FLAG = "evidence_flagged"


def _odr_snapshot(state: str, header: str = "widget.h") -> AbiSnapshot:
    """``odr_type_variant``: a recorded L4 cross-TU layout conflict for one
    type (mirrors ``test_crosscheck.py``'s own fixture shape)."""
    if state == _ODR_NONE:
        return AbiSnapshot(library="libfoo.so", version="1.0", from_headers=True)
    if state not in (_ODR_CLEAN, _ODR_FLAG):
        raise ValueError(state)
    surface = SourceAbiSurface(
        reachable_declarations=[
            SourceEntity(id="d0", kind="function", qualified_name="f")
        ],
        odr_conflicts=(
            [{"qualified_name": "Widget", "header": header}]
            if state == _ODR_FLAG
            else []
        ),
    )
    return AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        from_headers=True,
        build_source=BuildSourcePack(root="", source_abi=surface),
    )


_ODR_MATRIX: dict[tuple[str, str], CrossSourceEvolution | None] = {
    (_ODR_NONE, _ODR_NONE): None,
    (_ODR_NONE, _ODR_CLEAN): None,
    (_ODR_NONE, _ODR_FLAG): CrossSourceEvolution.NOT_EVALUATED,
    (_ODR_CLEAN, _ODR_NONE): None,
    (_ODR_CLEAN, _ODR_CLEAN): None,
    (_ODR_CLEAN, _ODR_FLAG): CrossSourceEvolution.INTRODUCED,
    (_ODR_FLAG, _ODR_NONE): CrossSourceEvolution.NOT_EVALUATED,
    (_ODR_FLAG, _ODR_CLEAN): CrossSourceEvolution.RESOLVED,
    (_ODR_FLAG, _ODR_FLAG): CrossSourceEvolution.PERSISTENT,
}


@pytest.mark.parametrize("old_state, new_state", sorted(_ODR_MATRIX))
def test_odr_type_variant_evolution_matrix(old_state: str, new_state: str) -> None:
    old = _odr_snapshot(old_state)
    new = _odr_snapshot(new_state)
    changes = compute_cross_source_evolution(old, new)
    hits = [c for c in changes if c.kind == ChangeKind.ODR_TYPE_VARIANT]
    expected = _ODR_MATRIX[(old_state, new_state)]
    if expected is None:
        assert hits == [], f"unexpected finding for ({old_state}, {new_state})"
    else:
        assert len(hits) == 1
        assert hits[0].cross_source_evolution == expected


def test_odr_type_variant_identity_distinguishes_two_headers_same_type() -> None:
    """The generalization this check needed: the same qualified type name
    can carry more than one recorded ODR conflict when it's declared/defined
    across more than one header -- a bare ``symbol`` identity would collapse
    two genuinely distinct per-header conflicts sharing one type name onto a
    single OLD/NEW pairing (same shape as ``private_header_leak``'s own
    motivating case)."""
    old = _odr_snapshot(_ODR_FLAG, header="old.h")
    new = _odr_snapshot(_ODR_FLAG, header="new.h")
    changes = compute_cross_source_evolution(old, new)
    hits = [c for c in changes if c.kind == ChangeKind.ODR_TYPE_VARIANT]
    by_location = {c.source_location: c.cross_source_evolution for c in hits}
    assert by_location == {
        "old.h": CrossSourceEvolution.RESOLVED,
        "new.h": CrossSourceEvolution.INTRODUCED,
    }


def test_odr_type_variant_authority_unchanged() -> None:
    from abicheck.checker_policy import API_BREAK_KINDS

    assert ChangeKind.ODR_TYPE_VARIANT in API_BREAK_KINDS
    old = _odr_snapshot(_ODR_NONE)
    new = _odr_snapshot(_ODR_FLAG)
    for c in compute_cross_source_evolution(old, new):
        if c.kind == ChangeKind.ODR_TYPE_VARIANT:
            assert c.effective_verdict is None


def test_odr_type_variant_wired_into_compare_by_default() -> None:
    old = _odr_snapshot(_ODR_NONE)
    new = _odr_snapshot(_ODR_FLAG)
    result = compare(old, new, scope_to_public_surface=False)
    hits = [c for c in result.changes if c.kind == ChangeKind.ODR_TYPE_VARIANT]
    assert len(hits) == 1
    assert hits[0].cross_source_evolution == CrossSourceEvolution.NOT_EVALUATED


_ICD_NONE = "no_evidence"  # no L4 source-ABI surface at all
_ICD_CLEAN = "evidence_clean"  # surface present, no recorded collisions
_ICD_FLAG = "evidence_flagged"


def _icd_snapshot(state: str, identity: str = "f#sha256:abc") -> AbiSnapshot:
    """``identity_collision_detected``: two distinct declarations linked
    onto one L4 identity key (mirrors ``test_crosscheck.py``'s own fixture
    shape)."""
    if state == _ICD_NONE:
        return AbiSnapshot(library="libfoo.so", version="1.0", from_headers=True)
    if state not in (_ICD_CLEAN, _ICD_FLAG):
        raise ValueError(state)
    collisions = (
        [
            {
                "identity": identity,
                "qualified_name": "f",
                "usr_a": "c:@F@f#",
                "usr_b": "c:@N@ns@F@f#",
            }
        ]
        if state == _ICD_FLAG
        else []
    )
    surface = SourceAbiSurface(
        reachable_declarations=[
            SourceEntity(id="d0", kind="function", qualified_name="f")
        ],
        identity_collisions=collisions,
    )
    return AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        from_headers=True,
        build_source=BuildSourcePack(root="", source_abi=surface),
    )


_ICD_MATRIX: dict[tuple[str, str], CrossSourceEvolution | None] = {
    (_ICD_NONE, _ICD_NONE): None,
    (_ICD_NONE, _ICD_CLEAN): None,
    (_ICD_NONE, _ICD_FLAG): CrossSourceEvolution.NOT_EVALUATED,
    (_ICD_CLEAN, _ICD_NONE): None,
    (_ICD_CLEAN, _ICD_CLEAN): None,
    (_ICD_CLEAN, _ICD_FLAG): CrossSourceEvolution.INTRODUCED,
    (_ICD_FLAG, _ICD_NONE): CrossSourceEvolution.NOT_EVALUATED,
    (_ICD_FLAG, _ICD_CLEAN): CrossSourceEvolution.RESOLVED,
    (_ICD_FLAG, _ICD_FLAG): CrossSourceEvolution.PERSISTENT,
}


@pytest.mark.parametrize("old_state, new_state", sorted(_ICD_MATRIX))
def test_identity_collision_detected_evolution_matrix(
    old_state: str, new_state: str
) -> None:
    old = _icd_snapshot(old_state)
    new = _icd_snapshot(new_state)
    changes = compute_cross_source_evolution(old, new)
    hits = [c for c in changes if c.kind == ChangeKind.IDENTITY_COLLISION_DETECTED]
    expected = _ICD_MATRIX[(old_state, new_state)]
    if expected is None:
        assert hits == [], f"unexpected finding for ({old_state}, {new_state})"
    else:
        assert len(hits) == 1
        assert hits[0].cross_source_evolution == expected


def test_identity_collision_detected_identity_distinguishes_two_collisions() -> None:
    """The generalization this check needed: the same colliding qualified
    name can plausibly resolve to more than one distinct identity key across
    separate USR pairs -- a bare ``symbol`` identity would collapse two
    genuinely distinct collisions sharing one qualified name."""
    old = _icd_snapshot(_ICD_FLAG, identity="f#sha256:old")
    new = _icd_snapshot(_ICD_FLAG, identity="f#sha256:new")
    changes = compute_cross_source_evolution(old, new)
    hits = [c for c in changes if c.kind == ChangeKind.IDENTITY_COLLISION_DETECTED]
    by_value = {c.new_value: c.cross_source_evolution for c in hits}
    assert by_value == {
        "f#sha256:old": CrossSourceEvolution.RESOLVED,
        "f#sha256:new": CrossSourceEvolution.INTRODUCED,
    }


def test_identity_collision_detected_authority_unchanged() -> None:
    from abicheck.checker_policy import RISK_KINDS

    assert ChangeKind.IDENTITY_COLLISION_DETECTED in RISK_KINDS
    old = _icd_snapshot(_ICD_NONE)
    new = _icd_snapshot(_ICD_FLAG)
    for c in compute_cross_source_evolution(old, new):
        if c.kind == ChangeKind.IDENTITY_COLLISION_DETECTED:
            assert c.effective_verdict is None


def test_identity_collision_detected_wired_into_compare_by_default() -> None:
    old = _icd_snapshot(_ICD_NONE)
    new = _icd_snapshot(_ICD_FLAG)
    result = compare(old, new, scope_to_public_surface=False)
    hits = [c for c in result.changes if c.kind == ChangeKind.IDENTITY_COLLISION_DETECTED]
    assert len(hits) == 1
    assert hits[0].cross_source_evolution == CrossSourceEvolution.NOT_EVALUATED


_CCC_NONE = "no_evidence"  # no L3 compile units at all
_CCC_CLEAN = "evidence_clean"  # 2 compile units, no disagreement
_CCC_FLAG = "evidence_flagged"


def _ccc_snapshot(state: str) -> AbiSnapshot:
    """``compile_context_conflict``: one build target's compile units
    disagree on an ABI-relevant flag family (mirrors the synthetic fixture
    ``tests/parity/_synthetic_snapshots.py`` already uses for this check)."""
    if state == _CCC_NONE:
        return AbiSnapshot(library="libfoo.so", version="1.0", from_headers=True)
    if state not in (_CCC_CLEAN, _CCC_FLAG):
        raise ValueError(state)
    second_flags = ["-frtti"] if state == _CCC_CLEAN else ["-fno-rtti"]
    build_evidence = BuildEvidence(
        compile_units=[
            CompileUnit(
                id="a",
                target_id="target://libfoo.so",
                abi_relevant_flags=["-frtti"],
                language="CXX",
            ),
            CompileUnit(
                id="b",
                target_id="target://libfoo.so",
                abi_relevant_flags=second_flags,
                language="CXX",
            ),
        ]
    )
    return AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        from_headers=True,
        build_source=BuildSourcePack(root="", build_evidence=build_evidence),
    )


_CCC_MATRIX: dict[tuple[str, str], CrossSourceEvolution | None] = {
    (_CCC_NONE, _CCC_NONE): None,
    (_CCC_NONE, _CCC_CLEAN): None,
    (_CCC_NONE, _CCC_FLAG): CrossSourceEvolution.NOT_EVALUATED,
    (_CCC_CLEAN, _CCC_NONE): None,
    (_CCC_CLEAN, _CCC_CLEAN): None,
    (_CCC_CLEAN, _CCC_FLAG): CrossSourceEvolution.INTRODUCED,
    (_CCC_FLAG, _CCC_NONE): CrossSourceEvolution.NOT_EVALUATED,
    (_CCC_FLAG, _CCC_CLEAN): CrossSourceEvolution.RESOLVED,
    (_CCC_FLAG, _CCC_FLAG): CrossSourceEvolution.PERSISTENT,
}


@pytest.mark.parametrize("old_state, new_state", sorted(_CCC_MATRIX))
def test_compile_context_conflict_evolution_matrix(
    old_state: str, new_state: str
) -> None:
    old = _ccc_snapshot(old_state)
    new = _ccc_snapshot(new_state)
    changes = compute_cross_source_evolution(old, new)
    hits = [c for c in changes if c.kind == ChangeKind.COMPILE_CONTEXT_CONFLICT]
    expected = _CCC_MATRIX[(old_state, new_state)]
    if expected is None:
        assert hits == [], f"unexpected finding for ({old_state}, {new_state})"
    else:
        assert len(hits) == 1
        assert hits[0].cross_source_evolution == expected


def test_compile_context_conflict_identity_distinguishes_two_conflict_kinds() -> None:
    """The generalization this check needed: one build target can carry
    MORE THAN ONE conflict at once (a flag-family disagreement and a
    `#define` value disagreement both fire off the same target label) --
    a bare ``symbol`` identity, or even ``(symbol, new_value)``, would
    collapse them; this check registers ``(symbol, old_value, new_value)``."""

    def _snap_with_both_conflicts() -> AbiSnapshot:
        build_evidence = BuildEvidence(
            compile_units=[
                CompileUnit(
                    id="a",
                    target_id="target://libfoo.so",
                    abi_relevant_flags=["-frtti"],
                    defines={"FOO": "1"},
                    language="CXX",
                ),
                CompileUnit(
                    id="b",
                    target_id="target://libfoo.so",
                    abi_relevant_flags=["-fno-rtti"],
                    defines={"FOO": "2"},
                    language="CXX",
                ),
            ]
        )
        return AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            build_source=BuildSourcePack(root="", build_evidence=build_evidence),
        )

    old = _snap_with_both_conflicts()
    new = _snap_with_both_conflicts()
    changes = compute_cross_source_evolution(old, new)
    hits = [c for c in changes if c.kind == ChangeKind.COMPILE_CONTEXT_CONFLICT]
    assert len(hits) == 2, "the flag conflict and the define conflict must not collapse"
    assert all(c.cross_source_evolution == CrossSourceEvolution.PERSISTENT for c in hits)
    by_old_value = {c.old_value: c for c in hits}
    assert set(by_old_value) == {"-frtti", "FOO"}


def test_compile_context_conflict_authority_unchanged() -> None:
    from abicheck.checker_policy import RISK_KINDS

    assert ChangeKind.COMPILE_CONTEXT_CONFLICT in RISK_KINDS
    old = _ccc_snapshot(_CCC_NONE)
    new = _ccc_snapshot(_CCC_FLAG)
    for c in compute_cross_source_evolution(old, new):
        if c.kind == ChangeKind.COMPILE_CONTEXT_CONFLICT:
            assert c.effective_verdict is None


def test_compile_context_conflict_wired_into_compare_by_default() -> None:
    old = _ccc_snapshot(_CCC_NONE)
    new = _ccc_snapshot(_CCC_FLAG)
    result = compare(old, new, scope_to_public_surface=False)
    hits = [c for c in result.changes if c.kind == ChangeKind.COMPILE_CONTEXT_CONFLICT]
    assert len(hits) == 1
    assert hits[0].cross_source_evolution == CrossSourceEvolution.NOT_EVALUATED


_SSDM_NONE = "no_evidence"  # no L4 source-ABI surface at all
_SSDM_CLEAN = "evidence_clean"  # surface maps to a symbol this binary exports
_SSDM_FLAG = "evidence_flagged"


def _ssdm_snapshot(state: str) -> AbiSnapshot:
    """``source_surface_dso_mismatch``: the linked L4 surface maps to none
    of this binary's exports (mirrors ``tests/parity/
    _synthetic_snapshots.py``'s own fixture for this check)."""
    exported = ElfMetadata(symbols=[ElfSymbol(name="_Z3foov")])
    if state == _SSDM_NONE:
        return AbiSnapshot(
            library="libfoo.so", version="1.0", from_headers=True, elf=exported
        )
    if state not in (_SSDM_CLEAN, _SSDM_FLAG):
        raise ValueError(state)
    surface = SourceAbiSurface(
        library="libfoo.so",
        reachable_declarations=[
            SourceEntity(id="d0", kind="function", qualified_name="d0")
        ],
    )
    surface.mappings["source_decl_to_binary_symbol"] = {
        "d0": "_Z3foov" if state == _SSDM_CLEAN else "_Z9otherlibv"
    }
    return AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        from_headers=True,
        elf=exported,
        build_source=BuildSourcePack(root="", source_abi=surface),
    )


_SSDM_MATRIX: dict[tuple[str, str], CrossSourceEvolution | None] = {
    (_SSDM_NONE, _SSDM_NONE): None,
    (_SSDM_NONE, _SSDM_CLEAN): None,
    (_SSDM_NONE, _SSDM_FLAG): CrossSourceEvolution.NOT_EVALUATED,
    (_SSDM_CLEAN, _SSDM_NONE): None,
    (_SSDM_CLEAN, _SSDM_CLEAN): None,
    (_SSDM_CLEAN, _SSDM_FLAG): CrossSourceEvolution.INTRODUCED,
    (_SSDM_FLAG, _SSDM_NONE): CrossSourceEvolution.NOT_EVALUATED,
    (_SSDM_FLAG, _SSDM_CLEAN): CrossSourceEvolution.RESOLVED,
    (_SSDM_FLAG, _SSDM_FLAG): CrossSourceEvolution.PERSISTENT,
}


@pytest.mark.parametrize("old_state, new_state", sorted(_SSDM_MATRIX))
def test_source_surface_dso_mismatch_evolution_matrix(
    old_state: str, new_state: str
) -> None:
    old = _ssdm_snapshot(old_state)
    new = _ssdm_snapshot(new_state)
    changes = compute_cross_source_evolution(old, new)
    hits = [c for c in changes if c.kind == ChangeKind.SOURCE_SURFACE_DSO_MISMATCH]
    expected = _SSDM_MATRIX[(old_state, new_state)]
    if expected is None:
        assert hits == [], f"unexpected finding for ({old_state}, {new_state})"
    else:
        assert len(hits) == 1
        assert hits[0].cross_source_evolution == expected


def test_source_surface_dso_mismatch_authority_unchanged() -> None:
    from abicheck.checker_policy import RISK_KINDS

    assert ChangeKind.SOURCE_SURFACE_DSO_MISMATCH in RISK_KINDS
    old = _ssdm_snapshot(_SSDM_NONE)
    new = _ssdm_snapshot(_SSDM_FLAG)
    for c in compute_cross_source_evolution(old, new):
        if c.kind == ChangeKind.SOURCE_SURFACE_DSO_MISMATCH:
            assert c.effective_verdict is None


def test_source_surface_dso_mismatch_wired_into_compare_by_default() -> None:
    old = _ssdm_snapshot(_SSDM_NONE)
    new = _ssdm_snapshot(_SSDM_FLAG)
    result = compare(old, new, scope_to_public_surface=False)
    hits = [c for c in result.changes if c.kind == ChangeKind.SOURCE_SURFACE_DSO_MISMATCH]
    assert len(hits) == 1
    assert hits[0].cross_source_evolution == CrossSourceEvolution.NOT_EVALUATED


class TestFiveMoreChecksNotEvaluatedCrux:
    """The correctness crux (plan §7 F-8/F-9), generalized across the five
    checks this PR migrates, mirroring ``TestFourChecksNotEvaluatedCrux``
    above: a finding flagged on an evaluated side must never read
    INTRODUCED/RESOLVED when its sibling side's own evidence could not
    confirm or deny it."""

    _CASES: tuple[tuple[ChangeKind, object, str, str], ...] = (
        (
            ChangeKind.HEADER_BUILD_CONTEXT_MISMATCH,
            _hbcm_snapshot,
            _HBCM_NONE,
            _HBCM_FLAG,
        ),
        (ChangeKind.ODR_TYPE_VARIANT, _odr_snapshot, _ODR_NONE, _ODR_FLAG),
        (
            ChangeKind.IDENTITY_COLLISION_DETECTED,
            _icd_snapshot,
            _ICD_NONE,
            _ICD_FLAG,
        ),
        (
            ChangeKind.COMPILE_CONTEXT_CONFLICT,
            _ccc_snapshot,
            _CCC_NONE,
            _CCC_FLAG,
        ),
        (
            ChangeKind.SOURCE_SURFACE_DSO_MISMATCH,
            _ssdm_snapshot,
            _SSDM_NONE,
            _SSDM_FLAG,
        ),
    )

    @pytest.mark.parametrize(
        "kind,builder,none_state,flag_state",
        _CASES,
        ids=lambda v: getattr(v, "value", v),
    )
    @pytest.mark.parametrize("swap_sides", [False, True])
    def test_never_introduced_or_resolved_when_a_side_lacks_evidence(
        self, kind, builder, none_state: str, flag_state: str, swap_sides: bool
    ) -> None:
        no_evidence = builder(none_state)
        flagged = builder(flag_state)
        old, new = (flagged, no_evidence) if swap_sides else (no_evidence, flagged)
        changes = compute_cross_source_evolution(old, new)
        hits = [c for c in changes if c.kind == kind]
        assert len(hits) == 1, f"{kind.value}: expected exactly one finding"
        assert hits[0].cross_source_evolution == CrossSourceEvolution.NOT_EVALUATED
        assert hits[0].cross_source_evolution not in (
            CrossSourceEvolution.INTRODUCED,
            CrossSourceEvolution.RESOLVED,
        )
