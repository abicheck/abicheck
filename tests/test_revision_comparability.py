"""ADR-063 amendment "L4 clang payload comparability": the A1-A4 guard.

Oracle: the amendment's own text (A1 mismatch definition, A3 intersection,
A4 reporting), written here independently of ``revision_comparability``'s
helpers. Each test runs through ``diff_source_abi`` and the assurance axis.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from abicheck.buildsource.revision_comparability import (
    CLANG_PRODUCER,
    REVISION_COMPARABLE_FIELDS,
    is_payload_revision_mismatch,
)
from abicheck.buildsource.source_abi import (
    SourceAbiSurface,
    SourceEntity,
    default_fact_set,
)
from abicheck.buildsource.source_diff import diff_source_abi
from abicheck.checker_policy import ChangeKind
from abicheck.policy.analysis_assurance._fact_set import _fact_set_comparability


def _fs(producer_version: str, producer: str = CLANG_PRODUCER) -> dict:
    return default_fact_set(producer=producer, producer_version=producer_version)


def _surface(
    fact_set: dict | None = None,
    declarations: list[SourceEntity] | None = None,
    types: list[SourceEntity] | None = None,
) -> SourceAbiSurface:
    s = SourceAbiSurface(library="libfoo.so", target_id="target://libfoo")
    s.coverage = {"fact_set": fact_set or {}, "fact_family_states": {}}
    s.reachable_declarations = declarations or []
    s.reachable_types = types or []
    return s


def _pack(surface: SourceAbiSurface) -> SimpleNamespace:
    """The assurance axis reads ``.source_abi`` off a build-source pack."""
    return SimpleNamespace(source_abi=surface)


def _kinds(changes) -> list[ChangeKind]:
    return [c.kind for c in changes]


# -- A1: the mismatch predicate ----------------------------------------------


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        # both clang, same contract, different producer_version: the condition
        (_fs("0.13"), _fs("0.14"), True),
        # same revision: the ordinary comparison applies
        (_fs("0.13"), _fs("0.13"), False),
        # a non-clang producer on either side keeps the existing rules
        (_fs("0.13"), _fs("0.14", producer="other"), False),
        (_fs("0.13", producer="other"), _fs("0.14"), False),
        # a contract (name/version) mismatch is hard-blocking on its own
        (_fs("0.13"), {**_fs("0.14"), "version": "999"}, False),
        (_fs("0.13"), {**_fs("0.14"), "name": "other-contract"}, False),
        # an empty side is not a revision pair
        ({}, _fs("0.14"), False),
        (_fs("0.13"), {}, False),
        # a missing producer_version is not a stamped revision
        (_fs("0.13"), {**_fs("0.14"), "producer_version": None}, False),
    ],
)
def test_mismatch_predicate(old: dict, new: dict, expected: bool) -> None:
    assert is_payload_revision_mismatch(old, new) is expected


def test_pinned_set_is_the_measured_typedef_pair() -> None:
    # Measured in the golden test: typedef qualified_name and value are equal on
    # every TU. visibility and api_relevant are excluded (equal by construction).
    assert REVISION_COMPARABLE_FIELDS == frozenset(
        {("typedef", "qualified_name"), ("typedef", "value")}
    )


# -- A3: the intersection, fabricated-break regression -----------------------


def _ctor(mangled: str, signature_hash: str = "sig") -> SourceEntity:
    return SourceEntity(
        id="Point::Point",
        kind="constructor",
        qualified_name="Point::Point",
        mangled_name=mangled,
        signature_hash=signature_hash,
    )


def _inline_ctor(mangled: str) -> SourceEntity:
    # Inline bodies are the removal-capable bucket: their identity is the
    # mangled name, so a blanked name on one side reads as a removal.
    return SourceEntity(
        id="Point::Point",
        kind="inline",
        qualified_name="Point::Point",
        mangled_name=mangled,
        body_hash="body",
    )


@pytest.mark.parametrize(
    ("old_name", "new_name"),
    [
        # the case in the measurement: blank mangled name on one side only
        ("", "_ZN5PointC1Ev"),
        # the reverse direction
        ("_ZN5PointC1Ev", ""),
    ],
)
def test_revision_mismatch_has_no_fabricated_removal(
    old_name: str, new_name: str
) -> None:
    """Fabricated-break regression (ADR-063 amendment test 3). Without the guard,
    the same-revision path reads the blanked name as INLINE_FUNCTION_REMOVED."""
    old = _surface(_fs("0.13"))
    new = _surface(_fs("0.14"))
    old.reachable_inline_bodies = [_inline_ctor(old_name)]
    new.reachable_inline_bodies = [_inline_ctor(new_name)]
    assert _kinds(diff_source_abi(old, new)) == [
        ChangeKind.SOURCE_FACT_COVERAGE_INCOMPLETE
    ]


@pytest.mark.parametrize(
    ("old_name", "new_name"),
    [("", "_ZN5PointC1Ev"), ("_ZN5PointC1Ev", "")],
)
def test_fabricated_removal_fixture_is_real_at_same_revision(
    old_name: str, new_name: str
) -> None:
    """Control for the regression above: the same fixture at one revision does
    produce the removal, so the regression is testing something real."""
    old = _surface(_fs("0.13"))
    new = _surface(_fs("0.13"))
    old.reachable_inline_bodies = [_inline_ctor(old_name)]
    new.reachable_inline_bodies = [_inline_ctor(new_name)]
    assert ChangeKind.INLINE_FUNCTION_REMOVED in _kinds(diff_source_abi(old, new))


def test_revision_mismatch_suppresses_unpinned_default_argument() -> None:
    """Same signature, different default-argument value: the unguarded path
    reports DEFAULT_ARGUMENT_CHANGED on the unpinned ``value`` field."""

    def fn(value: str) -> SourceEntity:
        return SourceEntity(
            id="f",
            kind="function",
            qualified_name="f",
            mangled_name="_Z1fi",
            signature_hash="s",
            value=value,
        )

    old = _surface(_fs("0.13"), declarations=[fn("1")])
    new = _surface(_fs("0.14"), declarations=[fn("2")])
    assert _kinds(diff_source_abi(old, new)) == [
        ChangeKind.SOURCE_FACT_COVERAGE_INCOMPLETE
    ]


def test_same_revision_still_runs_ordinary_checks() -> None:
    """Negative control: without a mismatch the declaration checks still run,
    so the guard is not silently disabling everything."""
    old = _surface(
        _fs("0.13"),
        declarations=[
            SourceEntity(
                id="f",
                kind="function",
                qualified_name="f",
                mangled_name="_Z1fi",
                signature_hash="s",
                value="1",
            )
        ],
    )
    new = _surface(
        _fs("0.13"),
        declarations=[
            SourceEntity(
                id="f",
                kind="function",
                qualified_name="f",
                mangled_name="_Z1fi",
                signature_hash="s",
                value="2",
            )
        ],
    )
    kinds = _kinds(diff_source_abi(old, new))
    assert ChangeKind.DEFAULT_ARGUMENT_CHANGED in kinds


def test_typedef_pinned_value_is_compared_and_reported() -> None:
    """A shared pinned typedef is counted in the coverage finding, not silently
    dropped, and a type_hash-only difference (unpinned) produces no finding."""
    typedef_old = SourceEntity(
        id="my_t", kind="typedef", qualified_name="my_t", type_hash="h1", value="int"
    )
    typedef_new = SourceEntity(
        id="my_t", kind="typedef", qualified_name="my_t", type_hash="h2", value="int"
    )
    old = _surface(_fs("0.13"), types=[typedef_old])
    new = _surface(_fs("0.14"), types=[typedef_new])
    changes = diff_source_abi(old, new)
    assert _kinds(changes) == [ChangeKind.SOURCE_FACT_COVERAGE_INCOMPLETE]
    assert "Compared 2 pinned facts (1 shared typedefs" in changes[0].description


# -- A4: reporting, zero intersection, assurance -----------------------------


def test_empty_intersection_reports_zero_compared_not_clean() -> None:
    old = _surface(_fs("0.13"))
    new = _surface(_fs("0.14"))
    changes = diff_source_abi(old, new)
    assert _kinds(changes) == [ChangeKind.SOURCE_FACT_COVERAGE_INCOMPLETE]
    assert "Compared 0 pinned facts" in changes[0].description
    assert "withheld, not clean" in changes[0].description


def test_revision_mismatch_assurance_reads_unknown_not_comparable() -> None:
    old = _surface(_fs("0.13"))
    new = _surface(_fs("0.14"))
    state, _notes = _fact_set_comparability(_pack(old), _pack(new))
    assert state == "unknown"


def test_same_revision_assurance_is_not_unknown_from_revision() -> None:
    old = _surface(_fs("0.13"))
    new = _surface(_fs("0.13"))
    state, _notes = _fact_set_comparability(_pack(old), _pack(new))
    assert state == "comparable"


# -- binary provenance: the moved block, same-revision path -------------------


def test_provenance_fires_when_public_functions_map_to_no_export() -> None:
    """Six exportable functions none of which the binary exports read as a
    provenance mismatch. The entity with an empty identity is skipped, not
    counted as an unmapped declaration."""

    def fn(mangled: str) -> SourceEntity:
        return SourceEntity(
            id=mangled, kind="function", qualified_name=mangled, mangled_name=mangled
        )

    decls = [fn(f"_Z{i}f") for i in range(6)]
    decls.append(
        SourceEntity(id="", kind="function", qualified_name="", mangled_name="")
    )
    old = _surface(_fs("0.13"), declarations=decls)
    new = _surface(_fs("0.13"), declarations=list(decls))
    old.roots = {"exported_symbols": ["_Zother"]}
    new.roots = {"exported_symbols": ["_Zother"]}
    assert ChangeKind.SOURCE_BINARY_PROVENANCE_MISMATCH in _kinds(
        diff_source_abi(old, new)
    )


def _public_fn(mangled: str) -> SourceEntity:
    return SourceEntity(
        id=mangled, kind="function", qualified_name=mangled, mangled_name=mangled
    )


@pytest.mark.parametrize(
    ("exports", "declarations"),
    [
        # no exported symbols at all: nothing to map against
        ([], [_public_fn(f"_Z{i}f") for i in range(6)]),
        # fewer exportable declarations than the minimum: too small to judge
        (["_Zother"], [_public_fn(f"_Z{i}f") for i in range(4)]),
        # most declarations map to a real export: the miss ratio is below 0.8
        (
            [f"_Z{i}f" for i in range(5)],
            [_public_fn(f"_Z{i}f") for i in range(6)],
        ),
    ],
)
def test_provenance_stays_silent_when_the_surface_cannot_be_judged(
    exports: list[str], declarations: list[SourceEntity]
) -> None:
    old = _surface(_fs("0.13"), declarations=declarations)
    new = _surface(_fs("0.13"), declarations=list(declarations))
    old.roots = {"exported_symbols": list(exports)}
    new.roots = {"exported_symbols": list(exports)}
    assert ChangeKind.SOURCE_BINARY_PROVENANCE_MISMATCH not in _kinds(
        diff_source_abi(old, new)
    )


def test_provenance_counts_only_exportable_declarations_with_a_symbol() -> None:
    """A record is not exportable, and a method with no mangled name has no
    comparable symbol. Neither is counted, so the six unmapped functions alone
    still decide the finding."""
    functions = [_public_fn(f"_Z{i}f") for i in range(6)]
    record = SourceEntity(id="R", kind="record", qualified_name="R")
    unmangled_method = SourceEntity(
        id="M::m", kind="method", qualified_name="M::m", mangled_name=""
    )
    decls = [*functions, record, unmangled_method]
    old = _surface(_fs("0.13"), declarations=decls)
    new = _surface(_fs("0.13"), declarations=list(decls))
    old.roots = {"exported_symbols": ["_Zother"]}
    new.roots = {"exported_symbols": ["_Zother"]}
    assert ChangeKind.SOURCE_BINARY_PROVENANCE_MISMATCH in _kinds(
        diff_source_abi(old, new)
    )
