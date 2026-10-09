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

"""Tests for :mod:`abicheck.workflows.consumer_scope` (ADR-005/043/044).

Consumer requirements are injected as hand-built
:class:`~abicheck.model.consumer_requirements.ConsumerImportFacts` through the
fact-level entry points (:func:`scope_diff_to_consumer_facts`,
:func:`check_against_facts`); library facts come from real ``AbiSnapshot``
objects read by :func:`read_library_export_facts`, or are built directly.
The ``ConsumerSpec`` tests drive :func:`scope_diff_to_app` end to end on real
files -- nothing in the read path is patched.
"""

from __future__ import annotations

import hashlib
import struct
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from abicheck.checker_types import DiffResult
from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.extract.library_export_facts import read_library_export_facts
from abicheck.model import AbiSnapshot
from abicheck.model.availability import FactStatus
from abicheck.model.change import Change
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.consumer_requirements import (
    AppRequirements,
    ConsumerImportFacts,
    LibraryExportFacts,
)
from abicheck.model.consumer_spec import (
    ConsumerRequirement,
    ConsumerSpec,
    ConsumerUnreadableError,
)
from abicheck.policy.classification import Verdict
from abicheck.suppression import Suppression, SuppressionList
from abicheck.workflows.consumer_scope import (
    check_against_facts,
    scope_diff_to_app,
    scope_diff_to_consumer_facts,
)


def _consumer(symbols, versions=None, path="app", fmt="elf") -> ConsumerImportFacts:
    return ConsumerImportFacts(
        path=Path(path),
        binary_format=fmt,
        target_library="libfoo.so.1",
        requirements=AppRequirements(
            undefined_symbols=set(symbols), required_versions=dict(versions or {})
        ),
    )


def _lib(exports, label="new.so", versions=None, unversioned=None):
    return LibraryExportFacts(
        label=label,
        binary_format="elf",
        soname="libfoo.so.1",
        export_names=frozenset(exports),
        unversioned_exports=None if unversioned is None else frozenset(unversioned),
        versions_defined=None if versions is None else frozenset(versions),
    )


def _diff(changes=(), library="libfoo") -> DiffResult:
    return DiffResult(
        old_version="1", new_version="2", library=library, changes=list(changes)
    )


def _removed(symbol):
    return Change(kind=ChangeKind.FUNC_REMOVED, symbol=symbol, description="removed")


def _scope(symbols, new_exports, changes=(), **kw):
    return scope_diff_to_consumer_facts(
        _diff(changes),
        _consumer(symbols, kw.pop("versions", None)),
        kw.pop("old", _lib(set(), label="old.so")),
        _lib(new_exports, versions=kw.pop("new_versions", None)),
        **kw,
    )


# ---------------------------------------------------------------------------
# Scoping verdicts (was TestCheckAppcompat, which patched the readers)
# ---------------------------------------------------------------------------


