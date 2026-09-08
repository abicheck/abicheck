# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Tests for the five L3/L4-evidence cross-source checks this PR migrates
onto ``workflows.cross_source_evolution.compute_cross_source_evolution``,
closing out ADR-068 §3 row 3 ("11 of 11 landed"):
``header_build_context_mismatch``, ``odr_type_variant``,
``identity_collision_detected``, ``compile_context_conflict``, and
``source_surface_dso_mismatch``. Split out of ``test_cross_source_evolution.py``
(the sibling module covering the six L0-L2-evidence checks migrated in the
two prior PRs) once that file reached the architecture gate's 1200-line
new-test-size cap -- purely a mechanical split, not a scope change; both
files test the identical ``compute_cross_source_evolution`` primitive.

Unlike the six checks in the sibling module, these five read L3 (build
evidence, ``BuildSourcePack.build_evidence``) or L4 (source-ABI replay,
``BuildSourcePack.source_abi``) evidence rather than L0-L2 (binary exports/
header AST) -- see ``workflows.cross_source_evolution``'s own module
docstring for exactly which evidence tier each check needs and why this
distinction matters more in practice for these five than for the earlier
six (L3/L4/L5 evidence realistically goes missing *asymmetrically* between
OLD/NEW far more often than L0-L2 evidence does).

The crux (plan §7 F-8/F-9) is unchanged from the sibling module: a
pre-existing problem must never read as ``introduced`` merely because one
side's evidence couldn't confirm it. Exercised here as each check's own 3x3
evidence/finding matrix test plus ``TestFiveChecksNotEvaluatedCrux``,
generalizing the property across all five via ``itertools.product`` --
mirroring (not duplicating) the sibling module's own
``TestFourChecksNotEvaluatedCrux``, scoped to this file's own five checks
since the two files intentionally share no fixtures (each is fully
self-contained). The ``odr_type_variant``, ``identity_collision_detected``,
and ``compile_context_conflict`` tests additionally exercise the per-check
identity generalization ``workflows.cross_source_evolution`` needed for
each -- see that module's own docstring.
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

_HBCM_NONE = "no_evidence"  # no L3 build evidence at all
_HBCM_CLEAN = "evidence_clean"
_HBCM_FLAG = "evidence_flagged"


def _hbcm_snapshot(state: str) -> AbiSnapshot:
    """``header_build_context_mismatch``: headers parsed context-free while
    the L3 build evidence records ABI-relevant flags (mirrors
    ``test_crosscheck.py``'s own fixture shape)."""
    if state == _HBCM_NONE:
        return AbiSnapshot(library="libfoo.so", version="1.0", from_headers=True)
    if state not in (_HBCM_CLEAN, _HBCM_FLAG):
        raise ValueError(state)
    options = (
        [BuildOption(key="std", value="c++17", abi_relevant=True)]
        if state == _HBCM_FLAG
        else []
    )
    build_evidence = BuildEvidence(build_options=options)
    return AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        from_headers=True,
        parsed_with_build_context=False,
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
        c for c in result.changes if c.kind == ChangeKind.HEADER_BUILD_CONTEXT_MISMATCH
    ]
    assert len(hits) == 1
    assert hits[0].cross_source_evolution == CrossSourceEvolution.NOT_EVALUATED


_ODR_NONE = "no_evidence"  # no L4 source-ABI surface at all
_ODR_CLEAN = "evidence_clean"
_ODR_FLAG = "evidence_flagged"


