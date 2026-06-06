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

"""Property-based tests for the *detector* pipeline (`abicheck.checker.compare`).

This file has three layers, weakest-to-strongest:

1. **Structural metamorphic properties** (idempotence, determinism, emitted-kind
   partition) over Hypothesis-generated snapshots. Cheap; catch crashes,
   non-determinism, and mis-partitioned output.

2. **Controlled-mutation *oracle* properties** — the core of the file. Instead
   of diffing two independently-random snapshots (which share no symbols, so
   `compare` only ever sees whole-symbol add/remove and never exercises the
   *modification* detectors), we build a randomized but **shared** context, then
   apply one **known** edit to a target symbol/type. Because we know the edit we
   know the answer, so we assert:
     * the expected `ChangeKind` is emitted,
     * the verdict lands in the right severity class,
     * the untouched context produces **no** changes (a false-positive guard).
   This turns "the line ran" into "the line produced the right result" — the
   property mutation testing rewards.

3. **Grounding in real serialized snapshots** — the same metamorphic properties
   run against committed snapshot fixtures (`tests/fixtures/**`), so the
   invariants are checked on data shaped by the real dumper, not only synthetic
   models. (Real *binaries* are covered by
   ``test_detector_properties_integration.py``.)
"""
from __future__ import annotations

import glob
from pathlib import Path

import pytest
from _detector_mutations import (
    ASYMMETRIC,
    CTX_PREFIX,
    MUTATIONS,
    build_snapshot,
    context_identifiers,
)
from hypothesis import HealthCheck, assume, given, settings
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
from abicheck.serialization import load_snapshot

pytestmark = pytest.mark.slow

_POLICY_SETS = (BREAKING_KINDS, API_BREAK_KINDS, COMPATIBLE_KINDS, RISK_KINDS)
_BREAKING_VERDICTS = {Verdict.API_BREAK, Verdict.BREAKING}
_NON_BREAKING_VERDICTS = {
    Verdict.NO_CHANGE,
    Verdict.COMPATIBLE,
    Verdict.COMPATIBLE_WITH_RISK,
}

_HSETTINGS = settings(
    max_examples=75,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)


# ---------------------------------------------------------------------------
# ABI-shaped strategies (richer than a flat name/type pair)
# ---------------------------------------------------------------------------

_ident = st.text(
    min_size=1, max_size=8, alphabet=st.characters(whitelist_categories=("L", "N"))
)
_types = st.sampled_from(
    ["int", "void", "char*", "double", "long", "unsigned int", "float", "bool"]
)


@st.composite
def _function(draw: st.DrawFn) -> Function:
    name = draw(_ident)
    return Function(
        name=name,
        mangled=f"_Z{len(name)}{name}{draw(st.integers(0, 99))}",
        return_type=draw(_types),
        params=draw(st.lists(st.builds(Param, name=_ident, type=_types), max_size=3)),
        visibility=draw(st.sampled_from(list(Visibility))),
        is_virtual=draw(st.booleans()),
        is_noexcept=draw(st.booleans()),
    )


@st.composite
def _record(draw: st.DrawFn) -> RecordType:
    return RecordType(
        name=draw(_ident),
        kind=draw(st.sampled_from(["struct", "class", "union"])),
        size_bits=draw(st.sampled_from([0, 32, 64, 128, 256])),
        fields=draw(st.lists(st.builds(TypeField, name=_ident, type=_types), max_size=4)),
        vtable=draw(st.lists(_ident, max_size=3)),
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
        variables=draw(
            st.lists(
                st.builds(
                    Variable,
                    name=_ident,
                    mangled=_ident.map(lambda s: f"_ZV{s}"),
                    type=_types,
                    visibility=st.sampled_from(list(Visibility)),
                ),
                max_size=3,
            )
        ),
        types=draw(st.lists(_record(), max_size=3)),
        enums=draw(st.lists(_enum(), max_size=2)),
    )


_pairs = st.tuples(_snapshot(version="1.0"), _snapshot(version="2.0"))


# ---------------------------------------------------------------------------
# Layer 1 — structural metamorphic properties
# ---------------------------------------------------------------------------


@given(snap=_snapshot(version="1.0"))
@_HSETTINGS
def test_compare_is_idempotent(snap: AbiSnapshot) -> None:
    """Comparing a snapshot against itself is always a clean NO_CHANGE."""
    result = compare(snap, snap)
    assert result.verdict == Verdict.NO_CHANGE
    assert result.changes == []


@given(pair=_pairs)
@_HSETTINGS
def test_compare_is_deterministic(pair: tuple[AbiSnapshot, AbiSnapshot]) -> None:
    """compare() is a pure function of its inputs: same inputs, same changes."""
    old, new = pair
    first = compare(old, new)
    second = compare(old, new)
    assert [c.kind for c in first.changes] == [c.kind for c in second.changes]
    assert first.verdict == second.verdict


@given(pair=_pairs)
@_HSETTINGS
def test_emitted_kinds_are_partitioned(pair: tuple[AbiSnapshot, AbiSnapshot]) -> None:
    """Every kind the pipeline emits is in exactly one policy set."""
    old, new = pair
    for change in compare(old, new).changes:
        membership = sum(change.kind in s for s in _POLICY_SETS)
        assert membership == 1, (
            f"{change.kind.name} appears in {membership} policy sets (must be 1)"
        )


# ---------------------------------------------------------------------------
# Layer 2 — controlled-mutation oracle properties (randomized context)
# ---------------------------------------------------------------------------
#
# The mutation catalogue and its oracles live in ``_detector_mutations`` and are
# shared with the deterministic ``test_detector_oracle.py``. Here we wrap each
# mutation in a Hypothesis-randomized but *identical* context, so the oracle is
# checked to hold for arbitrary surroundings (not just one fixed context).


