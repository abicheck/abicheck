"""A typedef's ADR-015 header origin decides its surface membership.

Bug class: a change to a public typedef that no function uses was demoted
out of the surface (``no-provenance``) because snapshot typedefs carried no
declaring header. The invariant, over every origin a typedef can carry and
every typedef finding kind, on either or both sides:

* declared in a public header (on either side) -> in surface;
* confidently non-public on every side it exists -> out of surface, with a
  non-public reason;
* unknown on every side (DWARF, no public set, an older snapshot) -> kept,
  or demoted only as ``no-provenance``;
* unknown/export-only on one side -> never a confident private/system label.

The oracle is that rule stated over the origin vocabulary, independent of
``public_surface_closure``. The producer half (``apply_provenance``) and the
storage half (``abicheck.typedef_origins`` extension fact; an older document
without it loads as unknown) are checked too.
"""

from __future__ import annotations

import json

import pytest

from abicheck.checker_policy import ChangeKind
from abicheck.errors import SnapshotError
from abicheck.extract.declaration_provenance import apply_provenance
from abicheck.model import AbiSnapshot, Function, ScopeOrigin
from abicheck.model.change import Change
from abicheck.model.declaration_headers import attributed
from abicheck.policy.public_surface_closure import resolve_public_surface
from abicheck.serialization import snapshot_from_dict
from abicheck.storage.snapshot_encode import snapshot_to_json
from abicheck.surface import (
    REASON_NO_PROVENANCE,
    REASON_NON_PUBLIC_TYPE,
    REASON_PRIVATE_HEADER,
    REASON_SYSTEM_HEADER,
    classify_change_surface,
)

_NON_PUBLIC_REASONS = {
    REASON_PRIVATE_HEADER,
    REASON_SYSTEM_HEADER,
    REASON_NON_PUBLIC_TYPE,
}
_ORIGINS: list[ScopeOrigin | None] = [*ScopeOrigin, None]
_KINDS = [
    ChangeKind.TYPEDEF_REMOVED,
    ChangeKind.TYPEDEF_VERSION_SENTINEL,
    ChangeKind.TYPEDEF_BASE_CHANGED,
]


def _snap(origin: ScopeOrigin | None, *, has_typedef: bool = True) -> AbiSnapshot:
    snap = AbiSnapshot(
        library="l",
        version="1",
        functions=[
            Function(
                name="api",
                mangled="_Z3apiv",
                return_type="int",
                params=[],
                origin=ScopeOrigin.PUBLIC_HEADER,
            )
        ],
        typedefs={"foo_version_1_2_3": "int"} if has_typedef else {},
    )
    if has_typedef and origin is not None:
        snap.typedef_origins = {"foo_version_1_2_3": origin}
    return snap


def _expected(origins: list[ScopeOrigin | None]) -> str:
    """'in', 'out-nonpublic' or 'unknown' -- the rule in the module docstring."""
    if ScopeOrigin.PUBLIC_HEADER in origins:
        return "in"
    nonpublic = {
        ScopeOrigin.PRIVATE_HEADER,
        ScopeOrigin.SYSTEM_HEADER,
        ScopeOrigin.GENERATED,
    }
    if origins and all(o in nonpublic for o in origins):
        return "out-nonpublic"
    if all(o in (None, ScopeOrigin.UNKNOWN) for o in origins):
        return "unknown"
    return "mixed"


def _assert_rule(result: tuple[bool, str | None], expected: str) -> None:
    in_surface, reason = result
    if expected == "in":
        assert result == (True, None)
    elif expected == "out-nonpublic":
        assert not in_surface and reason in _NON_PUBLIC_REASONS, result
    elif expected == "unknown":
        assert in_surface or reason == REASON_NO_PROVENANCE, result
    else:
        # Some side is not confidently non-public: never a confident
        # private/system-header label.
        assert in_surface or reason in {
            REASON_NO_PROVENANCE,
            REASON_NON_PUBLIC_TYPE,
        }, result


@pytest.mark.parametrize("kind", _KINDS, ids=lambda k: k.value)
@pytest.mark.parametrize("old", _ORIGINS, ids=str)
@pytest.mark.parametrize("new", _ORIGINS, ids=str)
def test_typedef_change_follows_its_origin(
    kind: ChangeKind, old: ScopeOrigin | None, new: ScopeOrigin | None
) -> None:
    change = Change(kind=kind, symbol="foo_version_1_2_3", description="")
    surf_old = resolve_public_surface(_snap(old))
    surf_new = resolve_public_surface(_snap(new))
    _assert_rule(
        classify_change_surface(change, surf_old, surf_new), _expected([old, new])
    )


