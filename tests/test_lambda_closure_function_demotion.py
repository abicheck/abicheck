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

"""``demote_lambda_closure_unexported_findings``: a function-level finding
whose subject is a template instantiated over a local lambda closure type,
and whose reported symbol is confirmed absent from BOTH binaries' real
exported symbol table, is demoted (never removed) via the ADR-025
``effective_verdict``/``modulation_reason``/``modulation_rule`` hook --
mirroring ``diff_versioning.demote_internal_version_node_findings``.

A genuinely-exported symbol of the identical shape must stay untouched. A
castxml-synthesized ctor/dtor key (never itself a real export by
construction) is handled differently: its OWNING class/class-template is
checked for export under any instantiation at all (see
``finding_identity_ctor_dtor.synthetic_ctor_dtor_template_base_name``/
``itanium_source_name_token``), since the exact per-instantiation mangling
embedding a closure's own compiler-internal unnamed-type encoding can't be
reconstructed from the snapshot text -- see AGENTS.md's "Lambda-closure
churn survives at the function level" entry, item 1, for the investigation
this closes.
"""

from __future__ import annotations

from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.diff_templates import demote_lambda_closure_unexported_findings
from abicheck.elf_metadata import ElfMetadata, ElfSymbol, SymbolType
from abicheck.model import AbiSnapshot, Function, Param, Visibility

_OLD_PARAM = "raii_guard<(lambda:task_group.h:100:5)>"
_NEW_PARAM = "raii_guard<(lambda:task_group.h:522:26)>"
# Deliberately not a real Itanium mangling and deliberately not in either
# side's exported symbol table -- see below.
_MANGLED = "_Z7processDoesNotMatchAnyRealDynsymEntry"


def _fn(mangled: str, ptype: str, name: str = "process") -> Function:
    return Function(
        name=name,
        mangled=mangled,
        return_type="void",
        params=[Param(name="g", type=ptype)],
        visibility=Visibility.PUBLIC,
    )


def _elf(*names: str) -> ElfMetadata:
    return ElfMetadata(symbols=[ElfSymbol(name=n, sym_type=SymbolType.FUNC) for n in names])


def _snap(version: str, fn: Function, elf: ElfMetadata | None) -> AbiSnapshot:
    return AbiSnapshot(library="lib.so", version=version, functions=[fn], elf=elf)


class TestDemotesWhenConfirmedAbsentFromBothExportTables:
    def test_primitive_sets_the_modulation_fields(self) -> None:
        # Neither the finding's own `symbol` (the guessed mangling) NOR the
        # function's real display name ("process") appears anywhere in
        # either side's exported symbol table -- genuinely, unambiguously
        # never exported under any accepted spelling.
        from abicheck.checker_types import Change

        change = Change(
            kind=ChangeKind.TEMPLATE_PARAM_TYPE_CHANGED,
            symbol=_MANGLED,
            description="d",
            old_value=_OLD_PARAM,
            new_value=_NEW_PARAM,
        )
        old = _snap("1", _fn(_MANGLED, _OLD_PARAM), _elf("unrelated_symbol"))
        new = _snap("2", _fn(_MANGLED, _NEW_PARAM), _elf("unrelated_symbol"))

        demote_lambda_closure_unexported_findings([change], old, new)

        assert change.effective_verdict is Verdict.COMPATIBLE_WITH_RISK
        assert change.modulation_rule == "lambda_closure_never_exported"
        assert "task_group.h" not in change.modulation_reason


