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

"""Property tests for the pure consumer-requirements evaluation (ADR-005).

Facts are built directly -- no binary is read -- and every expectation is
computed with plain set arithmetic, independent of the implementation.
"""

from __future__ import annotations

from pathlib import Path

from hypothesis import given, strategies as st

from abicheck.checker_types import DiffResult
from abicheck.model.availability import FactStatus
from abicheck.model.change import Change
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.consumer_requirements import (
    AppRequirements,
    ConsumerImportFacts,
    LibraryExportFacts,
)
from abicheck.policy.classification import Verdict
from abicheck.policy.consumer_requirements import (
    evaluate_consumer_requirements,
    missing_app_versions,
    resolve_pe_ordinal_imports,
    symbol_coverage,
    uncovered_missing_symbols,
)
from abicheck.workflows.consumer_scope import (
    check_against_facts,
    scope_diff_to_consumer_facts,
)

_names = st.frozensets(
    st.text(alphabet="abcdefgh_", min_size=1, max_size=6), max_size=12
)


def _consumer(symbols, fmt="pe", versions=None) -> ConsumerImportFacts:
    return ConsumerImportFacts(
        path=Path("app"),
        binary_format=fmt,
        target_library="libfoo",
        requirements=AppRequirements(
            needed_libs=["libfoo"],
            undefined_symbols=set(symbols),
            required_versions=dict(versions or {}),
        ),
    )


def _library(exports, fmt="pe", versions=None, label="lib") -> LibraryExportFacts:
    return LibraryExportFacts(
        label=label,
        binary_format=fmt,
        soname="libfoo",
        export_names=frozenset(exports),
        versions_defined=None if versions is None else frozenset(versions),
    )


def _removed(symbol: str) -> Change:
    return Change(
        kind=ChangeKind.FUNC_REMOVED, symbol=symbol, description=f"{symbol} removed"
    )


def _diff(changes=()) -> DiffResult:
    return DiffResult(
        old_version="1", new_version="2", library="libfoo", changes=list(changes)
    )


class TestRequirementsVersusExports:
    @given(required=_names, exports=_names)
    def test_missing_is_exactly_required_minus_exports(self, required, exports):
        evaluation = evaluate_consumer_requirements(
            _consumer(required), _library(required), _library(exports), []
        )
        assert evaluation.missing_symbols == sorted(required - exports)
        assert evaluation.required_count == len(required)

    @given(required=_names, extra=_names)
    def test_superset_of_exports_has_nothing_missing(self, required, extra):
        evaluation = evaluate_consumer_requirements(
            _consumer(required), _library(required), _library(required | extra), []
        )
        assert evaluation.missing_symbols == []
        assert evaluation.coverage == 100.0

    @given(required=_names, exports=_names)
    def test_coverage_is_share_of_required_still_exported(self, required, exports):
        evaluation = evaluate_consumer_requirements(
            _consumer(required), _library(required), _library(exports), []
        )
        if not exports:
            expected = 0.0 if required else 100.0
        elif not required:
            expected = 100.0
        else:
            expected = 100.0 * len(required & exports) / len(required)
        assert abs(evaluation.coverage - expected) < 1e-9

    @given(required=_names, defined=_names)
    def test_missing_versions_is_required_minus_defined(self, required, defined):
        reqs = AppRequirements(required_versions={v: "libfoo" for v in required})
        lib = _library((), fmt="elf", versions=defined)
        assert set(missing_app_versions(reqs, lib)) == required - defined
        # A library with no ELF version table makes no version claim at all.
        assert missing_app_versions(reqs, _library(())) == []

    def test_symbol_coverage_empty_exports_is_no_evidence(self):
        assert symbol_coverage(frozenset(), 3, 0) == 0.0
        assert symbol_coverage(frozenset(), 0, 0) == 100.0