@pytest.mark.parametrize("old", _ORIGINS, ids=str)
def test_removed_unused_typedef_follows_old_origin(old: ScopeOrigin | None) -> None:
    """TYPEDEF_REMOVED: the typedef exists only on the old side."""
    change = Change(
        kind=ChangeKind.TYPEDEF_REMOVED, symbol="foo_version_1_2_3", description=""
    )
    surf_old = resolve_public_surface(_snap(old))
    surf_new = resolve_public_surface(_snap(None, has_typedef=False))
    _assert_rule(classify_change_surface(change, surf_old, surf_new), _expected([old]))


def test_public_removal_is_kept_and_private_one_is_not() -> None:
    """The two reported shapes, spelled out."""
    change = Change(
        kind=ChangeKind.TYPEDEF_REMOVED, symbol="foo_version_1_2_3", description=""
    )
    gone = resolve_public_surface(_snap(None, has_typedef=False))
    pub = resolve_public_surface(_snap(ScopeOrigin.PUBLIC_HEADER))
    priv = resolve_public_surface(_snap(ScopeOrigin.PRIVATE_HEADER))
    assert classify_change_surface(change, pub, gone) == (True, None)
    assert classify_change_surface(change, priv, gone)[0] is False


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        (("/src/include/api.h",), ScopeOrigin.PUBLIC_HEADER),
        (("/src/internal/impl.h",), ScopeOrigin.PRIVATE_HEADER),
        (("/usr/include/stdint.h",), ScopeOrigin.SYSTEM_HEADER),
        (("/src/internal/impl.h", "/src/include/api.h"), ScopeOrigin.PUBLIC_HEADER),
        ((), None),
    ],
)
def test_apply_provenance_classifies_typedef_headers(
    headers: tuple[str, ...], expected: ScopeOrigin | None
) -> None:
    snap = AbiSnapshot(library="l", version="1")
    entries = [("T", "int", h) for h in headers] or [("T", "int", "")]
    snap.declarations.typedefs = attributed(entries)
    # A multi-header sighting: attributed() keeps the last; restate all.
    if len(headers) > 1:
        snap.declarations.typedefs.declaring_headers["T"] = headers
    apply_provenance(snap, public_header_dirs=["/src/include"])
    assert (snap.typedef_origins or {}).get("T") == expected


def test_apply_provenance_leaves_unattributed_typedefs_unknown() -> None:
    """A DWARF-shaped plain map carries no header: nothing is classified."""
    snap = AbiSnapshot(library="l", version="1", typedefs={"T": "int"})
    apply_provenance(snap, public_header_dirs=["/src/include"])
    assert snap.typedef_origins is None


def test_apply_provenance_without_public_set_classifies_nothing() -> None:
    snap = AbiSnapshot(library="l", version="1")
    snap.declarations.typedefs = attributed([("T", "int", "/src/include/api.h")])
    apply_provenance(snap)
    assert snap.typedef_origins is None


def test_typedef_origins_round_trip() -> None:
    snap = _snap(ScopeOrigin.PUBLIC_HEADER)
    snap.typedef_origins = {
        "foo_version_1_2_3": ScopeOrigin.PUBLIC_HEADER,
        "ns::Priv": ScopeOrigin.PRIVATE_HEADER,
        "Unk": ScopeOrigin.UNKNOWN,
    }
    loaded = snapshot_from_dict(json.loads(snapshot_to_json(snap)))
    assert loaded.typedef_origins == {
        "foo_version_1_2_3": ScopeOrigin.PUBLIC_HEADER,
        "ns::Priv": ScopeOrigin.PRIVATE_HEADER,
    }


def _strip_typedef_origins(doc: dict) -> None:  # type: ignore[type-arg]
    for value in doc.values():
        if isinstance(value, dict):
            exts = value.get("extensions")
            if isinstance(exts, dict):
                exts.pop("abicheck.typedef_origins", None)
                if not exts:
                    value.pop("extensions")
            _strip_typedef_origins(value)


def test_older_snapshot_loads_with_unknown_typedef_origin() -> None:
    """A document written before the fact existed reads as unknown, and the
    unused typedef's change is then never labelled private."""
    doc = json.loads(snapshot_to_json(_snap(ScopeOrigin.PUBLIC_HEADER)))
    assert "abicheck.typedef_origins" in json.dumps(doc)
    _strip_typedef_origins(doc)
    assert "abicheck.typedef_origins" not in json.dumps(doc)
    loaded = snapshot_from_dict(doc)
    assert loaded.typedef_origins is None
    surf = resolve_public_surface(loaded)
    change = Change(
        kind=ChangeKind.TYPEDEF_VERSION_SENTINEL,
        symbol="foo_version_1_2_3",
        description="",
    )
    _assert_rule(classify_change_surface(change, surf, surf), "unknown")


def test_undecodable_typedef_origins_is_refused() -> None:
    doc = json.loads(snapshot_to_json(_snap(ScopeOrigin.PUBLIC_HEADER)))
    text = json.dumps(doc).replace('"public_header"}', '"bogus"}')
    assert '"bogus"' in text
    with pytest.raises(SnapshotError, match="typedef_origins"):
        snapshot_from_dict(json.loads(text))