class TestGenuinelyExportedSymbolStaysBreaking:
    def test_symbol_exported_on_old_side_is_not_demoted(self) -> None:
        from abicheck.checker_types import Change

        change = Change(
            kind=ChangeKind.TEMPLATE_PARAM_TYPE_CHANGED,
            symbol=_MANGLED,
            description="d",
            old_value=_OLD_PARAM,
            new_value=_NEW_PARAM,
        )
        # The exact mangled symbol IS a real export on the old side -- an
        # already-linked consumer could really have resolved it.
        old = _snap("1", _fn(_MANGLED, _OLD_PARAM), _elf(_MANGLED))
        new = _snap("2", _fn(_MANGLED, _NEW_PARAM), _elf("process"))

        demote_lambda_closure_unexported_findings([change], old, new)

        assert change.effective_verdict is None
        assert change.modulation_rule is None

    def test_no_elf_evidence_at_all_is_not_demoted(self) -> None:
        """Fail closed: without a real dynsym table on BOTH sides, "not
        found" cannot be told apart from "we never checked"."""
        from abicheck.checker_types import Change

        change = Change(
            kind=ChangeKind.TEMPLATE_PARAM_TYPE_CHANGED,
            symbol=_MANGLED,
            description="d",
            old_value=_OLD_PARAM,
            new_value=_NEW_PARAM,
        )
        old = _snap("1", _fn(_MANGLED, _OLD_PARAM), None)
        new = _snap("2", _fn(_MANGLED, _NEW_PARAM), None)

        demote_lambda_closure_unexported_findings([change], old, new)

        assert change.effective_verdict is None

    def test_symbol_exported_only_under_its_export_alias_is_not_demoted(self) -> None:
        """Codex review, fresh evidence: `change.symbol` can be a guessed
        mangling from `_public_functions`' own bare-name export fallback --
        the function is retained because its real display name ("process")
        is exported, even though the *dict key*/`change.symbol` itself
        (here, a mangling that matches no real dynsym entry) never is.
        Checking `symbol` alone would wrongly call this "confirmed absent";
        an already-linked consumer could still have resolved the exported
        "process" spelling, so the finding must stay exactly as severe as
        the detector made it.
        """
        from abicheck.checker_types import Change

        change = Change(
            kind=ChangeKind.TEMPLATE_PARAM_TYPE_CHANGED,
            symbol=_MANGLED,
            description="d",
            old_value=_OLD_PARAM,
            new_value=_NEW_PARAM,
        )
        old = _snap("1", _fn(_MANGLED, _OLD_PARAM), _elf("process"))
        new = _snap("2", _fn(_MANGLED, _NEW_PARAM), _elf("process"))

        demote_lambda_closure_unexported_findings([change], old, new)

        assert change.effective_verdict is None
        assert change.modulation_rule is None

    def test_symbol_exported_only_under_its_export_alias_reported_through_compare(
        self,
    ) -> None:
        """The same scenario as above, exercised through the real detection
        pipeline rather than a hand-built ``Change`` -- proving the fix
        holds at the public ``compare()`` surface, not only against the
        primitive in isolation."""
        old = _snap("1", _fn(_MANGLED, _OLD_PARAM), _elf("process"))
        new = _snap("2", _fn(_MANGLED, _NEW_PARAM), _elf("process"))
        result = compare(old, new)

        lambda_kinds = {
            ChangeKind.FUNC_PARAMS_CHANGED,
            ChangeKind.TEMPLATE_PARAM_TYPE_CHANGED,
        }
        touched = [c for c in result.changes if c.kind in lambda_kinds]
        assert touched, "expected at least one lambda-closure param finding"
        for c in touched:
            assert c.effective_verdict is None
            assert c.modulation_rule is None