def _odr_snapshot(state: str) -> AbiSnapshot:
    """``odr_type_variant``: a type with divergent per-TU layouts recorded
    on the L4 surface (mirrors ``test_crosscheck.py``'s own fixture shape)."""
    if state == _ODR_NONE:
        return AbiSnapshot(library="libfoo.so", version="1.0", from_headers=True)
    if state not in (_ODR_CLEAN, _ODR_FLAG):
        raise ValueError(state)
    conflicts = (
        [{"qualified_name": "Widget", "header": "widget.h"}]
        if state == _ODR_FLAG
        else []
    )
    surface = SourceAbiSurface(
        odr_conflicts=conflicts,
        reachable_declarations=[
            SourceEntity(id="d0", kind="function", qualified_name="f")
        ],
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


def test_odr_type_variant_identity_distinguishes_two_conflicts_same_symbol() -> None:
    """This check's own identity is ``(symbol, source_location)``, not a
    bare ``symbol`` -- two distinct ODR conflicts can share a qualified
    name (the ``"<anonymous>"`` fallback especially), distinguished only by
    the recorded header. Exercised here across OLD/NEW where the identical
    fallback name resolves against a different header on each side."""

    def _snap_with_header(header: str) -> AbiSnapshot:
        surface = SourceAbiSurface(
            odr_conflicts=[{"qualified_name": "<anonymous>", "header": header}],
            reachable_declarations=[
                SourceEntity(id="d0", kind="function", qualified_name="f")
            ],
        )
        return AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            build_source=BuildSourcePack(root="", source_abi=surface),
        )

    from abicheck.buildsource.crosscheck import CHECK_ODR_TYPE_VARIANT
    from abicheck.workflows.cross_source_evolution import _IDENTITY_FUNCS

    identity = _IDENTITY_FUNCS[CHECK_ODR_TYPE_VARIANT]
    old = _snap_with_header("a.h")
    new = _snap_with_header("b.h")
    changes = compute_cross_source_evolution(old, new)
    hits = [c for c in changes if c.kind == ChangeKind.ODR_TYPE_VARIANT]
    by_evolution = {c.cross_source_evolution: identity(c) for c in hits}
    # `_check_odr_type_variant` now groups by (qualified_name, header) and
    # emits one Change per group, which makes (symbol, source_location)
    # unique on its own -- `_IDENTITY_FUNCS[CHECK_ODR_TYPE_VARIANT]` no
    # longer needs old_value/new_value as a tiebreaker.
    assert by_evolution == {
        CrossSourceEvolution.RESOLVED: ("<anonymous>", "a.h"),
        CrossSourceEvolution.INTRODUCED: ("<anonymous>", "b.h"),
    }


def test_odr_type_variant_three_way_conflict_from_same_header_merges_to_one() -> None:
    """Codex review, P2 finding 1 (plus its two order-independence
    follow-ups): three divergent per-TU definitions of one type recorded
    against the *same* header all key onto ``_route_type``'s
    ``(qualified_name, header)`` -- the fix is for ``_check_odr_type_variant``
    to group every pairwise conflict record by that same key and union their
    hashes into one ``Change`` per group, rather than emitting one ``Change``
    per pairwise record (which could never be made both lossless *and*
    order-independent: see ``workflows.cross_source_evolution``'s own
    docstring for the full history of why the earlier per-record designs
    each failed on a fresh counterexample). Exercised directly against the
    check function, since the merge now happens there, before
    ``_run_one_side`` ever sees the findings."""
    from abicheck.buildsource.crosscheck import run_crosschecks

    surface = SourceAbiSurface(
        odr_conflicts=[
            {
                "qualified_name": "Widget",
                "header": "widget.h",
                "old_type_hash": "sha256:base",
                "new_type_hash": "sha256:variant-b",
            },
            {
                "qualified_name": "Widget",
                "header": "widget.h",
                "old_type_hash": "sha256:base",
                "new_type_hash": "sha256:variant-c",
            },
        ],
        reachable_declarations=[
            SourceEntity(id="d0", kind="function", qualified_name="f")
        ],
    )
    snap = AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        from_headers=True,
        build_source=BuildSourcePack(root="", source_abi=surface),
    )
    result = run_crosschecks(snap)
    hits = [c for c in result.findings if c.kind == ChangeKind.ODR_TYPE_VARIANT]
    # Both records key onto (qualified_name="Widget", header="widget.h") --
    # they must merge into exactly one finding carrying the complete,
    # order-independent set of distinct layouts, never one finding per
    # pairwise record (which would be a bare regression, cf. #1147) and
    # never silently dropping either divergent layout.
    assert len(hits) == 1
    assert hits[0].new_value == "sha256:base|sha256:variant-b|sha256:variant-c"


