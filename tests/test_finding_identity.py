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

    def test_itanium_prefixed_garbage_with_invalid_characters_is_rejected(self) -> None:
        assert (
            normalize_mangled_name("_Znotreallymangled!!", "notreallymangled!!") is None
        )

    def test_neither_prefix_is_rejected(self) -> None:
        assert normalize_mangled_name("some_random_string", "foo") is None

    def test_none_input_is_rejected(self) -> None:
        assert normalize_mangled_name(None, "foo") is None

    def test_does_not_require_an_external_demangler(self, monkeypatch) -> None:
        # Regression guard (Codex review): identity must be deterministic and
        # host-independent, so this must never shell out to c++filt/cxxfilt --
        # a demangler being unavailable must not silently change the tier a
        # symbol resolves to.
        import abicheck.demangle as demangle_module

        def _boom(symbol: str) -> str | None:
            raise AssertionError(
                "normalize_mangled_name must not call an external demangler"
            )

        monkeypatch.setattr(demangle_module, "demangle", _boom)
        assert normalize_mangled_name(_ITANIUM_MANGLED, "foo") == _ITANIUM_MANGLED


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

    def test_param_types_feed_the_signature_for_non_extern_c_functions(self) -> None:
        # No real mangling (e.g. a DWARF-only snapshot) but genuinely
        # overloadable (is_extern_c=False, the default) -- param types must
        # disambiguate.
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
        assert (
            resolve_function_identity(one_param).primary_id
            != resolve_function_identity(two_params).primary_id
        )

    def test_extern_c_identity_is_stable_across_a_parameter_change(self) -> None:
        # Codex review: diff_symbols._diff_functions matches extern "C"
        # functions by name alone (C has no overloading), regardless of a
        # parameter-list change between old and new. The identity used to
        # drive that same matching must not fragment on a param diff.
        before = Function(
            name="foo",
            mangled="foo",
            return_type="int",
            is_extern_c=True,
            params=[Param(name="x", type="int")],
        )
        after = Function(
            name="foo",
            mangled="foo",
            return_type="int",
            is_extern_c=True,
            params=[Param(name="x", type="int"), Param(name="y", type="char")],
        )
        before_identity = resolve_function_identity(before)
        after_identity = resolve_function_identity(after)
        assert before_identity.tier == IDENTITY_TIER_NORMALIZED
        assert before_identity.primary_id == after_identity.primary_id

    def test_const_qualifier_distinguishes_otherwise_identical_overloads(self) -> None:
        # Codex review: `void f()` vs `void f() const` share a name and an
        # (empty) param-type tuple when neither has a real mangling.
        non_const = Function(name="f", mangled="f", return_type="void")
        const = Function(name="f", mangled="f", return_type="void", is_const=True)
        assert (
            resolve_function_identity(non_const).primary_id
            != resolve_function_identity(const).primary_id
        )

    def test_ref_qualifier_distinguishes_otherwise_identical_overloads(self) -> None:
        lvalue = Function(name="f", mangled="f", return_type="void", ref_qualifier="&")
        rvalue = Function(name="f", mangled="f", return_type="void", ref_qualifier="&&")
        assert (
            resolve_function_identity(lvalue).primary_id
            != resolve_function_identity(rvalue).primary_id
        )

    def test_overloads_sharing_a_bare_name_are_distinguished_by_mangling(self) -> None:
        overload_a = Function(name="foo", mangled="_Z3fooi", return_type="int")
        overload_b = Function(name="foo", mangled="_Z3food", return_type="int")
        assert (
            resolve_function_identity(overload_a).primary_id
            != resolve_function_identity(overload_b).primary_id
        )


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
        assert identity.primary_id.startswith(f"mangled:{_ITANIUM_MANGLED}\x1f")

    def test_canonical_id_distinguishes_two_findings_on_the_same_symbol(self) -> None:
        # Codex review: FUNC_RETURN_CHANGED and FUNC_PARAMS_CHANGED on the
        # same mangled function must not collapse onto one dedup key.
        return_changed = resolve_change_identity(
            Change(
                kind=ChangeKind.FUNC_RETURN_CHANGED,
                symbol=_ITANIUM_MANGLED,
                description="return type changed",
                qualified_name="foo",
            )
        )
        params_changed = resolve_change_identity(
            Change(
                kind=ChangeKind.FUNC_PARAMS_CHANGED,
                symbol=_ITANIUM_MANGLED,
                description="param 0 type changed",
                qualified_name="foo",
            )
        )
        assert return_changed.tier == IDENTITY_TIER_CANONICAL
        assert params_changed.tier == IDENTITY_TIER_CANONICAL
        assert return_changed.primary_id != params_changed.primary_id

    def test_equivalent_removal_kinds_collide_on_the_same_mangled_symbol(self) -> None:
        # Codex review: diff_filtering._deduplicate_cross_detector already
        # treats FUNC_REMOVED (rich detector) and FUNC_REMOVED_ELF_ONLY (L0)
        # as one logical event for the same symbol -- this identity must
        # collide too, or it can never drive that same reconciliation once
        # wired in, even though the two detectors report different
        # descriptions/old-new values for what is the same event.
        rich = resolve_change_identity(
            Change(
                kind=ChangeKind.FUNC_REMOVED,
                symbol=_ITANIUM_MANGLED,
                description="foo removed (header no longer declares it)",
                qualified_name="foo",
            )
        )
        l0 = resolve_change_identity(
            Change(
                kind=ChangeKind.FUNC_REMOVED_ELF_ONLY,
                symbol=_ITANIUM_MANGLED,
                description="foo removed (ELF export gone)",
                qualified_name="foo",
            )
        )
        assert rich.tier == IDENTITY_TIER_CANONICAL
        assert rich.primary_id == l0.primary_id

    def test_equivalent_removal_kinds_collide_without_mangling_too(self) -> None:
        rich = resolve_change_identity(
            Change(
                kind=ChangeKind.FUNC_REMOVED,
                symbol="MyStruct",
                description="removed (header)",
            )
        )
        l0 = resolve_change_identity(
            Change(
                kind=ChangeKind.FUNC_REMOVED_ELF_ONLY,
                symbol="MyStruct",
                description="removed (ELF)",
            )
        )
        assert rich.tier == IDENTITY_TIER_NORMALIZED
        assert rich.primary_id == l0.primary_id

    def test_type_name_resembling_a_mangling_is_not_treated_as_canonical(self) -> None:
        # Codex review: a type literally named "_Zebra" structurally passes
        # the Itanium prefix/character-set check, but TYPE_SIZE_CHANGED is
        # not a symbol-level kind -- change.qualified_name is documented as
        # unset for type-level changes, so this can't be caught via the
        # mangled-vs-plain-name check alone.
        change = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="_Zebra",
            description="size changed",
            old_value="8",
            new_value="16",
        )
        identity = resolve_change_identity(change)
        assert identity.tier == IDENTITY_TIER_NORMALIZED
        assert not identity.primary_id.startswith("mangled:")

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
            primary_id="mangled:_Z3fooi",
            tier=IDENTITY_TIER_CANONICAL,
            aliases=("a", "b"),
        )
        assert identity.to_dict() == {
            "primary_id": "mangled:_Z3fooi",
            "tier": IDENTITY_TIER_CANONICAL,
            "aliases": ["a", "b"],
        }