class TestSyntheticCtorDtorKeysDemotedWhenTemplateNeverExported:
    """A castxml-synthesized ctor/dtor key can never equal a real exported
    symbol by construction (see ``dumper_castxml._function_mangled_name``),
    so the OWNING class/class-template is checked instead: if it has zero
    exported members under ANY instantiation on either side, no consumer
    could ever have linked against this instantiation's ctor/dtor either --
    the exact real-world shape reported for oneTBB's
    ``tbb::detail::raii_guard``/``try_call_proxy``/``delegated_function``/
    ``task_arena_function`` (all confirmed, via ``nm -D --defined-only``,
    to export zero symbols under any instantiation)."""

    def test_synthetic_dtor_key_is_demoted_when_template_never_exported(self) -> None:
        from abicheck.checker_types import Change

        symbol = f"~raii_guard<{_OLD_PARAM[len('raii_guard'):]}"  # "~raii_guard<(lambda:...)>"
        change = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol=symbol,
            description="d",
            old_value="~raii_guard",
        )
        old = _snap("1", _fn(_MANGLED, _OLD_PARAM), _elf("unrelated_symbol"))
        new = _snap("2", _fn(_MANGLED, _NEW_PARAM), _elf("unrelated_symbol"))

        demote_lambda_closure_unexported_findings([change], old, new)

        assert change.effective_verdict is Verdict.COMPATIBLE_WITH_RISK
        assert change.modulation_rule == "lambda_closure_never_exported"

    def test_synthetic_ctor_key_is_demoted_when_template_never_exported(self) -> None:
        from abicheck.checker_types import Change

        symbol = f"__abicheck_ctor__raii_guard<{_OLD_PARAM[len('raii_guard<') :]}()"
        change = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol=symbol,
            description="d",
            old_value="raii_guard",
        )
        old = _snap("1", _fn(_MANGLED, _OLD_PARAM), _elf("unrelated_symbol"))
        new = _snap("2", _fn(_MANGLED, _NEW_PARAM), _elf("unrelated_symbol"))

        demote_lambda_closure_unexported_findings([change], old, new)

        assert change.effective_verdict is Verdict.COMPATIBLE_WITH_RISK
        assert change.modulation_rule == "lambda_closure_never_exported"


class TestSyntheticCtorDtorKeysNotDemotedWhenTemplateIsExported:
    """The owning class/class-template genuinely exports SOME instantiation
    on at least one side -- this check cannot rule out that the specific
    closure-parameterized instantiation was the one a consumer actually
    linked against, so the finding must stay exactly as severe as the
    detector made it (fails closed)."""

    def test_synthetic_ctor_key_untouched_when_another_instantiation_is_exported(
        self,
    ) -> None:
        from abicheck.checker_types import Change

        symbol = f"__abicheck_ctor__raii_guard<{_OLD_PARAM[len('raii_guard<') :]}()"
        change = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol=symbol,
            description="d",
            old_value="raii_guard",
        )
        # A real exported ctor of a DIFFERENT raii_guard<...> instantiation
        # (raii_guard<int>) -- the Itanium <source-name> encoding
        # "10raii_guard" embedded in it is exactly what this check searches
        # for, so this proves the class template does have linkable
        # consumers under some instantiation.
        other_instantiation_ctor = "_ZN3tbb6detail10raii_guardIiEC1Ev"
        old = _snap("1", _fn(_MANGLED, _OLD_PARAM), _elf(other_instantiation_ctor))
        new = _snap("2", _fn(_MANGLED, _NEW_PARAM), _elf(other_instantiation_ctor))

        demote_lambda_closure_unexported_findings([change], old, new)

        assert change.effective_verdict is None
        assert change.modulation_rule is None

    def test_synthetic_dtor_key_untouched_when_another_instantiation_is_exported(
        self,
    ) -> None:
        from abicheck.checker_types import Change

        symbol = f"~raii_guard<{_OLD_PARAM[len('raii_guard'):]}"
        change = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol=symbol,
            description="d",
            old_value="~raii_guard",
        )
        other_instantiation_dtor = "_ZN3tbb6detail10raii_guardIiED1Ev"
        old = _snap("1", _fn(_MANGLED, _OLD_PARAM), _elf(other_instantiation_dtor))
        new = _snap("2", _fn(_MANGLED, _NEW_PARAM), _elf(other_instantiation_dtor))

        demote_lambda_closure_unexported_findings([change], old, new)

        assert change.effective_verdict is None
        assert change.modulation_rule is None

    def test_malformed_synthetic_ctor_key_scope_unrecoverable_is_not_demoted(
        self,
    ) -> None:
        """``synthetic_ctor_scope`` returns ``None`` for a key with no
        recoverable ``(params)`` suffix -- fails closed, same as any other
        evidence gap in this function."""
        from abicheck.checker_types import Change
        from abicheck.dumper_castxml import SYNTHETIC_CTOR_KEY_PREFIX

        symbol = f"{SYNTHETIC_CTOR_KEY_PREFIX}Foo"  # missing "(params)" suffix
        change = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol=symbol,
            description="d",
            old_value=_OLD_PARAM,
        )
        old = _snap("1", _fn(_MANGLED, _OLD_PARAM), _elf("unrelated_symbol"))
        new = _snap("2", _fn(_MANGLED, _NEW_PARAM), _elf("unrelated_symbol"))

        demote_lambda_closure_unexported_findings([change], old, new)

        assert change.effective_verdict is None
        assert change.modulation_rule is None