def test_odr_type_variant_identity_order_independent_across_sides() -> None:
    """Codex review, P2 finding 1 follow-up: ``old_type_hash``/
    ``new_type_hash`` are assigned by TU visitation order within one side's
    own replay (``source_link._route_type`` calls whichever hash it saw
    first the stored baseline), not by any OLD/NEW-snapshot meaning. The
    *same* persistent conflict recorded as (old="X", new="Y") on one side
    and (old="Y", new="X") on the other must still read as one PERSISTENT
    finding, not a spurious RESOLVED+INTRODUCED pair."""

    def _snap(old_hash: str, new_hash: str) -> AbiSnapshot:
        surface = SourceAbiSurface(
            odr_conflicts=[
                {
                    "qualified_name": "Widget",
                    "header": "widget.h",
                    "old_type_hash": old_hash,
                    "new_type_hash": new_hash,
                }
            ],
            reachable_declarations=[
                SourceEntity(id="d0", kind="function", qualified_name="f")
            ],
        )
        return AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            build_source=BuildSourcePack(root="", source_abi=surface),
        )

    old = _snap("sha256:aaa", "sha256:zzz")
    new = _snap("sha256:zzz", "sha256:aaa")  # same pair, swapped visitation order
    changes = compute_cross_source_evolution(old, new)
    hits = [c for c in changes if c.kind == ChangeKind.ODR_TYPE_VARIANT]
    assert len(hits) == 1
    assert hits[0].cross_source_evolution == CrossSourceEvolution.PERSISTENT


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


_IDC_NONE = "no_evidence"  # no L4 source-ABI surface at all
_IDC_CLEAN = "evidence_clean"
_IDC_FLAG = "evidence_flagged"


def _idc_snapshot(state: str) -> AbiSnapshot:
    """``identity_collision_detected``: two distinct declarations linked
    onto the same L4 identity() key (mirrors ``_synthetic_snapshots.
    identity_collision_snapshot`` in ``tests/parity/``)."""
    if state == _IDC_NONE:
        return AbiSnapshot(library="libfoo.so", version="1.0", from_headers=True)
    if state not in (_IDC_CLEAN, _IDC_FLAG):
        raise ValueError(state)
    collisions = (
        [
            {
                "identity": "f#sha256:abc",
                "qualified_name": "f",
                "usr_a": "c:@F@f#",
                "usr_b": "c:@N@ns@F@f#",
            }
        ]
        if state == _IDC_FLAG
        else []
    )
    surface = SourceAbiSurface(
        identity_collisions=collisions,
        reachable_declarations=[
            SourceEntity(id="d0", kind="function", qualified_name="f")
        ],
    )
    return AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        from_headers=True,
        build_source=BuildSourcePack(root="", source_abi=surface),
    )


_IDC_MATRIX: dict[tuple[str, str], CrossSourceEvolution | None] = {
    (_IDC_NONE, _IDC_NONE): None,
    (_IDC_NONE, _IDC_CLEAN): None,
    (_IDC_NONE, _IDC_FLAG): CrossSourceEvolution.NOT_EVALUATED,
    (_IDC_CLEAN, _IDC_NONE): None,
    (_IDC_CLEAN, _IDC_CLEAN): None,
    (_IDC_CLEAN, _IDC_FLAG): CrossSourceEvolution.INTRODUCED,
    (_IDC_FLAG, _IDC_NONE): CrossSourceEvolution.NOT_EVALUATED,
    (_IDC_FLAG, _IDC_CLEAN): CrossSourceEvolution.RESOLVED,
    (_IDC_FLAG, _IDC_FLAG): CrossSourceEvolution.PERSISTENT,
}


@pytest.mark.parametrize("old_state, new_state", sorted(_IDC_MATRIX))
def test_identity_collision_detected_evolution_matrix(
    old_state: str, new_state: str
) -> None:
    old = _idc_snapshot(old_state)
    new = _idc_snapshot(new_state)
    changes = compute_cross_source_evolution(old, new)
    hits = [c for c in changes if c.kind == ChangeKind.IDENTITY_COLLISION_DETECTED]
    expected = _IDC_MATRIX[(old_state, new_state)]
    if expected is None:
        assert hits == [], f"unexpected finding for ({old_state}, {new_state})"
    else:
        assert len(hits) == 1
        assert hits[0].cross_source_evolution == expected


