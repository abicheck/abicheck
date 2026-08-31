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
    _source_name_end,
    _stringify_change_value,
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

    def test_bare_conversion_and_literal_operator_codes_are_rejected(self) -> None:
        # Codex review, fresh evidence: unlike the other 47 <operator-name>
        # two-letter codes, "cv" (conversion operator) requires a
        # following <type> and "li" (C++11 literal operator) requires a
        # following <source-name> for its suffix -- a bare "_Zcv"/"_Zli" is
        # a legal C export name, not a complete encoding, but previously
        # matched `rest[:2] in _ITANIUM_OPERATOR_CODES` outright with no
        # operand check.
        assert normalize_mangled_name("_Zcv", "_Zcv") is None
        assert normalize_mangled_name("_Zli", "_Zli") is None

    def test_conversion_operator_with_operand_is_accepted(self) -> None:
        # operator int() const -- "cv" followed by its target <type> "i".
        assert normalize_mangled_name("_Zcvi", None) == "_Zcvi"

    def test_conversion_and_literal_operators_with_invalid_digit_operand_are_rejected(
        self,
    ) -> None:
        # Codex review, fresh evidence, round 2: "_Zcv0"/"_Zli0" pass the
        # bare length check added for the earlier fix, but "0" is not a
        # valid operand for either production (not a real <type> code, and
        # not a positive-length <source-name>) -- the same digit-prefixed
        # gap _operand_looks_valid already closes for TV/GV.
        assert normalize_mangled_name("_Zcv0", "_Zcv0") is None
        assert normalize_mangled_name("_Zli0", "_Zli0") is None

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

    def test_special_name_with_invalid_digit_operand_is_rejected(self) -> None:
        # Codex review, fresh evidence: "_ZTV0" passed the earlier
        # length-only operand check (len(rest) > 2), but the operand "0"
        # is neither a valid type encoding nor a positive-length source
        # name -- a real Itanium <source-name> can never declare a
        # zero-byte identifier.
        assert normalize_mangled_name("_ZTV0", "_ZTV0") is None

    def test_guard_variable_with_invalid_digit_operand_is_rejected(self) -> None:
        # Same gap, same fix, for the guard-variable/reference-temporary
        # prefix.
        assert normalize_mangled_name("_ZGV0", "_ZGV0") is None

    def test_oversized_source_name_length_prefix_does_not_raise(self) -> None:
        # CodeRabbit review: a crafted mangled name with a huge digit-prefix
        # (untrusted ELF/DWARF/PE symbol-table input) must degrade to None,
        # never raise -- Python's int() rejects digit strings past
        # sys.get_int_max_str_digits() (~4300 by default).
        huge = "_Z" + "9" * 5000 + "abc"
        assert normalize_mangled_name(huge, None) is None

    def test_zero_length_source_name_is_rejected(self) -> None:
        # Codex review, round 3: declared_len == 0 previously "succeeded"
        # against any following content (len(identifier) >= 0 is always
        # true), so a legal C/extern-C identifier like "_Z0" was wrongly
        # promoted to the canonical tier despite not being a valid Itanium
        # encoding -- a real identifier can never be zero bytes long.
        assert normalize_mangled_name("_Z0", "_Z0") is None

    def test_guard_variable_special_name_is_accepted(self) -> None:
        assert normalize_mangled_name("_ZGVfoo", None) == "_ZGVfoo"

    def test_bare_special_name_prefix_without_operand_is_rejected(self) -> None:
        # Codex review: every <special-name> production (vtable/VTT/
        # typeinfo/typeinfo-name/thread-local-init/thread-local-wrapper)
        # requires an operand (a type or source-name) after its two-letter
        # prefix -- a bare "_ZTV" was previously accepted outright despite
        # having no operand at all and not being a complete encoding.
        assert normalize_mangled_name("_ZTV", "_ZTV") is None
        assert normalize_mangled_name("_ZTH", "_ZTH") is None

    def test_bare_guard_variable_prefix_without_operand_is_rejected(self) -> None:
        # Same gap, same fix, for the guard-variable/reference-temporary
        # prefix: "_ZGV" alone has no name to guard and is not a complete
        # encoding either.
        assert normalize_mangled_name("_ZGV", "_ZGV") is None

    def test_nested_name_is_accepted(self) -> None:
        assert (
            normalize_mangled_name("_ZN6Widget8getValueEv", None)
            == "_ZN6Widget8getValueEv"
        )

    def test_local_name_is_accepted(self) -> None:
        # <local-name> ::= Z <function encoding> E <entity name>
        assert (
            normalize_mangled_name("_ZZ4mainEN4Test1xE", None) == "_ZZ4mainEN4Test1xE"
        )

    def test_nested_name_with_no_terminator_is_rejected(self) -> None:
        # Codex review: rest[0] in "NZ" previously accepted any N/Z-prefixed
        # string outright with no structural check at all -- both
        # <nested-name> and <local-name> always terminate with a literal
        # "E" after at least one component, so "_ZNonsense" (no E at all)
        # and "_ZN" (nothing after N) are not real Itanium encodings despite
        # structurally passing the coarser _Z + character-class check.
        assert normalize_mangled_name("_ZNonsense", "_ZNonsense") is None

    def test_nested_name_source_name_embedded_e_is_not_a_terminator(self) -> None:
        # Codex review, fresh evidence: "_ZN1E" is incomplete -- "1E" is a
        # length-1 <source-name> whose one-byte identifier IS "E", leaving
        # no separate terminator -- but the naive `rest.find("E", 1)` scan
        # matched that embedded byte and wrongly promoted this incomplete
        # token to the canonical tier.
        assert normalize_mangled_name("_ZN1E", "_ZN1E") is None
        assert normalize_mangled_name("_ZN", "_ZN") is None

    def test_nested_name_with_malformed_digit_component_is_rejected(self) -> None:
        # Codex review, fresh evidence, round 3: _source_name_end(rest[1:])
        # correctly rejects "_ZN0E"/"_ZN9abcE"'s zero-length/truncated
        # first component (returning None), but the previous code then
        # fell back to the naive terminator scan, which treated the
        # trailing E as a valid terminator anyway -- no other
        # <nested-name>/<local-name> first component starts with a bare
        # digit, so a digit-prefixed component that fails to parse as a
        # <source-name> makes the whole production invalid, not just that
        # one component.
        assert normalize_mangled_name("_ZN0E", "_ZN0E") is None
        assert normalize_mangled_name("_ZN9abcE", "_ZN9abcE") is None

    def test_nested_name_second_component_embedded_e_is_not_a_terminator(
        self,
    ) -> None:
        # Codex review, fresh evidence, round 4: a single-component skip
        # left a SECOND chained source-name component's own trailing 'E'
        # byte exposed to the same embedded-terminator confusion the
        # first-component fix addressed. "_ZN1A1E" is incomplete -- after
        # consuming "1A", "1E" is a second length-1 <source-name> whose
        # one-byte identifier IS "E", leaving no separate terminator (the
        # complete form is "_ZN1A1EE").
        assert normalize_mangled_name("_ZN1A1E", "_ZN1A1E") is None

    def test_nested_name_with_two_complete_components_is_accepted(self) -> None:
        # "_ZN1A1EE" -- namespace A, entity named "E", real terminator.
        assert normalize_mangled_name("_ZN1A1EE", None) == "_ZN1A1EE"

    def test_nested_name_substitution_component_embedded_e_is_not_a_terminator(
        self,
    ) -> None:
        # Codex review, fresh evidence, round 5: "St" (the abbreviated
        # std:: substitution) is itself a <prefix> component and must be
        # followed by more encoding -- a loop that only skipped
        # digit-prefixed components never started skipping here (since
        # 'S' isn't a digit), so it fell back to the naive terminator
        # scan. "_ZNSt1E" is incomplete: after "St", "1E" is a length-1
        # <source-name> whose one-byte identifier IS "E", leaving no
        # separate terminator (the complete form is "_ZNSt1EE").
        assert normalize_mangled_name("_ZNSt1E", "_ZNSt1E") is None

    def test_nested_name_with_substitution_and_source_name_is_accepted(self) -> None:
        # "_ZNSt1EE" -- std:: substitution, entity named "E", real
        # terminator (std::E).
        assert normalize_mangled_name("_ZNSt1EE", None) == "_ZNSt1EE"

    def test_nested_name_bare_substitution_with_no_following_component_is_rejected(
        self,
    ) -> None:
        # Codex review, fresh evidence, round 6: "St" is only the std::
        # <prefix> component and must itself be followed by more encoding
        # -- "_ZNStE" leaves `pos` pointing straight at the 'E' right
        # after "St", which the terminator search wrongly accepted as
        # though "St" alone had completed the prefix.
        assert normalize_mangled_name("_ZNStE", "_ZNStE") is None

    def test_nested_name_other_standard_substitution_embedded_e_is_not_a_terminator(
        self,
    ) -> None:
        # Codex review, fresh evidence, round 7: "Sa" (std::allocator) is
        # a complete substitution on its own, unlike "St", but the loop
        # only recognized the literal "St" -- so a trailing digit-prefixed
        # <source-name> after any of the OTHER five standard
        # substitutions (Sa/Sb/Sd/Si/So/Ss) was never skipped either.
        # "_ZNSa1E" is incomplete: after "Sa", "1E" is a length-1
        # <source-name> whose identifier IS "E", leaving no separate
        # terminator (the complete form is "_ZNSa1EE").
        assert normalize_mangled_name("_ZNSa1E", "_ZNSa1E") is None

    def test_nested_name_with_other_substitution_and_source_name_is_accepted(
        self,
    ) -> None:
        # "_ZNSa1EE" -- std::allocator substitution, entity named "E",
        # real terminator.
        assert normalize_mangled_name("_ZNSa1EE", None) == "_ZNSa1EE"

    def test_nested_name_other_standard_substitution_with_no_following_component_is_rejected(  # noqa: E501
        self,
    ) -> None:
        # Codex review, fresh evidence, round 9: <nested-name> is always
        # N <prefix> <unqualified-name> E, and a <substitution> (what any
        # of the six standard letters spells) is never itself a valid
        # <unqualified-name> -- so "Sa"/"Sb"/"Sd"/"Si"/"So"/"Ss" need a
        # real trailing component just like "St" does, even though they
        # are complete, context-free substitutions in their own right
        # outside a nested name. "_ZNSaE" leaves `pos` pointing straight
        # at the 'E' right after "Sa", which the terminator search
        # previously accepted as though "Sa" alone had completed the
        # required trailing unqualified-name.
        assert normalize_mangled_name("_ZNSaE", "_ZNSaE") is None

    def test_local_name_with_nothing_after_terminator_is_rejected(self) -> None:
        # Codex review, fresh evidence: unlike <nested-name> (complete once
        # its own terminator E is found), a <local-name> is
        # "Z <function encoding> E <entity name>" -- the terminator MUST
        # be followed by a non-empty entity name. "_ZZ1fvE" has a
        # terminator (the trailing E) but nothing after it, so it
        # previously passed the shared N/Z terminator check despite being
        # incomplete.
        assert normalize_mangled_name("_ZZ1fvE", "_ZZ1fvE") is None

    def test_local_name_with_invalid_digit_entity_suffix_is_rejected(self) -> None:
        # Codex review, fresh evidence, round 2: a merely non-empty suffix
        # after the terminator isn't enough -- "_ZZ1fvE0" has one trailing
        # byte, but "0" is a zero-length <source-name>, not a valid entity
        # name, so c++filt leaves it undecoded too.
        assert normalize_mangled_name("_ZZ1fvE0", "_ZZ1fvE0") is None

    def test_local_name_with_no_terminator_is_rejected(self) -> None:
        assert normalize_mangled_name("_ZZnonsense", "_ZZnonsense") is None
        assert normalize_mangled_name("_ZZ", "_ZZ") is None

    def test_std_substitution_abbreviated_name_is_accepted(self) -> None:
        assert normalize_mangled_name("_ZSt3foo", None) == "_ZSt3foo"

    def test_bare_std_namespace_prefix_without_name_is_rejected(self) -> None:
        # Codex review, fresh evidence: "St" specifically abbreviates the
        # std:: namespace prefix only, not a complete substitution by
        # itself -- it must be followed by an unqualified-name. A bare
        # "_ZSt" previously passed the substitution-abbreviation check
        # with nothing after it.
        assert normalize_mangled_name("_ZSt", "_ZSt") is None

    def test_std_namespace_prefix_with_invalid_digit_name_is_rejected(self) -> None:
        # Codex review, fresh evidence, round 3: a length-only check let
        # "_ZSt0"/"_ZSt9abc" through -- "0" is a zero-length <source-name>
        # and "9abc" is truncated (claims 9 bytes, has 3), neither a valid
        # unqualified-name after the std:: namespace prefix.
        assert normalize_mangled_name("_ZSt0", "_ZSt0") is None
        assert normalize_mangled_name("_ZSt9abc", "_ZSt9abc") is None

    def test_other_std_substitution_abbreviations_stay_accepted(self) -> None:
        # Sa/Sb/Sd/Si/So/Ss (each a complete, context-free named
        # substitution) are unaffected by the "St" special-case -- they're
        # complete on their own and don't reference any earlier table
        # entry, unlike numbered back-references (see
        # test_numbered_substitution_is_rejected_at_top_level below).
        assert normalize_mangled_name("_ZSaIcE", None) == "_ZSaIcE"

    def test_invalid_single_letter_substitution_is_rejected(self) -> None:
        # Codex review, fresh evidence, round 2: only Sa/Sb/Sd/Si/So/Ss are
        # real complete single-letter abbreviations -- any other lowercase
        # letter (e.g. "_ZSx") is not a real Itanium substitution at all,
        # but previously matched the fallback outright regardless of which
        # letter followed "S".
        assert normalize_mangled_name("_ZSx", "_ZSx") is None

    def test_numbered_substitution_without_terminator_is_rejected(self) -> None:
        # Codex review, fresh evidence: a numbered substitution's <seq-id>
        # (base-36: digits then uppercase letters) is ALWAYS followed by a
        # literal terminating "_" (e.g. "S0_", "SA_") -- "_ZS0" has the
        # seq-id digit but no terminator and is not a complete encoding.
        assert normalize_mangled_name("_ZS0", "_ZS0") is None
        assert normalize_mangled_name("_ZSA", "_ZSA") is None

    def test_numbered_substitution_is_rejected_at_top_level(self) -> None:
        # Codex review, fresh evidence, round 8: a numbered substitution
        # (and bare "S_") always references an EARLIER substitution-table
        # entry, but this function only ever validates the FIRST
        # production of a top-level encoding, where no such entry can
        # exist yet -- a well-formed "S0_"/"SA_"/"S_" here is a
        # context-free reference to nothing, so even a well-terminated
        # spelling must still fall back to the NORMALIZED tier rather
        # than being promoted to canonical.
        assert normalize_mangled_name("_ZS_", "_ZS_") is None
        assert normalize_mangled_name("_ZS0_i", "_ZS0_i") is None
        assert normalize_mangled_name("_ZSA_i", "_ZSA_i") is None

    def test_extern_c_bare_name_is_rejected(self) -> None:
        assert normalize_mangled_name("foo", "foo") is None

    def test_msvc_prefixed_mangling_is_accepted_on_convention_alone(self) -> None:
        msvc = "?foo@@YAHH@Z"
        assert normalize_mangled_name(msvc, "foo") == msvc

    def test_bare_msvc_prefix_with_no_payload_is_rejected(self) -> None:
        # Codex review, fresh evidence: a bare "?" has no encoded payload
        # at all and is not a real mangling, but previously matched the
        # prefix-only check alone.
        assert normalize_mangled_name("?", "?") is None

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