class TestSyntheticDtorKeyReportedThroughComparePipeline:
    """The exact real-world shape reported for oneTBB: an unrelated edit
    shifts a lambda's source line, so the destructor's castxml-synthesized
    key changes spelling between old and new -- `compare()`'s own function
    matching (keyed by this same synthetic identity) reports a
    FUNC_REMOVED/FUNC_ADDED pair, not a rename. Exercised through the real
    detection pipeline, not only against the demotion primitive in
    isolation, proving the fix reaches the shape `compare()` actually
    produces."""

    def test_removal_demoted_when_owning_template_never_exported(self) -> None:
        from abicheck.dumper_castxml import _SYNTHETIC_DTOR_KEY_PREFIX

        old_key = f"{_SYNTHETIC_DTOR_KEY_PREFIX}{_OLD_PARAM}"
        new_key = f"{_SYNTHETIC_DTOR_KEY_PREFIX}{_NEW_PARAM}"
        old_fn = Function(
            name="~raii_guard",
            mangled=old_key,
            return_type="void",
            params=[],
            visibility=Visibility.PUBLIC,
        )
        new_fn = Function(
            name="~raii_guard",
            mangled=new_key,
            return_type="void",
            params=[],
            visibility=Visibility.PUBLIC,
        )
        old = _snap("1", old_fn, _elf("unrelated_symbol"))
        new = _snap("2", new_fn, _elf("unrelated_symbol"))

        result = compare(old, new)

        removed = [
            c
            for c in result.changes
            if c.kind == ChangeKind.FUNC_REMOVED and c.symbol == old_key
        ]
        assert removed, "expected a FUNC_REMOVED for the old synthetic dtor key"
        assert removed[0].effective_verdict is Verdict.COMPATIBLE_WITH_RISK
        assert removed[0].modulation_rule == "lambda_closure_never_exported"

    def test_removal_untouched_when_owning_template_is_exported(self) -> None:
        from abicheck.dumper_castxml import _SYNTHETIC_DTOR_KEY_PREFIX

        old_key = f"{_SYNTHETIC_DTOR_KEY_PREFIX}{_OLD_PARAM}"
        new_key = f"{_SYNTHETIC_DTOR_KEY_PREFIX}{_NEW_PARAM}"
        old_fn = Function(
            name="~raii_guard",
            mangled=old_key,
            return_type="void",
            params=[],
            visibility=Visibility.PUBLIC,
        )
        new_fn = Function(
            name="~raii_guard",
            mangled=new_key,
            return_type="void",
            params=[],
            visibility=Visibility.PUBLIC,
        )
        # A real exported dtor of a DIFFERENT raii_guard<...> instantiation.
        other_instantiation_dtor = "_ZN3tbb6detail10raii_guardIiED1Ev"
        old = _snap("1", old_fn, _elf(other_instantiation_dtor))
        new = _snap("2", new_fn, _elf(other_instantiation_dtor))

        result = compare(old, new)

        removed = [
            c
            for c in result.changes
            if c.kind == ChangeKind.FUNC_REMOVED and c.symbol == old_key
        ]
        assert removed, "expected a FUNC_REMOVED for the old synthetic dtor key"
        assert removed[0].effective_verdict is None
        assert removed[0].modulation_rule is None


class TestNonLambdaFindingsAreNeverTouched:
    def test_ordinary_param_change_with_no_lambda_marker_is_untouched(self) -> None:
        from abicheck.checker_types import Change

        change = Change(
            kind=ChangeKind.TEMPLATE_PARAM_TYPE_CHANGED,
            symbol=_MANGLED,
            description="d",
            old_value="Widget<int>",
            new_value="Widget<double>",
        )
        old = _snap("1", _fn(_MANGLED, "Widget<int>"), _elf("process"))
        new = _snap("2", _fn(_MANGLED, "Widget<double>"), _elf("process"))

        demote_lambda_closure_unexported_findings([change], old, new)

        assert change.effective_verdict is None


