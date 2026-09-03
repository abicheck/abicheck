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

"""Primitive-level property tests for ``extract.headers.scope_segments``.

Per AGENTS.md's "Primitive-level property tests" convention: this is a new
reusable construction primitive shared by two producers, so its contract is
stated here directly as invariants over an enumerated input domain, not only
through either backend's own domain tests (those live in
``test_typed_scope_paths.py``).

The invariants that matter for ADR-063 Phase 2 are all *anti-collision*
ones — the whole point of a typed ``ScopePath`` is that two constructs a
flat ``"::"``-joined string renders identically must not produce equal
segments — plus the parity invariant that keeps this slice additive
(``flat_names`` reproduces exactly the flat spelling both backends already
build).
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.extract.headers.scope_segments import (
    ANONYMOUS_KINDS,
    ANONYMOUS_NAMESPACE,
    NO_ACCESS,
    RECORD_TAG_KINDS,
    anonymous_segment,
    flat_names,
    namespace_segment,
    record_segment,
    strip_record_scopes,
)
from abicheck.model.identity import (
    Anonymous,
    InlineNamespace,
    LocalToFunction,
    Namespace,
    Record,
    entity_id_for_type,
)

#: A small, deliberately collision-prone name domain: every name is used for
#: more than one construct kind below, so any invariant that passes here
#: passes because the *segment kind* discriminates, not the spelling.
_NAMES = ("A", "B", "ns", "v1", "__1")


class TestSegmentKindsNeverCollide:
    """Two constructs a flat spelling renders identically stay distinct."""

    @pytest.mark.parametrize("name", _NAMES)
    def test_namespace_and_record_of_one_name_differ(self, name: str) -> None:
        assert namespace_segment(name) != record_segment(name)
        # Both are hashable and stay two distinct dict/set keys -- the
        # property every ``ScopePath`` consumer actually depends on.
        assert len({namespace_segment(name), record_segment(name)}) == 2

    @pytest.mark.parametrize("name", _NAMES)
    def test_inline_and_ordinary_namespace_of_one_name_differ(self, name: str) -> None:
        assert namespace_segment(name, is_inline=True) != namespace_segment(name)

    @pytest.mark.parametrize("kind", sorted(ANONYMOUS_KINDS))
    def test_anonymous_siblings_of_one_kind_differ_by_ordinal(self, kind: str) -> None:
        assert anonymous_segment(kind, 0) != anonymous_segment(kind, 1)

    def test_anonymous_kinds_differ_at_one_ordinal(self) -> None:
        segments = {anonymous_segment(kind, 0) for kind in ANONYMOUS_KINDS}
        assert len(segments) == len(ANONYMOUS_KINDS)

    def test_every_pair_of_distinct_constructs_is_distinct(self) -> None:
        """Exhaustive over the whole small construct domain, not a sample."""
        built = []
        for name in _NAMES:
            built.append(namespace_segment(name))
            built.append(namespace_segment(name, is_inline=True))
            built.append(record_segment(name))
        for kind, ordinal in itertools.product(sorted(ANONYMOUS_KINDS), (0, 1)):
            built.append(anonymous_segment(kind, ordinal))
        assert len(set(built)) == len(built)


class TestIdentityVersusPayload:
    """The identity/payload split ``model.identity`` states, at this layer."""

    @pytest.mark.parametrize("access", ("public", "protected", "private", ""))
    def test_record_access_is_payload_not_identity(self, access: str) -> None:
        assert record_segment("A", access=access) == record_segment("A")

    def test_record_access_is_still_carried(self) -> None:
        assert record_segment("A", access="private").access == "private"

    def test_absent_access_becomes_the_namespace_scope_spelling(self) -> None:
        """One spelling for "no access specifier here", across both backends."""
        assert record_segment("A", access="").access == NO_ACCESS
        assert record_segment("A").access == NO_ACCESS

    @pytest.mark.parametrize("name", ("__1", "v2", "_V3", "abi", "inner"))
    def test_inline_version_tag_is_left_empty_by_this_slice(self, name: str) -> None:
        """Pins the deliberate scope boundary, so a later slice populating
        this field has to do so consciously: the one existing "what is a
        version tag" signal (``qualified_name_segments.version_suffix``)
        lives in the ``compare`` layer, which ``extract`` may not import
        under ADR-061 — see the constructor's own docstring."""
        segment = namespace_segment(name, is_inline=True)
        assert isinstance(segment, InlineNamespace)
        assert segment.version_tag == ""

    def test_version_shaped_inline_namespaces_are_still_distinct(self) -> None:
        """No discriminating power is lost by the empty tag: the name is
        identity too."""
        assert namespace_segment("v1", is_inline=True) != namespace_segment(
            "v2", is_inline=True
        )

    def test_version_tag_participates_in_identity(self) -> None:
        assert InlineNamespace("v1", "1") != InlineNamespace("v1", "2")


