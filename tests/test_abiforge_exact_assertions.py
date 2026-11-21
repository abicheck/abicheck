"""Exact finding assertions (abiforge/model/exact.py): a right kind is not enough.

Bug class: a report with the right verdict and the right kind passed even when
the finding named the wrong member, carried the wrong old/new value, was
missing for a second member or duplicated (the finding-projection audit,
``docs/contribute/plans/abiforge-finding-projection-audit.md``). The oracle
here is a hand-written multiset of rows, never the judge's own helper; each
mutation is applied to every row of several independent baselines.
"""

from __future__ import annotations

import itertools
from dataclasses import replace

import pytest

from abiforge.model.config import Config, Toolchain, default_config
from abiforge.model.exact import (
    Bits,
    Bytes,
    ExactRow,
    FindingAssertion,
    FindingScope,
    Width,
    canonical_value,
)
from abiforge.model.expect import (
    Expectation,
    issue_keys,
    observability_floor,
    reconcile,
)
from abiforge.model.finding import Finding

_SCOPE_KINDS = frozenset(
    {
        "TYPE_SIZE_CHANGED",
        "TYPE_ALIGNMENT_CHANGED",
        "TYPE_FIELD_OFFSET_CHANGED",
        "FIELD_BITFIELD_CHANGED",
        "UNION_FIELD_TYPE_CHANGED",
        "UNION_FIELD_ADDED",
    }
)

# Three independent baselines: (rows, the checker's findings that satisfy them).
_BIT_SPILL = (
    (
        ExactRow("FIELD_BITFIELD_CHANGED", "Bits", "a", Width(16), Width(17)),
        ExactRow("TYPE_FIELD_OFFSET_CHANGED", "Bits", "b", Bits(16), Bits(32)),
        ExactRow("TYPE_FIELD_OFFSET_CHANGED", "Bits", "c", Bits(32), Bits(48)),
        ExactRow("TYPE_FIELD_OFFSET_CHANGED", "Bits", "tail", Bytes(5), Bytes(7)),
    ),
    (
        Finding(
            "FIELD_BITFIELD_CHANGED",
            "Bits",
            field_name="a",
            old_value="bits=16",
            new_value="bits=17",
        ),
        Finding(
            "TYPE_FIELD_OFFSET_CHANGED",
            "Bits",
            field_name="b",
            old_value="16",
            new_value="32",
        ),
        Finding(
            "TYPE_FIELD_OFFSET_CHANGED",
            "Bits",
            field_name="c",
            old_value="32",
            new_value="48",
        ),
        Finding(
            "TYPE_FIELD_OFFSET_CHANGED",
            "Bits",
            field_name="tail",
            old_value="40",
            new_value="56",
        ),
    ),
)
_UNION_EXTENT = (
    (
        ExactRow("TYPE_SIZE_CHANGED", "Payload", None, Bytes(8), Bytes(16)),
        ExactRow("TYPE_SIZE_CHANGED", "Envelope", None, Bytes(24), Bytes(32)),
        ExactRow("TYPE_FIELD_OFFSET_CHANGED", "Envelope", "t", Bytes(16), Bytes(24)),
        ExactRow("UNION_FIELD_TYPE_CHANGED", "Payload", "c", "char[8]", "char[9]"),
    ),
    (
        Finding("TYPE_SIZE_CHANGED", "Payload", old_value="64", new_value="128"),
        Finding("TYPE_SIZE_CHANGED", "Envelope", old_value="192", new_value="256"),
        Finding(
            "TYPE_FIELD_OFFSET_CHANGED",
            "Envelope",
            field_name="t",
            old_value="128",
            new_value="192",
        ),
        Finding(
            "UNION_FIELD_TYPE_CHANGED",
            "Payload",
            field_name="c",
            old_value="char[8]",
            new_value="char[9]",
        ),
    ),
)
_UNION_ADD = (
    (ExactRow("UNION_FIELD_ADDED", "Payload", "real", None, "double"),),
    (Finding("UNION_FIELD_ADDED", "Payload", field_name="real", new_value="double"),),
)
BASELINES = {
    "bit_spill": _BIT_SPILL,
    "union_extent": _UNION_EXTENT,
    "union_add": _UNION_ADD,
}


def _assertion(
    rows: tuple[ExactRow, ...], entities: frozenset[str] | None = None
) -> FindingAssertion:
    ents = entities or frozenset({"Bits", "Payload", "Envelope"})
    return FindingAssertion(FindingScope(_SCOPE_KINDS, ents), rows)


