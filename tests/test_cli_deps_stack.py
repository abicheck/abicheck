"""Tests for the CLI `deps` and `stack-check` commands."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.binder import BindingStatus, SymbolBinding
from abicheck.cli import main
from abicheck.resolver import DependencyGraph, ResolvedDSO
from abicheck.stack_checker import StackChange, StackCheckResult, StackVerdict

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
    stack_changes: list[StackChange] | None = None,
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
        stack_changes=stack_changes if stack_changes is not None else [],
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
        result = runner.invoke(main, ["deps", "tree", str(binary), "-o", "json=-"])
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
        result = runner.invoke(main, ["deps", "tree", str(binary), "-o", "markdown=-"])
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
            main,
            ["deps", "tree", str(binary), "-o", f"json={outfile}"],
        )
        assert result.exit_code == 0, result.output
        assert outfile.exists()
        parsed = json.loads(outfile.read_text())
        assert parsed["root_binary"] == str(binary)

    def test_deps_exit_code_1_on_fail(self, tmp_path, monkeypatch):
        binary = tmp_path / "myapp"
        binary.write_bytes(b"\x7fELF" + b"\x00" * 60)

        result_obj = _make_result(
            str(binary),
            loadability=StackVerdict.FAIL,
            risk_score="high",
        )
        monkeypatch.setattr(
            "abicheck.stack_checker.check_single_env",
            lambda *a, **kw: result_obj,
        )

        runner = CliRunner()
        result = runner.invoke(main, ["deps", "tree", str(binary), "-o", "json=-"])
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
        result = runner.invoke(
            main,
            [
                "deps",
                "tree",
                str(binary),
                "--sysroot",
                str(sysroot),
                "--search-path",
                str(search_dir),
                "-o",
                "json=-",
            ],
        )
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
        result = runner.invoke(
            main,
            [
                "deps",
                "compare",
                binary_rel,
                "--old-root",
                str(baseline),
                "--new-root",
                str(candidate),
                "-o",
                "json=-",
            ],
        )
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
        result = runner.invoke(
            main,
            [
                "deps",
                "compare",
                binary_rel,
                "--old-root",
                str(baseline),
                "--new-root",
                str(candidate),
                "-o",
                "markdown=-",
            ],
        )
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
        result = runner.invoke(
            main,
            [
                "deps",
                "compare",
                binary_rel,
                "--old-root",
                str(baseline),
                "--new-root",
                str(candidate),
                "-o",
                f"json={outfile}",
            ],
        )
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
        result = runner.invoke(
            main,
            [
                "deps",
                "compare",
                binary_rel,
                "--old-root",
                str(baseline),
                "--new-root",
                str(candidate),
                "-o",
                "json=-",
            ],
        )
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
        result = runner.invoke(
            main,
            [
                "deps",
                "compare",
                binary_rel,
                "--old-root",
                str(baseline),
                "--new-root",
                str(candidate),
                "-o",
                "json=-",
            ],
        )
        assert result.exit_code == 4

    def test_stack_check_exit_5_not_comparable_dominates_fail(
        self, tmp_path, env_dirs, monkeypatch
    ):
        # ADR-050 D2: a not_comparable dependency dominates the release-level
        # rollup even when the rest of the stack computes to FAIL -- the
        # comparison couldn't establish what changed for that library at all.
        baseline, candidate = env_dirs
        binary_rel = "usr/bin/myapp"

        result_obj = _make_result(
            binary_rel,
            loadability=StackVerdict.PASS,
            abi_risk=StackVerdict.FAIL,
            risk_score="high",
            baseline_env=str(baseline),
            candidate_env=str(candidate),
            stack_changes=[
                StackChange(
                    library="libbar.so",
                    change_type="content_changed",
                    not_comparable_reason="scope drift",
                ),
            ],
        )
        monkeypatch.setattr(
            "abicheck.stack_checker.check_stack",
            lambda *a, **kw: result_obj,
        )

        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "deps",
                "compare",
                binary_rel,
                "--old-root",
                str(baseline),
                "--new-root",
                str(candidate),
                "-o",
                "json=-",
            ],
        )
        assert result.exit_code == 5

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
        result = runner.invoke(
            main,
            [
                "deps",
                "compare",
                binary_rel,
                "--old-root",
                str(baseline),
                "--new-root",
                str(candidate),
                "-o",
                "json=-",
            ],
        )
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
        result = runner.invoke(
            main,
            [
                "deps",
                "compare",
                binary_rel,
                "--old-root",
                str(baseline),
                "--new-root",
                str(candidate),
                "-o",
                "json=-",
            ],
        )
        assert result.exit_code == 0

    def test_stack_check_rejects_same_sysroot(self, tmp_path):
        same = tmp_path / "root"
        same.mkdir()

        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "deps",
                "compare",
                "usr/bin/myapp",
                "--old-root",
                str(same),
                "--new-root",
                str(same),
            ],
        )
        assert result.exit_code != 0
        assert "same sysroot" in result.output


# ---------------------------------------------------------------------------
# Defaulted environment roots (one-comparison-product.md Phase 7l, `deps` item)
# ---------------------------------------------------------------------------


class TestDefaultedEnvironmentRoot:
    """An unspecified `--sysroot`/`--old-root`/`--new-root` is this host's
    own filesystem, and every projection has to say so rather than print a
    bare `/` that reads like a chosen deployment environment.

    The discriminating case is an *explicitly given* `/`: a report that
    labels that one "defaulted" is just as wrong as one that labels the
    fallback as chosen, so each assertion below has its explicit twin.
    """

    @staticmethod
    def _elf(tmp_path: Path, name: str = "myapp") -> Path:
        binary = tmp_path / name
        binary.write_bytes(b"\x7fELF" + b"\x00" * 60)
        return binary

    def test_single_env_result_records_the_defaulted_root(self, tmp_path) -> None:
        from abicheck.stack_checker import check_single_env

        binary = self._elf(tmp_path)
        defaulted = check_single_env(binary)
        assert defaulted.baseline_env == "/"
        assert defaulted.baseline_env_defaulted is True
        assert defaulted.candidate_env_defaulted is True

        chosen = check_single_env(binary, sysroot=tmp_path)
        assert chosen.baseline_env == str(tmp_path)
        assert chosen.baseline_env_defaulted is False

    def test_json_projection_states_it_for_both_sides(self) -> None:
        from abicheck.report.stack import compute_stack_report_mapping

        result = _make_result("/app", baseline_env="/", candidate_env="/img")
        result.baseline_env_defaulted = True
        mapping = compute_stack_report_mapping(result)
        assert mapping["baseline_env_defaulted"] is True
        # Always emitted, so an absent key can never be read as "chosen".
        assert mapping["candidate_env_defaulted"] is False

    def test_markdown_and_html_annotate_only_the_defaulted_side(self) -> None:
        from abicheck.report.stack import DEFAULTED_ROOT_NOTE
        from abicheck.stack_html import stack_to_html
        from abicheck.stack_report import stack_to_markdown

        result = _make_result("/app", baseline_env="/", candidate_env="/img")
        result.baseline_env_defaulted = True
        for rendered in (stack_to_markdown(result), stack_to_html(result)):
            assert DEFAULTED_ROOT_NOTE in rendered
            # One annotation, on the defaulted side only.
            assert rendered.count(DEFAULTED_ROOT_NOTE) == 1

        chosen = _make_result("/app", baseline_env="/", candidate_env="/img")
        for rendered in (stack_to_markdown(chosen), stack_to_html(chosen)):
            assert DEFAULTED_ROOT_NOTE not in rendered

    def test_single_environment_report_states_it_too(self, tmp_path) -> None:
        """A `deps tree` has one root on both sides, so the two env values
        are necessarily equal -- the differing-roots test alone made the
        note unreachable in Markdown and HTML, leaving it only in JSON and
        the plan (Codex review, PR #1278)."""
        from abicheck.report.stack import DEFAULTED_ROOT_NOTE
        from abicheck.stack_html import stack_to_html
        from abicheck.stack_report import stack_to_markdown

        defaulted = _make_result("/app", baseline_env="/", candidate_env="/")
        defaulted.baseline_env_defaulted = True
        defaulted.candidate_env_defaulted = True
        for rendered in (stack_to_markdown(defaulted), stack_to_html(defaulted)):
            assert DEFAULTED_ROOT_NOTE in rendered
            # One root, reported once -- not as a baseline/candidate pair
            # that would read as two environments.
            assert rendered.count(DEFAULTED_ROOT_NOTE) == 1
            assert "Baseline" not in rendered

        chosen = _make_result("/app", baseline_env="/img", candidate_env="/img")
        for rendered in (stack_to_markdown(chosen), stack_to_html(chosen)):
            assert DEFAULTED_ROOT_NOTE not in rendered

    def test_deps_tree_plan_states_the_defaulted_sysroot(self, tmp_path) -> None:
        from abicheck.report.stack import DEFAULTED_ROOT_NOTE

        binary = self._elf(tmp_path)
        runner = CliRunner()
        defaulted = runner.invoke(main, ["deps", "tree", str(binary), "--dry-run"])
        assert DEFAULTED_ROOT_NOTE in defaulted.output
        assert "sysroot: /" in defaulted.output

        chosen = runner.invoke(
            main, ["deps", "tree", str(binary), "--sysroot", str(tmp_path), "--dry-run"]
        )
        assert DEFAULTED_ROOT_NOTE not in chosen.output

    @pytest.mark.parametrize("explicit_side", ["old", "new", "both", "neither"])
    def test_deps_compare_plan_labels_exactly_the_defaulted_roots(
        self, tmp_path, explicit_side: str
    ) -> None:
        """Including the case the value alone cannot answer: an explicit
        `--old-root /` is a choice, and must not be labelled a fallback."""
        from abicheck.report.stack import DEFAULTED_ROOT_NOTE

        other = tmp_path / "img"
        other.mkdir()
        args = ["deps", "compare", "usr/bin/x"]
        if explicit_side in ("old", "both"):
            args += ["--old-root", "/"]
        if explicit_side in ("new", "both"):
            args += ["--new-root", str(other)]
        if explicit_side == "old":
            # --new-root must differ from --old-root, so name it too.
            args += ["--new-root", str(other)]
        if explicit_side in ("neither", "new"):
            pass
        args.append("--dry-run")

        result = CliRunner().invoke(main, args)
        expected_notes = {"old": 0, "both": 0, "new": 1, "neither": 0}[explicit_side]
        if explicit_side == "neither":
            # Both roots default to `/`, which the command rejects as a
            # no-op comparison before any plan is emitted -- so this case
            # asserts the usage error, not a label.
            assert result.exit_code == 64
            return
        assert result.output.count(DEFAULTED_ROOT_NOTE) == expected_notes
