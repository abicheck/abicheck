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

"""ADR-049 Phase 5 §6.3: the shared L0 hard-removal extraction used by both
``cli_helpers_compare.fold_l0_hard_removals`` (direct ``compare``) and
``cli_scan_baseline._run_baseline_compare`` (``scan --against``)."""

from __future__ import annotations

from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.checker_types import Change, DiffResult
from abicheck.errors import AbicheckError
from abicheck.l0_export_delta import collect_l0_export_delta


def test_resolve_failure_returns_empty_tuple(monkeypatch, tmp_path):
    """A path that can no longer be resolved (moved/missing binary) is
    swallowed -- this is a best-effort enrichment, never something that
    should raise out of a real compare/scan."""

    def _raise(*_a, **_kw):
        raise AbicheckError("no such file")

    monkeypatch.setattr("abicheck.service.resolve_input", _raise)
    result = collect_l0_export_delta(tmp_path / "old.so", tmp_path / "new.so", "c++")
    assert result == ()


def test_compare_failure_returns_empty_tuple(monkeypatch, tmp_path):
    """A resolve success followed by a compare_snapshots failure is just as
    much a "probe didn't pan out" case and must not escape."""
    monkeypatch.setattr("abicheck.service.resolve_input", lambda *a, **kw: object())

    def _raise(*_a, **_kw):
        raise AbicheckError("incompatible snapshots")

    monkeypatch.setattr("abicheck.service.compare_snapshots", _raise)
    result = collect_l0_export_delta(tmp_path / "old.so", tmp_path / "new.so", "c++")
    assert result == ()


def test_folds_elf_only_removal(monkeypatch, tmp_path):
    """The symbols-only re-probe finds a hard ELF-only removal (case97's exact
    shape) and returns exactly that fact."""
    monkeypatch.setattr("abicheck.service.resolve_input", lambda *a, **kw: object())
    removal = Change(
        kind=ChangeKind.FUNC_REMOVED_ELF_ONLY,
        symbol="_ZN3lib8extendedEv",
        description="ELF-only function removed",
    )
    unrelated = Change(
        kind=ChangeKind.FUNC_RETURN_CHANGED, symbol="other", description=""
    )
    diff = DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="lib.so",
        changes=[removal, unrelated],
        verdict=Verdict.BREAKING,
    )
    monkeypatch.setattr("abicheck.service.compare_snapshots", lambda *a, **kw: diff)
    result = collect_l0_export_delta(tmp_path / "old.so", tmp_path / "new.so", "c++")
    assert result == (removal,)


def test_ignores_non_elf_only_findings(monkeypatch, tmp_path):
    """A breaking finding that isn't func_removed_elf_only is never returned --
    this probe restores exactly one specific fact, never a general advisory
    dump."""
    monkeypatch.setattr("abicheck.service.resolve_input", lambda *a, **kw: object())
    diff = DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="lib.so",
        changes=[Change(kind=ChangeKind.FUNC_REMOVED, symbol="other", description="")],
        verdict=Verdict.BREAKING,
    )
    monkeypatch.setattr("abicheck.service.compare_snapshots", lambda *a, **kw: diff)
    result = collect_l0_export_delta(tmp_path / "old.so", tmp_path / "new.so", "c++")
    assert result == ()


def test_no_breaking_findings_returns_empty_tuple(monkeypatch, tmp_path):
    monkeypatch.setattr("abicheck.service.resolve_input", lambda *a, **kw: object())
    diff = DiffResult(
        old_version="1.0",
        new_version="1.0",
        library="lib.so",
        changes=[],
        verdict=Verdict.COMPATIBLE,
    )
    monkeypatch.setattr("abicheck.service.compare_snapshots", lambda *a, **kw: diff)
    result = collect_l0_export_delta(tmp_path / "old.so", tmp_path / "new.so", "c++")
    assert result == ()
