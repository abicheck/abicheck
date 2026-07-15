"""Unit tests for abicheck.stack_checker — verdicts, hashing, diffing, full check."""
from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import MagicMock, patch

from abicheck.binder import BindingStatus, SymbolBinding
from abicheck.checker import DiffResult
from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.checker_types import Change
from abicheck.resolver import DependencyGraph, ResolvedDSO
from abicheck.stack_checker import (
    StackChange,
    StackCheckResult,
    StackVerdict,
    _compute_abi_risk,
    _compute_loadability,
    _compute_risk_score,
    _diff_stacks,
    _file_hash,
    check_single_env,
    check_stack,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_graph(nodes=None, unresolved=None, root="binary"):
    """Build a minimal DependencyGraph."""
    g = DependencyGraph(root=root)
    if nodes:
        g.nodes = nodes
    if unresolved:
        g.unresolved = unresolved
    return g


def _make_binding(status=BindingStatus.RESOLVED_OK, provider="/lib/libfoo.so", symbol="sym"):
    return SymbolBinding(
        consumer="/app/binary",
        symbol=symbol,
        version="",
        provider=provider,
        status=status,
        explanation="test",
    )


def _make_resolved_dso(path, soname="libfoo.so"):
    return ResolvedDSO(
        path=Path(path),
        soname=soname,
        needed=[],
        rpath="",
        runpath="",
        resolution_reason="test",
        depth=1,
    )


def _make_diff_result(verdict=Verdict.NO_CHANGE, breaking=None):
    mock = MagicMock(spec=DiffResult)
    mock.verdict = verdict
    mock.breaking = [] if breaking is None else breaking
    return mock


def _make_breaking_change(symbol="sym", affected_symbols=None):
    return Change(
        kind=ChangeKind.FUNC_REMOVED_ELF_ONLY,
        symbol=symbol,
        description="test",
        affected_symbols=affected_symbols,
    )


# ---------------------------------------------------------------------------
# _compute_loadability
# ---------------------------------------------------------------------------


class TestComputeLoadability:
    def test_empty_graph_nodes_fail(self):
        graph = _make_graph(nodes={})
        assert _compute_loadability(graph, [], []) == StackVerdict.FAIL

    def test_unresolved_deps_fail(self):
        graph = _make_graph(
            nodes={"/lib/libfoo.so": _make_resolved_dso("/lib/libfoo.so")},
            unresolved=[("/app/binary", "libmissing.so")],
        )
        assert _compute_loadability(graph, [], []) == StackVerdict.FAIL

    def test_missing_symbols_fail(self):
        graph = _make_graph(
            nodes={"/lib/libfoo.so": _make_resolved_dso("/lib/libfoo.so")},
        )
        missing = [_make_binding(status=BindingStatus.MISSING)]
        assert _compute_loadability(graph, missing, []) == StackVerdict.FAIL

    def test_version_mismatches_warn(self):
        graph = _make_graph(
            nodes={"/lib/libfoo.so": _make_resolved_dso("/lib/libfoo.so")},
        )
        vm = [_make_binding(status=BindingStatus.VERSION_MISMATCH)]
        assert _compute_loadability(graph, [], vm) == StackVerdict.WARN

    def test_all_good_pass(self):
        graph = _make_graph(
            nodes={"/lib/libfoo.so": _make_resolved_dso("/lib/libfoo.so")},
        )
        assert _compute_loadability(graph, [], []) == StackVerdict.PASS


# ---------------------------------------------------------------------------
# _compute_abi_risk
# ---------------------------------------------------------------------------


class TestComputeAbiRisk:
    def test_no_changes_pass(self):
        assert _compute_abi_risk([]) == StackVerdict.PASS

    def test_removed_library_fail(self):
        changes = [StackChange(library="libgone.so", change_type="removed")]
        assert _compute_abi_risk(changes) == StackVerdict.FAIL

    def test_breaking_verdict_with_impacted_imports_fail(self):
        # P1.4: a root only FAILs when the imported symbol is among the
        # DSO's own BREAKING findings — here "sym" is both imported and
        # the symbol the breaking change removed, so this is a confirmed hit.
        diff = _make_diff_result(Verdict.BREAKING, breaking=[_make_breaking_change("sym")])
        binding = _make_binding(symbol="sym")
        changes = [StackChange(
            library="libfoo.so",
            change_type="content_changed",
            abi_diff=diff,
            impacted_imports=[binding],
        )]
        assert _compute_abi_risk(changes) == StackVerdict.FAIL

    def test_breaking_verdict_with_unrelated_impacted_import_warns_not_fail(self):
        # P1.4 regression: the DSO is BREAKING because it removed
        # "unrelated_sym", but this root only imports "sym" — an unrelated,
        # unaffected symbol from the same provider. That must not FAIL the
        # root; it's still a real "environment contains a broken DSO" signal
        # (WARN), not a confirmed impact on this specific root/import.
        diff = _make_diff_result(
            Verdict.BREAKING, breaking=[_make_breaking_change("unrelated_sym")]
        )
        binding = _make_binding(symbol="sym")
        changes = [StackChange(
            library="libfoo.so",
            change_type="content_changed",
            abi_diff=diff,
            impacted_imports=[binding],
        )]
        assert _compute_abi_risk(changes) == StackVerdict.WARN

    def test_breaking_verdict_multiple_imports_one_matches_fails(self):
        # One of several imports from this provider does match a broken
        # symbol — still a confirmed FAIL even though other imports don't.
        diff = _make_diff_result(
            Verdict.BREAKING, breaking=[_make_breaking_change("broken_sym")]
        )
        bindings = [_make_binding(symbol="unrelated_a"), _make_binding(symbol="broken_sym")]
        changes = [StackChange(
            library="libfoo.so",
            change_type="content_changed",
            abi_diff=diff,
            impacted_imports=bindings,
        )]
        assert _compute_abi_risk(changes) == StackVerdict.FAIL

    def test_breaking_type_change_matched_via_affected_symbols_fails(self):
        # Codex review regression: a BREAKING type/layout change (e.g. a
        # resized struct) names the *type* in Change.symbol ("Point"), not
        # the exported functions that use it — those live in
        # affected_symbols. A root importing "foo" (which takes a Point
        # parameter) must still FAIL, not be missed because "foo" != "Point".
        diff = _make_diff_result(
            Verdict.BREAKING,
            breaking=[_make_breaking_change("Point", affected_symbols=["foo", "bar"])],
        )
        binding = _make_binding(symbol="foo")
        changes = [StackChange(
            library="libfoo.so",
            change_type="content_changed",
            abi_diff=diff,
            impacted_imports=[binding],
        )]
        assert _compute_abi_risk(changes) == StackVerdict.FAIL

    def test_api_break_verdict_with_impacted_imports_warn(self):
        diff = _make_diff_result(Verdict.API_BREAK)
        binding = _make_binding()
        changes = [StackChange(
            library="libfoo.so",
            change_type="content_changed",
            abi_diff=diff,
            impacted_imports=[binding],
        )]
        assert _compute_abi_risk(changes) == StackVerdict.WARN

    def test_content_changed_no_abi_diff_warn(self):
        changes = [StackChange(
            library="libfoo.so",
            change_type="content_changed",
            abi_diff=None,
        )]
        assert _compute_abi_risk(changes) == StackVerdict.WARN

    def test_breaking_no_impacted_imports_warn(self):
        diff = _make_diff_result(Verdict.BREAKING)
        changes = [StackChange(
            library="libfoo.so",
            change_type="content_changed",
            abi_diff=diff,
            impacted_imports=[],
        )]
        assert _compute_abi_risk(changes) == StackVerdict.WARN


# ---------------------------------------------------------------------------
# _compute_risk_score
# ---------------------------------------------------------------------------


class TestComputeRiskScore:
    def test_fail_loadability_high(self):
        assert _compute_risk_score(StackVerdict.FAIL, StackVerdict.PASS) == "high"

    def test_fail_abi_risk_high(self):
        assert _compute_risk_score(StackVerdict.PASS, StackVerdict.FAIL) == "high"

    def test_warn_abi_risk_medium(self):
        assert _compute_risk_score(StackVerdict.PASS, StackVerdict.WARN) == "medium"

    def test_pass_both_low(self):
        assert _compute_risk_score(StackVerdict.PASS, StackVerdict.PASS) == "low"


# ---------------------------------------------------------------------------
# _file_hash
# ---------------------------------------------------------------------------


class TestFileHash:
    def test_readable_file(self, tmp_path):
        f = tmp_path / "lib.so"
        content = b"ELF binary content"
        f.write_bytes(content)
        expected = hashlib.sha256(content).hexdigest()
        assert _file_hash(f) == expected

    def test_nonexistent_file(self, tmp_path):
        assert _file_hash(tmp_path / "missing.so") is None


# ---------------------------------------------------------------------------
# _diff_stacks
# ---------------------------------------------------------------------------


class TestDiffStacks:
    def test_library_added_in_candidate(self, tmp_path):
        baseline = _make_graph(nodes={})
        cand_dso = _make_resolved_dso(tmp_path / "libnew.so", soname="libnew.so")
        candidate = _make_graph(nodes={str(tmp_path / "libnew.so"): cand_dso})

        changes = _diff_stacks(baseline, candidate)
        assert len(changes) == 1
        assert changes[0].change_type == "added"
        assert changes[0].library == "libnew.so"

    def test_library_removed_from_candidate(self, tmp_path):
        base_dso = _make_resolved_dso(tmp_path / "libold.so", soname="libold.so")
        baseline = _make_graph(nodes={str(tmp_path / "libold.so"): base_dso})
        candidate = _make_graph(nodes={})

        changes = _diff_stacks(baseline, candidate)
        assert len(changes) == 1
        assert changes[0].change_type == "removed"
        assert changes[0].library == "libold.so"

    def test_same_hash_no_change(self, tmp_path):
        lib = tmp_path / "libsame.so"
        lib.write_bytes(b"identical content")

        base_dso = _make_resolved_dso(str(lib), soname="libsame.so")
        cand_dso = _make_resolved_dso(str(lib), soname="libsame.so")
        baseline = _make_graph(nodes={str(lib): base_dso})
        candidate = _make_graph(nodes={str(lib): cand_dso})

        changes = _diff_stacks(baseline, candidate)
        assert len(changes) == 0

    @patch("abicheck.stack_checker._run_abi_diff")
    def test_different_hash_content_changed(self, mock_abi_diff, tmp_path):
        mock_abi_diff.return_value = _make_diff_result(Verdict.COMPATIBLE)

        base_lib = tmp_path / "base" / "libfoo.so"
        cand_lib = tmp_path / "cand" / "libfoo.so"
        base_lib.parent.mkdir(parents=True)
        cand_lib.parent.mkdir(parents=True)
        base_lib.write_bytes(b"old content")
        cand_lib.write_bytes(b"new content")

        base_dso = _make_resolved_dso(str(base_lib), soname="libfoo.so")
        cand_dso = _make_resolved_dso(str(cand_lib), soname="libfoo.so")
        baseline = _make_graph(nodes={str(base_lib): base_dso})
        candidate = _make_graph(nodes={str(cand_lib): cand_dso})

        changes = _diff_stacks(baseline, candidate)
        assert len(changes) == 1
        assert changes[0].change_type == "content_changed"
        assert changes[0].abi_diff is not None
        mock_abi_diff.assert_called_once()


# ---------------------------------------------------------------------------
# check_stack (integration with mocks)
# ---------------------------------------------------------------------------


class TestCheckStack:
    @patch("abicheck.stack_checker.compute_bindings")
    @patch("abicheck.stack_checker.resolve_dependencies")
    def test_full_check(self, mock_resolve, mock_bindings, tmp_path):
        baseline_root = tmp_path / "baseline"
        candidate_root = tmp_path / "candidate"
        binary = Path("usr/bin/myapp")

        # Create the binary files so paths exist
        (baseline_root / binary).parent.mkdir(parents=True)
        (baseline_root / binary).touch()
        (candidate_root / binary).parent.mkdir(parents=True)
        (candidate_root / binary).touch()

        # Both environments resolve a single shared library with identical content
        lib_path = candidate_root / "lib" / "libfoo.so"
        lib_path.parent.mkdir(parents=True)
        lib_path.write_bytes(b"ELF shared lib content")

        dso = _make_resolved_dso(str(lib_path), soname="libfoo.so")
        graph = _make_graph(nodes={str(lib_path): dso})
        mock_resolve.return_value = graph

        # No missing symbols
        mock_bindings.return_value = [
            _make_binding(status=BindingStatus.RESOLVED_OK),
        ]

        result = check_stack(binary, baseline_root, candidate_root)

        assert isinstance(result, StackCheckResult)
        assert result.root_binary == str(binary)
        assert result.baseline_env == str(baseline_root)
        assert result.candidate_env == str(candidate_root)
        assert result.loadability == StackVerdict.PASS
        assert result.risk_score == "low"
        assert mock_resolve.call_count == 2
        assert mock_bindings.call_count == 2


# ---------------------------------------------------------------------------
# check_single_env
# ---------------------------------------------------------------------------


class TestCheckSingleEnv:
    @patch("abicheck.stack_checker.compute_bindings")
    @patch("abicheck.stack_checker.resolve_dependencies")
    def test_binary_not_found_empty_graph(self, mock_resolve, mock_bindings):
        mock_resolve.return_value = _make_graph(nodes={})
        mock_bindings.return_value = []

        result = check_single_env(Path("/app/missing"))
        assert result.loadability == StackVerdict.FAIL
        assert result.risk_score == "high"

    @patch("abicheck.stack_checker.compute_bindings")
    @patch("abicheck.stack_checker.resolve_dependencies")
    def test_binary_with_unresolved(self, mock_resolve, mock_bindings):
        graph = _make_graph(
            nodes={"/lib/libfoo.so": _make_resolved_dso("/lib/libfoo.so")},
            unresolved=[("/app/binary", "libmissing.so")],
        )
        mock_resolve.return_value = graph
        mock_bindings.return_value = []

        result = check_single_env(Path("/app/binary"))
        assert result.loadability == StackVerdict.FAIL
        assert result.risk_score == "high"

    @patch("abicheck.stack_checker.compute_bindings")
    @patch("abicheck.stack_checker.resolve_dependencies")
    def test_binary_with_version_mismatch(self, mock_resolve, mock_bindings):
        graph = _make_graph(
            nodes={"/lib/libfoo.so": _make_resolved_dso("/lib/libfoo.so")},
        )
        mock_resolve.return_value = graph
        mock_bindings.return_value = [
            _make_binding(status=BindingStatus.VERSION_MISMATCH),
        ]

        result = check_single_env(Path("/app/binary"))
        assert result.loadability == StackVerdict.WARN
        assert result.risk_score == "medium"

    @patch("abicheck.stack_checker.compute_bindings")
    @patch("abicheck.stack_checker.resolve_dependencies")
    def test_binary_all_good(self, mock_resolve, mock_bindings):
        graph = _make_graph(
            nodes={"/lib/libfoo.so": _make_resolved_dso("/lib/libfoo.so")},
        )
        mock_resolve.return_value = graph
        mock_bindings.return_value = [
            _make_binding(status=BindingStatus.RESOLVED_OK),
        ]

        result = check_single_env(Path("/app/binary"))
        assert result.loadability == StackVerdict.PASS
        assert result.abi_risk == StackVerdict.PASS
        assert result.risk_score == "low"
