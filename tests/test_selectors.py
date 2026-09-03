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

"""``policy.selectors.SelectorSet`` (ADR-063 D10, implementation plan
Phase 9) -- the shared selector-matching primitive behind
``suppression.Suppression`` and ``reclassify.ReclassifyRule``.

These tests exercise the leaf module directly, against a minimal stand-in
that satisfies :class:`~abicheck.policy.selectors.SelectorMatchable`
structurally, without constructing a real
:class:`~abicheck.checker_types.Change` -- the point of this leaf module is
that it has zero dependency on ``checker_types.py``, so its own tests
shouldn't need one either. The full, real-``Change`` behavior stays covered
by ``test_suppression*.py``/``test_reclassify.py``/``test_frozen_namespace.py``,
which this phase's own acceptance criteria requires to keep passing
unchanged (selector-matching *behavior* must not change, only where the
grammar lives).
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from abicheck.checker_policy import ChangeKind
from abicheck.policy.selectors import SelectorSet


class _FakeChange:
    """Minimal :class:`~abicheck.policy.selectors.SelectorMatchable`."""

    def __init__(
        self,
        symbol: str,
        *,
        kind: ChangeKind = ChangeKind.FUNC_REMOVED,
        qualified_name: str | None = None,
        caused_by_type: str | None = None,
        source_location: str | None = None,
        symbol_binding: str | None = None,
    ) -> None:
        self.symbol = symbol
        self.kind = kind
        self.qualified_name = qualified_name
        self.caused_by_type = caused_by_type
        self.source_location = source_location
        self.symbol_binding = symbol_binding


class TestSelectorSetValidation:
    def test_empty_selector_set_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="at least one of"):
            SelectorSet()

    def test_namespace_and_entity_namespace_are_exclusive_aliases(self) -> None:
        with pytest.raises(ValueError, match="aliases"):
            SelectorSet(namespace="a::*", entity_namespace="b::*")

    def test_symbol_and_symbol_pattern_are_mutually_exclusive(self) -> None:
        with pytest.raises(ValueError, match="mutually exclusive"):
            SelectorSet(symbol="foo", symbol_pattern="foo.*")

    def test_member_name_cannot_combine_with_symbol(self) -> None:
        with pytest.raises(ValueError, match="member_name"):
            SelectorSet(symbol="foo", member_name="bar")

    def test_unknown_change_kind_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="Unknown change_kind"):
            SelectorSet(symbol="foo", change_kind="not_a_real_kind")

    def test_malformed_symbol_pattern_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="symbol_pattern"):
            SelectorSet(symbol_pattern="(unclosed")

    def test_invalid_binding_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="Invalid binding"):
            SelectorSet(symbol="foo", binding="bogus")

    def test_non_string_binding_is_rejected_not_raising_typeerror(self) -> None:
        with pytest.raises(ValueError, match="Invalid binding"):
            SelectorSet(symbol="foo", binding=["weak"])  # type: ignore[arg-type]

    def test_finding_id_alone_is_a_valid_selector(self) -> None:
        s = SelectorSet(finding_id="abc123")
        assert s.finding_id == "abc123"

    def test_binding_alone_is_still_rejected(self) -> None:
        """P2 (CLI-audit): `binding` stays conjunctive-only by design (see
        Suppression.binding's own docstring) -- a binding-only rule would
        suppress every change with that ELF linkage across the whole
        comparison, with nothing else scoping it. This is not the bug;
        the confusing generic error message naming every selector *except*
        binding was."""
        with pytest.raises(ValueError, match="conjunctive-only"):
            SelectorSet(binding="weak")

    def test_binding_alone_gets_a_dedicated_actionable_message(self) -> None:
        """The message must name `binding` explicitly and point at both
        intentional workarounds -- the generic "at least one of" list
        (which doesn't even mention binding) reads as though the field
        were unrecognized rather than deliberately excluded."""
        with pytest.raises(ValueError, match="binding") as exc_info:
            SelectorSet(binding="weak")
        message = str(exc_info.value)
        assert "conjunctive-only" in message
        assert "symbol_pattern" in message
        assert "namespace" in message

    def test_binding_with_symbol_pattern_wildcard_is_accepted(self) -> None:
        """The documented workaround this message points to must actually
        work (regression guard, not just an error-message claim)."""
        s = SelectorSet(binding="weak", symbol_pattern=".*")
        assert s.binding == "weak"

    def test_binding_with_a_real_narrowing_selector_is_accepted(self) -> None:
        s = SelectorSet(binding="weak", namespace="my::ns::*")
        assert s.binding == "weak"

    def test_datetime_expires_is_normalized_to_date(self) -> None:
        s = SelectorSet(symbol="foo", expires=datetime(2026, 1, 1, 12, 0))
        assert s.expires == date(2026, 1, 1)


class TestSelectorSetMatching:
    def test_symbol_exact_match(self) -> None:
        s = SelectorSet(symbol="foo")
        assert s.matches_selectors(_FakeChange("foo"))
        assert not s.matches_selectors(_FakeChange("bar"))

    def test_symbol_pattern_fullmatch(self) -> None:
        s = SelectorSet(symbol_pattern="ns::.*")
        assert s.matches_selectors(_FakeChange("ns::foo"))
        assert not s.matches_selectors(_FakeChange("other::foo"))

    def test_change_kind_is_conjunctive(self) -> None:
        s = SelectorSet(symbol="foo", change_kind=ChangeKind.FUNC_REMOVED.value)
        assert s.matches_selectors(_FakeChange("foo", kind=ChangeKind.FUNC_REMOVED))
        assert not s.matches_selectors(_FakeChange("foo", kind=ChangeKind.VAR_REMOVED))

    def test_member_name_matches_trailing_segment_regardless_of_container(self) -> None:
        s = SelectorSet(member_name="value_type")
        assert s.matches_selectors(_FakeChange("ns::Alloc::value_type"))
        assert s.matches_selectors(_FakeChange("other::Container::value_type"))
        assert not s.matches_selectors(_FakeChange("ns::Alloc::other_type"))

    def test_source_location_glob(self) -> None:
        s = SelectorSet(source_location="*/internal/*")
        assert s.matches_selectors(
            _FakeChange("foo", source_location="/repo/internal/x.h:10")
        )
        assert not s.matches_selectors(
            _FakeChange("foo", source_location="/repo/public/x.h:10")
        )

    def test_namespace_matches_ancestor_and_strips_templates(self) -> None:
        s = SelectorSet(namespace="ns::detail::**")
        assert s.matches_selectors(_FakeChange("ns::detail::x::Foo<int>::bar"))
        assert not s.matches_selectors(_FakeChange("ns::pub::bar"))

    def test_entity_namespace_matches_own_symbol_only_not_cause(self) -> None:
        s = SelectorSet(entity_namespace="ns::detail::**")
        c = _FakeChange("ns::pub::dispatch", caused_by_type="ns::detail::Impl")
        assert not s.matches_selectors(c)

    def test_cause_namespace_matches_caused_by_type_only(self) -> None:
        s = SelectorSet(cause_namespace="ns::detail::**")
        c = _FakeChange("ns::pub::dispatch", caused_by_type="ns::detail::Impl")
        assert s.matches_selectors(c)
        c2 = _FakeChange("ns::detail::dispatch", caused_by_type=None)
        assert not s.matches_selectors(c2)

    def test_type_pattern_only_matches_type_level_kinds(self) -> None:
        s = SelectorSet(type_pattern="ns::Color")
        type_change = _FakeChange(
            "ns::Color::GREEN", kind=ChangeKind.ENUM_MEMBER_REMOVED
        )
        assert s.matches_selectors(type_change)
        symbol_change = _FakeChange("ns::Color::GREEN", kind=ChangeKind.FUNC_REMOVED)
        assert not s.matches_selectors(symbol_change)

    def test_binding_is_conjunctive_and_exact(self) -> None:
        s = SelectorSet(symbol="foo", binding="weak")
        assert s.matches_selectors(_FakeChange("foo", symbol_binding="weak"))
        assert not s.matches_selectors(_FakeChange("foo", symbol_binding="global"))
        assert not s.matches_selectors(_FakeChange("foo", symbol_binding=None))

    def test_finding_id_matches_only_the_precomputed_value(self) -> None:
        s = SelectorSet(finding_id="abc123")
        c = _FakeChange("foo")
        assert s.matches_selectors(c, canonical_finding_id="abc123")
        assert not s.matches_selectors(c, canonical_finding_id="different")
        # No value computed at all (the caller's own grammar has no
        # finding_id field, e.g. ReclassifyRule) -- never matches.
        assert not s.matches_selectors(c, canonical_finding_id=None)

    def test_expired_selector_set_never_matches(self) -> None:
        s = SelectorSet(symbol="foo", expires=date(2020, 1, 1))
        assert not s.matches_selectors(_FakeChange("foo"), today=date(2026, 1, 1))
        assert s.matches_selectors(_FakeChange("foo"), today=date(2019, 1, 1))

    def test_is_expired(self) -> None:
        s = SelectorSet(symbol="foo", expires=date(2020, 1, 1))
        assert s.is_expired(today=date(2026, 1, 1))
        assert not s.is_expired(today=date(2019, 1, 1))
        s2 = SelectorSet(symbol="foo")
        assert not s2.is_expired()


class TestSelectorSetSharedAcrossBothRuleForms:
    """The point of this leaf module: the identical SelectorSet the real
    Suppression/ReclassifyRule build internally behaves the same standalone
    -- see test_suppression*.py / test_reclassify.py for the end-to-end
    versions through the real rule classes."""

    def test_binding_selector_shared_grammar_shape(self) -> None:
        # Both Suppression and ReclassifyRule pass their own `binding` field
        # straight through to a SelectorSet unchanged -- this is the exact
        # shape either constructs.
        s = SelectorSet(symbol_pattern="_ZN6oneapi3dal.*", binding="weak")
        assert s.matches_selectors(
            _FakeChange("_ZN6oneapi3dal4Test", symbol_binding="weak")
        )