class TestAnonymousSegmentRejectsUnknownInput:
    """A producer's own mis-inspection must fail loudly, not become identity."""

    @pytest.mark.parametrize("kind", ("Struct", "STRUCT", "enum", "record", ""))
    def test_unknown_kind_raises(self, kind: str) -> None:
        with pytest.raises(ValueError, match="unknown anonymous scope kind"):
            anonymous_segment(kind, 0)

    def test_negative_ordinal_raises(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            anonymous_segment(ANONYMOUS_NAMESPACE, -1)

    def test_every_declared_kind_is_accepted(self) -> None:
        for kind in ANONYMOUS_KINDS:
            assert anonymous_segment(kind, 0).kind == kind

    def test_record_tag_kinds_are_the_lower_cased_cxx_tags(self) -> None:
        """Pins the exact spellings both backends must converge on: clang's
        own ``tagUsed`` values, which castxml's lower-cased element tags are
        mapped onto."""
        assert RECORD_TAG_KINDS == {"struct", "class", "union"}


class TestFlatNamesParity:
    """``flat_names`` is what keeps this slice provably additive."""

    def test_named_segments_render_in_order(self) -> None:
        path = (Namespace("ns"), InlineNamespace("v1", "1"), Record("A", "private"))
        assert flat_names(path) == ("ns", "v1", "A")

    def test_anonymous_segments_render_nothing(self) -> None:
        """Matching both backends' pre-existing flat spelling, which never
        emitted a name for an unnamed scope."""
        assert flat_names((Anonymous("namespace", 0),)) == ()
        assert flat_names((Namespace("ns"), Anonymous("union", 3))) == ("ns",)

    def test_local_to_function_renders_nothing(self) -> None:
        owner = entity_id_for_type((), "Owner")
        assert flat_names((LocalToFunction(owner, 0),)) == ()

    def test_empty_path_renders_empty(self) -> None:
        assert flat_names(()) == ()

    def test_two_paths_with_one_flat_spelling_are_still_distinct(self) -> None:
        """The exact motivating case: ``A::B`` as record-in-record vs. as
        record-in-namespace renders identically and must not be one path."""
        nested_record = (Record("A"),)
        nested_namespace = (Namespace("A"),)
        assert flat_names(nested_record) == flat_names(nested_namespace)
        assert nested_record != nested_namespace


class TestStripRecordScopes:
    """``strip_record_scopes`` -- the hidden-friend namespace-injection fix
    (Codex review, PR #943; see the function's own docstring for the
    compilation-confirmed motivating case)."""

    def test_record_segments_are_dropped(self) -> None:
        path = (Namespace("ns"), Record("A"), Record("B"))
        assert strip_record_scopes(path) == (Namespace("ns"),)

    def test_record_kind_anonymous_segments_are_dropped(self) -> None:
        for kind in RECORD_TAG_KINDS:
            assert strip_record_scopes((anonymous_segment(kind, 0),)) == ()

    def test_anonymous_namespace_segments_are_kept(self) -> None:
        path = (anonymous_segment(ANONYMOUS_NAMESPACE, 0), Record("A"))
        assert strip_record_scopes(path) == (Anonymous(ANONYMOUS_NAMESPACE, 0),)

    def test_namespace_and_inline_namespace_segments_are_kept(self) -> None:
        path = (Namespace("ns"), InlineNamespace("v1", "1"), Record("A"))
        assert strip_record_scopes(path) == (
            Namespace("ns"),
            InlineNamespace("v1", "1"),
        )

    def test_all_nested_record_scopes_are_stripped_not_just_innermost(self) -> None:
        """A friend hidden inside doubly-nested classes is injected past
        BOTH, not just the immediately enclosing one."""
        path = (Namespace("ns"), Record("Outer"), Record("Inner"))
        assert strip_record_scopes(path) == (Namespace("ns"),)

    def test_empty_path_is_unchanged(self) -> None:
        assert strip_record_scopes(()) == ()

    def test_path_with_no_record_scopes_is_unchanged(self) -> None:
        path = (Namespace("ns"), InlineNamespace("v1"))
        assert strip_record_scopes(path) == path