class TestOverlayFindings:
    """Through the workflow: each uncovered missing symbol -> one finding."""

    @given(required=_names, exports=_names, covered=_names)
    def test_each_uncovered_missing_symbol_yields_exactly_one_finding(
        self, required, exports, covered
    ):
        missing = required - exports
        covered_missing = covered & missing
        removals = [_removed(s) for s in sorted(covered_missing)]
        result = scope_diff_to_consumer_facts(
            _diff(removals), _consumer(required), _library(required), _library(exports)
        )
        overlays = [
            c
            for c in result.breaking_for_app
            if c.kind == ChangeKind.CONSUMER_REQUIRED_SYMBOL_REMOVED
        ]
        assert sorted(c.symbol for c in overlays) == sorted(missing - covered_missing)
        assert result.missing_symbols == sorted(missing)
        assert (result.verdict == Verdict.BREAKING) == bool(missing)

    @given(required=_names, extra=_names)
    def test_superset_of_exports_yields_no_finding(self, required, extra):
        result = scope_diff_to_consumer_facts(
            _diff(), _consumer(required), _library(required), _library(required | extra)
        )
        assert result.breaking_for_app == []
        assert result.missing_symbols == []
        assert result.verdict == (Verdict.COMPATIBLE if required else Verdict.NO_CHANGE)

    @given(required=_names, exports=_names)
    def test_check_against_breaks_iff_something_is_missing(self, required, exports):
        result = check_against_facts(_consumer(required), _library(exports))
        assert result.missing_symbols == sorted(required - exports)
        assert (result.verdict == Verdict.BREAKING) == bool(required - exports)


class TestUncoveredMissingSymbols:
    @given(missing=_names, covered=_names)
    def test_removes_exactly_the_symbols_a_change_names(self, missing, covered):
        changes = [_removed(s) for s in covered]
        assert set(uncovered_missing_symbols(sorted(missing), changes)) == (
            missing - covered
        )


class TestElfScoping:
    def test_elf_requirements_narrow_to_old_library_exports(self):
        consumer = _consumer({"foo@LIB_1", "bar", "unrelated"}, fmt="elf")
        old = LibraryExportFacts(
            label="old",
            binary_format="elf",
            soname="libfoo",
            export_names=frozenset({"foo", "bar"}),
            unversioned_exports=frozenset({"foo", "bar"}),
        )
        evaluation = evaluate_consumer_requirements(
            consumer, old, _library({"foo"}, fmt="elf"), []
        )
        assert evaluation.requirements.undefined_symbols == {"foo", "bar"}
        assert evaluation.missing_symbols == ["bar"]

    def test_non_elf_consumer_is_not_narrowed(self):
        consumer = _consumer({"a", "b"}, fmt="pe")
        evaluation = evaluate_consumer_requirements(
            consumer, _library({"a"}), _library({"a", "b"}), []
        )
        assert evaluation.requirements.undefined_symbols == {"a", "b"}


class TestPeOrdinals:
    def _pe(self, table) -> LibraryExportFacts:
        return LibraryExportFacts(
            label="dll",
            binary_format="pe",
            soname="foo.dll",
            export_names=frozenset(n for n in table.values() if n),
            exports_by_ordinal=table,
        )

    @given(
        old=st.dictionaries(st.integers(1, 20), st.sampled_from(["f", "g", ""])),
        new=st.dictionaries(st.integers(1, 20), st.sampled_from(["f", "g", ""])),
        wanted=st.frozensets(st.integers(1, 20)),
    )
    def test_ordinal_resolution_matches_both_tables(self, old, new, wanted):
        reqs = AppRequirements(undefined_symbols={f"ordinal:{n}" for n in wanted})
        resolved, retargeted, _ = resolve_pe_ordinal_imports(
            reqs, self._pe(old), self._pe(new)
        )
        both = {n for n in wanted if n in old and n in new}
        assert resolved == {f"ordinal:{n}" for n in both}
        assert sorted(c.symbol for c in retargeted) == sorted(
            f"ordinal:{n}" for n in both if old[n] != new[n]
        )
        assert all(c.kind == ChangeKind.PE_ORDINAL_RETARGETED for c in retargeted)


class TestFailedFacts:
    def test_failed_consumer_fact_is_explicit(self):
        fact = ConsumerImportFacts(
            path=Path("x"),
            binary_format=None,
            target_library="libfoo",
            requirements=AppRequirements(),
            status=FactStatus.FAILED,
            failure_reason="Cannot detect binary format",
        )
        assert not fact.is_readable
        assert fact.status is FactStatus.FAILED
