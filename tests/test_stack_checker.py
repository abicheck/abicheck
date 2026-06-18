# Copyright 2026 Nikolay Petrov
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

"""Tests for abicheck.stack_checker — full-stack ABI checking."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from abicheck.binder import BindingStatus, SymbolBinding
from abicheck.resolver import DependencyGraph, ResolvedDSO
from abicheck.stack_checker import (
    StackChange,
    StackVerdict,
    _compute_abi_risk,
    _compute_loadability,
    _compute_risk_score,
    _diff_stacks,
    _file_hash,
    check_single_env,
)


def _require_linux_elf(path: Path) -> Path:
    """Verify we're on Linux and the candidate is an ELF binary."""
    if sys.platform != "linux":
        pytest.skip("Full-stack dependency tests require Linux")
    try:
        with open(path, "rb") as f:
            if f.read(4) != b"\x7fELF":
                pytest.skip(f"{path} is not an ELF binary")
    except OSError:
        pytest.skip(f"Cannot read {path}")
    return path


class TestCheckSingleEnv:
    # ``check_single_env`` follows the binary's whole dependency chain (~0.6s on
    # real libc), and every test below only asserts a different attribute of the
    # same result. Compute it once per class instead of re-running the analysis
    # 7× (~4s → ~0.6s). The result is read-only, so sharing it is safe.
    @staticmethod
    @pytest.fixture(scope="class")
    def result():
        if sys.platform != "linux":
            pytest.skip("Full-stack dependency tests require Linux")
        candidates = [Path("/usr/bin/python3"), Path("/usr/bin/ls"), Path("/bin/ls")]
        for p in candidates:
            if p.exists():
                return check_single_env(_require_linux_elf(p))
        pytest.skip("No suitable ELF binary found")

    def test_loadability_pass(self, result):
        assert result.loadability == StackVerdict.PASS

    def test_abi_risk_pass(self, result):
        assert result.abi_risk == StackVerdict.PASS

    def test_risk_score_low(self, result):
        assert result.risk_score == "low"

    def test_no_missing_symbols(self, result):
        assert len(result.missing_symbols) == 0

    def test_graph_populated(self, result):
        assert result.baseline_graph.node_count >= 2  # At least root + libc

    def test_bindings_populated(self, result):
        assert len(result.bindings_baseline) > 0

    def test_nonexistent_binary(self, tmp_path):
        result = check_single_env(tmp_path / "nonexistent")
        # Empty graph → should report FAIL loadability and high risk.
        assert result.baseline_graph.node_count == 0
        assert result.loadability == StackVerdict.FAIL
        assert result.risk_score == "high"


class TestStackVerdict:
    def test_verdict_values(self):
        assert StackVerdict.PASS.value == "pass"
        assert StackVerdict.WARN.value == "warn"
        assert StackVerdict.FAIL.value == "fail"


# ---------------------------------------------------------------------------
# Unit tests: helper functions
# ---------------------------------------------------------------------------


def _binding(status: BindingStatus, symbol: str = "sym", provider: str | None = "/lib/libfoo.so") -> SymbolBinding:
    return SymbolBinding(consumer="/app", symbol=symbol, version="", provider=provider, status=status, explanation="")


def _empty_graph(root: str = "/app") -> DependencyGraph:
    return DependencyGraph(root=root)


def _graph_with_nodes(**sonames: str) -> DependencyGraph:
    """Create a graph with named nodes.  sonames maps key -> soname."""
    g = DependencyGraph(root="/app")
    for i, (key, soname) in enumerate(sonames.items()):
        g.nodes[key] = ResolvedDSO(
            path=Path(key), soname=soname, needed=[], rpath="", runpath="",
            resolution_reason="root" if i == 0 else "default", depth=i,
        )
    return g


class TestComputeLoadability:
    def test_empty_graph_is_fail(self):
        assert _compute_loadability(_empty_graph(), [], []) == StackVerdict.FAIL

    def test_unresolved_is_fail(self):
        g = _graph_with_nodes(**{"/app": "app"})
        g.unresolved = [("/app", "libmissing.so")]
        assert _compute_loadability(g, [], []) == StackVerdict.FAIL

    def test_missing_symbols_is_fail(self):
        g = _graph_with_nodes(**{"/app": "app"})
        assert _compute_loadability(g, [_binding(BindingStatus.MISSING)], []) == StackVerdict.FAIL

    def test_version_mismatch_is_warn(self):
        g = _graph_with_nodes(**{"/app": "app"})
        assert _compute_loadability(g, [], [_binding(BindingStatus.VERSION_MISMATCH)]) == StackVerdict.WARN

    def test_all_ok_is_pass(self):
        g = _graph_with_nodes(**{"/app": "app"})
        assert _compute_loadability(g, [], []) == StackVerdict.PASS


