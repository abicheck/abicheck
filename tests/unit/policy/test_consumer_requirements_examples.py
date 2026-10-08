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

"""Fixed-example tests for :mod:`abicheck.policy.consumer_requirements`.

The property tests live in ``test_consumer_requirements.py``; these pin the
individual relevance rules, the RESOLVED verdict exclusion, ELF scoping and
PE-ordinal resolution with hand-built facts (no I/O).
"""

from __future__ import annotations

from pathlib import Path

from abicheck.checker_policy import CrossSourceEvolution
from abicheck.model.change import Change
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.consumer_requirements import (
    AppRequirements,
    ConsumerImportFacts,
    LibraryExportFacts,
)
from abicheck.policy.classification import Verdict
from abicheck.policy.consumer_requirements import (
    appcompat_verdict,
    is_relevant_to_app,
    missing_app_versions,
    partition_app_changes,
    resolve_pe_ordinal_imports,
    scope_requirements_to_library,
    symbol_coverage,
    uncovered_missing_symbols,
)


def _change(kind, symbol, **kw):
    return Change(kind=kind, symbol=symbol, description=f"{kind.value}: {symbol}", **kw)


class TestIsRelevantToApp:
    def _app(self, symbols=None, versions=None, needed_libs=None):
        return AppRequirements(
            needed_libs=needed_libs or [],
            undefined_symbols=symbols or {"foo_init", "foo_process", "foo_cleanup"},
            required_versions=versions or {},
        )

    def test_direct_symbol_match(self):
        assert is_relevant_to_app(
            _change(ChangeKind.FUNC_REMOVED, "foo_init"), self._app()
        )

    def test_no_match(self):
        assert not is_relevant_to_app(
            _change(ChangeKind.FUNC_REMOVED, "bar_init"), self._app()
        )

    def test_affected_symbols_match(self):
        change = _change(
            ChangeKind.TYPE_SIZE_CHANGED,
            "Config",
            affected_symbols=["foo_init", "bar_init"],
        )
        assert is_relevant_to_app(change, self._app())

    def test_affected_symbols_no_match(self):
        change = _change(
            ChangeKind.TYPE_SIZE_CHANGED,
            "Config",
            affected_symbols=["bar_init", "baz_init"],
        )
        assert not is_relevant_to_app(change, self._app())

    def test_soname_changed_relevant_when_app_needs_old_soname(self):
        change = _change(
            ChangeKind.SONAME_CHANGED,
            "",
            old_value="libfoo.so.1",
            new_value="libfoo.so.2",
        )
        assert is_relevant_to_app(change, self._app(needed_libs=["libfoo.so.1"]))

    def test_soname_changed_not_relevant_without_old_needed_soname(self):
        change = _change(
            ChangeKind.SONAME_CHANGED,
            "",
            old_value="libfoo.so.1",
            new_value="libfoo.so.2",
        )
        assert not is_relevant_to_app(change, self._app(needed_libs=["libbar.so.1"]))

    def test_compat_version_changed_always_relevant(self):
        assert is_relevant_to_app(
            _change(ChangeKind.COMPAT_VERSION_CHANGED, ""), self._app()
        )

    def test_symbol_version_removed_relevant(self):
        change = _change(
            ChangeKind.SYMBOL_VERSION_DEFINED_REMOVED, "FOO_1.0", old_value="FOO_1.0"
        )
        assert is_relevant_to_app(
            change, self._app(versions={"FOO_1.0": "libfoo.so.1"})
        )

    def test_symbol_version_removed_different_version(self):
        change = _change(
            ChangeKind.SYMBOL_VERSION_DEFINED_REMOVED, "FOO_2.0", old_value="FOO_2.0"
        )
        assert not is_relevant_to_app(
            change, self._app(versions={"FOO_1.0": "libfoo.so.1"})
        )

    def test_mixed_changes_partitioned(self):
        app = AppRequirements(undefined_symbols={"foo_init", "foo_process"})
        changes = [
            _change(ChangeKind.FUNC_REMOVED, "foo_init"),
            _change(ChangeKind.FUNC_REMOVED, "bar_init"),
            _change(ChangeKind.FUNC_ADDED, "baz_new"),
            _change(
                ChangeKind.TYPE_SIZE_CHANGED, "Config", affected_symbols=["foo_process"]
            ),
            _change(
                ChangeKind.TYPE_SIZE_CHANGED,
                "Internal",
                affected_symbols=["bar_helper"],
            ),
        ]
        relevant, irrelevant = partition_app_changes(changes, app)
        assert [c.symbol for c in relevant] == ["foo_init", "Config"]
        assert [c.symbol for c in irrelevant] == ["bar_init", "baz_new", "Internal"]