class TestSourceNameEnd:
    # Codex review, fresh evidence, round 3: every current call site
    # pre-checks that its input is non-empty and digit-prefixed before
    # calling _source_name_end, so its own defensive guard (kept for
    # robustness against future callers, matching _valid_source_name's
    # documented "only called when rest[0].isdigit()" contract) is
    # exercised directly here instead.
    def test_empty_string_returns_none(self) -> None:
        assert _source_name_end("") is None

    def test_non_digit_prefix_returns_none(self) -> None:
        assert _source_name_end("abc") is None


class TestStringifyChangeValue:
    # Codex review, fresh evidence: Change.old_value/new_value are annotated
    # str | None, but diff_python.py's PYTHON_STABLE_ABI_VIOLATION
    # emissions pass a list for several of its findings -- this helper
    # exists so _change_discriminator can join a deterministic string
    # instead of crashing on the structured value.
    def test_none_becomes_empty_string(self) -> None:
        assert _stringify_change_value(None) == ""

    def test_str_passes_through_unchanged(self) -> None:
        assert _stringify_change_value("callable") == "callable"

    def test_list_joins_deterministically(self) -> None:
        assert _stringify_change_value(["a", "b"]) == "a,b"

    def test_tuple_joins_deterministically(self) -> None:
        assert _stringify_change_value(("a", "b")) == "a,b"

    def test_unforeseen_structured_value_falls_back_to_str(self) -> None:
        assert _stringify_change_value({"a": 1}) == str({"a": 1})


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

    def test_raw_unverified_mangled_only_is_normalized_not_reduced(self) -> None:
        # Codex review, fresh evidence: a partial producer supplying only
        # `mangled` (no name/qualified_name at all -- e.g. a symbols-only
        # snapshot) has qn == "" but normalized_basis == the raw export,
        # already the actual basis `sig` was built from. Checking `qn`
        # alone previously demoted this to a REDUCED synthetic hash, even
        # though the identical export becomes NORMALIZED as soon as
        # another producer also supplies `name`, fragmenting one entity's
        # identity solely on metadata completeness.
        identity = resolve_symbol_identity(
            mangled="plain_export", name=None, kind="function"
        )
        assert identity.tier == IDENTITY_TIER_NORMALIZED
        assert identity.primary_id == normalized_signature("plain_export", "function")

    def test_raw_unverified_mangled_only_feeds_relsrc_alias(self) -> None:
        # Codex review, fresh evidence: `name or qn` alone drops the raw
        # export from the relsrc: alias's basis when name/qualified_name
        # are both absent -- normalized_basis (the same value sig already
        # uses) must feed it too, instead of silently losing the only
        # entity signal available.
        identity = resolve_symbol_identity(
            mangled="plain_export",
            name=None,
            kind="function",
            source_location="foo.c:1",
        )
        assert "relsrc:foo.c:1\x1fplain_export" in identity.aliases

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

    def test_extern_c_identity_is_stable_across_evidence_tiers(self) -> None:
        # Codex review: dwarf_snapshot.py scope-qualifies a namespaced
        # extern "C" function's Function.name ("ns::foo"), while a
        # symbols-only fallback snapshot of the SAME export has no scope
        # info at all (Function.name == Function.mangled == "foo"). extern
        # "C" linkage strips namespace qualification from the actual
        # exported symbol, so func.mangled is the tier-independent anchor
        # diff_symbols._diff_functions's primary mangled-key match already
        # relies on -- this identity must agree, not fragment on func.name.
        rich = Function(
            name="ns::foo", mangled="foo", return_type="int", is_extern_c=True
        )
        l0_fallback = Function(
            name="foo", mangled="foo", return_type="int", is_extern_c=True
        )
        rich_identity = resolve_function_identity(rich)
        l0_identity = resolve_function_identity(l0_fallback)
        assert rich_identity.tier == IDENTITY_TIER_NORMALIZED
        assert l0_identity.tier == IDENTITY_TIER_NORMALIZED
        assert rich_identity.primary_id == l0_identity.primary_id

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

    def test_variadic_distinguishes_otherwise_identical_overloads(self) -> None:
        # Codex review: void f(int) vs. void f(int, ...) share a name and
        # identical fixed param types when neither has a real mangling --
        # variadic (...) is not itself a Param, so it must be folded into
        # the discriminator separately.
        fixed = Function(
            name="f",
            mangled="f",
            return_type="void",
            params=[Param(name="x", type="int")],
            is_variadic=False,
        )
        variadic = Function(
            name="f",
            mangled="f",
            return_type="void",
            params=[Param(name="x", type="int")],
            is_variadic=True,
        )
        assert (
            resolve_function_identity(fixed).primary_id
            != resolve_function_identity(variadic).primary_id
        )

    def test_overloads_sharing_a_bare_name_are_distinguished_by_mangling(self) -> None:
        overload_a = Function(name="foo", mangled="_Z3fooi", return_type="int")
        overload_b = Function(name="foo", mangled="_Z3food", return_type="int")
        assert (
            resolve_function_identity(overload_a).primary_id
            != resolve_function_identity(overload_b).primary_id
        )

    def test_parameter_type_spelling_is_canonicalized(self) -> None:
        # Codex review: castxml ("char const*") vs. clang's -ast-dump=json
        # ("char const *") spell an identical param type differently;
        # canonicalizing here keeps two producers' NORMALIZED-tier
        # signatures from fragmenting the same declaration's identity.
        def sig(t: str) -> str:
            f = Function(
                name="foo", mangled=None, return_type="void", params=[Param("s", t)]
            )
            return resolve_function_identity(f).primary_id

        assert sig("char const*") == sig("char const *")

    def test_by_value_cv_delegates_to_shared_signature_primitive(self) -> None:
        # ADR-063 Phase 2: shares canonicalize_function_signature_param_type
        # with entity_id_for_function -- drops a BY-VALUE cv, keeps a POINTEE one.
        def sig(t: str) -> str:
            f = Function(
                name="f", mangled="f", return_type="void", params=[Param("x", t)]
            )
            return resolve_function_identity(f).primary_id

        assert sig("int") == sig("const int")
        assert sig("char *") != sig("const char *")
        sig("void (*)(" * 500 + "int" + ")" * 500)  # must not raise (PR #952)


