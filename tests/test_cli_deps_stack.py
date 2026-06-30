"""Tests for the CLI `deps` and `stack-check` commands."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.binder import BindingStatus, SymbolBinding
from abicheck.cli import main
from abicheck.resolver import DependencyGraph, ResolvedDSO
from abicheck.stack_checker import StackCheckResult, StackVerdict

# ---------------------------------------------------------------------------
# Helpers — build synthetic data
# ---------------------------------------------------------------------------

def _make_graph(root: str, *, with_nodes: bool = True) -> DependencyGraph:
    """Return a minimal DependencyGraph."""
    nodes: dict[str, ResolvedDSO] = {}
    if with_nodes:
        nodes[root] = ResolvedDSO(
            path=Path(root),
            soname=Path(root).name,
            needed=["libfoo.so.1"],
            rpath="",
            runpath="",
            resolution_reason="root",
            depth=0,
            elf_metadata=None,
        )
        lib_path = str(Path(root).parent / "libfoo.so.1")
        nodes[lib_path] = ResolvedDSO(
            path=Path(lib_path),
            soname="libfoo.so.1",
            needed=[],
            rpath="",
            runpath="",
            resolution_reason="default",
            depth=1,
            elf_metadata=None,
        )
    return DependencyGraph(
        root=root,
        nodes=nodes,
        edges=[(root, str(Path(root).parent / "libfoo.so.1"))] if with_nodes else [],
        unresolved=[],
    )


def _make_bindings(consumer: str) -> list[SymbolBinding]:
    return [
        SymbolBinding(
            consumer=consumer,
            symbol="foo_init",
            version="",
            provider="/usr/lib/libfoo.so.1",
            status=BindingStatus.RESOLVED_OK,
            explanation="resolved via default search",
        ),
    ]


def _make_result(
    binary: str,
    *,
    loadability: StackVerdict = StackVerdict.PASS,
    abi_risk: StackVerdict = StackVerdict.PASS,
    risk_score: str = "low",
    baseline_env: str = "",
    candidate_env: str = "",
) -> StackCheckResult:
    graph = _make_graph(binary)
    bindings = _make_bindings(binary)
    return StackCheckResult(
        root_binary=binary,
        baseline_env=baseline_env,
        candidate_env=candidate_env,
        loadability=loadability,
        abi_risk=abi_risk,
        baseline_graph=graph,
        candidate_graph=graph,
        bindings_baseline=bindings,
        bindings_candidate=bindings,
        missing_symbols=[],
        stack_changes=[],
        risk_score=risk_score,
    )


# ---------------------------------------------------------------------------
# deps command
# ---------------------------------------------------------------------------

class TestDepsCommand:
    """Tests for the `deps` CLI command."""

    def test_deps_json(self, tmp_path, monkeypatch):
        binary = tmp_path / "myapp"
        binary.write_bytes(b"\x7fELF" + b"\x00" * 60)

        result_obj = _make_result(str(binary))

        monkeypatch.setattr(
            "abicheck.stack_checker.check_single_env",
            lambda *a, **kw: result_obj,
        )

        runner = CliRunner()
        result = runner.invoke(main, ["deps", "tree", str(binary), "--format", "json"])
        assert result.exit_code == 0, result.output
        parsed = json.loads(result.output)
        assert parsed["root_binary"] == str(binary)
        assert parsed["verdict"]["loadability"] == "pass"

    def test_deps_markdown(self, tmp_path, monkeypatch):
        binary = tmp_path / "myapp"
        binary.write_bytes(b"\x7fELF" + b"\x00" * 60)

        result_obj = _make_result(str(binary))
        monkeypatch.setattr(
            "abicheck.stack_checker.check_single_env",
            lambda *a, **kw: result_obj,
        )

        runner = CliRunner()
        result = runner.invoke(main, ["deps", "tree", str(binary), "--format", "markdown"])
        assert result.exit_code == 0, result.output
        assert "# Stack Report:" in result.output
        assert "Loadability" in result.output

    def test_deps_output_file(self, tmp_path, monkeypatch):
        binary = tmp_path / "myapp"
        binary.write_bytes(b"\x7fELF" + b"\x00" * 60)
        outfile = tmp_path / "report.json"

        result_obj = _make_result(str(binary))
        monkeypatch.setattr(
            "abicheck.stack_checker.check_single_env",
            lambda *a, **kw: result_obj,
        )

        runner = CliRunner()
        result = runner.invoke(
            main, ["deps", "tree", str(binary), "--format", "json", "-o", str(outfile)],
        )
        assert result.exit_code == 0, result.output
        assert outfile.exists()
        parsed = json.loads(outfile.read_text())
        assert parsed["root_binary"] == str(binary)

    def test_deps_exit_code_1_on_fail(self, tmp_path, monkeypatch):
        binary = tmp_path / "myapp"
        binary.write_bytes(b"\x7fELF" + b"\x00" * 60)

        result_obj = _make_result(
            str(binary), loadability=StackVerdict.FAIL, risk_score="high",
        )
        monkeypatch.setattr(
            "abicheck.stack_checker.check_single_env",
            lambda *a, **kw: result_obj,
        )

        runner = CliRunner()
        result = runner.invoke(main, ["deps", "tree", str(binary), "--format", "json"])
        assert result.exit_code == 1

    def test_deps_sysroot_and_search_path(self, tmp_path, monkeypatch):
        binary = tmp_path / "myapp"
        binary.write_bytes(b"\x7fELF" + b"\x00" * 60)
        sysroot = tmp_path / "sysroot"
        sysroot.mkdir()
        search_dir = tmp_path / "extra_libs"
        search_dir.mkdir()

        captured_kwargs: dict = {}

        def fake_check_single_env(*args, **kwargs):
            captured_kwargs.update(kwargs)
            return _make_result(str(binary))

        monkeypatch.setattr(
            "abicheck.stack_checker.check_single_env",
            fake_check_single_env,
        )

        runner = CliRunner()
        result = runner.invoke(main, [
            "deps", "tree", str(binary),
            "--sysroot", str(sysroot),
            "--search-path", str(search_dir),
            "--format", "json",
        ])
        assert result.exit_code == 0, result.output
        assert captured_kwargs["sysroot"] == sysroot
        assert captured_kwargs["search_paths"] == [search_dir]


# ---------------------------------------------------------------------------
# stack-check command
# ---------------------------------------------------------------------------

class TestStackCheckCommand:
    """Tests for the `stack-check` CLI command."""

    @pytest.fixture()
    def env_dirs(self, tmp_path):
        """Create baseline and candidate sysroot directories."""
        baseline = tmp_path / "baseline"
        baseline.mkdir()
        candidate = tmp_path / "candidate"
        candidate.mkdir()
        return baseline, candidate

    def test_stack_check_json(self, tmp_path, env_dirs, monkeypatch):
        baseline, candidate = env_dirs
        binary_rel = "usr/bin/myapp"

        result_obj = _make_result(
            binary_rel,
            baseline_env=str(baseline),
            candidate_env=str(candidate),
        )
        captured: dict = {}

        def fake_check_stack(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return result_obj

        monkeypatch.setattr(
            "abicheck.stack_checker.check_stack",
            fake_check_stack,
        )

        runner = CliRunner()
        result = runner.invoke(main, [
            "deps", "compare", binary_rel,
            "--baseline", str(baseline),
            "--candidate", str(candidate),
            "--format", "json",
        ])
        assert result.exit_code == 0, result.output
        parsed = json.loads(result.output)
        assert parsed["root_binary"] == binary_rel
        assert parsed["verdict"]["loadability"] == "pass"
        assert parsed["verdict"]["abi_risk"] == "pass"
        # Verify CLI forwarded baseline/candidate to check_stack
        assert captured["kwargs"]["baseline_root"] == baseline
        assert captured["kwargs"]["candidate_root"] == candidate

    def test_stack_check_markdown(self, tmp_path, env_dirs, monkeypatch):
        baseline, candidate = env_dirs
        binary_rel = "usr/bin/myapp"

        result_obj = _make_result(
            binary_rel,
            baseline_env=str(baseline),
            candidate_env=str(candidate),
        )
        monkeypatch.setattr(
            "abicheck.stack_checker.check_stack",
            lambda *a, **kw: result_obj,
        )

        runner = CliRunner()
        result = runner.invoke(main, [
            "deps", "compare", binary_rel,
            "--baseline", str(baseline),
            "--candidate", str(candidate),
            "--format", "markdown",
        ])
        assert result.exit_code == 0, result.output
        assert "# Stack Report:" in result.output
        assert "Loadability" in result.output

    def test_stack_check_output_file(self, tmp_path, env_dirs, monkeypatch):
        baseline, candidate = env_dirs
        binary_rel = "usr/bin/myapp"
        outfile = tmp_path / "stack-report.json"

        result_obj = _make_result(
            binary_rel,
            baseline_env=str(baseline),
            candidate_env=str(candidate),
        )
        monkeypatch.setattr(
            "abicheck.stack_checker.check_stack",
            lambda *a, **kw: result_obj,
        )

        runner = CliRunner()
        result = runner.invoke(main, [
            "deps", "compare", binary_rel,
            "--baseline", str(baseline),
            "--candidate", str(candidate),
            "--format", "json",
            "-o", str(outfile),
        ])
        assert result.exit_code == 0, result.output
        assert outfile.exists()
        parsed = json.loads(outfile.read_text())
        assert parsed["root_binary"] == binary_rel

    def test_stack_check_exit_4_loadability_fail(self, tmp_path, env_dirs, monkeypatch):
        baseline, candidate = env_dirs
        binary_rel = "usr/bin/myapp"

        result_obj = _make_result(
            binary_rel,
            loadability=StackVerdict.FAIL,
            abi_risk=StackVerdict.PASS,
            risk_score="high",
            baseline_env=str(baseline),
            candidate_env=str(candidate),
        )
        monkeypatch.setattr(
            "abicheck.stack_checker.check_stack",
            lambda *a, **kw: result_obj,
        )

        runner = CliRunner()
        result = runner.invoke(main, [
            "deps", "compare", binary_rel,
            "--baseline", str(baseline),
            "--candidate", str(candidate),
            "--format", "json",
        ])
        assert result.exit_code == 4

    def test_stack_check_exit_4_abi_risk_fail(self, tmp_path, env_dirs, monkeypatch):
        baseline, candidate = env_dirs
        binary_rel = "usr/bin/myapp"

        result_obj = _make_result(
            binary_rel,
            loadability=StackVerdict.PASS,
            abi_risk=StackVerdict.FAIL,
            risk_score="high",
            baseline_env=str(baseline),
            candidate_env=str(candidate),
        )
        monkeypatch.setattr(
            "abicheck.stack_checker.check_stack",
            lambda *a, **kw: result_obj,
        )

        runner = CliRunner()
        result = runner.invoke(main, [
            "deps", "compare", binary_rel,
            "--baseline", str(baseline),
            "--candidate", str(candidate),
            "--format", "json",
        ])
        assert result.exit_code == 4

    def test_stack_check_exit_1_abi_risk_warn(self, tmp_path, env_dirs, monkeypatch):
        baseline, candidate = env_dirs
        binary_rel = "usr/bin/myapp"

        result_obj = _make_result(
            binary_rel,
            loadability=StackVerdict.PASS,
            abi_risk=StackVerdict.WARN,
            risk_score="medium",
            baseline_env=str(baseline),
            candidate_env=str(candidate),
        )
        monkeypatch.setattr(
            "abicheck.stack_checker.check_stack",
            lambda *a, **kw: result_obj,
        )

        runner = CliRunner()
        result = runner.invoke(main, [
            "deps", "compare", binary_rel,
            "--baseline", str(baseline),
            "--candidate", str(candidate),
            "--format", "json",
        ])
        assert result.exit_code == 1

    def test_stack_check_exit_0_on_pass(self, tmp_path, env_dirs, monkeypatch):
        baseline, candidate = env_dirs
        binary_rel = "usr/bin/myapp"

        result_obj = _make_result(
            binary_rel,
            loadability=StackVerdict.PASS,
            abi_risk=StackVerdict.PASS,
            risk_score="low",
            baseline_env=str(baseline),
            candidate_env=str(candidate),
        )
        monkeypatch.setattr(
            "abicheck.stack_checker.check_stack",
            lambda *a, **kw: result_obj,
        )

        runner = CliRunner()
        result = runner.invoke(main, [
            "deps", "compare", binary_rel,
            "--baseline", str(baseline),
            "--candidate", str(candidate),
            "--format", "json",
        ])
        assert result.exit_code == 0

    def test_stack_check_rejects_same_sysroot(self, tmp_path):
        same = tmp_path / "root"
        same.mkdir()

        runner = CliRunner()
        result = runner.invoke(main, [
            "deps", "compare", "usr/bin/myapp",
            "--baseline", str(same),
            "--candidate", str(same),
        ])
        assert result.exit_code != 0
        assert "same sysroot" in result.output