class TestUncoveredMissingSymbols:
    """A missing symbol that already has a matching scoped Change must not be
    counted as a second, separate ABI break (Codex review)."""

    def test_covered_by_matching_change_is_excluded(self):
        change = _change(ChangeKind.FUNC_REMOVED, "foo")
        assert uncovered_missing_symbols(["foo"], [change]) == []

    def test_no_matching_change_is_uncovered(self):
        change = _change(ChangeKind.FUNC_REMOVED, "bar")
        assert uncovered_missing_symbols(["foo"], [change]) == ["foo"]

    def test_covered_via_demangled_name(self, monkeypatch):
        import abicheck.demangle as demangle_mod

        monkeypatch.setattr(
            demangle_mod, "demangle", lambda s: "foo()" if s == "_Z3foov" else s
        )
        change = _change(ChangeKind.FUNC_REMOVED, "_Z3foov")
        assert uncovered_missing_symbols(["foo()"], [change]) == []

    def test_covered_via_affected_symbols(self):
        change = _change(
            ChangeKind.TYPE_SIZE_CHANGED, "Config", affected_symbols=["foo_init"]
        )
        assert uncovered_missing_symbols(["foo_init"], [change]) == []

    def test_no_relevant_changes_all_missing_are_uncovered(self):
        assert uncovered_missing_symbols(["foo", "bar"], []) == ["foo", "bar"]

    def test_empty_missing_returns_empty(self):
        change = _change(ChangeKind.FUNC_REMOVED, "foo")
        assert uncovered_missing_symbols([], [change]) == []


class TestAppcompatVerdictExcludesResolved:
    """Codex review, PR #1172, round 20: a ``RESOLVED`` cross-source finding
    stays in the relevant list for display/audit but must not score the
    consumer-scoped verdict."""

    def _resolved(self):
        return _change(
            ChangeKind.PRIVATE_HEADER_LEAK,
            "foo_internal",
            cross_source_evolution=CrossSourceEvolution.RESOLVED,
        )

    def _verdict(self, relevant, required_count=3, missing=(), versions=()):
        return appcompat_verdict(
            list(missing), list(versions), relevant, required_count, "strict", None
        )

    def test_resolved_only_finding_yields_compatible_not_breaking(self):
        assert self._verdict([self._resolved()]) == Verdict.COMPATIBLE

    def test_resolved_finding_does_not_mask_a_real_break(self):
        relevant = [self._resolved(), _change(ChangeKind.FUNC_REMOVED, "foo_init")]
        assert self._verdict(relevant) != Verdict.COMPATIBLE

    def test_no_resolved_state_still_gates_normally(self):
        relevant = [_change(ChangeKind.FUNC_REMOVED, "foo_init")]
        assert self._verdict(relevant) != Verdict.COMPATIBLE

    def test_empty_breaking_list_with_zero_required_is_no_change(self):
        assert self._verdict([], required_count=0) == Verdict.NO_CHANGE

    def test_missing_symbols_or_versions_force_breaking(self):
        assert self._verdict([], missing=["foo"]) == Verdict.BREAKING
        assert self._verdict([], versions=["FOO_1.0"]) == Verdict.BREAKING

    def test_policy_file_decides_when_given(self):
        class _PolicyFile:
            calls = 0

            def compute_verdict(self, changes):
                type(self).calls += 1
                return Verdict.COMPATIBLE_WITH_RISK

        pf = _PolicyFile()
        relevant = [_change(ChangeKind.FUNC_REMOVED, "foo_init")]
        verdict = appcompat_verdict([], [], relevant, 1, "strict_abi", pf)
        assert verdict == Verdict.COMPATIBLE_WITH_RISK
        assert _PolicyFile.calls == 1


def _elf_consumer(symbols):
    return ConsumerImportFacts(
        path=Path("app"),
        binary_format="elf",
        target_library="libz.so.1",
        requirements=AppRequirements(undefined_symbols=set(symbols)),
    )


def _elf_lib(unversioned, label="old.so"):
    return LibraryExportFacts(
        label=label,
        binary_format="elf",
        soname="libz.so.1",
        export_names=frozenset(unversioned),
        unversioned_exports=frozenset(unversioned),
        versions_defined=frozenset(),
    )


