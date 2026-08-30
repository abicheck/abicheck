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

"""ADR-063 Phase 2 (sixth slice): tests for
model/mangled_name_validity.py's is_real_mangled_name/normalize_mangled_name
and the Itanium-mangling-validation machinery behind them.

Relocated verbatim from tests/test_finding_identity.py along with the code
they test -- these regression tests pin real, previously-found
counterexamples (see each test's own Codex-review comments), and moving
them keeps the test suite's own module structure matching the production
module structure it exercises.
"""

from __future__ import annotations

from abicheck.model.mangled_name_validity import (
    _looks_like_itanium_encoding,
    _source_name_end,
    is_real_mangled_name,
    normalize_mangled_name,
)

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


def test_module_declares_no_dependency_above_model() -> None:
    """Leaf-module contract (ADR-063 D10), identical to
    ``test_model_identity.py``'s own check for its sibling
    ``model/identity.py``: ``model.mangled_name_validity`` imports nothing
    from ``checker_types``/``diff_*``/anything above ``model`` -- checked
    against the module's real ``import``/``from ... import`` AST nodes, not
    a substring scan of its source text (which would also match this
    module's own explanatory prose about what it deliberately avoids)."""
    import ast
    import inspect

    from abicheck.model import mangled_name_validity

    tree = ast.parse(inspect.getsource(mangled_name_validity))
    banned_prefixes = (
        "checker_types",
        "diff_",
        "checker",
        "compare",
        "finding_identity",
    )
    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.append(node.module)
    for name in imported_names:
        bare = name.lstrip(".")
        assert not bare.startswith(banned_prefixes), name