class TestSyntheticCtorDtorTemplateBaseNamePrimitive:
    """Direct, primitive-level coverage for ``synthetic_ctor_dtor_template_
    base_name`` (per this repo's own "Primitive-level property tests"
    convention) -- the demotion pipeline only ever calls it already knowing
    *some* synthetic key is present, so these branches (non-key input, a
    scope with no template args at all, and a scope with nested template
    brackets) are otherwise unreached from that one call site."""

    def test_non_synthetic_symbol_returns_none(self) -> None:
        from abicheck.finding_identity_ctor_dtor import (
            synthetic_ctor_dtor_template_base_name,
        )

        assert synthetic_ctor_dtor_template_base_name("_ZN3Foo3barEv") is None

    def test_scope_with_no_template_args_returns_the_bare_name(self) -> None:
        from abicheck.finding_identity_ctor_dtor import (
            synthetic_ctor_dtor_template_base_name,
        )

        assert (
            synthetic_ctor_dtor_template_base_name("~ns::PlainClass") == "PlainClass"
        )

    def test_nested_template_brackets_stop_at_the_outermost_open_bracket(
        self,
    ) -> None:
        from abicheck.finding_identity_ctor_dtor import (
            synthetic_ctor_dtor_template_base_name,
        )

        assert (
            synthetic_ctor_dtor_template_base_name("~Wrapper<Inner<int>>")
            == "Wrapper"
        )


class TestItaniumSourceNameTokenUsesEncodedByteLength:
    """Itanium ``<source-name>`` length prefixes count encoded **bytes**, not
    Python characters -- a non-ASCII identifier (GCC/Clang mangle a UTF-8
    source encoding) has a byte length longer than its character count, so
    using ``len(name)`` directly would under-count and search for a token
    that never appears in the real mangled symbol, silently defeating the
    "is this template exported anywhere" check for such a name."""

    def test_multi_byte_identifier_uses_byte_length_not_char_length(self) -> None:
        from abicheck.finding_identity_ctor_dtor import itanium_source_name_token

        # "Café" is 4 Python characters but 5 UTF-8 bytes (the "é" is 2
        # bytes) -- the real Itanium encoding is "5Café", not "4Café".
        assert itanium_source_name_token("Café") == "5Café"
        assert len("Café") == 4

    def test_ascii_identifier_byte_length_equals_char_length(self) -> None:
        from abicheck.finding_identity_ctor_dtor import itanium_source_name_token

        assert itanium_source_name_token("raii_guard") == "10raii_guard"

    def test_multi_byte_class_name_is_found_when_exported(self) -> None:
        """End-to-end through the primitive: a synthetic ctor key naming a
        non-ASCII class must still be recognized as exported when the real
        mangled symbol (using the correct UTF-8 byte-length encoding) is
        present -- proving the fix, not just the helper in isolation."""
        from abicheck.checker_types import Change
        from abicheck.dumper_castxml import SYNTHETIC_CTOR_KEY_PREFIX

        symbol = f"{SYNTHETIC_CTOR_KEY_PREFIX}Café<(lambda:f.h:1:1)>()"
        change = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol=symbol,
            description="d",
            old_value="Café",
        )
        # Real Itanium mangling of Café<...>::Café() uses the byte length 5,
        # not the Python character count 4.
        real_export = "_ZN5CaféIiEC1Ev"
        old = _snap("1", _fn(_MANGLED, _OLD_PARAM), _elf(real_export))
        new = _snap("2", _fn(_MANGLED, _NEW_PARAM), _elf(real_export))

        demote_lambda_closure_unexported_findings([change], old, new)

        assert change.effective_verdict is None
        assert change.modulation_rule is None