class TestResolveVariableIdentity:
    def test_mangled_variable_is_canonical(self) -> None:
        var = Variable(name="g_count", mangled="_ZL7g_count", type="int")
        identity = resolve_variable_identity(var)
        assert identity.tier == IDENTITY_TIER_CANONICAL

    def test_extern_c_variable_degrades_to_normalized(self) -> None:
        var = Variable(name="g_count", mangled="g_count", type="int")
        identity = resolve_variable_identity(var)
        assert identity.tier == IDENTITY_TIER_NORMALIZED

    def test_extern_c_variable_identity_is_stable_across_evidence_tiers(self) -> None:
        # Codex review: Variable has no is_extern_c field, so this can't be
        # gated the same way resolve_function_identity's extern-C case is --
        # instead resolve_symbol_identity itself prefers `mangled` whenever
        # it's present but not a verified mangling, which covers variables
        # too. A namespaced C-linkage variable's DWARF-derived record
        # scope-qualifies var.name ("ns::x"), while a symbols-only fallback
        # snapshot of the SAME export has no scope info ("x") -- both must
        # still resolve to the same identity.
        rich = Variable(name="ns::x", mangled="x", type="int")
        l0_fallback = Variable(name="x", mangled="x", type="int")
        rich_identity = resolve_variable_identity(rich)
        l0_identity = resolve_variable_identity(l0_fallback)
        assert rich_identity.tier == IDENTITY_TIER_NORMALIZED
        assert l0_identity.tier == IDENTITY_TIER_NORMALIZED
        assert rich_identity.primary_id == l0_identity.primary_id


