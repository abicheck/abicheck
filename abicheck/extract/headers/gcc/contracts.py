# SPDX-License-Identifier: Apache-2.0
"""GCC contract facts projected into existing abicheck model carriers."""

from __future__ import annotations

import re
from typing import Any

from ....model import Fact
from ....model.concept_constraint import constraint_digest
from ....model.identity import (
    Anonymous,
    InlineNamespace,
    Namespace,
    Record,
    canonicalize_type_param_references,
)
from ....model.template_default_table import (
    merge_template_defaults,
    template_default_key,
)

_ATOMIC = re.compile(r"^((?:const |volatile )*)atomic (.+)$")
_OPEN_ARRAY = re.compile(r"\[(?:0:|<unknown>)\]")


def clean(value: Any) -> Any:
    """GCC's type spelling in the spelling the other header backends use.

    GCC prints an ``_Atomic T`` as ``atomic T`` and an array of unknown
    bound (a flexible array member, ``extern const char s[]``) as
    ``T[0:]`` or ``T[<unknown>]``; CastXML and Clang write ``_Atomic(T)``
    and ``T[]``, which is what the type-text detectors read.
    """
    value = re.sub("^(?:struct|union|enum|class)\\s+", "", value)
    value = _OPEN_ARRAY.sub("[]", value)
    return _ATOMIC.sub(r"\1_Atomic(\2)", value)


def scope_names(row: Any) -> Any:
    result: list[Any] = []
    for segment in row.get("scope", []):
        name = clean(segment["name"])
        prefix = "::".join(result)
        if prefix and name.startswith(prefix + "::"):
            name = name[len(prefix) + 2 :]
        result.append(name or "(anonymous namespace)")
    return result


def scope(row: Any) -> Any:
    result: list[Any] = []
    for segment, name in zip(row.get("scope", []), scope_names(row)):
        kind = segment["kind"]
        result.append(
            InlineNamespace(name)
            if kind == "inline_namespace"
            else Anonymous("namespace", 0)
            if kind == "anonymous_namespace"
            else Namespace(name)
            if kind == "namespace"
            else Record(name)
        )
    return tuple(result)


def qualified(row: Any) -> Any:
    return "::".join((*scope_names(row), row["name"]))


def leaf(row: Any) -> Any:
    value = clean(row.get("leaf_name", row["name"]))
    prefix = "::".join(scope_names(row))
    return (
        value[len(prefix) + 2 :]
        if prefix and value.startswith(prefix + "::")
        else value
    )


def templates(rows: Any, policy: Any) -> Any:
    defaults: dict[str, Any] = {}
    constraints = {}
    patterns = {}
    for row in rows:
        if row["kind"] != "template" or not policy.public(row.get("source")):
            continue
        names = tuple(p["name"] for p in row["parameters"])

        def canonical(text: str, names: tuple[str, ...] = names) -> str:
            return canonicalize_type_param_references(text, names)

        name = qualified(row)
        family = row["family"]
        expression = row.get("concept_expression")
        if family == "concept":
            constraints["concept " + name] = "gcc-v2:" + constraint_digest(
                canonical(expression or "")
            )
            continue
        keyname = name
        if family == "function":
            keyname += "(" + canonical(row.get("signature") or "?") + ")"
        key = template_default_key(family, keyname)
        values = tuple(
            canonical(p["default"]) if p["default"] is not None else None
            for p in row["parameters"]
        )
        defaults[key] = (
            merge_template_defaults([defaults[key], values])
            if key in defaults
            else values
        )
        patterns[name] = values
        if family in ("class", "function"):
            text = row.get("constraint")
            constraints["template " + keyname] = "gcc-v2:" + constraint_digest(
                canonical(text or "")
            )
    return (defaults, constraints, patterns)


def vtable_fact(row: Any) -> Any:
    """The declared virtual-slot sequence of the record's primary vtable.

    Read from the class's own ``BINFO_VIRTUALS`` (the group at offset 0),
    which every translation unit that sees the class definition has. The
    emitted vtable initializer is only a fallback: it exists only where the
    key function is defined, and it spells a pure virtual slot as
    ``__cxa_pure_virtual``, so a pure method becoming concrete read as a
    vtable change.
    """
    groups = row.get("vtable_candidate_groups")
    if groups:
        primary = [g for g in groups if g.get("offset_bytes") == 0][:1]
        slots = primary[0]["slots"] if primary else []
        if slots and all(s.get("mangled") for s in slots):
            return Fact.present([s["mangled"] for s in slots])
    tables = [
        v for v in row.get("vtable_initializers", []) if v["name"].startswith("_ZTV")
    ]
    if tables and all(
        v.get("initializer_kind") == "constructor" and "entries" in v for v in tables
    ):
        targets = [
            e["target"]
            for v in tables
            for e in v["entries"]
            if e.get("target") and (not e["target"].startswith("_ZTI"))
        ]
        return Fact.present(targets)
    if "vtable_initializers" in row and (not row.get("vtable_candidate_groups")):
        return Fact.present([])
    return Fact.not_collected()


def attributes(row: Any) -> Any:
    supported = {
        "nonnull",
        "noreturn",
        "returns_nonnull",
        "warn_unused_result",
        "format",
        "alloc_size",
        "alloc_align",
        "malloc",
        "sentinel",
        "ms_abi",
        "sysv_abi",
        "cdecl",
        "stdcall",
        "fastcall",
        "thiscall",
        "vectorcall",
    }
    result = set()
    for attr in row.get("attribute_details", []):
        name = attr["name"].strip("_")
        if name not in supported:
            continue
        args = attr["arguments"]
        if any(a is None for a in args):
            continue
        result.add(name + ("(" + ",".join(args) + ")" if args else ""))
    return sorted(result)
