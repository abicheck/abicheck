"""A type finding whose name collides with a hidden constructor is judged by
type reachability, for every type/enum kind in the catalog.

At ``-O2`` gcc inlines ``KSD::KSD`` and exports nothing for it, yet DWARF keeps
the constructor under the bare name ``KSD`` -- the same spelling a type
finding carries in ``Change.symbol``. ``secondary_vtable_group_changed`` was
not in the hand-kept ``TYPE_LEVEL_KIND_NAMES``, so the symbol-level path
matched the hidden constructor and demoted a reachable class's break as
``not-exported``. The oracle here is independent of the classifier: ``KSD``
is reachable from an exported function's return type, ``Hidden`` from none.
"""

from __future__ import annotations

import pytest

from abicheck.checker_policy import ChangeKind
from abicheck.model import AbiSnapshot, Function, RecordType, Visibility
from abicheck.model.change import Change
from abicheck.model.change_catalog.assembled import REGISTRY
from abicheck.policy.public_surface_closure import resolve_public_surface
from abicheck.surface import REASON_NON_PUBLIC_TYPE, classify_change_surface

_TYPE_KINDS = sorted(
    k for k in REGISTRY._entries if str(REGISTRY.entity_for(k)) in ("type", "enum")
)


def _surface() -> object:
    snap = AbiSnapshot(
        library="libedit.so",
        version="1",
        functions=[
            Function(
                name="ksd_make",
                mangled="_Z8ksd_makev",
                return_type="KSD *",
                visibility=Visibility.PUBLIC,
            ),
            # Inlined constructors: in DWARF, never exported.
            Function(
                name="KSD",
                mangled="_ZN3KSDC2Ev",
                return_type="void",
                visibility=Visibility.HIDDEN,
            ),
            Function(
                name="Hidden",
                mangled="_ZN6HiddenC2Ev",
                return_type="void",
                visibility=Visibility.HIDDEN,
            ),
        ],
        types=[
            RecordType(name="KSD", kind="struct", size_bits=128),
            RecordType(name="Hidden", kind="struct", size_bits=64),
        ],
    )
    return resolve_public_surface(snap)


@pytest.mark.parametrize("kind", _TYPE_KINDS)
def test_a_reachable_type_is_not_demoted_by_its_hidden_constructor(kind: str) -> None:
    s = _surface()
    change = Change(kind=ChangeKind(kind), symbol="KSD", description="")
    assert classify_change_surface(change, s, s) == (True, None)


@pytest.mark.parametrize(
    "kind", ["secondary_vtable_group_changed", "vtable_slot_count_changed"]
)
def test_an_unreachable_type_still_demotes_as_a_non_public_type(kind: str) -> None:
    s = _surface()
    change = Change(kind=ChangeKind(kind), symbol="Hidden", description="")
    assert classify_change_surface(change, s, s) == (False, REASON_NON_PUBLIC_TYPE)
