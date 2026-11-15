"""Edit operators for the enum classes of ``abiforge.model.edit_model``.

The three enum constructs (unscoped, fixed underlying type, scoped) share
their classes (``_ENUM_CLASSES``), so one table generates the operators: one
per (construct, class) no older operator generated, each editing one
``en.*``/``ef.*``/``ec.*`` probe of ``cxx_more`` (``abiforge.model.probes``).
The probe enum is ``{P}A, {P}B, {P}C`` (values 0, 1, 2), taken by value by a
``*_use`` function so the library's DWARF carries it.

Ground truth, from C11 6.7.2.2 and [dcl.enum]: an enumerator is a constant
compiled into every user, so a value that changes (directly, by an insertion
before it, or by a swap) changes the meaning of every value old code stores
or passes, a break; a removed or renamed enumerator no longer compiles in
user code; an appended enumerator or an alias of an existing value adds to
the API; a scoped enum's enumerators are no longer names in the enclosing
scope and do not convert to int; an opaque declaration
(``enum E : int;``) keeps the type complete, with its size, but takes every
enumerator away.
"""

from __future__ import annotations

from typing import Any

from abiforge.model.opkit import EditOp, Roles, _set
from abiforge.model.probes import (
    ENUM_PROBE_EDITS,
    ENUM_PROBES,
    enum_names,
    enum_probe_text,
)

__all__ = ["ENUM_OPERATORS"]

_FEATURE = {"en": (), "ef": ("enum_underlying",), "ec": ("enum_class",)}
_WORD = {"en": "enum", "ef": "fixed_enum", "ec": "enum_class"}

_COMPAT: dict[str, Any] = {
    "verdict_min": "COMPATIBLE",
    "verdict_max": "COMPATIBLE_WITH_RISK",
}
_SOURCE: dict[str, Any] = {"verdict_min": "API_BREAK", "verdict_max": "API_BREAK"}
_VALUE = frozenset({"ENUM_MEMBER_VALUE_CHANGED", "ENUM_LAST_MEMBER_VALUE_CHANGED"})

# edit -> (operator suffix, enumerators or None for opaque, why, truth)
_EDITS: dict[str, tuple[str, str | None, str, dict[str, Any]]] = {
    "append": (
        "enumerator_appended",
        "{p}A, {p}B, {p}C, {p}D",
        "{p}D = 3 is added after the last enumerator: no existing value changes",
        {"expected": frozenset({"ENUM_MEMBER_ADDED"}), **_COMPAT},
    ),
    "insert": (
        "enumerator_inserted",
        "{p}A, {p}X, {p}B, {p}C",
        "{p}X goes before {p}B: {p}B becomes 2 and {p}C 3, so a value old code "
        "stores or passes as {p}B now means {p}X",
        {"any_of": (_VALUE,)},
    ),
    "remove": (
        "enumerator_removed",
        "{p}A, {p}B",
        "{p}C is removed: code naming it no longer compiles, and a 2 an old "
        "caller passes is no longer a value of the enum",
        {"expected": frozenset({"ENUM_MEMBER_REMOVED"})},
    ),
    "value": (
        "enumerator_value_changed",
        "{p}A, {p}B = 10, {p}C",
        "{p}B becomes 10 (and {p}C 11): stored and passed values change meaning",
        {"any_of": (_VALUE,)},
    ),
    "swap": (
        "enumerator_values_swapped",
        "{p}A, {p}B = 2, {p}C = 1",
        "{p}B and {p}C exchange values: old code's 1 now means {p}C",
        {"any_of": (_VALUE | {"SERIALIZATION_TAG_CHANGED"},)},
    ),
    "rename": (
        "enumerator_renamed",
        "{p}A, {p}BB, {p}C",
        "{p}B is renamed {p}BB with the same value: compiled code is unaffected, "
        "source naming {p}B no longer compiles",
        {
            "expected": frozenset({"ENUM_MEMBER_RENAMED"}),
            **_SOURCE,
            "min_evidence": "headers",
        },
    ),
    "alias": (
        "enumerator_alias_added",
        "{p}A, {p}B, {p}C, {p}ALIAS = {p}B",
        "{p}ALIAS is a second name for 1: no value is added or changed",
        {"expected": frozenset({"ENUM_MEMBER_ADDED"}), **_COMPAT},
    ),
    "scoped": (
        "enum_became_scoped",
        "{p}A, {p}B, {p}C",
        "the enum becomes scoped, its underlying type still int: layout and "
        "values are unchanged, but unqualified {p}A and the implicit "
        "conversion to int no longer compile",
        {
            "expected": frozenset({"ENUM_BECAME_SCOPED"}),
            **_SOURCE,
            "min_evidence": "headers",
        },
    ),
    "added": (
        "enum_added",
        "{p}A, {p}B, {p}C",
        "a new enum is declared: nothing existing changes",
        {
            "expected": frozenset({"TYPE_ADDED"}),
            "verdict_min": "COMPATIBLE",
            "verdict_max": "COMPATIBLE",
            "min_evidence": "headers",
        },
    ),
    "removed": (
        "enum_removed",
        None,
        "the enum (used by no exported function) is removed from the header: "
        "nothing is exported for it, but code naming it no longer compiles",
        {
            "expected": frozenset({"TYPE_REMOVED"}),
            **_SOURCE,
            "min_evidence": "headers",
        },
    ),
    "opaque": (
        "enum_became_opaque",
        None,
        "the definition becomes an opaque declaration: the type keeps its size "
        "(its underlying type is fixed), but every enumerator is gone",
        {"expected": frozenset({"ENUM_MEMBER_REMOVED"}), "min_evidence": "headers"},
    ),
}


def _edit(prefix: str, edit: str, enumerators: str | None) -> Any:
    role = f"{prefix}.{edit}"
    name, p, _ = enum_names(prefix, edit)

    if edit == "removed":

        def removed(r: Roles) -> dict[str, tuple[str, str]]:
            return _set(r, role, "", "")

        return removed

    opening = None
    if edit == "scoped":
        opening = "enum class {n} : int"
    text = None if enumerators is None else enumerators.format(p=p)
    header = enum_probe_text(prefix, edit, text, opening=opening)
    source = None
    if edit == "added":
        source = ""

    def edited(r: Roles) -> dict[str, tuple[str, str]]:
        return _set(r, role, header, source)

    return edited


def _operators() -> tuple[EditOp, ...]:
    out = []
    for prefix, _construct, _opening in ENUM_PROBES:
        for edit in ENUM_PROBE_EDITS[prefix]:
            suffix, enumerators, why, truth = _EDITS[edit]
            _, p, _ = enum_names(prefix, edit)
            role = f"{prefix}.{edit}"
            kw = dict(truth)
            kw.setdefault("min_evidence", "dwarf")
            out.append(
                EditOp(
                    f"{_WORD[prefix]}_{suffix}"
                    if not suffix.startswith("enum_")
                    else f"{_WORD[prefix]}{suffix[4:]}",
                    (role,),
                    _edit(prefix, edit, enumerators),
                    why.format(p=p),
                    touches=(role,),
                    features=_FEATURE[prefix],
                    **kw,
                )
            )
    return tuple(out)


ENUM_OPERATORS: tuple[EditOp, ...] = _operators()