@st.composite
def _context(draw: st.DrawFn) -> dict:
    """A random set of unrelated public symbols, prefixed so they never collide
    with a mutation's target identifiers."""
    n = draw(st.integers(0, 3))
    funcs = [
        Function(name=f"{CTX_PREFIX}f{i}", mangled=f"_Z{CTX_PREFIX}f{i}v",
                 return_type=draw(_types), visibility=Visibility.PUBLIC)
        for i in range(n)
    ]
    types = [
        RecordType(name=f"{CTX_PREFIX}T{i}", kind="struct",
                   size_bits=draw(st.sampled_from([32, 64, 128])))
        for i in range(draw(st.integers(0, 2)))
    ]
    return {"functions": funcs, "types": types}


@given(context=_context(), idx=st.integers(0, len(MUTATIONS) - 1), tag=st.integers(0, 9999))
@_HSETTINGS
def test_known_mutation_yields_expected_kind_and_verdict(
    context: dict, idx: int, tag: int
) -> None:
    """For any surrounding context, a known edit produces its known ChangeKind
    and a verdict in the right severity class — and never disturbs the context."""
    old_extra, new_extra, expected_kind, is_breaking = MUTATIONS[idx](tag)
    old = build_snapshot("1.0", context, old_extra)
    new = build_snapshot("2.0", context, new_extra)

    result = compare(old, new)
    emitted = {c.kind for c in result.changes}

    assert expected_kind in emitted, (
        f"{MUTATIONS[idx].__name__}: expected {expected_kind.name}, got "
        f"{sorted(k.name for k in emitted)}"
    )
    if is_breaking:
        assert result.verdict in _BREAKING_VERDICTS, (
            f"{expected_kind.name} should be breaking, verdict was {result.verdict.name}"
        )
    else:
        assert result.verdict in _NON_BREAKING_VERDICTS, (
            f"{expected_kind.name} should be non-breaking, verdict was {result.verdict.name}"
        )

    # False-positive guard: the untouched, identical context must stay silent.
    offenders = {c.symbol for c in result.changes} & context_identifiers(context)
    assert not offenders, f"context symbols spuriously flagged: {offenders}"


@given(context=_context(), idx=st.integers(0, len(MUTATIONS) - 1), tag=st.integers(0, 9999))
@_HSETTINGS
def test_known_mutation_is_direction_symmetric(
    context: dict, idx: int, tag: int
) -> None:
    """A *modification* must surface in both directions (this is the real
    asymmetry guard — the independent-pair version mostly tests add/remove)."""
    assume(MUTATIONS[idx].__name__ not in ASYMMETRIC)
    old_extra, new_extra, _kind, _ = MUTATIONS[idx](tag)
    old = build_snapshot("1.0", context, old_extra)
    new = build_snapshot("2.0", context, new_extra)
    forward = {c.symbol for c in compare(old, new).changes}
    backward = {c.symbol for c in compare(new, old).changes}
    assert forward == backward


# ---------------------------------------------------------------------------
# Layer 3 — grounding in real serialized snapshots
# ---------------------------------------------------------------------------

_FIXTURE_ROOT = Path(__file__).parent / "fixtures"


def _loadable_fixtures() -> list[Path]:
    out: list[Path] = []
    for pat in ("action/*.json", "schema/*.json"):
        for p in sorted(glob.glob(str(_FIXTURE_ROOT / pat))):
            # A fixture that does not deserialize into a full snapshot schema
            # (bad JSON, missing/typed-wrong keys, unreadable file) is simply
            # not a candidate — skip it. Exception types are named explicitly
            # so an unexpected failure still surfaces instead of being swallowed.
            if _is_loadable_snapshot(Path(p)):
                out.append(Path(p))
    return out


def _is_loadable_snapshot(path: Path) -> bool:
    """Return True iff *path* deserializes into a full snapshot schema."""
    try:
        load_snapshot(path)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False
    return True


_REAL_SNAPSHOTS = _loadable_fixtures()


@pytest.mark.parametrize("path", _REAL_SNAPSHOTS, ids=lambda p: p.name)
def test_real_snapshot_self_compare_is_no_change(path: Path) -> None:
    """Idempotence on real serialized snapshots, not just synthetic models."""
    snap = load_snapshot(path)
    result = compare(snap, snap)
    assert result.verdict == Verdict.NO_CHANGE
    assert result.changes == []


@pytest.mark.parametrize("path", _REAL_SNAPSHOTS, ids=lambda p: p.name)
def test_real_snapshot_compare_is_deterministic(path: Path) -> None:
    snap = load_snapshot(path)
    other = load_snapshot(path)
    a = [c.kind for c in compare(snap, other).changes]
    b = [c.kind for c in compare(snap, other).changes]
    assert a == b


@pytest.mark.parametrize(
    "pair",
    [
        (a, b)
        for a in _REAL_SNAPSHOTS
        for b in _REAL_SNAPSHOTS
        if a != b and a.parent == b.parent
    ],
    ids=lambda pr: f"{pr[0].stem}->{pr[1].stem}",
)
def test_real_snapshot_touched_symbols_symmetric(pair: tuple[Path, Path]) -> None:
    """Direction-symmetry of touched symbols on real snapshot pairs."""
    a, b = (load_snapshot(p) for p in pair)
    forward = {c.symbol for c in compare(a, b).changes}
    backward = {c.symbol for c in compare(b, a).changes}
    assert forward == backward
