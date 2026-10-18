"""Unit tests for abicheck.frontends.render: render_output and the dependencies section."""

from __future__ import annotations

import json

import pytest

from abicheck.checker_types import DiffResult
from abicheck.errors import ValidationError
from abicheck.frontends.render import (
    _render_deps_section_md,
    render_output,
)
from abicheck.model import AbiSnapshot, DependencyInfo, Function

# ── render_output() ─────────────────────────────────────────────────────────


class TestRenderOutput:
    @pytest.fixture
    def snap(self):
        return AbiSnapshot(
            library="libtest",
            version="1.0",
            functions=[Function(name="foo", mangled="foo", return_type="int")],
        )

    @pytest.fixture
    def diff_result(self):
        return DiffResult(old_version="1.0", new_version="2.0", library="libtest")

    def test_json_format(self, diff_result, snap):
        out = render_output("json", diff_result, snap)
        d = json.loads(out)
        assert d["library"] == "libtest"

    def test_markdown_format(self, diff_result, snap):
        out = render_output("markdown", diff_result, snap)
        assert isinstance(out, str)
        assert "libtest" in out

    def test_md_format(self, diff_result, snap):
        # "md" is an alias of "markdown": same renderer, same output.
        out = render_output("md", diff_result, snap)
        assert isinstance(out, str)
        assert "libtest" in out

    def test_sarif_format(self, diff_result, snap):
        out = render_output("sarif", diff_result, snap)
        d = json.loads(out)
        assert "$schema" in d or "runs" in d

    def test_html_format(self, diff_result, snap):
        out = render_output("html", diff_result, snap)
        assert (
            "<html" in out.lower()
            or "<!doctype" in out.lower()
            or "<div" in out.lower()
        )

    def test_unsupported_format_raises(self, diff_result, snap):
        with pytest.raises(ValidationError, match="Unsupported output format"):
            render_output("xml", diff_result, snap)

    def test_oneline_format(self, diff_result, snap):
        # CLI cleanup phase two, PR 1: --stat's boolean parameter is gone --
        # the one-line summary is now its own fmt value
        # (service_render.ONELINE_FORMAT), reached only via the built-in
        # `quick` --profile at the CLI layer, but directly callable here as
        # a plain fmt string like any other format.
        from abicheck.frontends.render import ONELINE_FORMAT

        out = render_output(ONELINE_FORMAT, diff_result, snap)
        assert isinstance(out, str)
        assert "\n" not in out.strip()

    def test_stat_json_document_is_summary_only(self, diff_result, snap):
        """``to_stat_json`` is the summary-only JSON document: no ``changes``
        array, but the verdict.

        This claim used to be asserted through ``render_output("json", ...,
        stat=True)``, a compatibility shim standing in for the removed
        ``--stat`` flag. The shim is gone -- it was a *dispatch* flag
        ("ignore the format I asked for and render a different document")
        that also sat above ``_reject_unsupported_format``, so an invalid
        format returned a summary instead of raising. The document it reached
        is unchanged and is asserted here directly, at the function that owns
        it."""
        from abicheck.reporter import to_stat_json

        d = json.loads(to_stat_json(diff_result))
        assert "changes" not in d
        assert d["verdict"] == diff_result.verdict.value

    def test_stat_json_forwards_require_complete_analysis(self, diff_result, snap):
        """Codex/CodeRabbit review: the assurance exit contribution must reach
        the summary-only document too, not just the full report.

        Asserted against ``to_stat_json`` directly now that ``render_output``
        has no ``stat`` keyword to forward through."""
        from abicheck.analysis_assurance import AnalysisAssurance
        from abicheck.reporter import to_stat_json

        diff_result.analysis_assurance = AnalysisAssurance(status="partial")
        d = json.loads(to_stat_json(diff_result, require_complete_analysis=True))
        assert d["analysis_assurance_exit_contribution"] == 1

    def test_show_recommendation_false_still_suppresses_the_section(
        self, diff_result, snap
    ):
        """``show_recommendation`` is a real, effective toggle, not an inert
        parameter -- an earlier revision hard-coded ``True`` into the
        ``to_markdown`` call regardless of what the caller passed, silently
        reintroducing the section for a caller that explicitly asked it be
        suppressed. Still the contract after the default flipped: the
        *default* changed, the parameter's effect did not."""
        with_rec = render_output(
            "markdown", diff_result, snap, show_recommendation=True
        )
        without_rec = render_output(
            "markdown", diff_result, snap, show_recommendation=False
        )
        assert with_rec != without_rec

    def test_show_recommendation_default_matches_the_cli(self, diff_result, snap):
        """The default is ``True``, and the CLI's own wrapper agrees with it.

        This deliberately reverses an earlier decision, so the reasoning
        matters. Two prior review rounds argued the default must stay
        ``False`` -- the pre-removal Tier-2 value -- with the CLI passing
        ``True`` explicitly to get its own unconditional inclusion. That kept
        an existing caller's output byte-stable, but it left the two front
        ends disagreeing about what omitting the keyword means: the CLI's
        recommendation was unconditional while a Tier-2 caller silently got
        no recommendation section at all, which made ``render_output``'s own
        docstring ("unconditionally included in every human-facing format")
        false for the audience it was written for.

        Aligning the default is the point: one behavior for every caller that
        does not ask otherwise, with suppression still available as an
        explicit opt-out (asserted above). The CLI's rendered output is
        unchanged by this -- it passed ``True`` before and gets ``True`` now,
        verified byte-for-byte across all seven ``--format`` values against
        the pre-change revision."""
        from abicheck.frontends.cli.compare_report import _render_output

        default_markdown = render_output("markdown", diff_result, snap)
        explicit_true = render_output(
            "markdown", diff_result, snap, show_recommendation=True
        )
        cli_markdown = _render_output("markdown", diff_result, snap)

        assert default_markdown == explicit_true
        assert cli_markdown == default_markdown

    def test_json_follow_deps(self, snap):
        snap.dependency_info = DependencyInfo(
            nodes=[{"soname": "libc.so.6", "depth": 0}],
        )
        diff_result = DiffResult(
            old_version="1.0", new_version="2.0", library="libtest"
        )
        out = render_output("json", diff_result, snap, follow_deps=True)
        d = json.loads(out)
        assert "old_dependency_info" in d

    def test_markdown_follow_deps(self, snap):
        snap.dependency_info = DependencyInfo(
            nodes=[{"soname": "libc.so.6", "depth": 0}],
        )
        diff_result = DiffResult(
            old_version="1.0", new_version="2.0", library="libtest"
        )
        out = render_output("markdown", diff_result, snap, follow_deps=True)
        assert "Dependency" in out

    def test_html_with_new_snap(self, snap):
        new_snap = AbiSnapshot(library="libtest", version="2.0")
        diff_result = DiffResult(
            old_version="1.0", new_version="2.0", library="libtest"
        )
        out = render_output("html", diff_result, snap, new=new_snap)
        assert isinstance(out, str)
        assert "<html" in out.lower()
        assert "libtest" in out


