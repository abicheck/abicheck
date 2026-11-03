"""A function-local static's contract membership is its enclosing
function's (case180): ``local_entity_owner`` maps an Itanium ``<local-name>``
symbol to an enclosing-function symbol the snapshots actually know, and never
invents one."""

from __future__ import annotations

import pytest

from abicheck.model.owner_recovery import local_entity_owner

KNOWN = frozenset(
    {
        "_Z8registryv",
        "_ZN8RegistryI6WidgetE8instanceEv",
        "_ZN2ns3getIiEERT_v",
        "_ZN2ns3getEv",
    }
)


@pytest.mark.parametrize(
    ("sym", "owner"),
    [
        # local static of a plain inline function
        ("_ZZ8registryvE1w", "_Z8registryv"),
        # local static of a class-template member (case180)
        (
            "_ZZN8RegistryI6WidgetE8instanceEvE5value",
            "_ZN8RegistryI6WidgetE8instanceEv",
        ),
        # its guard variable
        (
            "_ZGVZN8RegistryI6WidgetE8instanceEvE5value",
            "_ZN8RegistryI6WidgetE8instanceEv",
        ),
        # discriminated second static (`_0`)
        ("_ZZ8registryvE1w_0", "_Z8registryv"),
        # an encoding containing inner E's: longest known candidate wins
        ("_ZZN2ns3getIiEERT_vE1x", "_ZN2ns3getIiEERT_v"),
        # the enclosing function is unknown: unchanged, nothing invented
        ("_ZZ7unknownvE1w", "_ZZ7unknownvE1w"),
        # not a local entity at all
        ("_Z8registryv", "_Z8registryv"),
        ("_ZN8RegistryI6WidgetE5valueE", "_ZN8RegistryI6WidgetE5valueE"),
        ("plain_c_symbol", "plain_c_symbol"),
    ],
)
def test_local_entity_owner(sym, owner):
    assert local_entity_owner(sym, KNOWN) == owner


def test_owner_is_always_known_or_identity():
    # Property over every prefix/suffix split of the known symbols: the
    # result is either the input or a member of the known set.
    for known in KNOWN:
        enc = known[2:]
        for name in ("1v", "5value", "1x_0"):
            for prefix in ("_ZZ", "_ZGVZ"):
                sym = f"{prefix}{enc}E{name}"
                got = local_entity_owner(sym, KNOWN)
                assert got == known
    assert local_entity_owner("_ZZ8registryvE1w", frozenset()) == "_ZZ8registryvE1w"