def _mutants(f: Finding) -> dict[str, Finding]:
    """Each single-field corruption a projection that keeps only kind+entity misses."""
    out = {
        "wrong_entity": replace(f, symbol=f.symbol + "X"),
        "wrong_member": replace(f, field_name=(f.field_name or "") + "_other"),
    }
    for side in ("old_value", "new_value"):
        v = getattr(f, side)
        if v is None:
            continue
        bad = (
            v.replace("bits=", "bits=1")
            if v.startswith("bits=")
            else (str(int(v) + 8) if v.isdigit() else v + "_x")
        )
        out[f"wrong_{side}"] = replace(f, **{side: bad})
    return out


@pytest.mark.parametrize("name", sorted(BASELINES))
def test_baseline_and_every_reordering_pass(name: str) -> None:
    rows, findings = BASELINES[name]
    a = _assertion(rows)
    for perm in itertools.permutations(findings):
        assert a.judge(tuple(perm)).status == "pass"


@pytest.mark.parametrize("name", sorted(BASELINES))
def test_every_single_field_corruption_of_every_row_fails(name: str) -> None:
    rows, findings = BASELINES[name]
    a = _assertion(rows)
    checked = 0
    for i, f in enumerate(findings):
        for label, bad in _mutants(f).items():
            got = findings[:i] + (bad,) + findings[i + 1 :]
            out = a.judge(got)
            if label == "wrong_entity":
                # The corrupted finding leaves the scope: its row goes missing.
                assert out.status == "fail" and any(
                    "exact missing" in p for p in out.problems
                ), label
            else:
                assert out.status == "fail", (label, i)
                assert any("exact missing" in p for p in out.problems), label
                assert any("exact unexpected" in p for p in out.problems), label
            checked += 1
    assert checked >= 2 * len(findings)


@pytest.mark.parametrize("name", sorted(BASELINES))
def test_missing_duplicate_and_unexpected_rows_fail(name: str) -> None:
    rows, findings = BASELINES[name]
    a = _assertion(rows)
    for i in range(len(findings)):
        dropped = findings[:i] + findings[i + 1 :]
        assert a.judge(dropped).status == "fail"
        duplicated = findings + (findings[i],)
        out = a.judge(duplicated)
        assert out.status == "fail" and any(
            "(x1)" in p and "unexpected" in p for p in out.problems
        )
    extra = findings + (
        Finding("TYPE_ALIGNMENT_CHANGED", "Payload", old_value="8", new_value="64"),
    )
    assert a.judge(extra).status == "fail"


def test_findings_outside_the_declared_scope_never_fail() -> None:
    rows, findings = _BIT_SPILL
    a = _assertion(rows, frozenset({"Bits"}))
    noise = (
        Finding("SYMBOL_SIZE_CHANGED", "env", old_value="16", new_value="32"),
        Finding("TYPE_SIZE_CHANGED", "Unrelated", old_value="8", new_value="16"),
        Finding("SONAME_BUMP_RECOMMENDED", "DT_SONAME"),
    )
    assert a.judge(findings + noise).status == "pass"


def test_an_unchanged_control_is_a_required_absence() -> None:
    control = _assertion(())
    assert control.judge(()).status == "pass"
    assert (
        control.judge((Finding("SONAME_BUMP_RECOMMENDED", "DT_SONAME"),)).status
        == "pass"
    )
    spurious = Finding(
        "TYPE_FIELD_OFFSET_CHANGED",
        "Bits",
        field_name="b",
        old_value="16",
        new_value="16",
    )
    assert control.judge((spurious,)).status == "fail"


def test_units_are_explicit_and_converted() -> None:
    assert Bytes(5) == Bits(40)
    assert canonical_value("TYPE_FIELD_OFFSET_CHANGED", "40") == Bits(40).canonical
    assert canonical_value("FIELD_BITFIELD_CHANGED", "bits=17") == Width(17).canonical
    assert (
        canonical_value("FIELD_BITFIELD_CHANGED", "bits=None") == Width(None).canonical
    )
    for kind, raw in (
        ("TYPE_SIZE_CHANGED", "5 bytes"),
        ("TYPE_FIELD_OFFSET_CHANGED", "-8"),
        ("FIELD_BITFIELD_CHANGED", "17"),
    ):
        with pytest.raises(ValueError):
            canonical_value(kind, raw)


def test_an_unreadable_in_scope_value_fails_not_matches() -> None:
    a = _assertion(
        (ExactRow("TYPE_SIZE_CHANGED", "Payload", None, Bits(64), Bits(128)),)
    )
    out = a.judge(
        (Finding("TYPE_SIZE_CHANGED", "Payload", old_value="8B", new_value="16B"),)
    )
    assert out.status == "fail" and any(
        p.startswith("exact unreadable") for p in out.problems
    )