def test_identity_collision_detected_identity_distinguishes_two_collisions() -> None:
    """This check's own identity is ``(symbol, new_value)`` -- ``symbol`` is
    the colliding qualified name and ``new_value`` the L4 identity key.
    ``_check_identity_collision`` now groups every transition record by
    ``identity`` and emits one Change per group, which makes
    ``(symbol, new_value)`` unique on its own (a three-way collision on one
    key still produces exactly one Change, exercised directly in
    ``test_identity_collision_detected_three_way_collision_merges_to_one``
    below)."""

    def _snap_with_identity(identity_key: str) -> AbiSnapshot:
        surface = SourceAbiSurface(
            identity_collisions=[
                {
                    "identity": identity_key,
                    "qualified_name": "f",
                    "usr_a": "c:@F@f#",
                    "usr_b": "c:@N@ns@F@f#",
                }
            ],
            reachable_declarations=[
                SourceEntity(id="d0", kind="function", qualified_name="f")
            ],
        )
        return AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            build_source=BuildSourcePack(root="", source_abi=surface),
        )

    from abicheck.buildsource.crosscheck import CHECK_IDENTITY_COLLISION
    from abicheck.workflows.cross_source_evolution import _IDENTITY_FUNCS

    identity = _IDENTITY_FUNCS[CHECK_IDENTITY_COLLISION]
    old = _snap_with_identity("f#sha256:aaa")
    new = _snap_with_identity("f#sha256:bbb")
    changes = compute_cross_source_evolution(old, new)
    hits = [c for c in changes if c.kind == ChangeKind.IDENTITY_COLLISION_DETECTED]
    by_evolution = {c.cross_source_evolution: identity(c) for c in hits}
    assert by_evolution == {
        CrossSourceEvolution.RESOLVED: ("f", "f#sha256:aaa"),
        CrossSourceEvolution.INTRODUCED: ("f", "f#sha256:bbb"),
    }


def test_identity_collision_detected_three_way_collision_merges_to_one() -> None:
    """Codex review, P2 finding 1 (plus its two order-independence
    follow-ups): a three-way collision on one L4 identity key -- three
    distinct declarations (proven distinct by USR) all linked onto the same
    ``identity()`` key -- produces two pairwise transition records per
    ``source_link._route_declaration``. The fix is for
    ``_check_identity_collision`` to group every transition record by
    ``identity`` and union their USRs into one ``Change`` per group, rather
    than emitting one ``Change`` per pairwise transition (which could never
    be made both lossless *and* order-independent). Exercised directly
    against the check function, since the merge now happens there."""
    from abicheck.buildsource.crosscheck import run_crosschecks

    surface = SourceAbiSurface(
        identity_collisions=[
            {
                "identity": "f#sha256:abc",
                "qualified_name": "f",
                "usr_a": "c:@F@f#",
                "usr_b": "c:@N@ns1@F@f#",
            },
            {
                "identity": "f#sha256:abc",
                "qualified_name": "f",
                "usr_a": "c:@N@ns1@F@f#",
                "usr_b": "c:@N@ns2@F@f#",
            },
        ],
        reachable_declarations=[
            SourceEntity(id="d0", kind="function", qualified_name="f")
        ],
    )
    snap = AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        from_headers=True,
        build_source=BuildSourcePack(root="", source_abi=surface),
    )
    result = run_crosschecks(snap)
    hits = [
        c for c in result.findings if c.kind == ChangeKind.IDENTITY_COLLISION_DETECTED
    ]
    # All three transition records key onto identity="f#sha256:abc" -- they
    # must merge into exactly one finding carrying the complete,
    # order-independent participant set, never one finding per pairwise
    # transition and never silently dropping a participant.
    assert len(hits) == 1
    assert hits[0].old_value == "c:@F@f#|c:@N@ns1@F@f#|c:@N@ns2@F@f#"


def test_identity_collision_detected_identity_order_independent_across_sides() -> None:
    """Codex review, P2 finding 1 follow-up: ``usr_a``/``usr_b`` are assigned
    by entity/TU visitation order within one side's own replay
    (``source_link._route_declaration`` calls whichever USR it saw first
    "prev"), not by any OLD/NEW-snapshot meaning. The *same* persistent
    collision recorded as (usr_a="X", usr_b="Y") on one side and
    (usr_a="Y", usr_b="X") on the other must still read as one PERSISTENT
    finding, not a spurious RESOLVED+INTRODUCED pair."""

    def _snap(usr_a: str, usr_b: str) -> AbiSnapshot:
        surface = SourceAbiSurface(
            identity_collisions=[
                {
                    "identity": "f#sha256:abc",
                    "qualified_name": "f",
                    "usr_a": usr_a,
                    "usr_b": usr_b,
                }
            ],
            reachable_declarations=[
                SourceEntity(id="d0", kind="function", qualified_name="f")
            ],
        )
        return AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            build_source=BuildSourcePack(root="", source_abi=surface),
        )

    old = _snap("c:@F@f#", "c:@N@ns@F@f#")
    new = _snap("c:@N@ns@F@f#", "c:@F@f#")  # same pair, swapped order
    changes = compute_cross_source_evolution(old, new)
    hits = [c for c in changes if c.kind == ChangeKind.IDENTITY_COLLISION_DETECTED]
    assert len(hits) == 1
    assert hits[0].cross_source_evolution == CrossSourceEvolution.PERSISTENT


