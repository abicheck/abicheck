# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Metamorphic, property-based tests for the *detector* pipeline.

The existing property suite (``test_property_based.py``) fuzzes serialization
and policy bookkeeping; the symmetry suite (``test_bidirectional_symmetry.py``)
checks hand-written change pairs.  This module closes the gap the testing
review called out: the detectors themselves (``diff_symbols`` / ``diff_types``
/ ``diff_platform`` via :func:`abicheck.checker.compare`) were exercised almost
entirely with example-shaped inputs.

Example-shaped tests can only catch the cases the author imagined.  Metamorphic
properties instead assert relations that must hold for **any** input, so a
detector bug that only shows up on an un-imagined shape still trips a test.
Each property below is a generalization guard, not a coverage filler — it makes
a claim about behaviour, not about which lines run.

Properties asserted (for arbitrary generated snapshot pairs):

1. **Idempotence** — ``compare(s, s)`` is always ``NO_CHANGE`` with no changes.
2. **Determinism** — ``compare(a, b)`` twice yields the identical change list.
3. **Touched-symbol symmetry** — the set of symbols reported by
   ``compare(a, b)`` equals the set reported by ``compare(b, a)``.  This catches
   asymmetric detectors that see a change in one direction but miss its dual.
4. **Emitted-kind partition** — every ``ChangeKind`` actually emitted by the
   pipeline lives in exactly one policy set (the static partition test only
   covers the *enum*; this covers what detectors *produce*).
5. **Additive monotonicity** — adding a brand-new public symbol is never an ABI
   break; removing a public symbol is never ``NO_CHANGE``.
