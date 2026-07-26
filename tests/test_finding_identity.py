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
    _looks_like_itanium_encoding,
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

    def test_fallback_dumper_mangled_equal_to_name_is_still_real(self) -> None:
        # dumper_elf_fallback.py/dumper.py's PE-only path set both
        # name=mangled=the raw exported symbol for a genuine C++/MSVC
        # export when no debug info is available to demangle it -- bare
        # equality must not be mistaken for extern "C" here (Codex review).
        assert is_real_mangled_name(_ITANIUM_MANGLED, _ITANIUM_MANGLED) is True
        assert is_real_mangled_name("?foo@@YAHXZ", "?foo@@YAHXZ") is True

    def test_bare_name_equal_to_itself_is_still_not_real(self) -> None:
        # A genuinely non-mangled name riding in both fields (real extern
        # "C" linkage) must still degrade -- only a value that
        # independently looks like a real mangling overrides the equality
        # check.
        assert is_real_mangled_name("foo", "foo") is False


class TestLooksLikeItaniumEncoding:
    def test_empty_string_is_rejected(self) -> None:
        assert _looks_like_itanium_encoding("") is False


class TestNormalizeMangledName:
    def test_itanium_mangling_that_demangles_is_accepted(self) -> None:
        assert normalize_mangled_name(_ITANIUM_MANGLED, "foo") == _ITANIUM_MANGLED

    def test_itanium_lookalike_with_no_real_encoding_is_rejected(self) -> None:
        # Codex review: "_Zebra" passes the coarse _Z + character-class
        # check but does not start any real Itanium <encoding> production.
        assert normalize_mangled_name("_Zebra", None) is None

    def test_source_name_with_declared_length_too_long_is_rejected(self) -> None:
        # Codex review: "9" claims a 9-byte name but only "abc" (3) follows.
        assert normalize_mangled_name("_Z9abc", None) is None

    def test_local_linkage_source_name_with_invalid_length_is_rejected(self) -> None:
        assert normalize_mangled_name("_ZL9abc", None) is None

    def test_global_operator_new_mangling_is_accepted(self) -> None:
        # operator new(unsigned long) -- a real mangling that starts with
        # an <operator-name> two-letter code ("nw"), not a digit/N/L/T/G/S,
        # so the encoding-start check must not reject it.
        assert normalize_mangled_name("_Znwm", None) == "_Znwm"

    def test_vtable_special_name_is_accepted(self) -> None:
        assert normalize_mangled_name("_ZTV6Widget", None) == "_ZTV6Widget"

    def test_thread_local_init_special_name_is_accepted(self) -> None:
        # CodeRabbit review: "TH" (thread-local initialization function) is
        # a real Itanium production that was missing from the original
        # letter set.
        assert normalize_mangled_name("_ZTH3str", None) == "_ZTH3str"

    def test_unverified_special_name_letters_are_rejected(self) -> None:
        # CodeRabbit review: "F"/"J" had no corresponding Itanium
        # <special-name> production and were dropped rather than guessed at.
        assert normalize_mangled_name("_ZTFfoo", None) is None
        assert normalize_mangled_name("_ZTJfoo", None) is None

    def test_oversized_source_name_length_prefix_does_not_raise(self) -> None:
        # CodeRabbit review: a crafted mangled name with a huge digit-prefix
        # (untrusted ELF/DWARF/PE symbol-table input) must degrade to None,
        # never raise -- Python's int() rejects digit strings past
        # sys.get_int_max_str_digits() (~4300 by default).
        huge = "_Z" + "9" * 5000 + "abc"
        assert normalize_mangled_name(huge, None) is None

    def test_guard_variable_special_name_is_accepted(self) -> None:
        assert normalize_mangled_name("_ZGVfoo", None) == "_ZGVfoo"

    def test_nested_name_is_accepted(self) -> None:
        assert (
            normalize_mangled_name("_ZN6Widget8getValueEv", None)
            == "_ZN6Widget8getValueEv"
        )

    def test_std_substitution_abbreviated_name_is_accepted(self) -> None:
        assert normalize_mangled_name("_ZSt3foo", None) == "_ZSt3foo"

    def test_extern_c_bare_name_is_rejected(self) -> None:
        assert normalize_mangled_name("foo", "foo") is None

    def test_msvc_prefixed_mangling_is_accepted_on_convention_alone(self) -> None:
        msvc = "?foo@@YAHH@Z"
        assert normalize_mangled_name(msvc, "foo") == msvc

    def test_itanium_prefixed_garbage_with_invalid_characters_is_rejected(self) -> None:
        assert (
            normalize_mangled_name("_Znotreallymangled!!", "notreallymangled!!") is None
        )

    def test_gcc_clone_suffix_is_accepted(self) -> None:
        # CodeRabbit review: GCC clone symbols (_Z3fooi.isra.0,
        # .constprop.0, .cold, ...) are real manglings that
        # demangle._MANGLED_TOKEN_RE already recognizes via a repeated
        # dotted suffix; this module's no-tool structural check must too.
        cloned = f"{_ITANIUM_MANGLED}.isra.0"
        assert normalize_mangled_name(cloned, "foo") == cloned

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

    def test_fallback_dumper_shaped_function_is_still_canonical(self) -> None:
        # dumper_elf_fallback.py's symbols-only ELF path constructs
        # Function(name=sym, mangled=sym, ...) for every exported symbol,
        # including a genuine C++ export with no separate demangled name
        # available -- this must still resolve to CANONICAL, not degrade
        # to NORMALIZED just because name and mangled happen to be equal.
        func = Function(
            name=_ITANIUM_MANGLED,
            mangled=_ITANIUM_MANGLED,
            return_type="?",
            is_extern_c=False,
        )
        identity = resolve_function_identity(func)
        assert identity.tier == IDENTITY_TIER_CANONICAL
        assert identity.primary_id == f"mangled:{_ITANIUM_MANGLED}"

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
    def test_symbol_slug_kind_with_mangled_symbol_is_canonical(self) -> None:
        # Codex review: SYMBOL_TYPE_CHANGED etc. (the ELF symbol_* family)
        # carry a real mangled symbol too, not just func_*/var_*/ifunc_*.
        change = Change(
            kind=ChangeKind.SYMBOL_TYPE_CHANGED,
            symbol=_ITANIUM_MANGLED,
            description="FUNC -> OBJECT",
            qualified_name="foo",
        )
        identity = resolve_change_identity(change)
        assert identity.tier == IDENTITY_TIER_CANONICAL

    def test_individually_named_symbol_level_kind_is_canonical(self) -> None:
        # Codex review: VIRTUAL_METHOD_ADDED/CALLING_CONVENTION_CHANGED don't
        # share a func_*/var_*/symbol_* prefix but are still symbol-level.
        change = Change(
            kind=ChangeKind.CALLING_CONVENTION_CHANGED,
            symbol=_ITANIUM_MANGLED,
            description="cdecl -> fastcall",
            qualified_name="foo",
        )
        identity = resolve_change_identity(change)
        assert identity.tier == IDENTITY_TIER_CANONICAL

    def test_version_node_label_resembling_a_mangling_is_not_canonical(self) -> None:
        # Codex review: diff_versioning.py's SYMBOL_VERSION_NODE_REMOVED
        # stores a version-node label (e.g. "GLIBC_2.17") in Change.symbol,
        # not a real exported symbol -- even if that label happened to look
        # like an Itanium mangling, it must not be promoted to CANONICAL and
        # aliased alongside an actual function's mangled name.
        change = Change(
            kind=ChangeKind.SYMBOL_VERSION_NODE_REMOVED,
            symbol=_ITANIUM_MANGLED,
            description="version node removed",
            old_value=_ITANIUM_MANGLED,
        )
        identity = resolve_change_identity(change)
        assert identity.tier == IDENTITY_TIER_NORMALIZED

    def test_batch_rename_synthetic_id_is_not_canonical(self) -> None:
        # diff_symbols.py's _emit_batch_rename stores a synthetic
        # "batch_rename:<prefix>*" identifier, never a real symbol.
        change = Change(
            kind=ChangeKind.SYMBOL_RENAMED_BATCH,
            symbol="batch_rename:_Z*",
            description="5 symbols renamed",
        )
        identity = resolve_change_identity(change)
        assert identity.tier == IDENTITY_TIER_NORMALIZED

    def test_symbol_moved_version_node_is_still_canonical(self) -> None:
        # Regression guard: SYMBOL_MOVED_VERSION_NODE lives in the same file
        # as the version-node-label kinds above but carries a real exported
        # symbol (diff_versioning.py: symbol=sym_name) -- it must stay
        # entity-bearing, not get swept into the same exclusion.
        change = Change(
            kind=ChangeKind.SYMBOL_MOVED_VERSION_NODE,
            symbol=_ITANIUM_MANGLED,
            description="moved to a new version node",
            qualified_name="foo",
        )
        identity = resolve_change_identity(change)
        assert identity.tier == IDENTITY_TIER_CANONICAL

    def test_method_access_changed_is_canonical(self) -> None:
        # Codex review: two overloaded methods undergoing the same access
        # transition must not collapse onto one primary id via a shared
        # qualified name -- their distinct mangled symbols must stay
        # authoritative.
        change = Change(
            kind=ChangeKind.METHOD_ACCESS_CHANGED,
            symbol=_ITANIUM_MANGLED,
            description="public -> protected",
            qualified_name="Widget::getValue",
        )
        identity = resolve_change_identity(change)
        assert identity.tier == IDENTITY_TIER_CANONICAL

    def test_param_level_kind_is_canonical(self) -> None:
        # Codex review: two overloads (A::f(int) vs A::f(double)) both
        # emitting PARAM_DEFAULT_VALUE_CHANGED with the same old/new value
        # get the same enriched qualified_name -- their distinct mangled
        # symbols must stay authoritative, not collapse via the prefix gate.
        change = Change(
            kind=ChangeKind.PARAM_DEFAULT_VALUE_CHANGED,
            symbol=_ITANIUM_MANGLED,
            description="default value changed",
            qualified_name="A::f",
        )
        identity = resolve_change_identity(change)
        assert identity.tier == IDENTITY_TIER_CANONICAL

    def test_return_level_kind_is_canonical(self) -> None:
        change = Change(
            kind=ChangeKind.RETURN_POINTER_LEVEL_CHANGED,
            symbol=_ITANIUM_MANGLED,
            description="T* -> T**",
            qualified_name="foo",
        )
        identity = resolve_change_identity(change)
        assert identity.tier == IDENTITY_TIER_CANONICAL

    def test_default_argument_changed_is_canonical(self) -> None:
        change = Change(
            kind=ChangeKind.DEFAULT_ARGUMENT_CHANGED,
            symbol=_ITANIUM_MANGLED,
            description="default arg changed",
            qualified_name="foo",
        )
        identity = resolve_change_identity(change)
        assert identity.tier == IDENTITY_TIER_CANONICAL

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

    def test_aliases_do_not_intersect_for_two_findings_on_the_same_symbol(
        self,
    ) -> None:
        # Codex review: bare `mangled:`/`symbol:`/`qualified:` aliases with
        # no discriminator would let a future alias-match reconciliation
        # tier wrongly pair a return-type change with an unrelated
        # param-type change on the same function, since both would carry
        # identical entity-scoped aliases despite being different findings.
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
        assert set(return_changed.aliases).isdisjoint(params_changed.aliases)

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

    def test_dwarf_ast_equivalent_type_size_kinds_collide(self) -> None:
        # Codex review: diff_filtering._DWARF_TO_AST_EQUIV already treats
        # STRUCT_SIZE_CHANGED (DWARF) and TYPE_SIZE_CHANGED (AST) as one
        # logical event for the same type -- this identity must collide too.
        dwarf = resolve_change_identity(
            Change(
                kind=ChangeKind.STRUCT_SIZE_CHANGED,
                symbol="MyStruct",
                description="sizeof(MyStruct) changed (DWARF)",
                old_value="8",
                new_value="16",
            )
        )
        ast = resolve_change_identity(
            Change(
                kind=ChangeKind.TYPE_SIZE_CHANGED,
                symbol="MyStruct",
                description="MyStruct layout changed (AST)",
                old_value="8",
                new_value="16",
            )
        )
        assert dwarf.tier == IDENTITY_TIER_NORMALIZED
        assert dwarf.primary_id == ast.primary_id

    def test_field_level_equivalent_kinds_are_not_collapsed(self) -> None:
        # Codex review: unlike the two whole-type pairs above,
        # STRUCT_FIELD_REMOVED/TYPE_FIELD_REMOVED are deliberately NOT
        # normalized into a shared category -- diff_types.py's AST-side
        # TYPE_FIELD_REMOVED uses a bare parent-type symbol (field name only
        # in description), so collapsing by category+symbol alone would
        # make two DIFFERENT fields of the same struct (e.g. "MyStruct::x"
        # removed vs. "MyStruct::y" removed, both reported with
        # symbol="MyStruct") collide with each other -- a real fact loss,
        # not just a missed DWARF/AST dedup. Kept as separate kinds (no
        # collision at all) is the safe default until a reliable per-field
        # discriminator exists.
        field_x_removed = resolve_change_identity(
            Change(
                kind=ChangeKind.TYPE_FIELD_REMOVED,
                symbol="MyStruct",
                description="field 'x' removed (AST)",
            )
        )
        field_y_removed = resolve_change_identity(
            Change(
                kind=ChangeKind.TYPE_FIELD_REMOVED,
                symbol="MyStruct",
                description="field 'y' removed (AST)",
            )
        )
        assert field_x_removed.primary_id != field_y_removed.primary_id

        dwarf = resolve_change_identity(
            Change(
                kind=ChangeKind.STRUCT_FIELD_REMOVED,
                symbol="MyStruct::x",
                description="field 'x' removed (DWARF)",
            )
        )
        assert dwarf.primary_id != field_x_removed.primary_id

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
        # TYPE_FIELD_ADDED, not TYPE_SIZE_CHANGED: the latter is now in
        # _EQUIVALENT_CHANGE_CATEGORIES (it collides with STRUCT_SIZE_CHANGED
        # by design, see test_dwarf_ast_equivalent_type_size_kinds_collide),
        # so it can't also be used to test the general "old/new value
        # distinguishes findings of a kind with no equivalence pairing" case.
        base = {
            "kind": ChangeKind.TYPE_FIELD_ADDED,
            "symbol": "MyStruct",
            "description": "field added",
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