class TestScopeDiffToConsumerFacts:
    def test_compatible_no_changes(self):
        result = _scope({"foo_init", "foo_process"}, {"foo_init", "foo_process", "x"})
        assert result.verdict == Verdict.COMPATIBLE
        assert result.symbol_coverage == 100.0
        assert result.missing_symbols == []

    def test_missing_symbols_breaking(self):
        result = _scope({"foo_init", "foo_gone"}, {"foo_init"})
        assert result.verdict == Verdict.BREAKING
        assert result.missing_symbols == ["foo_gone"]
        assert result.symbol_coverage == 50.0

    def test_relevant_changes_verdict(self):
        result = _scope({"foo_init"}, {"foo_init"}, [_removed("foo_init")])
        assert len(result.breaking_for_app) == 1
        assert result.verdict == Verdict.BREAKING

    def test_irrelevant_changes_compatible(self):
        added = Change(kind=ChangeKind.FUNC_ADDED, symbol="bar_new", description="a")
        result = _scope({"foo_init"}, {"foo_init", "bar_new"}, [added])
        assert result.verdict == Verdict.COMPATIBLE
        assert result.irrelevant_for_app == [added]

    def test_no_required_symbols_no_change(self):
        assert _scope(set(), {"foo_init"}).verdict == Verdict.NO_CHANGE

    def test_no_exports_zero_coverage(self):
        assert _scope({"foo_init"}, set()).symbol_coverage == 0.0

    def test_policy_file_used_for_verdict(self):
        pf = MagicMock()
        pf.compute_verdict.return_value = Verdict.COMPATIBLE_WITH_RISK
        result = _scope(
            {"foo_init"}, {"foo_init"}, [_removed("foo_init")], policy_file=pf
        )
        assert result.verdict == Verdict.COMPATIBLE_WITH_RISK
        pf.compute_verdict.assert_called_once()

    def test_missing_versions_breaking(self):
        result = _scope(
            {"foo_init"},
            {"foo_init"},
            versions={"FOO_1.0": "libfoo.so"},
            new_versions={"FOO_2.0"},
        )
        assert result.verdict == Verdict.BREAKING
        assert result.missing_versions == ["FOO_1.0"]

    def test_elf_scopes_symbols_to_old_lib_exports(self):
        """Symbols the target old DSO never exported are ignored."""
        old = _lib({"inflate"}, label="old.so", unversioned={"inflate"})
        result = _scope({"inflate", "XML_Parse"}, {"inflate"}, old=old)
        assert result.required_symbols == {"inflate"}
        assert result.missing_symbols == []
        assert result.verdict == Verdict.COMPATIBLE

    def test_elf_versioned_symbol_names_are_normalized_for_scoping(self):
        old = _lib({"inflate"}, label="old.so", unversioned={"inflate"})
        result = _scope({"inflate@@ZLIB_1.2.0"}, {"inflate"}, old=old)
        assert result.required_symbols == {"inflate"}
        assert result.missing_symbols == []
        assert result.verdict == Verdict.COMPATIBLE

    def test_result_labels_and_ledger_attached(self):
        diff = _diff()
        result = scope_diff_to_consumer_facts(
            diff, _consumer({"a"}), _lib({"a"}, label="old.so"), _lib({"a"})
        )
        assert (result.app_path, result.old_lib_path, result.new_lib_path) == (
            "app",
            "old.so",
            "new.so",
        )
        assert result.full_diff is diff
        assert diff.disposition_ledger is not None


class TestCheckAgainstFacts:
    def test_compatible(self):
        result = check_against_facts(_consumer({"foo_init"}), _lib({"foo_init"}))
        assert result.verdict == Verdict.COMPATIBLE
        assert result.symbol_coverage == 100.0
        assert result.old_lib_path == ""

    def test_missing_symbols_breaking(self):
        result = check_against_facts(
            _consumer({"foo_init", "foo_missing"}), _lib({"foo_init"})
        )
        assert result.verdict == Verdict.BREAKING
        assert result.missing_symbols == ["foo_missing"]
        assert result.symbol_coverage == 50.0

    def test_missing_versions_breaking(self):
        result = check_against_facts(
            _consumer({"foo_init"}, versions={"FOO_1.0": "libfoo.so"}),
            _lib({"foo_init"}, versions={"FOO_2.0"}),
        )
        assert result.verdict == Verdict.BREAKING
        assert result.missing_versions == ["FOO_1.0"]

    def test_no_exports_zero_coverage(self):
        result = check_against_facts(_consumer({"foo_init"}), _lib(set()))
        assert result.symbol_coverage == 0.0

    def test_no_required_symbols(self):
        result = check_against_facts(_consumer(set()), _lib({"foo"}))
        assert result.verdict == Verdict.COMPATIBLE
        assert result.symbol_coverage == 100.0


# ---------------------------------------------------------------------------
# OLD/NEW as saved snapshots (ADR-043 follow-up) + consumer overlay (ADR-044)
# ---------------------------------------------------------------------------


def _snap(version, symbol_names, soname="libfoo.so.1") -> AbiSnapshot:
    return AbiSnapshot(
        library="libfoo.so.1",
        version=version,
        elf=ElfMetadata(
            soname=soname, symbols=[ElfSymbol(name=n) for n in symbol_names]
        ),
    )


def _scope_snapshots(symbols, old_syms, new_syms, changes=(), *, path="app", **kw):
    old, new = _snap("1.0", old_syms), _snap("2.0", new_syms)
    return scope_diff_to_consumer_facts(
        _diff(changes, library="libfoo.so.1"),
        _consumer(symbols, path=path),
        read_library_export_facts(old),
        read_library_export_facts(new),
        old_lib=old,
        **kw,
    )


def _overlays(result):
    return [
        c
        for c in result.breaking_for_app
        if c.kind == ChangeKind.CONSUMER_REQUIRED_SYMBOL_REMOVED
    ]


