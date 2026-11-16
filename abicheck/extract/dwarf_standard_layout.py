# SPDX-License-Identifier: Apache-2.0
"""The standard-layout trait of a DWARF record, where DWARF proves it.

This is the DWARF counterpart of :mod:`abicheck.extract.headers.castxml.record_traits`
(C++ [class.prop]/3, N4659 numbering in the comments below), reading the same
rules off the compiler's debug records instead of a CastXML tree.

Two steps, so no DIE's children are walked twice:

1. While a record is processed, :meth:`StandardLayoutCollector.add` reads its
   already-materialized children once and keeps a small summary: the answers
   that need only this record (virtual functions and base_edges, reference members,
   access uniformity), plus references to the record types its by-value members
   and base_edges name.
2. :meth:`StandardLayoutCollector.finalize` resolves those references across
   every collected record (memoized, cycle-safe) and writes the tri-state trait
   onto each ``RecordType``. ``None`` is "DWARF does not settle it" (an
   incomplete type, a declaration, an unresolved reference): never a break,
   never a clean ``True``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..dwarf_utils import resolve_type_die, unwrap_cv_typedef
from ..model.fact import Fact
from .dwarf_records import access_from_dwarf, default_member_access_for_tag

_RECORD_TAGS = frozenset(
    {"DW_TAG_structure_type", "DW_TAG_class_type", "DW_TAG_union_type"}
)
#: Types whose values are standard-layout by definition.
_SCALAR_TAGS = frozenset(
    {
        "DW_TAG_base_type",
        "DW_TAG_pointer_type",
        "DW_TAG_enumeration_type",
        "DW_TAG_ptr_to_member_type",
    }
)
_REFERENCE_TAGS = frozenset({"DW_TAG_reference_type", "DW_TAG_rvalue_reference_type"})
#: How deep a chain of by-value members/bases is followed before giving up.
_MAX_DEPTH = 32

Key = tuple[int, int]
#: A by-value type reference: ``("scalar", None)``, ``("record", key)`` or
#: ``("unknown", None)``.
Ref = tuple[str, Key | None]
_SCALAR: Ref = ("scalar", None)
_UNKNOWN: Ref = ("unknown", None)


@dataclass(frozen=True)
class _Summary:
    """What one record's own DIEs say, with other records left as references."""

    #: True when a rule fails on this record's own children alone.
    local_false: bool
    #: A declaration or otherwise not a complete type.
    incomplete: bool
    #: Its non-artificial data members in declaration order, as type references.
    members: tuple[Ref, ...]
    #: Its direct base classes in order: ``(base key or None, is virtual)``.
    base_edges: tuple[tuple[Key | None, bool], ...]
    union: bool


def _die_key(die: Any) -> Key:
    return (int(die.cu.cu_offset), int(die.offset))


def _flag(die: Any, attr: str) -> bool:
    return attr in die.attributes and bool(die.attributes[attr].value)


def _is_virtual(die: Any) -> bool:
    return (
        "DW_AT_virtuality" in die.attributes
        and int(die.attributes["DW_AT_virtuality"].value) > 0
    )


def _strip(type_die: Any) -> Any | None:
    if type_die is None:
        return None
    return unwrap_cv_typedef(type_die, type_die.cu)


def _ref_of(type_die: Any, depth: int = 0) -> Ref:
    """The by-value reference a declared type denotes (arrays unwrap to their element)."""
    stripped = _strip(type_die)
    if stripped is None or depth > _MAX_DEPTH:
        return _UNKNOWN
    if stripped.tag == "DW_TAG_array_type":
        return _ref_of(resolve_type_die(stripped, stripped.cu), depth + 1)
    if stripped.tag in _SCALAR_TAGS:
        return _SCALAR
    if stripped.tag in _RECORD_TAGS:
        return ("record", _die_key(stripped))
    return _UNKNOWN


def _is_reference(type_die: Any) -> bool:
    stripped = _strip(type_die)
    return stripped is not None and stripped.tag in _REFERENCE_TAGS


def _base_key(inheritance: Any) -> Key | None:
    target = _strip(resolve_type_die(inheritance, inheritance.cu))
    if target is None or target.tag not in _RECORD_TAGS:
        return None
    return _die_key(target)


def _summarize(die: Any, children: list[Any]) -> _Summary:
    members = [
        c
        for c in children
        if c.tag == "DW_TAG_member"
        and not _flag(c, "DW_AT_artificial")
        and not _flag(c, "DW_AT_external")
        and not _flag(c, "DW_AT_declaration")
    ]
    base_edges = [c for c in children if c.tag == "DW_TAG_inheritance"]
    # [class.prop]/3.2: no virtual function declared here, no virtual base.
    virtual = any(c.tag == "DW_TAG_subprogram" and _is_virtual(c) for c in children)
    virtual = virtual or any(_is_virtual(b) for b in base_edges)
    # [class.prop]/3.1: no reference data member.
    reference = any(_is_reference(resolve_type_die(m, m.cu)) for m in members)
    # [class.prop]/3.3: one access control for every non-static data member.
    default = default_member_access_for_tag(die.tag)
    accesses = {
        access_from_dwarf(
            int(m.attributes["DW_AT_accessibility"].value)
            if "DW_AT_accessibility" in m.attributes
            else 0,
            default,
        ).value
        for m in members
    }
    return _Summary(
        local_false=virtual or reference or len(accesses) > 1,
        incomplete=_flag(die, "DW_AT_declaration"),
        members=tuple(_ref_of(resolve_type_die(m, m.cu)) for m in members),
        base_edges=tuple((_base_key(b), _is_virtual(b)) for b in base_edges),
        union=die.tag == "DW_TAG_union_type",
    )


