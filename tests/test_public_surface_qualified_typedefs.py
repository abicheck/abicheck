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

"""Public-surface closure follows typedefs through ``typedefs_qualified``.

``AbiSnapshot.declarations.typedefs`` is keyed by the bare alias name, so two
classes that each declare a member ``value_type`` collide on one key and only
one target survives. The closure used to look typedefs up by that bare key
alone, so the other class's target -- and anything a public signature reached
through it -- silently dropped out of the public surface. The schema-v25 twin
``typedefs_qualified`` keeps both. These tests pin the resolution rule and the
exact-identity rule it feeds, and the end-to-end verdict that the rule
protects.

Oracle: the expected public / exact sets are spelled out by hand from the
declarations each test builds, never derived from the closure's own helpers.
"""

from __future__ import annotations

from abicheck.checker import Verdict, compare
from abicheck.model import (
    AbiSnapshot,
    Function,
    Param,
    RecordType,
    TypeField,
)
from abicheck.policy.public_surface_closure import resolve_public_surface


def _record(qualified: str, field_type: str = "int") -> RecordType:
    return RecordType(
        name=qualified.rsplit("::", 1)[-1],
        qualified_name=qualified,
        kind="struct",
        fields=[TypeField(name="x", type=field_type, offset_bits=0)],
    )


def _returning(return_type: str) -> Function:
    return Function(name="get", mangled="get", return_type=return_type)


def _taking(param_type: str) -> Function:
    return Function(
        name="take",
        mangled="take",
        return_type="void",
        params=[Param(name="v", type=param_type)],
    )


def _colliding_snapshot(function: Function) -> AbiSnapshot:
    """Two classes, each with its own ``value_type`` alias; the bare dict
    keeps only ``Bar``'s target, the way a real producer's last-wins collapse
    does."""
    snap = AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        functions=[function],
        types=[_record("Foo::Inner"), _record("Bar::Other")],
    )
    snap.declarations.typedefs = {"value_type": "Bar::Other"}
    snap.declarations.typedefs_qualified = {
        "Foo::value_type": "Foo::Inner",
        "Bar::value_type": "Bar::Other",
    }
    return snap


class TestUniqueQualifiedMatchIsFollowed:
    """A bare spelling whose tail names exactly one qualified typedef reaches
    that typedef's target even when the lossy bare dict does not carry it."""

    def test_bare_spelling_follows_its_only_qualified_twin(self) -> None:
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[_taking("value_type")],
            types=[_record("Foo::Inner")],
        )
        snap.declarations.typedefs = {}  # bare dict lost the alias entirely
        snap.declarations.typedefs_qualified = {"Foo::value_type": "Foo::Inner"}

        surf = resolve_public_surface(snap)

        assert "Foo::value_type" in surf.public_typedefs
        # Unique tail: the exact walk may vouch for it.
        assert {"value_type", "Foo::Inner"} <= surf.exact_type_identities


class TestAmbiguousTailFollowsEveryMatch:
    """Two classes sharing a bare ``value_type`` tail: a bare spelling must
    keep both targets public (anti-hiding), but may not claim either as an
    exact identity, since the spelling cannot say which one it means."""

    def test_both_aliases_followed_and_no_exact_identity(self) -> None:
        surf = resolve_public_surface(_colliding_snapshot(_taking("value_type")))

        assert {"Foo::value_type", "Bar::value_type"} <= surf.public_typedefs
        # Ambiguous fork: nothing reached through it is exact.
        assert "value_type" not in surf.exact_type_identities
        assert "Foo::Inner" not in surf.exact_type_identities
        assert "Bar::Other" not in surf.exact_type_identities

    def test_each_colliding_alias_is_walked_for_its_own_dependencies(self) -> None:
        # ``Foo::value_type`` names ``Foo::Inner``, whose target is itself a
        # typedef chain. Reaching ``Foo::Inner``'s chain proves the walk
        # followed the colliding alias, not just the bare dict's survivor.
        snap = _colliding_snapshot(_taking("value_type"))
        snap.declarations.typedefs_qualified["Foo::value_type"] = "Foo::Alias"
        snap.declarations.typedefs_qualified["Foo::Alias"] = "Baz::Deep"

        surf = resolve_public_surface(snap)

        assert "Foo::Alias" in surf.public_typedefs


class TestAlreadyQualifiedSpellingIsExact:
    """A qualified spelling resolves only by exact key, and a unique one is
    exact -- the tail rule applies only to bare spellings."""

    def test_exact_qualified_spelling_is_followed_and_exact(self) -> None:
        surf = resolve_public_surface(
            _colliding_snapshot(_returning("Foo::value_type"))
        )

        assert "Foo::value_type" in surf.public_typedefs
        assert {"Foo::value_type", "Foo::Inner"} <= surf.exact_type_identities
        # The sibling alias is reached only by the bare-tail candidate the
        # identifier tokenizer emits (anti-hiding, as for records), never as
        # an exact identity.
        assert "Bar::Other" not in surf.exact_type_identities


class TestPreV25SnapshotUnchanged:
    """A snapshot without ``typedefs_qualified`` resolves through the bare
    dict exactly as before this change -- the same set, the same exactness."""

    def test_bare_dict_resolution_and_exactness_unchanged(self) -> None:
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[_taking("value_type")],
            types=[_record("Inner")],
        )
        snap.declarations.typedefs = {"value_type": "Inner"}
        assert snap.declarations.typedefs_qualified == {}

        surf = resolve_public_surface(snap)

        assert "value_type" in surf.public_typedefs
        assert {"value_type", "Inner"} <= surf.exact_type_identities


class TestRealBreakBehindCollidingTypedefIsNotHidden:
    """End to end through ``compare()``: a field-type change in the class that
    only the colliding ``Foo::value_type`` alias reaches must still be a
    BREAKING finding. Before the closure followed qualified typedefs, the
    bare dict's surviving ``Bar`` target hid it and the run read NO_CHANGE."""

    @staticmethod
    def _snap(inner_field_type: str) -> AbiSnapshot:
        snap = AbiSnapshot(
            library="libfoo.so",
            version="1.0",
            functions=[_taking("value_type")],
            types=[
                _record("Foo::Inner", field_type=inner_field_type),
                _record("Bar::Other"),
            ],
        )
        snap.declarations.typedefs = {"value_type": "Bar::Other"}
        snap.declarations.typedefs_qualified = {
            "Foo::value_type": "Foo::Inner",
            "Bar::value_type": "Bar::Other",
        }
        return snap

    def test_field_type_change_behind_colliding_alias_is_breaking(self) -> None:
        result = compare(self._snap("int"), self._snap("long"))

        assert result.verdict is Verdict.BREAKING
        assert any(
            c.kind.value == "type_field_type_changed" and c.symbol == "Inner"
            for c in result.changes
        )
