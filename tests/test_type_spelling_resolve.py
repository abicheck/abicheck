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

"""Direct cases for ``model.type_spelling_resolve.TypeResolver``.

The oracle is C++ name lookup as the standard states it, written out per case
(which declaration a spelling written in a given scope names), not the
resolver's own helpers. The detector-level matrix lives in
``test_detector_frontend_spelling.py``; these pin the individual resolution
steps that matrix reaches only indirectly.
"""

from __future__ import annotations

import pytest

from abicheck.model import RecordType
from abicheck.model.type_spelling_resolve import (
    TypeResolver,
    normalize_spelling,
    split_components,
)


def _rec(qualified: str) -> RecordType:
    leaf = split_components(qualified)[-1]
    return RecordType(name=leaf, kind="struct", qualified_name=qualified)


RECORDS = [
    "lib::detail::Impl",
    "lib::Impl",
    "other::detail::Impl",
    "lib::detail::Box<int>",
    "lib::detail::Box<lib::detail::Box<int>>",
    "lib::Unique",
    "Global",
]


@pytest.fixture
def resolver() -> TypeResolver:
    return TypeResolver([_rec(q) for q in RECORDS], extra_names=["lib::detail::Mode"])


@pytest.mark.parametrize(
    ("spelling", "scope", "expected"),
    [
        # Exact identity.
        ("lib::detail::Impl", None, "lib::detail::Impl"),
        # Relative to the writing scope: innermost enclosing scope wins.
        ("detail::Impl", "lib", "lib::detail::Impl"),
        ("detail::Impl", "other", "other::detail::Impl"),
        ("Impl", "lib::detail", "lib::detail::Impl"),
        ("Impl", "lib", "lib::Impl"),
        # A global-scope qualifier bypasses the enclosing scopes.
        ("::lib::Impl", "lib::detail", "lib::Impl"),
        # Template arguments are looked up from the same scope as the head.
        (
            "detail::Box<detail::Box<int> >",
            "lib",
            "lib::detail::Box<lib::detail::Box<int>>",
        ),
        ("Box<int>", "lib::detail", "lib::detail::Box<int>"),
        # A unique leaf names its only record from anywhere (DWARF, no scope).
        ("Unique", None, "lib::Unique"),
        (" Unique ", None, "lib::Unique"),
        ("Global", "lib", "Global"),
    ],
)
def test_resolves_by_scoped_lookup(
    resolver: TypeResolver, spelling: str, scope: str | None, expected: str
) -> None:
    assert resolver.resolve(spelling, scope) == expected


@pytest.mark.parametrize(
    ("spelling", "scope"),
    [
        # Two records share the leaf and no scope disambiguates: unproven.
        ("Impl", None),
        # Nothing named `nowhere::Impl` is visible from `lib`.
        ("nowhere::Impl", "lib"),
        # A non-record name stays as written.
        ("detail::Mode", "lib"),
        ("int", "lib"),
        ("", "lib"),
    ],
)
def test_unproven_spelling_is_returned_unchanged(
    resolver: TypeResolver, spelling: str, scope: str | None
) -> None:
    assert resolver.resolve(spelling, scope) == spelling


def test_a_global_qualifier_naming_nothing_known_stays_unresolved(
    resolver: TypeResolver,
) -> None:
    """`::detail::Impl` names a global-scope `detail`, which does not exist;
    `lib::detail::Impl` is not what it names, nor is a unique leaf's owner
    what `::Unique` names."""
    assert resolver.resolve("::detail::Impl", "lib") == "::detail::Impl"
    assert resolver.resolve("::Unique", None) == "::Unique"
    assert resolver.resolve("::Global", "lib") == "Global"


def test_recorded_identities_disambiguate_a_shared_leaf(
    resolver: TypeResolver,
) -> None:
    """castxml's bare `Impl` plus the slot identity names the record, even
    with no scope; an identity whose leaf differs is not taken."""
    assert (
        resolver.resolve("Impl", None, ("other::detail::Impl",))
        == "other::detail::Impl"
    )
    assert resolver.resolve("Impl", None, ("lib::Unique",)) == "Impl"


def test_extra_names_qualify_template_arguments_but_are_not_records() -> None:
    """An enum argument qualifies (so the instance key matches), yet the enum
    itself never resolves to a record key."""
    r = TypeResolver(
        [_rec("lib::Wrap<lib::detail::Mode>")], extra_names=["lib::detail::Mode"]
    )
    assert r.resolve("Wrap<detail::Mode>", "lib") == "lib::Wrap<lib::detail::Mode>"
    assert r.resolve("detail::Mode", "lib") == "detail::Mode"
    without = TypeResolver([_rec("lib::Wrap<lib::detail::Mode>")])
    assert without.resolve("Wrap<detail::Mode>", "lib") == "Wrap<detail::Mode>"


def test_answer_does_not_depend_on_query_order(resolver: TypeResolver) -> None:
    """The memo must key on scope: the same spelling from two scopes names
    two records, whichever is asked first."""
    fresh = TypeResolver([_rec(q) for q in RECORDS])
    assert resolver.resolve("detail::Impl", "other") == "other::detail::Impl"
    assert resolver.resolve("detail::Impl", "lib") == "lib::detail::Impl"
    assert fresh.resolve("detail::Impl", "lib") == "lib::detail::Impl"
    assert fresh.resolve("detail::Impl", "other") == "other::detail::Impl"
    # Asked again (served from the memo): the same answers.
    assert fresh.resolve("detail::Impl", "lib") == "lib::detail::Impl"
    assert fresh.resolve("detail::Impl", "other") == "other::detail::Impl"


@pytest.mark.parametrize(
    ("a", "b"),
    [("A< B ,C >", "A<B, C>"), ("x :: Y < int >", "x::Y<int>"), ("T *", "T*")],
)
def test_normalize_spelling_ignores_whitespace(a: str, b: str) -> None:
    assert normalize_spelling(a) == normalize_spelling(b)


@pytest.mark.parametrize(
    ("name", "parts"),
    [
        ("a::B<c::D>::E", ["a", "B<c::D>", "E"]),
        ("Plain", ["Plain"]),
        ("::lead", ["lead"]),
        ("X<Y<a::b>, c::d>", ["X<Y<a::b>, c::d>"]),
    ],
)
def test_split_components_is_template_aware(name: str, parts: list[str]) -> None:
    assert split_components(name) == parts