class TestScopeDiffWithSnapshots:
    def test_both_sides_are_snapshots_detects_missing_symbol(self):
        result = _scope_snapshots(
            {"foo_init", "foo_process"},
            ["foo_init", "foo_process"],
            ["foo_init"],
            [_removed("foo_process")],
        )
        assert result.verdict == Verdict.BREAKING
        assert result.missing_symbols == ["foo_process"]
        assert [c.symbol for c in result.breaking_for_app] == ["foo_process"]
        assert result.old_lib_path == "libfoo.so.1"
        assert result.new_lib_path == "libfoo.so.1"

    def test_old_snapshot_new_native_binary_mixed_sources(self, tmp_path):
        old = _snap("1.0", ["foo_init", "foo_process"])
        new_lib = tmp_path / "new.so"
        new_lib.write_bytes(b"\x7fELF" + b"\x00" * 100)
        meta = ElfMetadata(soname="libfoo.so.1", symbols=[ElfSymbol(name="foo_init")])
        with patch("abicheck.elf_metadata.parse_elf_metadata", return_value=meta):
            new_facts = read_library_export_facts(new_lib)
        result = scope_diff_to_consumer_facts(
            _diff(library="libfoo.so.1"),
            _consumer({"foo_init"}),
            read_library_export_facts(old),
            new_facts,
        )
        assert result.verdict == Verdict.COMPATIBLE
        assert result.missing_symbols == []
        assert result.old_lib_path == "libfoo.so.1"
        assert result.new_lib_path == str(new_lib)

    def test_snapshot_missing_binary_evidence_yields_no_exports(self):
        """A headers-only snapshot has nothing to scope against: its facts
        are FAILED and every required symbol reads as missing."""
        old = AbiSnapshot(library="libfoo.so.1", version="1.0")
        new = AbiSnapshot(library="libfoo.so.1", version="2.0")
        new_facts = read_library_export_facts(new)
        assert new_facts.status is FactStatus.FAILED
        result = scope_diff_to_consumer_facts(
            _diff(library="libfoo.so.1"),
            _consumer({"foo_init"}),
            read_library_export_facts(old),
            new_facts,
        )
        assert result.missing_symbols == ["foo_init"]
        assert result.symbol_coverage == 0.0

    def test_uncovered_missing_symbol_becomes_consumer_required_symbol_removed(self):
        """ADR-044 P2 item 1: a missing symbol with no matching library-diff
        Change is promoted to a first-class, suppressible overlay finding."""
        result = _scope_snapshots(
            {"foo_init", "foo_process"},
            ["foo_init", "foo_process"],
            ["foo_init"],
            path="myapp",
        )
        assert result.missing_symbols == ["foo_process"]
        (overlay,) = _overlays(result)
        assert overlay.symbol == "foo_process"
        assert "myapp" in overlay.description

    def test_missing_symbol_covered_by_diff_change_gets_no_overlay(self):
        result = _scope_snapshots(
            {"foo_init", "foo_process"},
            ["foo_init", "foo_process"],
            ["foo_init"],
            [_removed("foo_process")],
        )
        assert result.missing_symbols == ["foo_process"]
        assert [c.kind for c in result.breaking_for_app] == [ChangeKind.FUNC_REMOVED]

    def test_overlay_is_suppressible(self):
        """A suppressed overlay also drops out of missing_symbols (which
        independently forces BREAKING); coverage stays factual."""
        result = _scope_snapshots(
            {"foo_init", "foo_process"},
            ["foo_init", "foo_process"],
            ["foo_init"],
            suppression=SuppressionList([Suppression(symbol="foo_process")]),
        )
        assert result.missing_symbols == []
        assert result.breaking_for_app == []
        assert result.verdict == Verdict.COMPATIBLE
        assert result.symbol_coverage < 100.0

    def test_overlay_survives_unmatched_suppression(self):
        result = _scope_snapshots(
            {"foo_init", "foo_process"},
            ["foo_init", "foo_process"],
            ["foo_init"],
            suppression=SuppressionList([Suppression(symbol="unrelated_symbol")]),
        )
        assert [c.kind for c in result.breaking_for_app] == [
            ChangeKind.CONSUMER_REQUIRED_SYMBOL_REMOVED
        ]

    def test_overlay_not_hidden_by_broad_namespace_rule(self):
        """The overlay is consumer-proven reachable, so a broad rule's default
        unreachable-only reachability must not hide it."""
        result = _scope_snapshots(
            {"foo_init", "ns::detail::foo"},
            ["foo_init", "ns::detail::foo"],
            ["foo_init"],
            suppression=SuppressionList(
                [Suppression(namespace="ns::detail::**", reason="detail churn")]
            ),
        )
        assert result.verdict == Verdict.BREAKING
        assert result.missing_symbols == ["ns::detail::foo"]
        kinds = [c.kind for c in result.breaking_for_app]
        assert ChangeKind.CONSUMER_REQUIRED_SYMBOL_REMOVED in kinds
        assert ChangeKind.SUPPRESSION_WOULD_HIDE_PUBLIC_BREAK in kinds

    def test_overlay_broad_rule_applies_with_override(self):
        result = _scope_snapshots(
            {"foo_init", "ns::detail::foo"},
            ["foo_init", "ns::detail::foo"],
            ["foo_init"],
            suppression=SuppressionList(
                [
                    Suppression(
                        namespace="ns::detail::**",
                        reason="reviewed",
                        allow_public_break=True,
                    )
                ]
            ),
        )
        assert result.missing_symbols == []
        assert result.breaking_for_app == []