"""
from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from abicheck.checker import compare
from abicheck.checker_policy import (
    API_BREAK_KINDS,
    BREAKING_KINDS,
    COMPATIBLE_KINDS,
    RISK_KINDS,
    Verdict,
)
from abicheck.model import (
    AbiSnapshot,
    EnumMember,
    EnumType,
    Function,
    Param,
    RecordType,
    TypeField,
    Variable,
    Visibility,
)

pytestmark = pytest.mark.slow

_POLICY_SETS = (BREAKING_KINDS, API_BREAK_KINDS, COMPATIBLE_KINDS, RISK_KINDS)
_NON_BREAKING = {Verdict.NO_CHANGE, Verdict.COMPATIBLE, Verdict.COMPATIBLE_WITH_RISK}

# Identifiers restricted to ASCII letters/digits: real C/C++ symbol names, and
# enough to drive every detector without dragging in encoding edge cases that
# belong to the serialization suite.
_ident = st.text(
    min_size=1,
    max_size=8,
    alphabet=st.characters(whitelist_categories=("L", "N")),
)
_types = st.sampled_from(
    ["int", "void", "char*", "double", "long", "unsigned int", "float"]
)


@st.composite
def _param(draw: st.DrawFn) -> Param:
    return Param(name=draw(_ident), type=draw(_types))


@st.composite
def _function(draw: st.DrawFn) -> Function:
    name = draw(_ident)
    return Function(
        name=name,
        mangled=f"_Z{len(name)}{name}{draw(st.integers(0, 99))}",
        return_type=draw(_types),
        params=draw(st.lists(_param(), max_size=3)),
        visibility=draw(st.sampled_from(list(Visibility))),
        is_virtual=draw(st.booleans()),
        is_noexcept=draw(st.booleans()),
    )


@st.composite
def _variable(draw: st.DrawFn) -> Variable:
    name = draw(_ident)
    return Variable(
        name=name,
        mangled=f"_ZV{len(name)}{name}{draw(st.integers(0, 99))}",
        type=draw(_types),
        visibility=draw(st.sampled_from(list(Visibility))),
    )


@st.composite
def _field(draw: st.DrawFn) -> TypeField:
    return TypeField(name=draw(_ident), type=draw(_types))


@st.composite
def _record(draw: st.DrawFn) -> RecordType:
    return RecordType(
        name=draw(_ident),
        kind=draw(st.sampled_from(["struct", "class", "union"])),
        size_bits=draw(st.sampled_from([0, 32, 64, 128, 256])),
        fields=draw(st.lists(_field(), max_size=4)),
    )


@st.composite
def _enum(draw: st.DrawFn) -> EnumType:
    return EnumType(
        name=draw(_ident),
        members=draw(
            st.lists(
                st.builds(EnumMember, name=_ident, value=st.integers(-50, 50)),
                max_size=4,
            )
        ),
        underlying_type=draw(st.sampled_from(["int", "unsigned int", "long"])),
    )


@st.composite
def _snapshot(draw: st.DrawFn, version: str) -> AbiSnapshot:
    return AbiSnapshot(
        library="libprop.so.1",
        version=version,
        functions=draw(st.lists(_function(), max_size=5)),
        variables=draw(st.lists(_variable(), max_size=3)),
        types=draw(st.lists(_record(), max_size=3)),
        enums=draw(st.lists(_enum(), max_size=2)),
    )


# A pair strategy keeps both sides on the same library/structure space so the
# detectors do real diffing rather than "whole library replaced" noise.
_snapshot_pairs = st.tuples(_snapshot(version="1.0"), _snapshot(version="2.0"))

# compare() builds indices and walks every detector; keep example counts modest
# and silence the per-example timing health check (slow lane, runs in CI 3.13).
_HSETTINGS = settings(
    max_examples=75,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)


@given(snap=_snapshot(version="1.0"))
@_HSETTINGS
def test_compare_is_idempotent(snap: AbiSnapshot) -> None:
    """Comparing a snapshot against itself is always a clean NO_CHANGE.

    A non-empty change list here means a detector invents a difference where
    none exists — the canonical false-positive bug.
    """
    result = compare(snap, snap)
    assert result.verdict == Verdict.NO_CHANGE
    assert result.changes == []


@given(pair=_snapshot_pairs)
@_HSETTINGS
def test_compare_is_deterministic(pair: tuple[AbiSnapshot, AbiSnapshot]) -> None:
    """compare() is a pure function of its inputs: same inputs, same changes."""
    old, new = pair
    first = compare(old, new)
    second = compare(old, new)
    assert [c.kind for c in first.changes] == [c.kind for c in second.changes]
    assert first.verdict == second.verdict


@given(pair=_snapshot_pairs)
@_HSETTINGS
def test_touched_symbols_are_direction_symmetric(
    pair: tuple[AbiSnapshot, AbiSnapshot],
) -> None:
    """The *set of symbols* reported is the same forwards and backwards.

    A symbol added in v1->v2 is removed in v2->v1; a field/size/param change is
    visible in both directions.  The breaking-ness differs by direction, but
    *which* symbols are implicated must not — an asymmetry means a detector
    fires in only one direction.
    """
    old, new = pair
    forward = {c.symbol for c in compare(old, new).changes}
    backward = {c.symbol for c in compare(new, old).changes}
    assert forward == backward


@given(pair=_snapshot_pairs)
@_HSETTINGS
def test_emitted_kinds_are_partitioned(
    pair: tuple[AbiSnapshot, AbiSnapshot],
) -> None:
    """Every kind the pipeline actually emits is in exactly one policy set.

    The static partition test guards the enum; this guards the *output*, so a
    detector cannot emit a kind that the verdict logic would mis-classify.
    """
    old, new = pair
    for change in compare(old, new).changes:
        membership = sum(change.kind in s for s in _POLICY_SETS)
        assert membership == 1, (
            f"{change.kind.name} appears in {membership} policy sets (must be 1)"
        )


@given(base=_snapshot(version="1.0"), data=st.data())
@_HSETTINGS
def test_adding_public_symbol_is_never_an_abi_break(
    base: AbiSnapshot, data: st.DrawFn
) -> None:
    """Adding a brand-new public function is a compatible change, never a break.

    The new symbol carries a guaranteed-unique mangled name so it cannot clash
    with anything in *base*; introducing it must not produce API_BREAK/BREAKING.
    """
    tag = data.draw(st.integers(0, 10_000))
    new_fn = Function(
        name=f"abicheck_unique_added_{tag}",
        mangled=f"_Z30abicheck_unique_added_sym{tag}",
        return_type="int",
        visibility=Visibility.PUBLIC,
        is_extern_c=True,
    )
    extended = AbiSnapshot(
        library=base.library,
        version="2.0",
        functions=[*base.functions, new_fn],
        variables=list(base.variables),
        types=list(base.types),
        enums=list(base.enums),
    )
    verdict = compare(base, extended).verdict
    assert verdict in _NON_BREAKING, f"adding a public symbol gave {verdict}"


@given(base=_snapshot(version="1.0"), data=st.data())
@_HSETTINGS
def test_removing_public_symbol_is_never_no_change(
    base: AbiSnapshot, data: st.DrawFn
) -> None:
    """Removing an exported public function is always *some* reported change.

    The dual of the additive property: a public removal must never be silently
    classified as NO_CHANGE.  We add then remove a known public symbol so the
    property holds regardless of what *base* already contains.
    """
    tag = data.draw(st.integers(0, 10_000))
    removable = Function(
        name=f"abicheck_unique_removable_{tag}",
        mangled=f"_Z34abicheck_unique_removable_sym{tag}",
        return_type="int",
        visibility=Visibility.PUBLIC,
        is_extern_c=True,
    )
    with_sym = AbiSnapshot(
        library=base.library,
        version="1.0",
        functions=[*base.functions, removable],
        variables=list(base.variables),
        types=list(base.types),
        enums=list(base.enums),
    )
    without_sym = AbiSnapshot(
        library=base.library,
        version="2.0",
        functions=list(base.functions),
        variables=list(base.variables),
        types=list(base.types),
        enums=list(base.enums),
    )
    result = compare(with_sym, without_sym)
    assert result.verdict != Verdict.NO_CHANGE
    assert removable.mangled in {c.symbol for c in result.changes} or any(
        removable.name in (c.symbol or "") for c in result.changes
    )
