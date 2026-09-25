"""ADR-075 D3: which extraction-scope pairs are refused, which are compared
with a note, and that recording a scope changes no verdict.

The oracle is the ADR's own table, restated below by hand as expected
outcomes per (old, new) combination -- not derived from
``compare_extraction_scopes``.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.checker import compare
from abicheck.comparability import check_contracts_comparable
from abicheck.errors import ScopeMismatchError
from abicheck.model import AbiSnapshot, Function
from abicheck.model.extraction_scope import (
    EXTRACTION_SCOPE_NOTE_MARKER,
    EntityOwnership,
    ExtractionScope,
    compare_extraction_scopes,
    extraction_scope_notes,
    moved_declarations,
)
from abicheck.model.fact import Fact
from abicheck.model.ownership_rules import DependencyRoots, OwnershipRules

_RULES_A = OwnershipRules(target_roots=("include",))
_RULES_B = OwnershipRules(
    target_roots=("include",), dependencies=(DependencyRoots("fmt", ("third",)),)
)

# Named scope variants.
_VARIANTS: dict[str, ExtractionScope | None] = {
    "unrecorded": None,
    "full_a": ExtractionScope(_RULES_A),
    "full_a_again": ExtractionScope(
        OwnershipRules(target_roots=("include", "include"))
    ),
    "full_b": ExtractionScope(_RULES_B),
    "ref_a": ExtractionScope(_RULES_A, dependency_evidence="referenced"),
    "ref_b": ExtractionScope(_RULES_B, dependency_evidence="referenced"),
    "pre_a": ExtractionScope(
        _RULES_A, prefilter={"kind": "castxml_start", "names": ["x"]}
    ),
    "pre2_a": ExtractionScope(
        _RULES_A, prefilter={"kind": "castxml_start", "names": ["y"]}
    ),
}

# Outcome per the ADR-075 D3 table: "same", "note" (compared, report line),
# "moved" (compared, rules differ under full) or "refuse".
_REFUSE = "refuse"


def _expected(old: str, new: str) -> str:
    table = {
        # unrecorded rows
        ("unrecorded", "unrecorded"): "same",
        ("unrecorded", "full_a"): "note",
        ("unrecorded", "full_b"): "note",
        ("unrecorded", "full_a_again"): "note",
        ("unrecorded", "ref_a"): _REFUSE,
        ("unrecorded", "ref_b"): _REFUSE,
        ("unrecorded", "pre_a"): _REFUSE,
        ("unrecorded", "pre2_a"): _REFUSE,
    }
    if (old, new) in table:
        return table[(old, new)]
    if (new, old) in table:
        return table[(new, old)]
    kinds = {
        "full_a": ("full", "A", None),
        "full_a_again": ("full", "A", None),
        "full_b": ("full", "B", None),
        "ref_a": ("ref", "A", None),
        "ref_b": ("ref", "B", None),
        "pre_a": ("full", "A", "x"),
        "pre2_a": ("full", "A", "y"),
    }
    (oe, orules, opre), (ne, nrules, npre) = kinds[old], kinds[new]
    if oe != ne:
        return _REFUSE
    if opre != npre:
        return _REFUSE
    if orules == nrules:
        return "same"
    if oe == "ref":
        return _REFUSE
    return "moved"


_PAIRS = list(itertools.product(_VARIANTS, repeat=2))


def test_oracle_is_not_vacuous() -> None:
    outcomes = {_expected(a, b) for a, b in _PAIRS}
    assert outcomes == {"same", "note", "moved", _REFUSE}


def test_every_pair_matches_the_adr_table() -> None:
    wrong = []
    for old, new in _PAIRS:
        got = compare_extraction_scopes(_VARIANTS[old], _VARIANTS[new])
        actual = (
            _REFUSE
            if got.refuse_reason
            else "moved"
            if got.rules_differ
            else "note"
            if got.notes
            else "same"
        )
        if actual != _expected(old, new):
            wrong.append((old, new, actual, _expected(old, new)))
    assert not wrong


def _snap(scope: ExtractionScope | None, *, owner: str = "target") -> AbiSnapshot:
    fn = Function(name="api", mangled="api", return_type="int")
    contract = "public" if owner == "target" else "external"
    fn.ownership_fact = Fact.present(EntityOwnership(owner, contract, "r"))
    snap = AbiSnapshot(library="lib", version="1", functions=[fn], from_headers=True)
    snap.extraction_scope = scope
    return snap


@pytest.mark.parametrize(
    ("old", "new"), [p for p in _PAIRS if _expected(*p) == _REFUSE]
)
def test_refused_pairs_raise_scope_mismatch(old: str, new: str) -> None:
    with pytest.raises(ScopeMismatchError):
        check_contracts_comparable(_snap(_VARIANTS[old]), _snap(_VARIANTS[new]))


@pytest.mark.parametrize(
    ("old", "new"), [p for p in _PAIRS if _expected(*p) != _REFUSE]
)
def test_comparable_pairs_pass_the_gate(old: str, new: str) -> None:
    assert (
        check_contracts_comparable(_snap(_VARIANTS[old]), _snap(_VARIANTS[new])) is None
    )


def test_binary_only_side_is_never_judged() -> None:
    old = _snap(None)
    old.from_headers = False
    assert check_contracts_comparable(old, _snap(_VARIANTS["ref_a"])) is None


def test_unrecorded_baseline_gets_a_note_naming_it() -> None:
    notes = extraction_scope_notes(_snap(None), _snap(_VARIANTS["full_a"]))
    assert len(notes) == 1
    assert "old snapshot" in notes[0] and EXTRACTION_SCOPE_NOTE_MARKER in notes[0]


def test_differing_rules_under_full_name_the_moved_declarations() -> None:
    old = _snap(_VARIANTS["full_a"], owner="target")
    new = _snap(_VARIANTS["full_b"], owner="dependency:fmt")
    assert moved_declarations(old, new) == [
        ("api", "target/public", "dependency:fmt/external")
    ]
    notes = extraction_scope_notes(old, new)
    assert any("1 declaration(s) moved" in n and "api" in n for n in notes)


def test_same_rules_produce_no_note() -> None:
    assert (
        extraction_scope_notes(
            _snap(_VARIANTS["full_a"]), _snap(_VARIANTS["full_a_again"])
        )
        == []
    )


def _pair_with_a_change() -> tuple[AbiSnapshot, AbiSnapshot]:
    old = AbiSnapshot(
        library="lib",
        version="1",
        functions=[
            Function(name="keep", mangled="keep", return_type="int"),
            Function(name="gone", mangled="gone", return_type="int"),
        ],
        from_headers=True,
    )
    new = AbiSnapshot(
        library="lib",
        version="2",
        functions=[Function(name="keep", mangled="keep", return_type="long")],
        from_headers=True,
    )
    return old, new


def _stamped(snap: AbiSnapshot) -> AbiSnapshot:
    for fn in snap.functions:
        fn.ownership_fact = Fact.present(EntityOwnership("target", "public", "r"))
    snap.extraction_scope = ExtractionScope(_RULES_A)
    return snap


def test_recording_equal_scopes_changes_no_verdict_or_finding() -> None:
    plain = compare(*_pair_with_a_change())
    old, new = _pair_with_a_change()
    stamped = compare(_stamped(old), _stamped(new))
    assert stamped.verdict == plain.verdict
    assert [(c.kind, c.symbol) for c in stamped.changes] == [
        (c.kind, c.symbol) for c in plain.changes
    ]
    assert plain.changes  # non-vacuous: the pair really differs
    assert stamped.extraction_scope_identity == ExtractionScope(_RULES_A).fingerprint
    assert plain.extraction_scope_identity == ""