def test_identity_collision_detected_symbol_order_independent_across_sides() -> None:
    """Codex review, third follow-up: when colliding declarations carry
    *different* qualified names, ``_route_declaration`` records the
    qualified name of whichever declaration was visited second for each
    transition -- so which name survives as ``qualified_name`` on a given
    record depends on visitation order too, not just which USRs pair up.
    Retaining "whichever record is first in the list" (a `setdefault`) for
    the group's `symbol` would be exactly as order-dependent as the USR
    pair was before the previous fix: the same persistent collision could
    be reported as `Change.symbol="f"` on one side and `Change.symbol="g"`
    on the other, and since `_IDENTITY_FUNCS` keys this check by
    `(symbol, new_value)`, that alone would produce a spurious
    RESOLVED+INTRODUCED pair instead of one PERSISTENT finding -- even
    though the swapped-USR fix above already made the USR pair agree.
    `_check_identity_collision` now derives the canonical symbol as
    ``min()`` over the *set* of qualified names seen for the group, which
    is a deterministic function of that set, not of arrival order."""

    def _snap(first_qname: str, second_qname: str) -> AbiSnapshot:
        # Two transitions on one identity key, differing only in which
        # qualified name is recorded first vs. second.
        surface = SourceAbiSurface(
            identity_collisions=[
                {
                    "identity": "shared#sha256:abc",
                    "qualified_name": first_qname,
                    "usr_a": "c:@F@f#",
                    "usr_b": "c:@N@ns1@F@f#",
                },
                {
                    "identity": "shared#sha256:abc",
                    "qualified_name": second_qname,
                    "usr_a": "c:@N@ns1@F@f#",
                    "usr_b": "c:@N@ns2@F@f#",
                },
            ],
            reachable_declarations=[
                SourceEntity(id="d0", kind="function", qualified_name="f")
            ],
        )
        return AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            build_source=BuildSourcePack(root="", source_abi=surface),
        )

    old = _snap("f", "g")
    new = _snap("g", "f")  # same two names, swapped which record carries which
    changes = compute_cross_source_evolution(old, new)
    hits = [c for c in changes if c.kind == ChangeKind.IDENTITY_COLLISION_DETECTED]
    assert len(hits) == 1
    assert hits[0].cross_source_evolution == CrossSourceEvolution.PERSISTENT
    assert hits[0].symbol == "f"  # min("f", "g"), deterministic either way


def test_identity_collision_detected_authority_unchanged() -> None:
    from abicheck.checker_policy import RISK_KINDS

    assert ChangeKind.IDENTITY_COLLISION_DETECTED in RISK_KINDS
    old = _idc_snapshot(_IDC_NONE)
    new = _idc_snapshot(_IDC_FLAG)
    for c in compute_cross_source_evolution(old, new):
        if c.kind == ChangeKind.IDENTITY_COLLISION_DETECTED:
            assert c.effective_verdict is None


def test_identity_collision_detected_wired_into_compare_by_default() -> None:
    old = _idc_snapshot(_IDC_NONE)
    new = _idc_snapshot(_IDC_FLAG)
    result = compare(old, new, scope_to_public_surface=False)
    hits = [
        c for c in result.changes if c.kind == ChangeKind.IDENTITY_COLLISION_DETECTED
    ]
    assert len(hits) == 1
    assert hits[0].cross_source_evolution == CrossSourceEvolution.NOT_EVALUATED


_CCC_NONE = "no_evidence"  # no L3 build evidence at all
_CCC_CLEAN = "evidence_clean"
_CCC_FLAG = "evidence_flagged"