class TestComputeAbiRisk:
    def test_no_changes_is_pass(self):
        assert _compute_abi_risk([]) == StackVerdict.PASS

    def test_removed_is_fail(self):
        assert _compute_abi_risk([StackChange(library="libfoo.so", change_type="removed")]) == StackVerdict.FAIL

    def test_added_is_pass(self):
        assert _compute_abi_risk([StackChange(library="libfoo.so", change_type="added")]) == StackVerdict.PASS

    def test_breaking_unused_is_warn(self):
        """A BREAKING diff with no impacted imports → WARN (risk, not hard fail)."""
        from unittest.mock import Mock
        diff = Mock()
        diff.verdict.value = "BREAKING"
        sc = StackChange(library="libfoo.so", change_type="content_changed", abi_diff=diff, impacted_imports=[])
        assert _compute_abi_risk([sc]) == StackVerdict.WARN

    def test_api_break_with_impacted_is_warn(self):
        from unittest.mock import Mock
        diff = Mock()
        diff.verdict.value = "API_BREAK"
        sc = StackChange(
            library="libfoo.so", change_type="content_changed",
            abi_diff=diff, impacted_imports=[_binding(BindingStatus.RESOLVED_OK)],
        )
        assert _compute_abi_risk([sc]) == StackVerdict.WARN


class TestComputeRiskScore:
    def test_fail_loadability_is_high(self):
        assert _compute_risk_score(StackVerdict.FAIL, StackVerdict.PASS) == "high"

    def test_fail_abi_is_high(self):
        assert _compute_risk_score(StackVerdict.PASS, StackVerdict.FAIL) == "high"

    def test_warn_abi_is_medium(self):
        assert _compute_risk_score(StackVerdict.PASS, StackVerdict.WARN) == "medium"

    def test_all_pass_is_low(self):
        assert _compute_risk_score(StackVerdict.PASS, StackVerdict.PASS) == "low"


class TestFileHash:
    def test_readable_file(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello")
        h = _file_hash(f)
        assert h is not None
        assert len(h) == 64  # SHA-256 hex

    def test_nonexistent_file(self, tmp_path):
        assert _file_hash(tmp_path / "noexist") is None


class TestDiffStacks:
    def test_added_library(self):
        base = _graph_with_nodes(**{"/app": "app"})
        cand = _graph_with_nodes(**{"/app": "app", "/lib/libfoo.so": "libfoo.so"})
        changes = _diff_stacks(base, cand)
        added = [c for c in changes if c.change_type == "added"]
        assert len(added) == 1
        assert added[0].library == "libfoo.so"

    def test_removed_library(self):
        base = _graph_with_nodes(**{"/app": "app", "/lib/libfoo.so": "libfoo.so"})
        cand = _graph_with_nodes(**{"/app": "app"})
        changes = _diff_stacks(base, cand)
        removed = [c for c in changes if c.change_type == "removed"]
        assert len(removed) == 1
        assert removed[0].library == "libfoo.so"

    def test_identical_libraries_no_changes(self, tmp_path):
        lib = tmp_path / "libfoo.so"
        lib.write_bytes(b"\x7fELFsamecontent")
        base = DependencyGraph(root="/app")
        base.nodes[str(lib)] = ResolvedDSO(
            path=lib, soname="libfoo.so", needed=[], rpath="", runpath="",
            resolution_reason="default", depth=1,
        )
        # Same path in both → same hash → no change
        changes = _diff_stacks(base, base)
        assert len(changes) == 0

    def test_unreadable_file_treated_as_changed(self, tmp_path):
        """When a file can't be read (hash is None), treat as content_changed."""
        real_lib = tmp_path / "libfoo.so"
        real_lib.write_bytes(b"\x7fELFcontent")
        base = DependencyGraph(root="/app")
        base.nodes[str(real_lib)] = ResolvedDSO(
            path=real_lib, soname="libfoo.so", needed=[], rpath="", runpath="",
            resolution_reason="default", depth=1,
        )
        # Candidate points to nonexistent file with same soname
        fake_lib = tmp_path / "other" / "libfoo.so"
        cand = DependencyGraph(root="/app")
        cand.nodes[str(fake_lib)] = ResolvedDSO(
            path=fake_lib, soname="libfoo.so", needed=[], rpath="", runpath="",
            resolution_reason="default", depth=1,
        )
        changes = _diff_stacks(base, cand)
        assert len(changes) == 1
        assert changes[0].change_type == "content_changed"

    def test_changed_library_detected(self, tmp_path):
        old_lib = tmp_path / "old" / "libfoo.so"
        new_lib = tmp_path / "new" / "libfoo.so"
        old_lib.parent.mkdir()
        new_lib.parent.mkdir()
        old_lib.write_bytes(b"\x7fELFoldcontent")
        new_lib.write_bytes(b"\x7fELFnewcontent")

        base = DependencyGraph(root="/app")
        base.nodes[str(old_lib)] = ResolvedDSO(
            path=old_lib, soname="libfoo.so", needed=[], rpath="", runpath="",
            resolution_reason="default", depth=1,
        )
        cand = DependencyGraph(root="/app")
        cand.nodes[str(new_lib)] = ResolvedDSO(
            path=new_lib, soname="libfoo.so", needed=[], rpath="", runpath="",
            resolution_reason="default", depth=1,
        )
        changes = _diff_stacks(base, cand)
        content_changed = [c for c in changes if c.change_type == "content_changed"]
        assert len(content_changed) == 1
