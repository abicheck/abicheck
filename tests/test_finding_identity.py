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

"""ADR-049 Phase 2: tests for the flat-finding identity resolver."""

from __future__ import annotations

from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import Change
from abicheck.finding_identity import (
    IDENTITY_TIER_CANONICAL,
    IDENTITY_TIER_NORMALIZED,
    IDENTITY_TIER_REDUCED,
    FindingIdentity,
    is_real_mangled_name,
    normalize_mangled_name,
    normalized_signature,
    resolve_change_identity,
    resolve_function_identity,
    resolve_symbol_identity,
    resolve_variable_identity,
    source_relative_identity,
)
from abicheck.model import Function, Param, Variable

_ITANIUM_MANGLED = "_Z3fooi"  # foo(int)


class TestIsRealMangledName:
    def test_real_mangled_differs_from_plain_name(self) -> None:
        assert is_real_mangled_name(_ITANIUM_MANGLED, "foo") is True

    def test_extern_c_bare_name_in_mangled_field_is_not_real(self) -> None:
        assert is_real_mangled_name("foo", "foo") is False

    def test_missing_mangled_is_not_real(self) -> None:
        assert is_real_mangled_name(None, "foo") is False
        assert is_real_mangled_name("", "foo") is False


class TestNormalizeMangledName:
    def test_itanium_mangling_that_demangles_is_accepted(self) -> None:
        assert normalize_mangled_name(_ITANIUM_MANGLED, "foo") == _ITANIUM_MANGLED

    def test_extern_c_bare_name_is_rejected(self) -> None:
        assert normalize_mangled_name("foo", "foo") is None

    def test_msvc_prefixed_mangling_is_accepted_on_convention_alone(self) -> None:
        msvc = "?foo@@YAHH@Z"
        assert normalize_mangled_name(msvc, "foo") == msvc

    def test_itanium_prefixed_garbage_that_fails_to_demangle_is_rejected(self) -> None:
        assert normalize_mangled_name("_Znotreallymangled!!", "notreallymangled!!") is None

    def test_neither_prefix_is_rejected(self) -> None:
        assert normalize_mangled_name("some_random_string", "foo") is None

    def test_none_input_is_rejected(self) -> None:
        assert normalize_mangled_name(None, "foo") is None


class TestNormalizedSignature:
    def test_deterministic(self) -> None:
        a = normalized_signature("ns::foo", "function", ("int", "char*"))
        b = normalized_signature("ns::foo", "function", ("int", "char*"))
        assert a == b

    def test_arity_distinguishes_overloads(self) -> None:
        one_arg = normalized_signature("ns::foo", "function", ("int",))
        two_arg = normalized_signature("ns::foo", "function", ("int", "int"))
        assert one_arg != two_arg

    def test_empty_inputs_still_produce_a_string(self) -> None:
        assert normalized_signature("", "", ()) == "sig:\x1f\x1f0"


class TestSourceRelativeIdentity:
    def test_combines_file_and_name(self) -> None:
        assert source_relative_identity("foo.h", "bar") == "foo.h\x1fbar"

    def test_missing_parts_default_to_empty(self) -> None:
        assert source_relative_identity("", "") == "\x1f"


class TestResolveSymbolIdentity:
    def test_real_mangled_name_is_canonical(self) -> None:
        identity = resolve_symbol_identity(mangled=_ITANIUM_MANGLED, name="foo")
        assert identity.tier == IDENTITY_TIER_CANONICAL
        assert identity.primary_id == f"mangled:{_ITANIUM_MANGLED}"
        assert f"mangled:{_ITANIUM_MANGLED}" in identity.aliases
        assert "name:foo" in identity.aliases

    def test_extern_c_bare_name_degrades_to_normalized(self) -> None:
        identity = resolve_symbol_identity(mangled="foo", name="foo", kind="function")
        assert identity.tier == IDENTITY_TIER_NORMALIZED
        assert identity.primary_id.startswith("sig:")

    def test_qualified_name_used_when_no_mangling(self) -> None:
        identity = resolve_symbol_identity(
            name="foo", qualified_name="ns::foo", kind="function"
        )
        assert identity.tier == IDENTITY_TIER_NORMALIZED
        assert "qualified:ns::foo" in identity.aliases

    def test_nothing_available_falls_back_to_synthetic(self) -> None:
        identity = resolve_symbol_identity()
        assert identity.tier == IDENTITY_TIER_REDUCED
        assert identity.primary_id.startswith("synthetic:sha256:")

    def test_synthetic_fallback_is_deterministic(self) -> None:
        a = resolve_symbol_identity(source_location="foo.c:1")
        b = resolve_symbol_identity(source_location="foo.c:1")
        assert a.primary_id == b.primary_id

    def test_synthetic_fallback_distinguishes_different_inputs(self) -> None:
        a = resolve_symbol_identity(source_location="foo.c:1")
        b = resolve_symbol_identity(source_location="foo.c:2")
        assert a.primary_id != b.primary_id

    def test_source_location_recorded_as_alias_when_mangled(self) -> None:
        identity = resolve_symbol_identity(
            mangled=_ITANIUM_MANGLED, name="foo", source_location="foo.h:1"
        )
        assert any(a.startswith("relsrc:") for a in identity.aliases)

    def test_never_fabricates_qualified_name_from_nothing(self) -> None:
        # No name/qualified_name/mangled at all -- must not silently promote
        # an empty string to NORMALIZED; falls all the way to REDUCED.
        identity = resolve_symbol_identity(kind="function")
        assert identity.tier == IDENTITY_TIER_REDUCED