def _ccc_snapshot(state: str) -> AbiSnapshot:
    """``compile_context_conflict``: two compile units of one target
    disagreeing on an ABI-relevant flag family (mirrors
    ``_synthetic_snapshots.compile_context_conflict_snapshot``)."""
    if state == _CCC_NONE:
        return AbiSnapshot(library="libfoo.so", version="1.0", from_headers=True)
    if state not in (_CCC_CLEAN, _CCC_FLAG):
        raise ValueError(state)
    second_flags = ["-fno-rtti"] if state == _CCC_FLAG else ["-frtti"]
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


def test_compile_context_conflict_identity_distinguishes_flag_and_define() -> None:
    """This check's own identity is ``(symbol, old_value)`` -- ``symbol`` is
    the shared build-target label, which a flag-family conflict AND a
    ``#define`` value conflict on the very same target both carry, and
    ``old_value`` (the specific flag or define name) is what distinguishes
    the two findings from each other."""
    build_evidence = BuildEvidence(
        compile_units=[
            CompileUnit(
                id="a",
                target_id="target://libfoo.so",
                abi_relevant_flags=["-frtti"],
                language="CXX",
                defines={"FOO": "1"},
            ),
            CompileUnit(
                id="b",
                target_id="target://libfoo.so",
                abi_relevant_flags=["-fno-rtti"],
                language="CXX",
                defines={"FOO": "2"},
            ),
        ]
    )
    snapshot = AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        from_headers=True,
        build_source=BuildSourcePack(root="", build_evidence=build_evidence),
    )
    from abicheck.buildsource.crosscheck import CHECK_COMPILE_CONTEXT_CONFLICT
    from abicheck.workflows.cross_source_evolution import _IDENTITY_FUNCS

    identity = _IDENTITY_FUNCS[CHECK_COMPILE_CONTEXT_CONFLICT]
    changes = compute_cross_source_evolution(snapshot, snapshot)
    hits = [c for c in changes if c.kind == ChangeKind.COMPILE_CONTEXT_CONFLICT]
    assert len(hits) == 2, "expected both the flag-family and define conflicts"
    identities = {identity(c) for c in hits}
    assert identities == {
        ("target://libfoo.so", "-frtti"),
        ("target://libfoo.so", "FOO"),
    }


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
_SSDM_CLEAN = "evidence_clean"
_SSDM_FLAG = "evidence_flagged"


def _ssdm_snapshot(state: str) -> AbiSnapshot:
    """``source_surface_dso_mismatch``: the linked L4 surface maps to none
    of this binary's exports (mirrors ``_synthetic_snapshots.
    source_surface_dso_mismatch_snapshot``)."""
    if state == _SSDM_NONE:
        return AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            from_headers=True,
            elf=ElfMetadata(symbols=[ElfSymbol(name="_Z3foov")]),
        )
    if state not in (_SSDM_CLEAN, _SSDM_FLAG):
        raise ValueError(state)
    surface = SourceAbiSurface(
        library="libfoo.so",
        reachable_declarations=[
            SourceEntity(id="d0", kind="function", qualified_name="d0"),
        ],
    )
    if state == _SSDM_FLAG:
        # Maps to a symbol never present in this binary's own export table.
        surface.mappings["source_decl_to_binary_symbol"] = {"d0": "_Z9otherlibv"}
    else:
        # Maps to a symbol that IS in this binary's own export table below.
        surface.mappings["source_decl_to_binary_symbol"] = {"d0": "_Z3foov"}
    return AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        from_headers=True,
        elf=ElfMetadata(symbols=[ElfSymbol(name="_Z3foov")]),
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
    hits = [
        c for c in result.changes if c.kind == ChangeKind.SOURCE_SURFACE_DSO_MISMATCH
    ]
    assert len(hits) == 1
    assert hits[0].cross_source_evolution == CrossSourceEvolution.NOT_EVALUATED


class TestFiveChecksNotEvaluatedCrux:
    """The correctness crux (plan §7 F-8/F-9), generalized across all five
    checks this PR migrates via ``itertools.product``, mirroring
    ``test_cross_source_evolution.py``'s own ``TestNotEvaluatedCrux``
    (``unversioned_exported_symbol``) and ``TestFourChecksNotEvaluatedCrux``
    (the prior PR's four L0-L2 checks): a finding flagged on an evaluated
    side must never read INTRODUCED/RESOLVED when its sibling side's own
    evidence could not confirm or deny it -- generalized here to the L3/L4
    evidence tiers these five checks depend on instead of L0-L2."""

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
            _idc_snapshot,
            _IDC_NONE,
            _IDC_FLAG,
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