# ── _render_deps_section_md() ──────────────────────────────────────────────


class TestRenderDepsSection:
    def test_basic_deps(self):
        old = AbiSnapshot(library="lib", version="1.0")
        old.dependency_info = DependencyInfo(
            nodes=[{"soname": "libc.so.6", "depth": 0, "resolution_reason": "system"}],
            bindings_summary={"GLOBAL": 5},
            unresolved=[{"soname": "libmissing.so", "consumer": "lib.so"}],
            missing_symbols=[
                {"symbol": "foo", "version": "GLIBC_2.17"},
                {"symbol": "bar"},
            ],
        )
        result = _render_deps_section_md(old, None)
        assert "libc.so.6" in result
        assert "GLOBAL" in result
        assert "libmissing.so" in result
        assert "foo" in result
        assert "bar" in result

    def test_no_dep_info(self):
        old = AbiSnapshot(library="lib", version="1.0")
        result = _render_deps_section_md(old, None)
        assert "Dependency" in result
        # Should still have the header

    def test_missing_symbols_truncated(self):
        old = AbiSnapshot(library="lib", version="1.0")
        old.dependency_info = DependencyInfo(
            missing_symbols=[{"symbol": f"sym{i}"} for i in range(15)],
        )
        result = _render_deps_section_md(old, None)
        assert "+5 more" in result

    def test_non_int_depth(self):
        old = AbiSnapshot(library="lib", version="1.0")
        old.dependency_info = DependencyInfo(
            nodes=[{"soname": "libc.so.6", "depth": "invalid"}],
        )
        result = _render_deps_section_md(old, None)
        assert "libc.so.6" in result

    def test_both_old_and_new(self):
        old = AbiSnapshot(library="lib", version="1.0")
        old.dependency_info = DependencyInfo(nodes=[{"soname": "old.so", "depth": 0}])
        new = AbiSnapshot(library="lib", version="2.0")
        new.dependency_info = DependencyInfo(nodes=[{"soname": "new.so", "depth": 0}])
        result = _render_deps_section_md(old, new)
        assert "old.so" in result
        assert "new.so" in result