def test_rows_outside_their_own_scope_are_rejected() -> None:
    with pytest.raises(ValueError):
        FindingAssertion(
            FindingScope(frozenset({"TYPE_SIZE_CHANGED"}), frozenset({"A"})),
            (ExactRow("TYPE_SIZE_CHANGED", "B", None, Bits(8), Bits(16)),),
        )


# --- configuration floor: unknown evidence is never a pass ------------------

_GCC = Toolchain("gcc", 13, "gcc", "g++")


def _cfg(evidence: str) -> Config:
    return default_config("c", _GCC).with_(evidence=evidence)


_ALIGN_ONLY = FindingAssertion(
    FindingScope(
        frozenset({"TYPE_ALIGNMENT_CHANGED", "TYPE_SIZE_CHANGED"}), frozenset({"U"})
    ),
    (ExactRow("TYPE_ALIGNMENT_CHANGED", "U", None, Bytes(8), Bytes(1)),),
    kind_needs={"TYPE_ALIGNMENT_CHANGED": frozenset({"H"})},
)


def test_an_alignment_row_without_header_evidence_is_unknown_not_passed() -> None:
    exp = observability_floor(
        Expectation(verdict_min="NO_CHANGE", assertions=(_ALIGN_ONLY,)), _cfg("dwarf")
    )
    assert exp.undecided
    (a,) = exp.assertions
    assert a.rows == () and "TYPE_ALIGNMENT_CHANGED" not in a.scope.kinds
    assert a.judge(()).status == "inconclusive"
    from abiforge.model.expect import ExecTruth

    got = reconcile(
        ExecTruth(available=False, reason="off"),
        exp,
        "NO_CHANGE",
        frozenset(),
        findings=(),
    )
    assert got.cls == "evidence-limited"


def test_the_same_alignment_row_is_judged_with_headers() -> None:
    exp = observability_floor(
        Expectation(verdict_min="NO_CHANGE", assertions=(_ALIGN_ONLY,)), _cfg("headers")
    )
    (a,) = exp.assertions
    assert not exp.undecided and len(a.rows) == 1
    good = (Finding("TYPE_ALIGNMENT_CHANGED", "U", old_value="64", new_value="8"),)
    assert a.judge(good).status == "pass"
    assert a.judge(()).status == "fail"


def test_a_narrowed_scope_without_dropped_rows_is_not_unknown() -> None:
    control = FindingAssertion(
        FindingScope(
            frozenset({"TYPE_ALIGNMENT_CHANGED", "TYPE_SIZE_CHANGED"}), frozenset({"U"})
        ),
        (),
        kind_needs={"TYPE_ALIGNMENT_CHANGED": frozenset({"H"})},
    )
    exp = observability_floor(
        Expectation(verdict_min="NO_CHANGE", assertions=(control,)), _cfg("dwarf")
    )
    assert not exp.undecided
    assert exp.assertions[0].judge(()).status == "pass"


def test_exact_problems_reconcile_as_wrong_detail_with_stable_keys() -> None:
    from abiforge.model.expect import ExecTruth

    rows, findings = _BIT_SPILL
    exp = Expectation(
        expected=frozenset({"FIELD_BITFIELD_CHANGED"}), assertions=(_assertion(rows),)
    )
    wrong = findings[:1] + (replace(findings[1], field_name="c"),) + findings[2:]
    kinds = frozenset(f.kind for f in findings)
    got = reconcile(
        ExecTruth(available=False, reason="off"), exp, "BREAKING", kinds, findings=wrong
    )
    assert got.cls == "wrong-detail"
    keys = issue_keys(got.problems)
    assert any(k.startswith("exact-missing:") for k in keys)
    assert any(k.startswith("exact-unexpected:") for k in keys)
    # Without structured findings the assertion is not judged (an adapter
    # that gives only kinds keeps the kind-set judgement).
    assert (
        reconcile(ExecTruth(available=False, reason="off"), exp, "BREAKING", kinds).cls
        == "agree"
    )


def test_unexpected_rows_mixing_none_and_text_still_fail() -> None:
    # Two unexpected rows that differ only in a None versus a text member
    # (and value) must be reported, not crash the report's ordering.
    control = _assertion(())
    rows = (
        Finding("TYPE_SIZE_CHANGED", "Bits", old_value="32", new_value="64"),
        Finding(
            "TYPE_SIZE_CHANGED", "Bits", field_name="a", old_value="32", new_value="64"
        ),
        Finding("UNION_FIELD_ADDED", "Payload", field_name="x", new_value="int"),
        Finding("UNION_FIELD_ADDED", "Payload", field_name="x", old_value="int"),
    )
    out = control.judge(rows)
    assert out.status == "fail"
    assert sum(p.startswith("exact unexpected") for p in out.problems) == 4