class _Resolver:
    """Tri-state trait over collected summaries; memoized and cycle-safe."""

    def __init__(self, summaries: dict[Key, _Summary]) -> None:
        self._s = summaries
        self._trait: dict[Key, bool | None] = {}
        self._has_data: dict[Key, bool | None] = {}
        self._first: dict[Key, frozenset[Key] | None] = {}

    def trait(self, key: Key, depth: int = 0) -> bool | None:
        if key in self._trait:
            return self._trait[key]
        s = self._s.get(key)
        if s is None or s.incomplete or depth > _MAX_DEPTH:
            return None
        self._trait[key] = None  # provisional: breaks an ill-formed cycle
        result = self._compute(key, s, depth)
        self._trait[key] = result
        return result

    def _compute(self, key: Key, s: _Summary, depth: int) -> bool | None:
        if s.local_false:
            return False
        traits: list[bool | None] = [
            None if base is None or virtual else self.trait(base, depth + 1)
            for base, virtual in s.base_edges
        ]
        traits += [self._ref_trait(ref, depth) for ref in s.members]
        traits.append(self._data_member_rules(key, s))
        if False in traits:
            return False
        if None in traits:
            return None
        return True

    def _ref_trait(self, ref: Ref, depth: int) -> bool | None:
        kind, key = ref
        if kind == "scalar":
            return True
        if kind == "record" and key is not None:
            return self.trait(key, depth + 1)
        return None

    def _has_data_members(self, key: Key, depth: int = 0) -> bool | None:
        """Whether record *key* or any of its base_edges has a non-static data member."""
        if key in self._has_data:
            return self._has_data[key]
        s = self._s.get(key)
        if s is None or s.incomplete or depth > _MAX_DEPTH:
            return None
        if s.members:
            self._has_data[key] = True
            return True
        flags = [
            None if base is None else self._has_data_members(base, depth + 1)
            for base, _ in s.base_edges
        ]
        out: bool | None = True if True in flags else (None if None in flags else False)
        self._has_data[key] = out
        return out

    def _subobjects(self, key: Key, depth: int = 0) -> list[Key] | None:
        """Every base subobject along every inheritance path, one per occurrence."""
        s = self._s.get(key)
        if s is None or depth > _MAX_DEPTH:
            return None
        ids: list[Key] = []
        for base, _ in s.base_edges:
            inner = None if base is None else self._subobjects(base, depth + 1)
            if base is None or inner is None:
                return None
            ids += [base, *inner]
        return ids

    def _first_member_types(self, ref: Ref, depth: int = 0) -> frozenset[Key] | None:
        """Record keys in [class.prop]/3's set M(X) for a type reference."""
        kind, key = ref
        if kind == "scalar":
            return frozenset()
        if kind != "record" or key is None or depth > _MAX_DEPTH:
            return None
        if key in self._first:
            return self._first[key]
        s = self._s.get(key)
        if s is None or s.incomplete:
            return None
        out: set[Key] = {key}
        for member in s.members if s.union else s.members[:1]:
            inner = self._first_member_types(member, depth + 1)
            if inner is None:
                self._first[key] = None
                return None
            out |= inner
        self._first[key] = frozenset(out)
        return self._first[key]

    def _data_member_rules(self, key: Key, s: _Summary) -> bool | None:
        """[class.prop]/3.5 (data members in at most one class of the hierarchy,
        and none in the most derived one when a base has some), /3.7 (one base
        subobject per type), /3.8 (no base in M(D))."""
        flags = [
            None if b is None else self._has_data_members(b) for b, _ in s.base_edges
        ]
        with_data = flags.count(True)
        unknown = flags.count(None)
        if s.members:
            if with_data:
                return False
            hierarchy: bool | None = None if unknown else True
        else:
            if with_data >= 2:
                return False
            hierarchy = None if with_data + unknown >= 2 else True
        if not s.base_edges:
            return hierarchy
        subobjects = self._subobjects(key)
        if subobjects is None:
            return None
        if len(subobjects) != len(set(subobjects)):
            return False
        if not s.members:
            return hierarchy
        first = self._first_member_types(s.members[0])
        if first is None:
            return None
        if first & set(subobjects):
            return False
        return hierarchy


class StandardLayoutCollector:
    """The summaries of one binary's records, resolved once every record is known."""

    def __init__(self) -> None:
        self._summaries: dict[Key, _Summary] = {}
        self._records: list[tuple[Any, Key]] = []

    def add(self, die: Any, children: list[Any] | None, record: Any) -> None:
        """Summarize record *die* from its already-materialized *children*.

        A record with no child list at hand is left unresolved (None): this
        never walks DWARF a second time just to answer the trait.
        """
        if children is None:
            return
        key = _die_key(die)
        self._summaries[key] = _summarize(die, children)
        self._records.append((record, key))

    def finalize(self) -> None:
        """Write each collected record's resolved trait; None leaves it unknown."""
        resolver = _Resolver(self._summaries)
        for record, key in self._records:
            value = resolver.trait(key)
            if value is not None:
                record.is_standard_layout = value
                record.is_standard_layout_fact = Fact.present(value)
        self._records.clear()


def collector_for(builder: Any) -> StandardLayoutCollector:
    """The collector of *builder*, created on first use and kept on the builder."""
    collector = getattr(builder, "_standard_layout_collector", None)
    if collector is None:
        collector = StandardLayoutCollector()
        builder._standard_layout_collector = collector
    return collector