# ---------------------------------------------------------------------------
# ConsumerSpec advisory/required (Workstream D-S1), end to end on real files
# ---------------------------------------------------------------------------

# A minimal, valid 64-bit little-endian ELF header with no sections: pyelftools
# reads it without error, so it is a recognised, *readable* consumer that
# imports nothing (a corrupt one is unreadable -- see
# test_failed_consumer_read.py).
_MINIMAL_ELF = (
    b"\x7fELF"
    + bytes([2, 1, 1, 0])
    + b"\x00" * 8
    + struct.pack("<HHIQQQIHHHHHH", 2, 62, 1, 0, 0, 0, 0, 64, 0, 0, 0, 0, 0)
)


class TestScopeDiffToAppConsumerSpec:
    def _run(self, consumer):
        return scope_diff_to_app(
            _diff(library="libfoo.so.1"),
            consumer,
            _snap("1.0", ["foo_init"]),
            _snap("2.0", ["foo_init"]),
        )

    def _unreadable(self, tmp_path):
        f = tmp_path / "not-a-binary"
        f.write_bytes(b"\x00\x00\x00\x00")
        return f

    def test_required_unreadable_consumer_raises(self, tmp_path):
        spec = ConsumerSpec(path=self._unreadable(tmp_path))
        with pytest.raises(
            ConsumerUnreadableError, match="Cannot detect binary format"
        ):
            self._run(spec)

    def test_bare_unreadable_path_raises_value_error(self, tmp_path):
        with pytest.raises(ValueError, match="Cannot detect binary format"):
            self._run(self._unreadable(tmp_path))

    def test_advisory_unreadable_consumer_is_skipped_not_raised(self, tmp_path):
        spec = ConsumerSpec(
            path=self._unreadable(tmp_path),
            platform="linux-x86_64",
            profile="release",
            provider_baseline="nightly-2026-09-05",
            requirement=ConsumerRequirement.ADVISORY,
        )
        result = self._run(spec)
        assert result.unreadable is True
        assert result.unreadable_reason is not None
        assert result.verdict == Verdict.NO_CHANGE
        assert result.breaking_for_app == []
        assert result.platform == "linux-x86_64"
        assert result.profile == "release"
        assert result.provider_baseline == "nightly-2026-09-05"
        assert result.requirement == "advisory"

    def test_digest_mismatch_advisory_is_skipped(self, tmp_path):
        app = tmp_path / "app"
        app.write_bytes(_MINIMAL_ELF)
        spec = ConsumerSpec(
            path=app,
            digest="sha256:" + "0" * 64,
            requirement=ConsumerRequirement.ADVISORY,
        )
        result = self._run(spec)
        assert result.unreadable is True
        assert "digest mismatch" in result.unreadable_reason

    def test_digest_match_succeeds(self, tmp_path):
        app = tmp_path / "app"
        app.write_bytes(_MINIMAL_ELF)
        digest = "sha256:" + hashlib.sha256(_MINIMAL_ELF).hexdigest()
        result = self._run(ConsumerSpec(path=app, digest=digest))
        assert result.unreadable is False
        assert result.digest == digest

    def test_bare_path_summary_carries_no_provenance(self, tmp_path):
        """A plain Path consumer reports the default 'required' provenance."""
        app = tmp_path / "app"
        app.write_bytes(_MINIMAL_ELF)
        result = self._run(app)
        assert result.requirement == "required"
        assert result.platform is None
        assert result.digest is None
        assert result.unreadable is False

    def test_spec_provenance_flows_through_fact_entry_point(self):
        spec = ConsumerSpec(path=Path("app"), platform="win-x64", profile="debug")
        result = scope_diff_to_consumer_facts(
            _diff(), _consumer({"a"}), _lib({"a"}), _lib({"a"}), spec=spec
        )
        assert (result.platform, result.profile) == ("win-x64", "debug")
