"""Two distinct entities that share one bare name must both survive into findings.

Several readers keyed a declaration by its bare name (``AbiSnapshot.typedefs``
by alias, the record maps by castxml's leaf ``name``) and let the last entry
win, so a second entity with the same bare name was never seen. Each row below
is one such site: a two-entities-one-bare-name input and the observation that
shows the entity survived. Oracle: the expected values are hand-derived from
the inputs, never read back from the implementation's own helpers.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from abicheck.checker import compare
from abicheck.elf_metadata import ElfMetadata
from abicheck.export_surface import compute_export_surface
from abicheck.model import AbiSnapshot, Function, Param, RecordType, TypeField
from abicheck.model.elf_facts import ElfSymbol

_TIME64_FLIP = "off_t (long → long long)"


def _elf(*, bits32: bool = False) -> ElfMetadata:
    if bits32:
        return ElfMetadata(
            symbols=[ElfSymbol(name="take")], elf_class=32, pointer_size=4
        )
    return ElfMetadata(symbols=[ElfSymbol(name="take")])


def _take(param_type: str) -> Function:
    return Function(
        name="take",
        mangled="take",
        return_type="void",
        params=[Param(name="v", type=param_type)],
    )


def _record(qualified: str, field_type: str = "int") -> RecordType:
    # castxml convention: ``name`` is the leaf, ``qualified_name`` the full spelling.
    return RecordType(
        name=qualified.rsplit("::", 1)[-1],
        qualified_name=qualified,
        kind="struct",
        fields=[TypeField(name="x", type=field_type, offset_bits=0)],
    )


# ---------------------------------------------------------------- diff_time64


def _time64_snapshot(
    off_t_target: str,
    param_type: str,
    *,
    typedefs_qualified: dict[str, str],
    types: list[RecordType] | None = None,
    bare_value_type: str | None = None,
) -> AbiSnapshot:
    snap = AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        functions=[_take(param_type)],
        types=types or [],
    )
    snap.elf = _elf(bits32=True)
    bare = {"off_t": off_t_target}
    if bare_value_type is not None:
        bare["value_type"] = bare_value_type
    snap.declarations.typedefs = bare
    snap.declarations.typedefs_qualified = typedefs_qualified
    return snap


def _time64_flip_count(old: AbiSnapshot, new: AbiSnapshot) -> int:
    result = compare(old, new)
    return sum(
        1
        for c in result.changes
        if c.kind.value == "time64_abi_changed" and _TIME64_FLIP in c.description
    )


def _colliding_alias_time64() -> int:
    # Foo::value_type -> off_t is reached by f(value_type); Bar::value_type ->
    # int shares the bare key and is the last entry the bare dict kept.
    def snap(off_t_target: str) -> AbiSnapshot:
        return _time64_snapshot(
            off_t_target,
            "value_type",
            typedefs_qualified={
                "off_t": off_t_target,
                "Foo::value_type": "off_t",
                "Bar::value_type": "int",
            },
            bare_value_type="int",
        )

    return _time64_flip_count(snap("long"), snap("long long"))


def _namespaced_record_time64() -> int:
    # Only ns::Holder is reached, spelled qualified; its off_t field must roll up.
    def snap(off_t_target: str) -> AbiSnapshot:
        s = _time64_snapshot(
            off_t_target,
            "ns::Holder *",
            typedefs_qualified={"off_t": off_t_target},
            types=[_record("ns::Holder", "off_t")],
        )
        return s

    return _time64_flip_count(snap("long"), snap("long long"))


def _same_leaf_record_time64() -> int:
    # ns::Holder (int) is reached; other::Holder (off_t) shares the leaf and must
    # not ride along, so no roll-up is expected.
    def snap(off_t_target: str) -> AbiSnapshot:
        return _time64_snapshot(
            off_t_target,
            "ns::Holder *",
            typedefs_qualified={"off_t": off_t_target},
            types=[_record("ns::Holder", "int"), _record("other::Holder", "off_t")],
        )

    return _time64_flip_count(snap("long"), snap("long long"))


# ----------------------------------------------------------- export_surface


def _export_surface_observation() -> tuple[bool, tuple[str, ...]]:
    # Foo::value_type -> Missing::Gone is a real hole; the bare dict keeps only
    # Bar's surviving target, which resolves. The hole must still be reported.
    snap = AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        functions=[_take("value_type")],
        types=[_record("Foo::Inner"), _record("Bar::Other")],
    )
    snap.elf = _elf()
    snap.declarations.typedefs = {"value_type": "Bar::Other"}
    snap.declarations.typedefs_qualified = {
        "Foo::value_type": "Missing::Gone",
        "Bar::value_type": "Bar::Other",
    }
    surface = compute_export_surface(snap)
    return surface.exclusion_is_provable, tuple(sorted(surface.unresolved_type_edges))


# ---------------------------------------------------------- embedded growth


def _growth_snapshot(
    box_size: int, outer: str, member_type: str, box_name: str, extra: list[RecordType]
) -> AbiSnapshot:
    return AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        types=[
            RecordType(
                name=box_name.rsplit("::", 1)[-1],
                qualified_name=box_name,
                kind="struct",
                size_bits=box_size,
                fields=[TypeField(name="p", type="long", offset_bits=0)],
            ),
            *extra,
            RecordType(
                name=outer.rsplit("::", 1)[-1],
                qualified_name=outer,
                kind="struct",
                size_bits=32 + box_size,
                fields=[
                    TypeField(name="b", type=member_type, offset_bits=0),
                    TypeField(name="x", type="int", offset_bits=box_size),
                ],
            ),
        ],
    )


def _growth_bits(old: AbiSnapshot, new: AbiSnapshot) -> int | None:
    result = compare(old, new)
    shifts = [c for c in result.changes if c.kind.value == "type_field_offset_changed"]
    assert len(shifts) == 1
    cause = (shifts[0].review_evidence or {}).get("offset_shift_cause")
    if cause is None:
        return None
    (member,) = cause["grown_members"]
    return member["bits"]


def _namespaced_growth() -> int | None:
    return _growth_bits(
        _growth_snapshot(32, "ns::Outer", "ns::Box", "ns::Box", []),
        _growth_snapshot(64, "ns::Outer", "ns::Box", "ns::Box", []),
    )


def _same_leaf_growth() -> int | None:
    # other::Box (same leaf, unchanged size) must not stand in for ns::Box.
    sibling = [_record("other::Box")]
    sibling[0].size_bits = 128
    return _growth_bits(
        _growth_snapshot(32, "ns::Outer", "ns::Box", "ns::Box", sibling),
        _growth_snapshot(64, "ns::Outer", "ns::Box", "ns::Box", sibling),
    )


SITES: list[tuple[str, Callable[[], object], object]] = [
    ("diff_time64.typedef_chain_behind_colliding_alias", _colliding_alias_time64, 1),
    ("diff_time64.namespaced_record", _namespaced_record_time64, 1),
    ("diff_time64.same_leaf_record_does_not_ride_along", _same_leaf_record_time64, 0),
    (
        "export_surface.dangling_target_behind_colliding_alias",
        _export_surface_observation,
        (False, ("Missing::Gone",)),
    ),
    ("embedded_growth.namespaced_owner_and_member", _namespaced_growth, 32),
    ("embedded_growth.qualified_member_beside_same_leaf", _same_leaf_growth, 32),
]


@pytest.mark.parametrize(
    ("site", "observe", "expected"),
    SITES,
    ids=[s[0] for s in SITES],
)
def test_both_same_bare_name_entities_survive(
    site: str, observe: Callable[[], object], expected: object
) -> None:
    assert observe() == expected