class TestResolveFunctionIdentity:
    def test_mangled_function_is_canonical(self) -> None:
        func = Function(name="foo", mangled=_ITANIUM_MANGLED, return_type="int")
        identity = resolve_function_identity(func)
        assert identity.tier == IDENTITY_TIER_CANONICAL
        assert identity.primary_id == f"mangled:{_ITANIUM_MANGLED}"

    def test_extern_c_function_degrades_to_normalized(self) -> None:
        func = Function(name="foo", mangled="foo", return_type="int")
        identity = resolve_function_identity(func)
        assert identity.tier == IDENTITY_TIER_NORMALIZED

    def test_param_types_feed_the_signature(self) -> None:
        one_param = Function(
            name="foo",
            mangled="foo",
            return_type="int",
            params=[Param(name="x", type="int")],
        )
        two_params = Function(
            name="foo",
            mangled="foo",
            return_type="int",
            params=[Param(name="x", type="int"), Param(name="y", type="int")],
        )
        assert resolve_function_identity(one_param).primary_id != resolve_function_identity(
            two_params
        ).primary_id

    def test_overloads_sharing_a_bare_name_are_distinguished_by_mangling(self) -> None:
        overload_a = Function(name="foo", mangled="_Z3fooi", return_type="int")
        overload_b = Function(name="foo", mangled="_Z3food", return_type="int")
        assert resolve_function_identity(overload_a).primary_id != resolve_function_identity(
            overload_b
        ).primary_id


class TestResolveVariableIdentity:
    def test_mangled_variable_is_canonical(self) -> None:
        var = Variable(name="g_count", mangled="_ZL7g_count", type="int")
        identity = resolve_variable_identity(var)
        assert identity.tier == IDENTITY_TIER_CANONICAL

    def test_extern_c_variable_degrades_to_normalized(self) -> None:
        var = Variable(name="g_count", mangled="g_count", type="int")
        identity = resolve_variable_identity(var)
        assert identity.tier == IDENTITY_TIER_NORMALIZED


class TestResolveChangeIdentity:
    def test_real_mangled_symbol_is_canonical(self) -> None:
        change = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol=_ITANIUM_MANGLED,
            description="foo removed",
            qualified_name="foo",
        )
        identity = resolve_change_identity(change)
        assert identity.tier == IDENTITY_TIER_CANONICAL
        assert identity.primary_id == f"mangled:{_ITANIUM_MANGLED}"

    def test_type_level_change_degrades_to_normalized(self) -> None:
        change = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="MyStruct",
            description="size changed",
            old_value="8",
            new_value="16",
        )
        identity = resolve_change_identity(change)
        assert identity.tier == IDENTITY_TIER_NORMALIZED

    def test_old_and_new_value_distinguish_otherwise_identical_findings(self) -> None:
        base = {
            "kind": ChangeKind.TYPE_SIZE_CHANGED,
            "symbol": "MyStruct",
            "description": "size changed",
        }
        a = resolve_change_identity(Change(old_value="8", new_value="16", **base))
        b = resolve_change_identity(Change(old_value="16", new_value="32", **base))
        assert a.primary_id != b.primary_id

    def test_no_symbol_at_all_falls_back_to_synthetic(self) -> None:
        change = Change(kind=ChangeKind.FUNC_REMOVED, symbol="", description="x")
        identity = resolve_change_identity(change)
        assert identity.tier == IDENTITY_TIER_REDUCED

    def test_source_location_recorded_as_alias(self) -> None:
        change = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="MyStruct",
            description="size changed",
            source_location="foo.h:10",
        )
        identity = resolve_change_identity(change)
        assert any(a.startswith("relsrc:") for a in identity.aliases)

    def test_extern_c_symbol_riding_in_symbol_field_is_not_canonical(self) -> None:
        # symbol == qualified_name (no real mangling) must not be
        # misclassified as a verified mangled name.
        change = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="foo",
            description="foo removed",
            qualified_name="foo",
        )
        identity = resolve_change_identity(change)
        assert identity.tier == IDENTITY_TIER_NORMALIZED


class TestFindingIdentityToDict:
    def test_round_trips_fields(self) -> None:
        identity = FindingIdentity(
            primary_id="mangled:_Z3fooi", tier=IDENTITY_TIER_CANONICAL, aliases=("a", "b")
        )
        assert identity.to_dict() == {
            "primary_id": "mangled:_Z3fooi",
            "tier": IDENTITY_TIER_CANONICAL,
            "aliases": ["a", "b"],
        }
