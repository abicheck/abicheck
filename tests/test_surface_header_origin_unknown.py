"""Public-surface seeding never reads an *unread* header origin as a
confirmed non-public one.

``policy/public_surface_closure.py`` seeds a header-declared enum/record
into the public surface from its ``source_header`` (and, for a record, its
``qualified_name``). When a producer stated it did not observe those facts
(``unsupported``/``failed``, or ``not_collected`` with a diagnostic), the
legacy strings are ``None``; the type used to be demoted as
``non-public-type`` and a real break vanished with no stated gap (F1
evidence-ablation finding). The invariant, over every unknown status and
every seed-relevant fact: a finding on such a type is either kept, or
demoted as ``header-origin-unknown`` -- which the run then states as a
scope note and coverage warning -- never as a confirmed exclusion.

The oracle is the documented seeding rule stated independently: a type
whose header facts are *present* is in the surface; a type with no fact
statement at all (the legacy ``None`` backfill) keeps its long-standing
``non-public-type`` reading.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from abicheck.checker_policy import ChangeKind
from abicheck.model import (
    AbiSnapshot,
    EnumMember,
    EnumType,
    Fact,
    Function,
    RecordType,
    ScopeOrigin,
    TypeField,
)
from abicheck.model.change import Change
from abicheck.policy.public_surface_closure import resolve_public_surface
from abicheck.surface import (
    REASON_HEADER_ORIGIN_UNKNOWN,
    REASON_NO_PROVENANCE,
    REASON_NON_PUBLIC_TYPE,
    SCOPE_NOTE_HEADER_ORIGIN_UNKNOWN,
    classify_change_surface,
    scope_note_coverage_warnings,
    surface_scope_confidence,
)

#: Every way a producer can *state* that it did not observe a fact.
_STATED_UNKNOWN: dict[str, Callable[[], Fact[Any]]] = {
    "not_collected_with_reason": lambda: Fact.not_collected("dwarf-only"),
    "not_collected_with_producer": lambda: Fact.not_collected(producer="castxml"),
    "unsupported": lambda: Fact.unsupported(),
    "unsupported_with_reason": lambda: Fact.unsupported("pdb"),
    "failed": lambda: Fact.failed("parse error"),
}


def _api_fn() -> Function:
    return Function(
        name="api",
        mangled="_Z3apiv",
        return_type="void",
        params=[],
        origin=ScopeOrigin.PUBLIC_HEADER,
    )


def _snap(
    *,
    types: tuple[RecordType, ...] | list[RecordType] = (),
    enums: tuple[EnumType, ...] | list[EnumType] = (),
) -> AbiSnapshot:
    return AbiSnapshot(
        library="l",
        version="1",
        functions=[_api_fn()],
        types=list(types),
        enums=list(enums),
    )


def _enum(**kw: Any) -> EnumType:
    return EnumType(
        name="Color",
        members=[EnumMember("RED", 0)],
        qualified_name="Color",
        **kw,
    )


def _record(**kw: Any) -> RecordType:
    kw.setdefault("qualified_name", "ns::Handle")
    return RecordType(
        name="Handle",
        kind="struct",
        size_bits=64,
        origin=ScopeOrigin.PUBLIC_HEADER,
        **kw,
    )


def _enum_change() -> Change:
    return Change(
        kind=ChangeKind.ENUM_MEMBER_VALUE_CHANGED, symbol="Color::RED", description=""
    )


def _record_change() -> Change:
    return Change(kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="Handle", description="")


#: (label, snapshot builder taking the unknown fact, change) -- one per
#: seed-relevant fact site.
_SITES: list[tuple[str, Callable[[Fact[Any]], AbiSnapshot], Callable[[], Change]]] = [
    (
        "enum.source_header",
        lambda f: _snap(enums=[_enum(source_header=None, source_header_fact=f)]),
        _enum_change,
    ),
    (
        "record.source_header",
        lambda f: _snap(types=[_record(source_header=None, source_header_fact=f)]),
        _record_change,
    ),
    (
        "record.qualified_name",
        lambda f: _snap(
            types=[
                _record(
                    source_header="/inc/api.h",
                    qualified_name=None,
                    qualified_name_fact=f,
                )
            ]
        ),
        _record_change,
    ),
]


@pytest.mark.parametrize("status", sorted(_STATED_UNKNOWN))
@pytest.mark.parametrize(
    ("site", "build", "change"), _SITES, ids=[s[0] for s in _SITES]
)
def test_unknown_header_origin_is_never_a_confirmed_exclusion(
    site: str,
    build: Callable[..., AbiSnapshot],
    change: Callable[[], Change],
    status: str,
) -> None:
    surf = resolve_public_surface(build(_STATED_UNKNOWN[status]()))
    in_surface, reason = classify_change_surface(change(), surf, surf)
    assert in_surface or reason == REASON_HEADER_ORIGIN_UNKNOWN, (site, status, reason)


@pytest.mark.parametrize("status", sorted(_STATED_UNKNOWN))
@pytest.mark.parametrize("side", ["old", "new"])
def test_one_sided_unknown_still_labels_or_keeps(status: str, side: str) -> None:
    unknown = resolve_public_surface(
        _snap(
            enums=[
                _enum(source_header=None, source_header_fact=_STATED_UNKNOWN[status]())
            ]
        )
    )
    known = resolve_public_surface(_snap(enums=[_enum(source_header="/inc/x.h")]))
    old, new = (unknown, known) if side == "old" else (known, unknown)
    in_surface, reason = classify_change_surface(_enum_change(), old, new)
    assert in_surface or reason == REASON_HEADER_ORIGIN_UNKNOWN


@pytest.mark.parametrize(
    ("site", "build", "change"), _SITES, ids=[s[0] for s in _SITES]
)
def test_present_header_facts_seed_the_type(
    site: str, build: Callable[..., AbiSnapshot], change: Callable[[], Change]
) -> None:
    # Oracle: with every header fact present the type is public on its own.
    if site.startswith("enum"):
        snap = _snap(enums=[_enum(source_header="/inc/x.h")])
    else:
        snap = _snap(types=[_record(source_header="/inc/api.h")])
    surf = resolve_public_surface(snap)
    assert classify_change_surface(change(), surf, surf) == (True, None)
    assert not surf.header_origin_unknown_types


@pytest.mark.parametrize(
    "snap",
    [
        _snap(enums=[_enum()]),
        _snap(types=[_record()]),
        _snap(types=[_record(source_header="/inc/api.h", qualified_name=None)]),
    ],
    ids=["enum", "record-no-header", "record-no-qname"],
)
def test_no_fact_statement_keeps_the_legacy_non_public_reading(
    snap: AbiSnapshot,
) -> None:
    # A legacy ``None`` with no fact statement backfills to a bare
    # not_collected -- the established "no header recorded" spelling.
    surf = resolve_public_surface(snap)
    assert not surf.header_origin_unknown_types
    change = _enum_change() if snap.declarations.enums else _record_change()
    assert classify_change_surface(change, surf, surf) in {
        (False, REASON_NON_PUBLIC_TYPE),
        (False, REASON_NO_PROVENANCE),
    }


@pytest.mark.parametrize(
    "origin", [ScopeOrigin.PRIVATE_HEADER, ScopeOrigin.SYSTEM_HEADER]
)
def test_a_demoting_origin_is_not_relabelled(origin: ScopeOrigin) -> None:
    snap = _snap(
        enums=[
            _enum(
                source_header=None, source_header_fact=Fact.failed("x"), origin=origin
            )
        ]
    )
    assert not resolve_public_surface(snap).header_origin_unknown_types


def test_the_demotion_is_stated_as_scope_note_and_coverage_warning() -> None:
    snap = _snap(enums=[_enum(source_header=None, source_header_fact=Fact.failed("x"))])
    surf = resolve_public_surface(snap)
    c = _enum_change()
    c.surface_exclusion_reason = classify_change_surface(c, surf, surf)[1]
    confidence, notes = surface_scope_confidence(
        snap, snap, scope_enabled=True, surf_old=surf, surf_new=surf, demoted=[c]
    )
    assert SCOPE_NOTE_HEADER_ORIGIN_UNKNOWN in notes
    assert confidence == "reduced"
    assert scope_note_coverage_warnings(notes)
    # and nothing is stated when no demotion rested on it
    _, clean = surface_scope_confidence(
        snap, snap, scope_enabled=True, surf_old=surf, surf_new=surf, demoted=[]
    )
    assert SCOPE_NOTE_HEADER_ORIGIN_UNKNOWN not in clean
    assert scope_note_coverage_warnings(clean) == []


def _nested_pair(outer_header_fact: Fact[Any] | None) -> AbiSnapshot:
    """``ns::Handle`` (the seed) holds a pointer to ``ns::Handle::Impl``,
    whose only route into the surface is through that seed: nested in a
    known record, it is never seeded on its own header origin."""
    outer = _record(
        source_header=None if outer_header_fact is not None else "/inc/api.h",
        source_header_fact=outer_header_fact,
        fields=[TypeField(name="impl", type="ns::Handle::Impl *", offset_bits=0)],
    )
    inner = RecordType(
        name="ns::Handle::Impl",
        kind="struct",
        size_bits=32,
        qualified_name="ns::Handle::Impl",
        source_header="/inc/api.h",
        origin=ScopeOrigin.PUBLIC_HEADER,
    )
    return _snap(types=[outer, inner])


def _inner_change() -> Change:
    return Change(
        kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="ns::Handle::Impl", description=""
    )


@pytest.mark.parametrize("status", sorted(_STATED_UNKNOWN))
def test_unknown_origin_reaches_types_only_the_blocked_seed_reaches(
    status: str,
) -> None:
    """A type reachable only through a blocked seed inherits its undecided
    state: its finding is kept or labelled, never a quiet exclusion."""
    surf = resolve_public_surface(_nested_pair(_STATED_UNKNOWN[status]()))
    in_surface, reason = classify_change_surface(_inner_change(), surf, surf)
    assert in_surface or reason == REASON_HEADER_ORIGIN_UNKNOWN, (status, reason)


def test_nested_type_of_a_read_seed_is_public_and_not_relabelled() -> None:
    """Controls: with the seed's origin read, the nested type is simply in
    the surface, and nothing is marked unknown."""
    surf = resolve_public_surface(_nested_pair(None))
    assert classify_change_surface(_inner_change(), surf, surf) == (True, None)
    assert not surf.header_origin_unknown_types


def test_unreached_type_keeps_its_confirmed_exclusion() -> None:
    """The extension follows the closure only: a record no blocked seed
    reaches keeps ``non-public-type``."""
    snap = _nested_pair(Fact.failed("parse error"))
    snap.declarations.types.append(
        RecordType(
            name="detail::Other",
            kind="struct",
            size_bits=32,
            qualified_name="detail::Other",
        )
    )
    surf = resolve_public_surface(snap)
    change = Change(
        kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="detail::Other", description=""
    )
    assert "detail::Other" not in surf.header_origin_unknown_types
    assert (
        classify_change_surface(change, surf, surf)[1] != REASON_HEADER_ORIGIN_UNKNOWN
    )