class TestScopeRequirementsToLibrary:
    def test_elf_scopes_symbols_to_old_lib_exports(self):
        """Symbols not exported by the target old DSO are ignored."""
        reqs = scope_requirements_to_library(
            _elf_consumer({"inflate", "XML_Parse"}), _elf_lib({"inflate"})
        )
        assert reqs.undefined_symbols == {"inflate"}

    def test_elf_versioned_symbol_names_are_normalized_for_scoping(self):
        reqs = scope_requirements_to_library(
            _elf_consumer({"inflate@@ZLIB_1.2.0"}), _elf_lib({"inflate"})
        )
        assert reqs.undefined_symbols == {"inflate"}

    def test_empty_old_exports_skip_scoping(self):
        """No parsed exports for the old library (was the parse-failure case
        of ``_get_old_lib_exports_for_scoping``): requirements are not
        narrowed to nothing."""
        reqs = scope_requirements_to_library(
            _elf_consumer({"inflate", "XML_Parse"}), _elf_lib(set())
        )
        assert reqs.undefined_symbols == {"inflate", "XML_Parse"}

    def test_input_requirements_are_not_mutated(self):
        consumer = _elf_consumer({"inflate", "XML_Parse"})
        scope_requirements_to_library(consumer, _elf_lib({"inflate"}))
        assert consumer.requirements.undefined_symbols == {"inflate", "XML_Parse"}


class TestVersionsAndCoverage:
    def test_missing_versions(self):
        reqs = AppRequirements(required_versions={"FOO_1.0": "libfoo.so"})
        lib = LibraryExportFacts(
            label="new",
            binary_format="elf",
            soname="libfoo.so",
            export_names=frozenset({"foo_init"}),
            versions_defined=frozenset({"FOO_2.0"}),
        )
        assert missing_app_versions(reqs, lib) == ["FOO_1.0"]

    def test_coverage_examples(self):
        assert symbol_coverage({"foo_init"}, 2, 1) == 50.0
        assert symbol_coverage({"foo_init"}, 0, 0) == 100.0
        assert symbol_coverage(set(), 1, 1) == 0.0


def _pe(table):
    if table is None:
        return LibraryExportFacts(
            label="foo.dll",
            binary_format=None,
            soname="foo.dll",
            export_names=frozenset(),
        )
    return LibraryExportFacts(
        label="foo.dll",
        binary_format="pe",
        soname="foo.dll",
        export_names=frozenset(n for n in table.values() if n),
        exports_by_ordinal=table,
    )


class TestResolvePeOrdinalImports:
    def _run(self, old, new, symbols):
        return resolve_pe_ordinal_imports(
            AppRequirements(undefined_symbols=set(symbols)), _pe(old), _pe(new)
        )

    def test_no_ordinal_requirements_short_circuits(self):
        assert self._run({1: "Foo"}, {1: "Foo"}, {"Foo"}) == (set(), [], set())

    def test_new_side_missing_pe_evidence_returns_empty(self):
        assert self._run({1: "Foo"}, None, {"ordinal:1"}) == (set(), [], set())

    def test_old_side_missing_pe_evidence_returns_empty(self):
        """Replaces the old ``_lib_pe_meta``-raises case: no ordinal table on
        a side means nothing is resolved."""
        assert self._run(None, {1: "Foo"}, {"ordinal:1"}) == (set(), [], set())

    def test_ordinal_resolves_to_same_name_not_retargeted(self):
        resolved, retargeted, names = self._run({1: "Foo"}, {1: "Foo"}, {"ordinal:1"})
        assert resolved == {"ordinal:1"}
        assert retargeted == []
        assert names == {"Foo"}

    def test_ordinal_retargeted_to_different_name(self):
        resolved, retargeted, names = self._run({1: "Foo"}, {1: "Bar"}, {"ordinal:1"})
        assert resolved == {"ordinal:1"}
        assert [c.kind for c in retargeted] == [ChangeKind.PE_ORDINAL_RETARGETED]
        assert names == {"Foo", "Bar"}

    def test_malformed_ordinal_requirement_is_skipped(self):
        assert self._run({1: "Foo"}, {1: "Foo"}, {"ordinal:abc"}) == (set(), [], set())

    def test_empty_export_names_are_not_added(self):
        resolved, _, names = self._run({1: ""}, {1: ""}, {"ordinal:1"})
        assert resolved == {"ordinal:1"}
        assert names == set()
