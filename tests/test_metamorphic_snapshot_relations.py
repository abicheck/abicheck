# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Metamorphic relations of ``compare`` over the controlled-mutation catalogue.

Each relation holds between two in-process comparisons of snapshots built from
:mod:`tests._detector_mutations`, the same catalogue ``test_detector_oracle``
checks against its own expected kinds. No relation consults that expectation
beyond a sanity check; the oracle is the relation itself.

* **mirror** -- ``compare(new, old)`` reports the registry twin of every
  directional kind ``compare(old, new)`` reports (``*_removed`` <-> ``*_added``,
  paired by :func:`registry_twins`), and the non-directional kinds are the same
  set in both directions. A mutation whose reverse is legitimately silent is
  exempt by name (``ASYMMETRIC``), as the oracle already documents.
* **self-compare** -- a snapshot compared with an identical copy has no
  changes, for a live operand and for the same operand after a stored
  round trip.
"""

from __future__ import annotations

import json
import re

import pytest

from abicheck.checker import compare
from abicheck.model.change_catalog.assembled import REGISTRY
from abicheck.serialization import snapshot_from_dict, snapshot_to_json
from tests._detector_mutations import (
    ASYMMETRIC,
    MUTATIONS,
    build_snapshot,
)

#: Per mutation, the mirror pairs the registry names no twin for, each with the
#: reason it is not a name swap. Scoped to the one mutation that needs it, so it
#: cannot redirect an ordinary pair (``func_removed`` is always ``func_added``'s
#: twin). Both directions are implied. Everything else must be a name twin.
MIRROR_OVERRIDES: dict[str, dict[str, str]] = {
    # The introduction of an executable stack is ``executable_stack`` (modified
    # operation, binary entity); its reverse is the registered removal kind.
    "_m_executable_stack_introduced": {"executable_stack": "executable_stack_removed"},
    "_m_executable_stack_removed": {"executable_stack": "executable_stack_removed"},
    # A virtual method's removal is reported as the function's removal, the
    # registered function-entity kind; no ``virtual_method_removed`` exists.
    "_m_virtual_method_added": {"virtual_method_added": "func_removed"},
}


def _name_twins() -> dict[str, str]:
    """Each ``*_removed`` kind mapped to its ``*_added`` twin, both registered
    for the same entity, whatever their catalog operation (trait kinds such as
    ``func_noexcept_added`` are ``modified``, not ``added``)."""
    entries = {str(getattr(k, "value", k)): e for k, e in REGISTRY.entries.items()}
    twins: dict[str, str] = {}
    for name, entry in entries.items():
        if not re.search(r"(^|_)removed(?=_|$)", name):
            continue
        added = re.sub(
            r"(^|_)removed(?=_|$)", lambda m: m.group(1) + "added", name, count=1
        )
        other = entries.get(added)
        if other is not None and other.entity == entry.entity:
            twins[name] = added
    return twins


_NAME_PAIRS: dict[str, str] = {}
for _removed, _added in _name_twins().items():
    _NAME_PAIRS[_removed] = _added
    _NAME_PAIRS[_added] = _removed


def _kinds(result: object) -> set[str]:
    return {str(getattr(c.kind, "value", c.kind)) for c in result.changes}  # type: ignore[attr-defined]


def _sides(mutation: object) -> tuple[object, object]:
    old_extra, new_extra, _kind, _breaking = mutation(1)  # type: ignore[operator]
    return (
        build_snapshot("1", {}, old_extra),
        build_snapshot("2", {}, new_extra),
    )


def _pairs_for(mutation: object) -> dict[str, str]:
    """The mirror map for one mutation: its explicit overrides, then the name twins."""
    pairs = dict(_NAME_PAIRS)
    for a, b in MIRROR_OVERRIDES.get(mutation.__name__, {}).items():  # type: ignore[attr-defined]
        pairs[a] = b
        pairs[b] = a
    return pairs


@pytest.mark.parametrize("mutation", MUTATIONS, ids=lambda m: m.__name__)
def test_mirror_maps_directional_kinds_and_keeps_the_rest(mutation: object) -> None:
    old, new = _sides(mutation)
    forward = _kinds(compare(old, new))
    old2, new2 = _sides(mutation)
    backward = _kinds(compare(new2, old2))
    _old, _new, expected, _breaking = mutation(1)  # type: ignore[operator]
    assert str(getattr(expected, "value", expected)) in forward

    pairs = _pairs_for(mutation)
    directional_f = {k for k in forward if k in pairs}
    directional_b = {k for k in backward if k in pairs}
    if mutation.__name__ in ASYMMETRIC:  # type: ignore[attr-defined]
        # The reverse of these edits is a compatible improvement the detectors
        # deliberately do not report; only the forward direction is asserted.
        return

    for kind in directional_f:
        twin = pairs[kind]
        assert twin in backward, f"forward {kind} mirrors to {twin}, absent in reverse"
    for kind in directional_b:
        twin = pairs[kind]
        assert twin in forward, f"reverse {kind} mirrors to {twin}, absent forward"

    undirected_f = forward - directional_f
    undirected_b = backward - directional_b
    assert undirected_f == undirected_b, (
        f"non-directional kinds differ: forward-only "
        f"{sorted(undirected_f - undirected_b)}, reverse-only "
        f"{sorted(undirected_b - undirected_f)}"
    )


@pytest.mark.parametrize("mutation", MUTATIONS, ids=lambda m: m.__name__)
def test_self_compare_has_no_changes_live_or_stored(mutation: object) -> None:
    for index in (0, 1):
        operand = _sides(mutation)[index]
        # An independently built, identical operand (the live twin).
        twin = _sides(mutation)[index]
        assert compare(operand, operand).changes == []
        assert compare(operand, twin).changes == []
        # The stored form of the same operand, and a second stored round trip.
        stored = snapshot_from_dict(json.loads(snapshot_to_json(operand)))  # type: ignore[arg-type]
        assert compare(operand, stored).changes == []
        assert compare(stored, stored).changes == []
        again = snapshot_from_dict(json.loads(snapshot_to_json(stored)))  # type: ignore[arg-type]
        assert compare(stored, again).changes == []