class TestResolveChangeIdentity:
    def test_list_valued_new_value_does_not_crash(self) -> None:
        # Codex review, fresh evidence: Change.old_value/new_value are
        # annotated str | None, but diff_python.py's
        # PYTHON_STABLE_ABI_VIOLATION emissions pass a list (e.g.
        # new_value=sorted(group)) for several of its findings -- joining
        # that list directly into _change_discriminator's parts previously
        # crashed with "TypeError: sequence item 0: expected str instance,
        # list found" instead of producing an identity.
        change = Change(
            kind=ChangeKind.PYTHON_STABLE_ABI_VIOLATION,
            symbol="mymodule",
            description="Gained private imports outside the stable ABI",
            new_value=["_PyObject_GC_New", "_Py_Dealloc"],
        )
        identity = resolve_change_identity(change)
        assert "_PyObject_GC_New" in identity.primary_id

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

    def test_tls_var_size_changed_identity_is_stable_across_evidence_tiers(
        self,
    ) -> None:
        # Codex review: diff_platform.py's _diff_tls_symbols passes a real
        # exported variable's mangled name as Change.symbol, and
        # _enrich_source_locations (diff_filtering.py) fills in
        # qualified_name for any change whenever a rich snapshot has the
        # matching variable -- TLS_VAR_SIZE_CHANGED wasn't in the
        # symbol-level allowlist, so the L0 (no qualified_name) and rich
        # (qualified_name populated) findings for the SAME variable would
        # resolve to different NORMALIZED-tier identities instead of
        # sharing one CANONICAL mangled-based identity.
        l0 = Change(
            kind=ChangeKind.TLS_VAR_SIZE_CHANGED,
            symbol=_ITANIUM_MANGLED,
            description="TLS variable size changed",
            old_value="4",
            new_value="8",
        )
        rich = Change(
            kind=ChangeKind.TLS_VAR_SIZE_CHANGED,
            symbol=_ITANIUM_MANGLED,
            description="TLS variable size changed",
            old_value="4",
            new_value="8",
            qualified_name="ns::x",
        )
        l0_identity = resolve_change_identity(l0)
        rich_identity = resolve_change_identity(rich)
        assert l0_identity.tier == IDENTITY_TIER_CANONICAL
        assert rich_identity.tier == IDENTITY_TIER_CANONICAL
        assert l0_identity.primary_id == rich_identity.primary_id

    def test_protected_visibility_changed_is_canonical(self) -> None:
        # Regression guard: diff_platform.py's _diff_protected_visibility
        # has the same producer shape (symbol=sym_name, a real mangled
        # variable name) as TLS_VAR_SIZE_CHANGED above.
        change = Change(
            kind=ChangeKind.PROTECTED_VISIBILITY_CHANGED,
            symbol=_ITANIUM_MANGLED,
            description="default -> protected",
            qualified_name="ns::x",
        )
        identity = resolve_change_identity(change)
        assert identity.tier == IDENTITY_TIER_CANONICAL

    def test_exported_object_alignment_reduced_is_canonical(self) -> None:
        # Codex review: diff_platform_elf_symbols.py's alignment check
        # passes the real exported data object's symbol (symbol=sym_name),
        # the same producer shape as TLS_VAR_SIZE_CHANGED/
        # PROTECTED_VISIBILITY_CHANGED above -- omitting it fragments the
        # same alignment finding across evidence tiers.
        change = Change(
            kind=ChangeKind.EXPORTED_OBJECT_ALIGNMENT_REDUCED,
            symbol=_ITANIUM_MANGLED,
            description="alignment reduced",
            qualified_name="ns::g_lookup_table",
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
        # diff_symbols_renames.py's emit_prefix_batch_rename stores a synthetic
        # "batch_rename:<prefix>*" identifier, never a real symbol.
        change = Change(
            kind=ChangeKind.SYMBOL_RENAMED_BATCH,
            symbol="batch_rename:_Z*",
            description="5 symbols renamed",
        )
        identity = resolve_change_identity(change)
        assert identity.tier == IDENTITY_TIER_NORMALIZED

    def test_batch_gnu_unique_sample_is_not_canonical(self) -> None:
        # Codex review: diff_platform_elf_symbols.py's _check_gained_gnu_unique
        # fires once per release and samples an arbitrary, alphabetically-first
        # affected export into Change.symbol -- even when that sample happens
        # to be a real Itanium mangling, it must not be promoted to CANONICAL
        # and aliased against whatever unrelated finding genuinely owns that
        # symbol. The batch call site's distinctive old_value sentinel is what
        # tells this apart from _check_binding_change's genuine per-symbol
        # SYMBOL_BINDING_BECAME_UNIQUE emission (see test right below).
        change = Change(
            kind=ChangeKind.SYMBOL_BINDING_BECAME_UNIQUE,
            symbol=_ITANIUM_MANGLED,
            description="1 GNU_UNIQUE export(s)",
            old_value="(no GNU_UNIQUE exports)",
            new_value="1 GNU_UNIQUE export(s)",
        )
        identity = resolve_change_identity(change)
        assert identity.tier == IDENTITY_TIER_REDUCED

    def test_batch_gnu_unique_sample_is_not_in_qn_or_aliases_either(self) -> None:
        # Codex review, round 2: the first fix only guarded CANONICAL
        # (mangled-name) promotion -- the sampled symbol was still leaking
        # into the NORMALIZED-tier `qn`/`sig` and the `symbol:`/`relsrc:`
        # aliases, so the identity still changed whenever the sorted sample
        # changed and could still collide with an unrelated finding that
        # genuinely owns that symbol. Neither the primary id nor any alias
        # may mention the sampled symbol at all.
        change = Change(
            kind=ChangeKind.SYMBOL_BINDING_BECAME_UNIQUE,
            symbol=_ITANIUM_MANGLED,
            description="1 GNU_UNIQUE export(s)",
            old_value="(no GNU_UNIQUE exports)",
            new_value="1 GNU_UNIQUE export(s)",
        )
        identity = resolve_change_identity(change)
        assert _ITANIUM_MANGLED not in identity.primary_id
        assert all(_ITANIUM_MANGLED not in alias for alias in identity.aliases)

    def test_batch_gnu_unique_identity_is_stable_when_the_sample_changes(self) -> None:
        # Codex review, round 3: the prior two tests used a generic
        # description ("1 GNU_UNIQUE export(s)") that doesn't reproduce the
        # real producer's shape -- _check_gained_gnu_unique's actual
        # make_change() call formats the sampled symbol into `description`
        # via the registry template ("Symbol binding became GNU_UNIQUE:
        # {name} -- inhibits dlclose() on this library"), which `entity_symbol`
        # alone doesn't strip from `discriminator`. Reproducing that exact
        # shape and changing only the sampled export must not change the
        # resolved identity at all.
        description_template = (
            "Symbol binding became GNU_UNIQUE: {name} "
            "-- inhibits dlclose() on this library"
        )
        first = Change(
            kind=ChangeKind.SYMBOL_BINDING_BECAME_UNIQUE,
            symbol=_ITANIUM_MANGLED,
            description=description_template.format(name=_ITANIUM_MANGLED),
            old_value="(no GNU_UNIQUE exports)",
            new_value="1 GNU_UNIQUE export(s)",
        )
        second = Change(
            kind=ChangeKind.SYMBOL_BINDING_BECAME_UNIQUE,
            symbol="_ZN3FooC1Ev",
            description=description_template.format(name="_ZN3FooC1Ev"),
            old_value="(no GNU_UNIQUE exports)",
            new_value="1 GNU_UNIQUE export(s)",
        )
        first_identity = resolve_change_identity(first)
        second_identity = resolve_change_identity(second)
        assert first_identity.primary_id == second_identity.primary_id
        assert first_identity.tier == IDENTITY_TIER_REDUCED

    def test_batch_change_ignores_enrichment_derived_qualified_name_too(
        self,
    ) -> None:
        # Codex review: _enrich_source_locations runs on every Change and
        # can populate qualified_name by looking up change.symbol -- for a
        # batch-shaped change that symbol is the arbitrary sample, so an
        # enrichment hit derives qualified_name from the sample too,
        # leaking it back in even with entity_symbol cleared. Two batch
        # changes differing only in their (enrichment-derived)
        # qualified_name must still resolve to the same identity.
        first = Change(
            kind=ChangeKind.SYMBOL_BINDING_BECAME_UNIQUE,
            symbol=_ITANIUM_MANGLED,
            description="1 GNU_UNIQUE export(s)",
            old_value="(no GNU_UNIQUE exports)",
            new_value="1 GNU_UNIQUE export(s)",
            qualified_name="ns::foo",
        )
        second = Change(
            kind=ChangeKind.SYMBOL_BINDING_BECAME_UNIQUE,
            symbol="_ZN3FooC1Ev",
            description="1 GNU_UNIQUE export(s)",
            old_value="(no GNU_UNIQUE exports)",
            new_value="1 GNU_UNIQUE export(s)",
            qualified_name="ns::Foo::Foo",
        )
        first_identity = resolve_change_identity(first)
        second_identity = resolve_change_identity(second)
        assert first_identity.primary_id == second_identity.primary_id
        assert not any(a.startswith("qualified:") for a in first_identity.aliases)

    def test_batch_change_ignores_enrichment_derived_source_location_too(
        self,
    ) -> None:
        # Codex review, round 3: _enrich_source_locations (diff_filtering.py)
        # also populates source_location from the sampled export, same as
        # qualified_name -- unlike qualified_name/entity_symbol, it wasn't
        # cleared for a batch-shaped change, so it still leaked into the
        # relsrc: alias and the REDUCED-tier synthetic basis. Two batch
        # changes differing only in their (enrichment-derived)
        # source_location must still resolve to the same identity, with no
        # relsrc: alias at all.
        first = Change(
            kind=ChangeKind.SYMBOL_BINDING_BECAME_UNIQUE,
            symbol=_ITANIUM_MANGLED,
            description="1 GNU_UNIQUE export(s)",
            old_value="(no GNU_UNIQUE exports)",
            new_value="1 GNU_UNIQUE export(s)",
            source_location="a.c:1",
        )
        second = Change(
            kind=ChangeKind.SYMBOL_BINDING_BECAME_UNIQUE,
            symbol="_ZN3FooC1Ev",
            description="1 GNU_UNIQUE export(s)",
            old_value="(no GNU_UNIQUE exports)",
            new_value="1 GNU_UNIQUE export(s)",
            source_location="b.c:99",
        )
        first_identity = resolve_change_identity(first)
        second_identity = resolve_change_identity(second)
        assert first_identity.primary_id == second_identity.primary_id
        assert not any(a.startswith("relsrc:") for a in first_identity.aliases)

    def test_allocator_replacement_kinds_are_batch_shaped(self) -> None:
        # Codex review: diff_platform_elf_symbols.py's
        # _diff_allocator_replacement has the same "arbitrary sample as
        # spokesperson" shape as _check_gained_gnu_unique -- it fires once
        # per release, sampling the alphabetically-first affected allocator
        # export into both symbol and (via the description_template's
        # {detail}) description. Unlike SYMBOL_BINDING_BECAME_UNIQUE, these
        # two kinds have no per-symbol sibling detector at all, so every
        # emission is batch-shaped.
        first = Change(
            kind=ChangeKind.ALLOCATOR_REPLACEMENT_ADDED,
            symbol="_Znwm",
            description="Global allocator replacement introduced: _Znwm",
            new_value="1",
        )
        second = Change(
            kind=ChangeKind.ALLOCATOR_REPLACEMENT_ADDED,
            symbol="_ZdlPv",
            description="Global allocator replacement introduced: _ZdlPv",
            new_value="1",
        )
        first_identity = resolve_change_identity(first)
        second_identity = resolve_change_identity(second)
        assert first_identity.primary_id == second_identity.primary_id
        assert "_Znwm" not in first_identity.primary_id
        assert all("_Znwm" not in a for a in first_identity.aliases)

    def test_visibility_leak_is_batch_shaped(self) -> None:
        # Codex review, fresh evidence: diff_platform_elf_dynamic.py's
        # _diff_visibility_leak fires once per release with a fixed
        # symbol="<visibility>" sentinel (never a real exported name) and
        # embeds up to five names sampled from unsorted old.functions into
        # description -- two semantically identical snapshots serialized in
        # a different function order would otherwise get different
        # discriminators and fragment into separate primary_ids.
        first = Change(
            kind=ChangeKind.VISIBILITY_LEAK,
            symbol="<visibility>",
            description="_internal_helper_a, _internal_helper_b",
            old_value="2",
        )
        second = Change(
            kind=ChangeKind.VISIBILITY_LEAK,
            symbol="<visibility>",
            description="_internal_helper_b, _internal_helper_a",
            old_value="2",
        )
        first_identity = resolve_change_identity(first)
        second_identity = resolve_change_identity(second)
        assert first_identity.primary_id == second_identity.primary_id
        assert "_internal_helper_a" not in first_identity.primary_id
        assert all("_internal_helper_a" not in a for a in first_identity.aliases)

    def test_header_binary_context_mismatch_ignores_sample_order(self) -> None:
        # Codex review, fresh evidence: diff_layout_coherence.py's
        # _mismatch_change embeds up to five uncorroborated record names
        # sampled from dwarf_layout_coherence_mismatches into description
        # -- two semantically identical snapshots whose mismatch tuple
        # happens to be ordered differently would otherwise get different
        # discriminators and fragment into separate primary_ids. Uses
        # affected_symbols (the complete, structured evidence set) instead
        # of the order-dependent description prose.
        first = Change(
            kind=ChangeKind.HEADER_BINARY_CONTEXT_MISMATCH,
            symbol="libfoo.so",
            description="The old snapshot's ... found 2 record(s): Foo, Bar.",
            affected_symbols=["Foo", "Bar"],
        )
        second = Change(
            kind=ChangeKind.HEADER_BINARY_CONTEXT_MISMATCH,
            symbol="libfoo.so",
            description="The old snapshot's ... found 2 record(s): Bar, Foo.",
            affected_symbols=["Bar", "Foo"],
        )
        first_identity = resolve_change_identity(first)
        second_identity = resolve_change_identity(second)
        assert first_identity.primary_id == second_identity.primary_id

    def test_header_binary_context_mismatch_old_and_new_side_stay_distinct(
        self,
    ) -> None:
        # Codex review, fresh evidence, round 2: _mismatch_change can emit
        # TWO findings from one comparison -- one for the old snapshot, one
        # for the new -- each with its OWN mismatched-record set, but the
        # SAME symbol=snapshot.library on both. Treating this kind as fully
        # batch-shaped (clearing entity_symbol/dropping description like
        # visibility_leak/allocator_replacement) previously collapsed both
        # findings to the same primary_id regardless of their actual
        # (different) evidence -- a real collision between two distinct
        # findings, not just an unnecessary specificity degrade.
        old_side = Change(
            kind=ChangeKind.HEADER_BINARY_CONTEXT_MISMATCH,
            symbol="libfoo.so",
            description="The old snapshot's ... found 2 record(s): Foo, Bar.",
            affected_symbols=["Foo", "Bar"],
        )
        new_side = Change(
            kind=ChangeKind.HEADER_BINARY_CONTEXT_MISMATCH,
            symbol="libfoo.so",
            description="The new snapshot's ... found 1 record(s): Baz.",
            affected_symbols=["Baz"],
        )
        old_identity = resolve_change_identity(old_side)
        new_identity = resolve_change_identity(new_side)
        assert old_identity.primary_id != new_identity.primary_id

    def test_header_binary_context_mismatch_same_evidence_different_side_stays_distinct(
        self,
    ) -> None:
        # Codex review, fresh evidence, round 3: the previous fix left one
        # residual gap -- when old-side and new-side happen to report the
        # exact SAME mismatched-record set, sorted affected_symbols alone
        # can't tell them apart. description's stable lead-in ("The
        # old/new snapshot's...") is the only place Change carries which
        # side a finding is about; extracting just that fixed prefix
        # (immune to the sampled-name-order issue, since it's never part
        # of the order-dependent five-name sample) closes the gap.
        old_side = Change(
            kind=ChangeKind.HEADER_BINARY_CONTEXT_MISMATCH,
            symbol="libfoo.so",
            description="The old snapshot's ... found 2 record(s): Foo, Bar.",
            affected_symbols=["Foo", "Bar"],
        )
        new_side = Change(
            kind=ChangeKind.HEADER_BINARY_CONTEXT_MISMATCH,
            symbol="libfoo.so",
            description="The new snapshot's ... found 2 record(s): Foo, Bar.",
            affected_symbols=["Foo", "Bar"],
        )
        old_identity = resolve_change_identity(old_side)
        new_identity = resolve_change_identity(new_side)
        assert old_identity.primary_id != new_identity.primary_id

    def test_header_binary_context_mismatch_comma_in_record_name_stays_distinct(
        self,
    ) -> None:
        # Codex review, fresh evidence, round 4: a comma join is not
        # injective when a C++ record name itself contains a comma
        # (common for template types, e.g. "A<int,int>") -- mismatch
        # sets ("A,B", "C") and ("A", "B,C") previously both joined to
        # "A,B,C", colliding two distinct evidence sets.
        first = Change(
            kind=ChangeKind.HEADER_BINARY_CONTEXT_MISMATCH,
            symbol="libfoo.so",
            description="The old snapshot's ... found 2 record(s): A,B, C.",
            affected_symbols=["A,B", "C"],
        )
        second = Change(
            kind=ChangeKind.HEADER_BINARY_CONTEXT_MISMATCH,
            symbol="libfoo.so",
            description="The old snapshot's ... found 2 record(s): A, B,C.",
            affected_symbols=["A", "B,C"],
        )
        first_identity = resolve_change_identity(first)
        second_identity = resolve_change_identity(second)
        assert first_identity.primary_id != second_identity.primary_id

    def test_per_symbol_gnu_unique_transition_is_still_canonical(self) -> None:
        # Regression guard: _check_binding_change's genuine per-symbol
        # SYMBOL_BINDING_BECAME_UNIQUE emission -- a real SymbolBinding value
        # in old_value, never the batch sentinel above -- must stay
        # entity-bearing, not get swept into the same exclusion.
        change = Change(
            kind=ChangeKind.SYMBOL_BINDING_BECAME_UNIQUE,
            symbol=_ITANIUM_MANGLED,
            description="GLOBAL -> UNIQUE",
            old_value="GLOBAL",
            new_value="UNIQUE",
            qualified_name="foo",
        )
        identity = resolve_change_identity(change)
        assert identity.tier == IDENTITY_TIER_CANONICAL

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

    def test_enum_member_value_changed_collapses_across_ast_and_dwarf_wording(
        self,
    ) -> None:
        # Codex review: diff_filtering._dedup_enum_same_kind already
        # collapses ENUM_MEMBER_VALUE_CHANGED/ENUM_MEMBER_REMOVED/
        # ENUM_LAST_MEMBER_VALUE_CHANGED findings by (kind, symbol) alone --
        # diff_types.py's AST detector uses the bare registry description
        # template while diff_platform.py's DWARF detector passes a bespoke
        # description embedding "(old -> new)". Both must resolve to the
        # same identity or this module could never actually perform that
        # same reconciliation once wired in.
        ast_side = Change(
            kind=ChangeKind.ENUM_MEMBER_VALUE_CHANGED,
            symbol="Color::RED",
            description="Enum member value changed",
            old_value="1",
            new_value="2",
        )
        dwarf_side = Change(
            kind=ChangeKind.ENUM_MEMBER_VALUE_CHANGED,
            symbol="Color::RED",
            description="Enum member value changed: Color::RED (1 → 2)",
            old_value="1",
            new_value="2",
        )
        ast_identity = resolve_change_identity(ast_side)
        dwarf_identity = resolve_change_identity(dwarf_side)
        assert ast_identity.primary_id == dwarf_identity.primary_id

    def test_enum_member_removed_on_a_different_symbol_does_not_collapse(self) -> None:
        # Regression guard: the same-kind category collapse must still
        # discriminate by symbol -- two different enum members must not
        # collide just because they share ENUM_MEMBER_REMOVED.
        red = Change(
            kind=ChangeKind.ENUM_MEMBER_REMOVED,
            symbol="Color::RED",
            description="removed",
            old_value="1",
        )
        blue = Change(
            kind=ChangeKind.ENUM_MEMBER_REMOVED,
            symbol="Color::BLUE",
            description="removed",
            old_value="3",
        )
        assert (
            resolve_change_identity(red).primary_id
            != resolve_change_identity(blue).primary_id
        )

    def test_func_deleted_collapses_across_castxml_and_dwarf(self) -> None:
        # Codex review: diff_symbols._detect_newly_deleted_functions emits
        # FUNC_DELETED (castxml is_deleted attribute) or FUNC_DELETED_DWARF
        # (DWARF DW_AT_deleted) for the same symbol/callable->deleted
        # transition -- which kind you get depends only on which evidence
        # source observed the deletion, not on a different underlying event.
        castxml_side = Change(
            kind=ChangeKind.FUNC_DELETED,
            symbol=_ITANIUM_MANGLED,
            description="Function deleted",
            old_value="callable",
            new_value="deleted",
        )
        dwarf_side = Change(
            kind=ChangeKind.FUNC_DELETED_DWARF,
            symbol=_ITANIUM_MANGLED,
            description="Function deleted (DWARF)",
            old_value="callable",
            new_value="deleted",
        )
        assert (
            resolve_change_identity(castxml_side).primary_id
            == resolve_change_identity(dwarf_side).primary_id
        )

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

    def test_equivalent_removal_kinds_collide_despite_qualified_name_skew(
        self,
    ) -> None:
        # Codex review: a namespaced extern "C" removal's rich-evidence
        # finding gets qualified_name="ns::foo" from _enrich_source_locations
        # while the equivalent L0/ELF-only finding for the SAME event has no
        # qualified_name at all (symbol="foo" only) -- preferring
        # qualified_name for the NORMALIZED-tier basis would give the two
        # _EQUIVALENT_CHANGE_CATEGORIES-collapsed findings different sig:
        # primary ids even though the category discriminator itself already
        # collapses them, defeating the collapse the same way the earlier
        # entity-level extern-C fix addressed for declarations.
        rich = resolve_change_identity(
            Change(
                kind=ChangeKind.FUNC_REMOVED,
                symbol="foo",
                description="foo removed (header no longer declares it)",
                qualified_name="ns::foo",
            )
        )
        l0 = resolve_change_identity(
            Change(
                kind=ChangeKind.FUNC_REMOVED_ELF_ONLY,
                symbol="foo",
                description="foo removed (ELF export gone)",
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
