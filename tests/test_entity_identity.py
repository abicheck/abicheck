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

"""Tests for canonical entity identity (G31 Phase B B1, ADR-048)."""

from __future__ import annotations

from abicheck.buildsource.entity_identity import (
    IDENTITY_TIER_CANONICAL,
    IDENTITY_TIER_NORMALIZED,
    IDENTITY_TIER_REDUCED,
    candidate_lookup_keys,
    is_real_mangled_name,
    normalize_mangled_name,
    resolve_canonical_identity,
    resolve_identity_for_node,
)
from abicheck.buildsource.source_graph import GraphNode


def test_tier1_usr_wins_over_everything_else() -> None:
    ident = resolve_canonical_identity(
        usr="c:@F@foo#",
        mangled_name="_Z3fooi",
        qualified_name="ns::foo",
        kind="function",
    )
    assert ident.tier == IDENTITY_TIER_CANONICAL
    assert ident.primary_id == "usr:c:@F@foo#"
    assert "mangled:_Z3fooi" in ident.aliases
    assert "qualified:ns::foo" in ident.aliases


def test_tier1_real_mangled_name_used_when_no_usr() -> None:
    # _ZN2ns3fooEi demangles to something different from itself; the plain
    # name ("foo") differs from the mangled field, so it is a real mangling
    # (not the extern "C" mangled==name shape below).
    ident = resolve_canonical_identity(
        mangled_name="_ZN2ns3fooEi", name="foo", qualified_name="ns::foo"
    )
    assert ident.tier == IDENTITY_TIER_CANONICAL
    assert ident.primary_id == "mangled:_ZN2ns3fooEi"


def test_extern_c_bare_name_in_mangled_field_is_not_real_mangling() -> None:
    # extern "C" producers report mangled_name == name deliberately.
    assert is_real_mangled_name("foo", "foo") is False
    assert normalize_mangled_name("foo", "foo") is None
    ident = resolve_canonical_identity(
        mangled_name="foo", name="foo", qualified_name="foo"
    )
    assert ident.tier != IDENTITY_TIER_CANONICAL


def test_extern_c_bare_mangled_field_does_not_leak_a_mangled_alias() -> None:
    """Codex review: a producer reporting mangled_name == name (extern "C"
    linkage) is explicitly not a real mangling (is_real_mangled_name),
    but the alias list previously added "mangled:<bare>" anyway -- unlike
    the "name:" alias, "mangled:" isn't excluded by the Tier-2 bare-name
    filter, so two unrelated old/new declarations sharing only a bare
    C-linkage name could reconcile as the same entity."""
    ident = resolve_canonical_identity(
        mangled_name="foo", name="foo", qualified_name="foo"
    )
    assert not any(a.startswith("mangled:") for a in ident.aliases)


def test_itanium_name_that_does_not_demangle_is_rejected() -> None:
    # Starts with _Z but is not a valid mangled name; demangle() should
    # return None or the same string, so normalize_mangled_name refuses it.
    result = normalize_mangled_name("_Znonsense_not_real", "_Znonsense_not_real")
    assert result is None


def test_tier2_normalized_signature_fallback() -> None:
    ident = resolve_canonical_identity(qualified_name="ns::Widget", kind="record_type")
    assert ident.tier == IDENTITY_TIER_NORMALIZED
    assert ident.primary_id.startswith("sig:")
    assert ident.qualified_name == "ns::Widget"


def test_tier2_signature_distinguishes_arity() -> None:
    a = resolve_canonical_identity(
        qualified_name="ns::f", kind="function", param_types=("int",)
    )
    b = resolve_canonical_identity(
        qualified_name="ns::f", kind="function", param_types=("int", "int")
    )
    assert a.primary_id != b.primary_id


def test_tier4_source_relative_is_alias_not_primary() -> None:
    # A plain name is itself usable as a qualified-name fallback (tier 2),
    # so source-relative identity never becomes primary_id here -- it is
    # always an alias (scope doc tier 4: "additional alias, not a primary
    # key"), recorded on every identity that has a file.
    ident = resolve_canonical_identity(name="anon", file="a.h", scope="ns::detail")
    assert ident.tier == IDENTITY_TIER_NORMALIZED
    assert ident.source_relative == "a.h\x1fns::detail\x1fanon"
    assert f"relsrc:{ident.source_relative}" in ident.aliases


def test_source_relative_alias_present_even_when_synthetic_tier() -> None:
    ident = resolve_canonical_identity(file="a.h", scope="ns::detail")
    assert ident.tier == IDENTITY_TIER_REDUCED
    assert ident.primary_id.startswith("synthetic:")
    assert any(a.startswith("relsrc:") for a in ident.aliases)


def test_tier5_synthetic_fallback_when_nothing_available() -> None:
    ident = resolve_canonical_identity()
    assert ident.tier == IDENTITY_TIER_REDUCED
    assert ident.primary_id.startswith("synthetic:sha256:")


def test_synthetic_fallback_is_deterministic() -> None:
    a = resolve_canonical_identity(kind="record_type")
    b = resolve_canonical_identity(kind="record_type")
    assert a.primary_id == b.primary_id


def test_no_fact_is_invented_absent_mangled_name_stays_absent() -> None:
    ident = resolve_canonical_identity(qualified_name="ns::Widget", kind="record_type")
    assert not any(a.startswith("mangled:") for a in ident.aliases)


def test_resolve_identity_for_node_reads_only_producer_supplied_attrs() -> None:
    node = GraphNode(
        id="type://ns::Widget",
        kind="record_type",
        label="ns::Widget",
        attrs={"qualified_name": "ns::Widget"},
    )
    ident = resolve_identity_for_node(node)
    assert ident.tier == IDENTITY_TIER_NORMALIZED
    assert ident.qualified_name == "ns::Widget"


def test_resolve_identity_for_node_falls_back_to_label() -> None:
    node = GraphNode(id="type://Bare", kind="record_type", label="Bare")
    ident = resolve_identity_for_node(node)
    assert ident.qualified_name == "Bare"


def test_candidate_lookup_keys_generalizes_ad_hoc_key_set() -> None:
    keys = candidate_lookup_keys("primary", "extra1", None, "extra1", "")
    assert keys == {"primary", "extra1"}


def test_candidate_lookup_keys_handles_no_primary() -> None:
    keys = candidate_lookup_keys(None, "a", "b")
    assert keys == {"a", "b"}
