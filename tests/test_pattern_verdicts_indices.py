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

"""``pattern_verdicts``' per-comparison indices answer exactly what the
per-finding linear scans they replaced answered.

The scans (rebuild every type name, rescan for a unique short-name owner,
rescan every PIMPL tag) made ``--pattern-verdicts`` quadratic in the type
count; ``test_compare_call_complexity.py``'s ``type_churn`` workload is the
gate that caught it once the workload's type population scaled with ``n``.
These tests pin the other half -- that the replacement changes no answer --
against an *independent* oracle: the original linear-scan algorithm,
restated here verbatim rather than imported, over generated name universes
dense in exactly the collisions the rules exist for (one short name in
several namespaces, a duplicated record name, an exact pointee next to a
same-short-name one).
"""

from __future__ import annotations

from hypothesis import given, settings, strategies as st

from abicheck.idioms import Confidence, Idiom, IdiomTag
from abicheck.model import AbiSnapshot, RecordType
from abicheck.pattern_verdicts import (
    _exact_record,
    _pimpl_pointee_match,
    _PimplPointeeIndex,
    _TypeNameIndex,
)

# A deliberately small vocabulary, so collisions are the common case.
_names = st.builds(
    lambda ns, short: f"{ns}::{short}" if ns else short,
    st.sampled_from(["", "a", "b", "a::b"]),
    st.sampled_from(["Ctx", "Impl", "Handle"]),
)


def _snapshot(names: list[str]) -> AbiSnapshot:
    return AbiSnapshot(
        library="l",
        version="1",
        types=[
            RecordType(name=n, kind="struct", size_bits=8 * (i + 1))
            for i, n in enumerate(names)
        ],
    )


# --- oracle: the pre-index algorithm -----------------------------------------


def _oracle_resolve(keys: list[str], name: str) -> str | None:
    if name in keys:
        return name
    short = name.rsplit("::", 1)[-1]
    candidates = [k for k in keys if k.rsplit("::", 1)[-1] == short]
    return candidates[0] if len(candidates) == 1 else None


def _oracle_pimpl(pointee, old_idioms, new_idioms, new_names):
    short = pointee.rsplit("::", 1)[-1]
    unambiguous = sum(1 for n in new_names if n.rsplit("::", 1)[-1] == short) <= 1
    exact, short_matches = [], []
    for wrapper, tags in old_idioms.items():
        for t in tags:
            if t.idiom != Idiom.PIMPL or t.hidden_pointee is None:
                continue
            if t.hidden_pointee == pointee:
                exact.append((wrapper, t))
            elif t.hidden_pointee.rsplit("::", 1)[-1] == short:
                short_matches.append((wrapper, t))
    if exact:
        candidates = exact
    elif len(short_matches) == 1 and unambiguous:
        candidates = short_matches
    else:
        return None
    for wrapper, t in candidates:
        key = _oracle_resolve(list(new_names), wrapper)
        new_tag = (
            next((x for x in new_idioms.get(key, []) if x.idiom == Idiom.PIMPL), None)
            if key
            else None
        )
        if new_tag is None or t.layout_signature != new_tag.layout_signature:
            continue
        return [
            f"{pointee} is the hidden impl of PIMPL {wrapper}; wrapper layout byte-identical across versions"
        ]
    return None


# --- properties --------------------------------------------------------------


@settings(max_examples=300, deadline=None)
@given(universe=st.lists(_names, max_size=8), query=_names)
def test_name_resolution_matches_the_linear_scan(
    universe: list[str], query: str
) -> None:
    index = _TypeNameIndex(_snapshot(universe))
    keys = list(dict.fromkeys(universe))
    assert index.resolve(query) == _oracle_resolve(keys, query)
    short = query.rsplit("::", 1)[-1]
    assert index.short_name_count(short) == sum(
        1 for k in keys if k.rsplit("::", 1)[-1] == short
    )


@settings(max_examples=200, deadline=None)
@given(universe=st.lists(_names, min_size=1, max_size=8), query=_names)
def test_exact_record_is_the_first_declared_match(
    universe: list[str], query: str
) -> None:
    snap = _snapshot(universe)
    expected = next((r for r in snap.declarations.types if r.name == query), None)
    assert _exact_record(_TypeNameIndex(snap), query) is expected


_tag = st.builds(
    lambda idiom, pointee, layout: IdiomTag(
        idiom=idiom,
        confidence=Confidence.HIGH,
        layout_signature=layout,
        hidden_pointee=pointee,
    ),
    st.sampled_from([Idiom.PIMPL, Idiom.OPAQUE_POINTER]),
    st.one_of(st.none(), _names),
    st.sampled_from(["L1", "L2"]),
)
_idioms = st.dictionaries(_names, st.lists(_tag, max_size=3), max_size=5)


@settings(max_examples=400, deadline=None)
@given(
    old_idioms=_idioms,
    new_idioms=_idioms,
    new_universe=st.lists(_names, max_size=8),
    pointee=_names,
)
def test_pimpl_guard_matches_the_linear_scan(
    old_idioms, new_idioms, new_universe, pointee
) -> None:
    new_names = _TypeNameIndex(_snapshot(new_universe))
    got = _pimpl_pointee_match(
        pointee, _PimplPointeeIndex(old_idioms), new_idioms, new_names
    )
    assert got == _oracle_pimpl(
        pointee, old_idioms, new_idioms, list(dict.fromkeys(new_universe))
    )
