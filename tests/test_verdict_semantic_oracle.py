"""Independent expected-verdict oracle for representative ``ChangeKind``s.

``tests/test_severity.py::TestClassifyChange::test_exhaustive_all_kinds_classified``
and ``tests/test_case_analysis_validation.py::TestExpectedKindsVerdictAlignment``
derive their expectations from the change registry itself (``BREAKING_KINDS``,
``REGISTRY.entries[...].default_verdict``), so they are *consistency* tests: a
wrong ``default_verdict`` moves the oracle with the implementation and both stay
green. This table is written from C/C++ ABI semantics instead (Itanium C++ ABI,
the SysV psABIs, ELF dynamic linking), one justification per row, and is
checked against all three places a verdict surfaces: the registry entry, the
``checker_policy`` verdict sets, and the strict-profile ``compute_verdict`` a
report shows. A row changes only when the ABI argument changes, never to match
the registry.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from abicheck.checker_policy import (
    API_BREAK_KINDS,
    BREAKING_KINDS,
    COMPATIBLE_KINDS,
    RISK_KINDS,
    ChangeKind,
)
from abicheck.model.change import Change
from abicheck.model.change_catalog.assembled import REGISTRY
from abicheck.policy.classification import compute_verdict

B, API, RISK, OK = "BREAKING", "API_BREAK", "COMPATIBLE_WITH_RISK", "COMPATIBLE"

#: kind value -> (expected verdict, why, from ABI semantics alone)
ORACLE: dict[str, tuple[str, str]] = {
    # -- an already-linked consumer stops working ------------------------------
    "func_removed": (
        B,
        "an old binary's undefined reference to the symbol no longer resolves at load/bind time",
    ),
    "var_removed": (
        B,
        "an old binary's data relocation (GOT or copy relocation) names a symbol that is gone",
    ),
    "func_params_changed": (
        B,
        "the caller marshals arguments per the old prototype; the callee reads them per the new one (C++: the mangled name changes too)",
    ),
    "func_return_changed": (
        B,
        "the return value's register class or hidden sret pointer is fixed in the caller at compile time",
    ),
    "var_type_changed": (
        B,
        "the consumer's copy relocation and every access width were sized for the old type",
    ),
    "type_size_changed": (
        B,
        "consumers allocate the object (stack, arrays, embedding) with the old sizeof and the library touches the new extent",
    ),
    "type_field_offset_changed": (
        B,
        "member accesses are compiled into the consumer as fixed offsets",
    ),
    "type_field_removed": (
        B,
        "consumer code compiled against the member reads storage that now belongs to something else",
    ),
    "type_alignment_changed": (
        B,
        "alignment fixes struct padding, array stride and argument passing in the consumer",
    ),
    "enum_member_value_changed": (
        B,
        "enumerator values are integer constants baked into the consumer's code",
    ),
    "enum_underlying_size_changed": (
        B,
        "sizeof(enum) changes argument passing and the layout of every struct embedding it",
    ),
    "func_virtual_added": (
        B,
        "a new virtual shifts vtable slots that consumers and derived classes index by number",
    ),
    "func_virtual_removed": (
        B,
        "removing a virtual shifts the vtable slots of every later virtual the consumer calls by index",
    ),
    "base_class_position_changed": (
        B,
        "this-pointer adjustments for base subobjects are compiled-in offsets",
    ),
    "calling_convention_changed": (
        B,
        "caller and callee disagree on argument registers and on who cleans the stack",
    ),
    "func_static_changed": (
        B,
        "a member function gaining or losing static drops or adds the implicit this argument",
    ),
    # -- binaries keep working, rebuilding the consumer fails ------------------
    "field_renamed": (
        API,
        "type, size and offset are unchanged, so binaries agree; source naming the old member no longer compiles",
    ),
    "enum_member_renamed": (
        API,
        "the enumerator's value is unchanged; source naming the old enumerator no longer compiles",
    ),
    "ctor_explicit_added": (
        API,
        "explicit is a compile-time restriction on implicit conversions; the constructor symbol is unchanged",
    ),
    # -- nothing an existing consumer relies on changed ------------------------
    "func_added": (OK, "an old binary references no symbol it did not already have"),
    "var_added": (OK, "an old binary references no symbol it did not already have"),
    "func_deprecated_added": (
        OK,
        "[[deprecated]] only adds a compile-time diagnostic; codegen and symbols are unchanged",
    ),
    "func_noexcept_added": (
        OK,
        "a function's own mangled name excludes its exception specification and callers already handle a non-throwing callee",
    ),
    # -- works, with a stated hazard -------------------------------------------
    "func_noexcept_removed": (
        RISK,
        "callers compiled against noexcept may omit unwind paths, so a new throw reaches std::terminate",
    ),
    "enum_last_member_value_changed": (
        RISK,
        "a trailing COUNT/MAX enumerator is commonly compiled into consumers as an array bound or range check",
    ),
}

_SETS = {
    B: BREAKING_KINDS,
    API: API_BREAK_KINDS,
    RISK: RISK_KINDS,
    OK: COMPATIBLE_KINDS,
}


def oracle_mismatches(verdict_of: Callable[[str], str]) -> list[str]:
    """Rows whose verdict, as *verdict_of* reports it, disagrees with the table."""
    return [
        f"{kind}: expected {want} ({why}), got {verdict_of(kind)}"
        for kind, (want, why) in ORACLE.items()
        if verdict_of(kind) != want
    ]


def _registry_verdict(kind: str) -> str:
    return str(REGISTRY.entries[kind].default_verdict)


def _set_verdict(kind: str) -> str:
    member = ChangeKind(kind)
    found = [name for name, kinds in _SETS.items() if member in kinds]
    return found[0] if len(found) == 1 else f"in sets {found}"


def _report_verdict(kind: str) -> str:
    change = Change(kind=ChangeKind(kind), symbol="sym", description="oracle")
    return str(compute_verdict([change]))


def test_oracle_spans_every_verdict_class() -> None:
    wants = [want for want, _ in ORACLE.values()]
    assert len(ORACLE) >= 20
    assert {want: wants.count(want) >= 2 for want in _SETS} == dict.fromkeys(
        _SETS, True
    )
    assert all(len(why) > 40 for _, why in ORACLE.values())


@pytest.mark.parametrize(
    "verdict_of",
    [_registry_verdict, _set_verdict, _report_verdict],
    ids=["registry", "checker_policy_sets", "strict_compute_verdict"],
)
def test_verdicts_match_the_abi_semantics_oracle(
    verdict_of: Callable[[str], str],
) -> None:
    mismatches = oracle_mismatches(verdict_of)
    assert not mismatches, "\n".join(mismatches)


@pytest.mark.parametrize("kind", sorted(ORACLE))
def test_flipping_one_default_verdict_fails_the_oracle(kind: str) -> None:
    """Negative control: the registry-derived consistency tests would accept any
    flipped default_verdict; this oracle names the flipped kind and no other."""
    want = ORACLE[kind][0]
    flipped = OK if want != OK else B

    def verdict_of(k: str) -> str:
        return flipped if k == kind else _registry_verdict(k)

    got = oracle_mismatches(verdict_of)
    assert [m.split(":", 1)[0] for m in got] == [kind]