class TestItaniumStandardSubstitutionToken:
    """Six ``std::`` names (``allocator``, ``basic_string``, ``basic_istream``,
    ``basic_ostream``, ``basic_iostream``) always mangle via a fixed Itanium
    ABI substitution (``Sa``/``Sb``/``Si``/``So``/``Sd``), never via their
    literal source-name -- a literal-token-only search would therefore always
    read such a class as "never exported" regardless of the truth."""

    def test_known_std_names_map_to_their_fixed_substitution(self) -> None:
        from abicheck.dumper_castxml import SYNTHETIC_CTOR_KEY_PREFIX
        from abicheck.finding_identity_ctor_dtor import (
            itanium_standard_substitution_token,
        )

        cases = {
            "allocator": "Sa",
            "basic_string": "Sb",
            "basic_istream": "Si",
            "basic_ostream": "So",
            "basic_iostream": "Sd",
        }
        for name, expected in cases.items():
            symbol = f"{SYNTHETIC_CTOR_KEY_PREFIX}std::{name}<(lambda:f.h:1:1)>()"
            assert itanium_standard_substitution_token(symbol) == expected

    def test_non_std_owner_returns_none(self) -> None:
        from abicheck.dumper_castxml import SYNTHETIC_CTOR_KEY_PREFIX
        from abicheck.finding_identity_ctor_dtor import (
            itanium_standard_substitution_token,
        )

        symbol = f"{SYNTHETIC_CTOR_KEY_PREFIX}raii_guard<(lambda:f.h:1:1)>()"
        assert itanium_standard_substitution_token(symbol) is None

    def test_std_name_outside_the_fixed_set_returns_none(self) -> None:
        from abicheck.dumper_castxml import SYNTHETIC_CTOR_KEY_PREFIX
        from abicheck.finding_identity_ctor_dtor import (
            itanium_standard_substitution_token,
        )

        symbol = f"{SYNTHETIC_CTOR_KEY_PREFIX}std::vector<(lambda:f.h:1:1)>()"
        assert itanium_standard_substitution_token(symbol) is None

    def test_non_synthetic_symbol_returns_none(self) -> None:
        from abicheck.finding_identity_ctor_dtor import (
            itanium_standard_substitution_token,
        )

        assert itanium_standard_substitution_token("_ZN3Foo3barEv") is None


class TestStdAllocatorSyntheticKeyNotFalselyDemoted:
    """The exact counterexample from review: a real exported
    ``std::allocator<int>::allocator()`` mangles to ``_ZNSaIiEC1Ev`` (the
    fixed ``Sa`` substitution), never to anything containing the literal
    source-name substring ``"9allocator"``. Checking only the literal token
    would read this class as "never exported" and wrongly demote a genuine
    removal to ``COMPATIBLE_WITH_RISK``."""

    def test_allocator_ctor_not_demoted_when_another_instantiation_is_exported(
        self,
    ) -> None:
        from abicheck.checker_types import Change
        from abicheck.dumper_castxml import SYNTHETIC_CTOR_KEY_PREFIX

        symbol = f"{SYNTHETIC_CTOR_KEY_PREFIX}std::allocator<(lambda:f.h:1:1)>()"
        change = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol=symbol,
            description="d",
            old_value="std::allocator",
        )
        real_export = "_ZNSaIiEC1Ev"  # std::allocator<int>::allocator()
        old = _snap("1", _fn(_MANGLED, _OLD_PARAM), _elf(real_export))
        new = _snap("2", _fn(_MANGLED, _NEW_PARAM), _elf(real_export))

        demote_lambda_closure_unexported_findings([change], old, new)

        assert change.effective_verdict is None
        assert change.modulation_rule is None

    def test_allocator_ctor_still_demoted_when_genuinely_unexported(self) -> None:
        from abicheck.checker_types import Change
        from abicheck.dumper_castxml import SYNTHETIC_CTOR_KEY_PREFIX

        symbol = f"{SYNTHETIC_CTOR_KEY_PREFIX}std::allocator<(lambda:f.h:1:1)>()"
        change = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol=symbol,
            description="d",
            old_value="std::allocator",
        )
        old = _snap("1", _fn(_MANGLED, _OLD_PARAM), _elf("unrelated_symbol"))
        new = _snap("2", _fn(_MANGLED, _NEW_PARAM), _elf("unrelated_symbol"))

        demote_lambda_closure_unexported_findings([change], old, new)

        assert change.effective_verdict is Verdict.COMPATIBLE_WITH_RISK
        assert change.modulation_rule == "lambda_closure_never_exported"
