"""Tests for abicheck.annotations — GitHub Actions workflow command annotations."""

from __future__ import annotations

from unittest.mock import patch

from abicheck.annotations import (
    _MAX_ANNOTATIONS,
    _classify_change,
    _escape_annotation_data,
    _escape_annotation_value,
    _parse_source_location,
    _title_for_change,
    collect_annotations,
    emit_github_annotations,
    emit_github_step_summary,
    format_annotations,
    is_github_actions,
)
from abicheck.checker import Change, DiffResult, Verdict
from abicheck.checker_policy import ChangeKind


def _result(
    verdict: Verdict,
    changes: list[Change] | None = None,
    policy: str = "strict_abi",
) -> DiffResult:
    return DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="libtest.so.1",
        changes=changes or [],
        verdict=verdict,
        policy=policy,
    )


# ---------------------------------------------------------------------------
# Escaping
# ---------------------------------------------------------------------------

class TestEscapeAnnotationValue:
    def test_colons(self):
        assert _escape_annotation_value("foo:bar") == "foo%3Abar"

    def test_commas(self):
        assert _escape_annotation_value("a,b") == "a%2Cb"

    def test_percent(self):
        assert _escape_annotation_value("100%") == "100%25"

    def test_newlines(self):
        assert _escape_annotation_value("line1\nline2") == "line1%0Aline2"

    def test_carriage_return(self):
        assert _escape_annotation_value("a\rb") == "a%0Db"


class TestEscapeAnnotationData:
    def test_newlines(self):
        assert _escape_annotation_data("line1\nline2") == "line1%0Aline2"

    def test_carriage_return(self):
        assert _escape_annotation_data("a\rb") == "a%0Db"

    def test_percent(self):
        assert _escape_annotation_data("100%") == "100%25"

    def test_preserves_colons(self):
        """Message body should preserve colons (only newlines/percent escaped)."""
        assert _escape_annotation_data("foo:bar") == "foo:bar"

    def test_preserves_commas(self):
        assert _escape_annotation_data("a,b") == "a,b"

    def test_double_encoding_percent_then_newline(self):
        """Percent must be escaped before newline to avoid double-encoding."""
        assert _escape_annotation_data("100%\n") == "100%25%0A"

    def test_double_encoding_percent_then_cr(self):
        assert _escape_annotation_data("50%\r") == "50%25%0D"


# ---------------------------------------------------------------------------
# Source location parsing
# ---------------------------------------------------------------------------

class TestParseSourceLocation:
    def test_file_and_line(self):
        assert _parse_source_location("include/foo.h:42") == ("include/foo.h", "42")

    def test_no_location(self):
        assert _parse_source_location(None) == (None, None)

    def test_empty_string(self):
        assert _parse_source_location("") == (None, None)

    def test_file_only_no_colon(self):
        assert _parse_source_location("foo.h") == ("foo.h", None)

    def test_non_numeric_line(self):
        assert _parse_source_location("foo.h:abc") == ("foo.h:abc", None)

    def test_windows_path_with_line(self):
        """Windows path like C:\\include\\foo.h:42 should parse correctly."""
        assert _parse_source_location("C:\\include\\foo.h:42") == ("C:\\include\\foo.h", "42")

    def test_colon_at_position_zero(self):
        """Leading colon: empty file part, line number extracted."""
        assert _parse_source_location(":42") == ("", "42")

    def test_path_line_col(self):
        """path:line:col should extract file and line, discarding column."""
        assert _parse_source_location("include/foo.h:42:7") == ("include/foo.h", "42")

    def test_windows_path_line_col(self):
        """C:\\foo\\bar.h:42:7 should parse correctly."""
        assert _parse_source_location("C:\\foo\\bar.h:42:7") == ("C:\\foo\\bar.h", "42")


# ---------------------------------------------------------------------------
# _classify_change (direct unit tests)
# ---------------------------------------------------------------------------

class TestClassifyChange:
    """Direct unit tests for _classify_change with explicit kind sets."""

    _breaking = frozenset({ChangeKind.FUNC_REMOVED})
    _api_break = frozenset({ChangeKind.ENUM_MEMBER_RENAMED})
    _risk = frozenset({ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED})
    _compatible = frozenset({ChangeKind.FUNC_ADDED})

    def test_breaking_returns_error(self):
        assert _classify_change(
            ChangeKind.FUNC_REMOVED, self._breaking, self._api_break,
            self._risk, self._compatible, False,
        ) == "error"

    def test_api_break_returns_warning(self):
        assert _classify_change(
            ChangeKind.ENUM_MEMBER_RENAMED, self._breaking, self._api_break,
            self._risk, self._compatible, False,
        ) == "warning"

    def test_risk_returns_warning(self):
        assert _classify_change(
            ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED, self._breaking, self._api_break,
            self._risk, self._compatible, False,
        ) == "warning"

    def test_compatible_returns_none_by_default(self):
        assert _classify_change(
            ChangeKind.FUNC_ADDED, self._breaking, self._api_break,
            self._risk, self._compatible, False,
        ) is None

    def test_compatible_returns_notice_with_flag(self):
        assert _classify_change(
            ChangeKind.FUNC_ADDED, self._breaking, self._api_break,
            self._risk, self._compatible, True,
        ) == "notice"

    def test_unknown_kind_returns_none_even_with_additions_flag(self):
        """A kind not in any set should return None even with annotate_additions=True."""
        assert _classify_change(
            ChangeKind.FUNC_REMOVED, frozenset(), frozenset(),
            frozenset(), frozenset(), True,
        ) is None


# ---------------------------------------------------------------------------
# _title_for_change (direct unit tests)
# ---------------------------------------------------------------------------

class TestTitleForChange:
    """Verify risk changes are labeled differently from API breaks."""

    _breaking = frozenset({ChangeKind.FUNC_REMOVED})
    _api_break = frozenset({ChangeKind.ENUM_MEMBER_RENAMED})
    _risk = frozenset({ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED})
    _compatible = frozenset({ChangeKind.FUNC_ADDED})

    def test_breaking_title(self):
        title = _title_for_change(
            ChangeKind.FUNC_REMOVED, self._breaking, self._api_break,
            self._risk, self._compatible,
        )
        assert title == "ABI Break: func_removed"

    def test_api_break_title(self):
        title = _title_for_change(
            ChangeKind.ENUM_MEMBER_RENAMED, self._breaking, self._api_break,
            self._risk, self._compatible,
        )
        assert title == "API Break: enum_member_renamed"

    def test_risk_title(self):
        title = _title_for_change(
            ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED, self._breaking, self._api_break,
            self._risk, self._compatible,
        )
        assert title == "Deployment Risk: symbol_version_required_added"

    def test_addition_title(self):
        title = _title_for_change(
            ChangeKind.FUNC_ADDED, self._breaking, self._api_break,
            self._risk, self._compatible,
        )
        assert title == "ABI Addition: func_added"

    def test_unknown_kind_title(self):
        """A kind not in any set gets a generic title."""
        title = _title_for_change(
            ChangeKind.FUNC_REMOVED, frozenset(), frozenset(),
            frozenset(), frozenset(),
        )
        assert title == "ABI Change: func_removed"


# ---------------------------------------------------------------------------
# Annotation format (integration via emit_github_annotations)
# ---------------------------------------------------------------------------

class TestAnnotationFormat:
    def test_breaking_change_produces_error(self):
        c = Change(
            ChangeKind.FUNC_REMOVED, "_Z3foov",
            "Public function removed: foo",
            source_location="include/foo.h:42",
        )
        result = _result(Verdict.BREAKING, [c])
        output = emit_github_annotations(result)
        assert output.startswith("::error ")
        assert "file=include/foo.h" in output
        assert "line=42" in output
        assert "title=ABI Break%3A func_removed" in output
        assert "::Public function removed: foo" in output

    def test_api_break_produces_warning(self):
        c = Change(
            ChangeKind.ENUM_MEMBER_RENAMED, "MyEnum::kOld",
            "Enum member renamed: kOld -> kNew",
        )
        result = _result(Verdict.API_BREAK, [c])
        output = emit_github_annotations(result)
        assert output.startswith("::warning ")
        assert "title=API Break%3A enum_member_renamed" in output

    def test_risk_change_produces_warning_with_deployment_risk_title(self):
        c = Change(
            ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED, "libc.so.6",
            "New GLIBC_2.34 version requirement added",
        )
        result = _result(Verdict.COMPATIBLE_WITH_RISK, [c])
        output = emit_github_annotations(result)
        assert output.startswith("::warning ")
        assert "Deployment Risk" in output

    def test_compatible_addition_skipped_by_default(self):
        c = Change(
            ChangeKind.FUNC_ADDED, "_Z6newapiv",
            "New public function: new_api",
        )
        result = _result(Verdict.COMPATIBLE, [c])
        output = emit_github_annotations(result)
        assert output == ""

    def test_compatible_addition_emitted_with_flag(self):
        c = Change(
            ChangeKind.FUNC_ADDED, "_Z6newapiv",
            "New public function: new_api",
        )
        result = _result(Verdict.COMPATIBLE, [c])
        output = emit_github_annotations(result, annotate_additions=True)
        assert output.startswith("::notice ")
        assert "title=ABI Addition%3A func_added" in output

    def test_no_file_line_when_no_source_location(self):
        c = Change(
            ChangeKind.FUNC_REMOVED, "_Z3foov",
            "Public function removed: foo",
        )
        result = _result(Verdict.BREAKING, [c])
        output = emit_github_annotations(result)
        assert "file=" not in output
        assert "line=" not in output
        assert "title=" in output

    def test_no_file_line_when_source_location_empty(self):
        c = Change(
            ChangeKind.FUNC_REMOVED, "_Z3foov",
            "Public function removed: foo",
            source_location="",
        )
        result = _result(Verdict.BREAKING, [c])
        output = emit_github_annotations(result)
        assert "file=" not in output
        assert "line=" not in output

    def test_empty_result(self):
        result = _result(Verdict.NO_CHANGE)
        output = emit_github_annotations(result)
        assert output == ""


# ---------------------------------------------------------------------------
# Annotation limit
# ---------------------------------------------------------------------------

class TestAnnotationLimit:
    def test_max_50_annotations(self):
        changes = [
            Change(
                ChangeKind.FUNC_REMOVED, f"_Z{i}foov",
                f"Public function removed: foo{i}",
            )
            for i in range(60)
        ]
        result = _result(Verdict.BREAKING, changes)
        output = emit_github_annotations(result)
        lines = output.strip().split("\n")
        assert len(lines) == _MAX_ANNOTATIONS

    def test_truncation_preserves_highest_severity(self):
        """When truncated to 50, errors must survive over warnings."""
        errors = [
            Change(ChangeKind.FUNC_REMOVED, f"_Z{i}foov", f"removed: foo{i}")
            for i in range(30)
        ]
        warnings = [
            Change(ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED, f"libc{i}", f"version req {i}")
            for i in range(30)
        ]
        result = _result(Verdict.BREAKING, errors + warnings)
        output = emit_github_annotations(result)
        lines = output.strip().split("\n")
        assert len(lines) == _MAX_ANNOTATIONS
        error_lines = [ln for ln in lines if ln.startswith("::error ")]
        warning_lines = [ln for ln in lines if ln.startswith("::warning ")]
        # All 30 errors must survive; only 20 of 30 warnings fit.
        assert len(error_lines) == 30
        assert len(warning_lines) == 20

    def test_custom_max_annotations(self):
        changes = [
            Change(ChangeKind.FUNC_REMOVED, f"_Z{i}foov", f"removed: foo{i}")
            for i in range(20)
        ]
        result = _result(Verdict.BREAKING, changes)
        output = emit_github_annotations(result, max_annotations=5)
        lines = output.strip().split("\n")
        assert len(lines) == 5


# ---------------------------------------------------------------------------
# Sorting
# ---------------------------------------------------------------------------

class TestAnnotationSorting:
    def test_errors_before_warnings(self):
        changes = [
            Change(
                ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED, "libc.so.6",
                "New version requirement added",
            ),
            Change(
                ChangeKind.FUNC_REMOVED, "_Z3foov",
                "Public function removed: foo",
            ),
        ]
        result = _result(Verdict.BREAKING, changes)
        output = emit_github_annotations(result)
        lines = output.strip().split("\n")
        assert len(lines) == 2
        assert lines[0].startswith("::error ")
        assert lines[1].startswith("::warning ")

    def test_warnings_before_notices(self):
        changes = [
            Change(
                ChangeKind.FUNC_ADDED, "_Z6newapiv",
                "New public function: new_api",
            ),
            Change(
                ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED, "libc.so.6",
                "New version requirement added",
            ),
        ]
        result = _result(Verdict.COMPATIBLE_WITH_RISK, changes)
        output = emit_github_annotations(result, annotate_additions=True)
        lines = output.strip().split("\n")
        assert len(lines) == 2
        assert lines[0].startswith("::warning ")
        assert lines[1].startswith("::notice ")

    def test_errors_before_notices(self):
        changes = [
            Change(
                ChangeKind.FUNC_ADDED, "_Z6newapiv",
                "New public function: new_api",
            ),
            Change(
                ChangeKind.FUNC_REMOVED, "_Z3foov",
                "Public function removed: foo",
            ),
        ]
        result = _result(Verdict.BREAKING, changes)
        output = emit_github_annotations(result, annotate_additions=True)
        lines = output.strip().split("\n")
        assert len(lines) == 2
        assert lines[0].startswith("::error ")
        assert lines[1].startswith("::notice ")


# ---------------------------------------------------------------------------
# is_github_actions
# ---------------------------------------------------------------------------

class TestIsGitHubActions:
    def test_true_when_set(self):
        with patch.dict("os.environ", {"GITHUB_ACTIONS": "true"}):
            assert is_github_actions() is True

    def test_false_when_unset(self):
        with patch.dict("os.environ", {}, clear=True):
            assert is_github_actions() is False

    def test_false_when_other_value(self):
        with patch.dict("os.environ", {"GITHUB_ACTIONS": "false"}):
            assert is_github_actions() is False


# ---------------------------------------------------------------------------
# Special characters in full annotations
# ---------------------------------------------------------------------------

class TestSpecialCharactersInAnnotations:
    def test_description_with_colons(self):
        c = Change(
            ChangeKind.FUNC_PARAMS_CHANGED, "_Z3bazv",
            "Parameter 1 of foo::baz changed from int to long (binary incompatible)",
            source_location="include/foo.h:42",
        )
        result = _result(Verdict.BREAKING, [c])
        output = emit_github_annotations(result)
        # Message body should preserve colons
        assert "::Parameter 1 of foo::baz changed from int to long (binary incompatible)" in output

    def test_description_with_newlines(self):
        c = Change(
            ChangeKind.FUNC_REMOVED, "_Z3foov",
            "Public function removed:\nfoo",
        )
        result = _result(Verdict.BREAKING, [c])
        output = emit_github_annotations(result)
        assert "%0A" in output
        # The output should be a single line (no literal newlines inside the annotation).
        assert output.count("\n") == 0


# ---------------------------------------------------------------------------
# Message truncation
# ---------------------------------------------------------------------------

class TestMessageTruncation:
    def test_long_message_is_truncated(self):
        desc = "x" * 300
        c = Change(ChangeKind.FUNC_REMOVED, "_Z3foov", desc)
        result = _result(Verdict.BREAKING, [c])
        output = emit_github_annotations(result)
        # Extract message after the last `::`
        message_part = output.split("::")[-1]
        assert len(message_part) <= 200
        assert message_part.endswith("...")

    def test_short_message_not_truncated(self):
        desc = "Short description"
        c = Change(ChangeKind.FUNC_REMOVED, "_Z3foov", desc)
        result = _result(Verdict.BREAKING, [c])
        output = emit_github_annotations(result)
        assert "..." not in output
        assert "Short description" in output


# ---------------------------------------------------------------------------
# emit_github_step_summary
# ---------------------------------------------------------------------------

class TestEmitGitHubStepSummary:
    def test_returns_none_when_env_unset(self):
        with patch.dict("os.environ", {}, clear=True):
            result = _result(Verdict.NO_CHANGE)
            assert emit_github_step_summary(result) is None

    def test_returns_none_when_env_empty(self):
        with patch.dict("os.environ", {"GITHUB_STEP_SUMMARY": ""}):
            result = _result(Verdict.NO_CHANGE)
            assert emit_github_step_summary(result) is None

    def test_writes_markdown_and_returns_path(self, tmp_path):
        summary_file = tmp_path / "summary.md"
        with patch.dict("os.environ", {"GITHUB_STEP_SUMMARY": str(summary_file)}):
            result = _result(Verdict.BREAKING, [
                Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "Public function removed: foo"),
            ])
            returned = emit_github_step_summary(result)
        assert returned == str(summary_file)
        content = summary_file.read_text(encoding="utf-8")
        assert "BREAKING" in content
        assert "func_removed" in content

    def test_appends_not_overwrites(self, tmp_path):
        summary_file = tmp_path / "summary.md"
        summary_file.write_text("existing content\n", encoding="utf-8")
        with patch.dict("os.environ", {"GITHUB_STEP_SUMMARY": str(summary_file)}):
            result = _result(Verdict.NO_CHANGE)
            emit_github_step_summary(result)
        content = summary_file.read_text(encoding="utf-8")
        assert content.startswith("existing content\n")
        assert "NO_CHANGE" in content

    def test_forwards_severity_config_to_markdown(self, tmp_path):
        """Codex review on #549: `_maybe_emit_annotations` (cli.py) makes the
        inline GitHub annotations severity-aware, but still called
        `emit_github_step_summary(result)` without `severity_config` — so
        e.g. `--severity-addition error` could fail the annotations/exit code
        while the step summary rendered the legacy compatible report with no
        severity gate section, contradicting the actual gate on the same
        PR."""
        from abicheck.severity import PRESET_DEFAULT

        summary_file = tmp_path / "summary.md"
        with patch.dict("os.environ", {"GITHUB_STEP_SUMMARY": str(summary_file)}):
            result = _result(Verdict.COMPATIBLE, [
                Change(ChangeKind.FUNC_ADDED, "_Z3barv", "New function added: bar"),
            ])
            emit_github_step_summary(result, severity_config=PRESET_DEFAULT)
        content = summary_file.read_text(encoding="utf-8")
        assert "Severity Configuration" in content


# ---------------------------------------------------------------------------
# collect_annotations / format_annotations
# ---------------------------------------------------------------------------

class TestCollectAndFormatAnnotations:
    """Test the building-block functions used for multi-library annotation."""

    def test_collect_returns_tuples(self):
        c = Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "removed: foo")
        result = _result(Verdict.BREAKING, [c])
        annotations = collect_annotations(result)
        assert len(annotations) == 1
        sort_key, line = annotations[0]
        assert sort_key == 0  # error
        assert line.startswith("::error ")

    def test_format_sorts_and_truncates(self):
        raw = [(2, "::notice n"), (0, "::error e"), (1, "::warning w")]
        text = format_annotations(raw, max_annotations=2)
        lines = text.split("\n")
        assert len(lines) == 2
        assert lines[0] == "::error e"
        assert lines[1] == "::warning w"

    def test_cross_library_global_sort(self):
        """Annotations from multiple DiffResults sort globally by severity."""
        warnings = [
            Change(ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED, f"libc{i}", f"req {i}")
            for i in range(40)
        ]
        errors = [
            Change(ChangeKind.FUNC_REMOVED, f"_Z{i}foov", f"removed {i}")
            for i in range(20)
        ]
        result_a = _result(Verdict.COMPATIBLE_WITH_RISK, warnings)
        result_b = _result(Verdict.BREAKING, errors)

        all_annotations = collect_annotations(result_a) + collect_annotations(result_b)
        text = format_annotations(all_annotations)
        lines = text.split("\n")
        assert len(lines) == _MAX_ANNOTATIONS
        error_lines = [ln for ln in lines if ln.startswith("::error ")]
        warning_lines = [ln for ln in lines if ln.startswith("::warning ")]
        # All 20 errors survive; 30 of 40 warnings fit.
        assert len(error_lines) == 20
        assert len(warning_lines) == 30


# ---------------------------------------------------------------------------
# severity_config-aware annotation levels
# ---------------------------------------------------------------------------

class TestSeverityConfigAwareAnnotations:
    """Without severity_config, annotation levels follow the fixed kind-set
    mapping regardless of what actually gates CI. These guard the fix: when a
    SeverityConfig is supplied, the annotation level mirrors the real gate."""

    def test_addition_configured_as_error_gets_error_annotation(self):
        """An addition that severity config promotes to `error` must not be
        silently absent — the build fails on it, so the annotation must too."""
        from abicheck.severity import resolve_severity_config

        c = Change(ChangeKind.FUNC_ADDED, "_Z3newv", "new public function")
        result = _result(Verdict.COMPATIBLE, [c])

        # Legacy behaviour (no severity_config, additions not opted in): silent.
        assert collect_annotations(result) == []

        cfg = resolve_severity_config("default", addition="error")
        annotations = collect_annotations(result, severity_config=cfg)
        assert len(annotations) == 1
        sort_key, line = annotations[0]
        assert sort_key == 0
        assert line.startswith("::error ")

    def test_breaking_demoted_to_info_does_not_emit_error(self):
        """A breaking kind that severity config demotes below `error` must not
        emit `::error` — that would misreport a non-blocking finding as fatal."""
        from abicheck.severity import resolve_severity_config

        c = Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "removed: foo")
        result = _result(Verdict.BREAKING, [c])

        cfg = resolve_severity_config("default", abi_breaking="info")
        # info-level findings are opt-in (annotate_additions), same gate as
        # the legacy compatible/notice behaviour.
        assert collect_annotations(result, severity_config=cfg) == []

        annotations = collect_annotations(
            result, severity_config=cfg, annotate_additions=True,
        )
        assert len(annotations) == 1
        sort_key, line = annotations[0]
        assert sort_key == 2  # notice
        assert line.startswith("::notice ")

    def test_emit_github_annotations_forwards_severity_config(self):
        from abicheck.severity import resolve_severity_config

        c = Change(ChangeKind.FUNC_ADDED, "_Z3newv", "new public function")
        result = _result(Verdict.COMPATIBLE, [c])
        cfg = resolve_severity_config("default", addition="error")
        output = emit_github_annotations(result, severity_config=cfg)
        assert output.startswith("::error ")

    def test_quality_finding_not_mislabeled_as_addition(self):
        """Codex review on #549: a compatible *quality* finding (e.g.
        soname_bump_unnecessary) surfaced by severity_config (the default
        preset sets quality_issues=warning, no annotate_additions needed)
        must not be titled "ABI Addition" — it isn't one."""
        from abicheck.severity import resolve_severity_config

        c = Change(
            ChangeKind.SONAME_BUMP_UNNECESSARY, "libfoo.so", "unnecessary bump",
        )
        result = _result(Verdict.COMPATIBLE, [c])
        cfg = resolve_severity_config("default")  # quality_issues=warning

        annotations = collect_annotations(result, severity_config=cfg)
        assert len(annotations) == 1
        _sort_key, line = annotations[0]
        assert line.startswith("::warning ")
        assert "title=Quality Issue%3A soname_bump_unnecessary" in line
        assert "ABI Addition" not in line

    def test_genuine_addition_still_labeled_as_addition(self):
        from abicheck.severity import resolve_severity_config

        c = Change(ChangeKind.FUNC_ADDED, "_Z3newv", "new public function")
        result = _result(Verdict.COMPATIBLE, [c])
        cfg = resolve_severity_config("default", addition="error")

        annotations = collect_annotations(result, severity_config=cfg)
        assert len(annotations) == 1
        _sort_key, line = annotations[0]
        assert "title=ABI Addition%3A func_added" in line

    def test_potential_breaking_title_honours_effective_verdict_override(self):
        """A raw-risk-set kind (SYMBOL_VERSION_REQUIRED_ADDED) whose
        `effective_verdict` is overridden to API_BREAK must get an "API
        Break" title, not "Deployment Risk" — the label raw kind-set
        membership alone would produce, contradicting the override
        (CodeRabbit review, PR #557)."""
        from abicheck.severity import resolve_severity_config

        c = Change(
            ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED, "f", "version req added",
        )
        c.effective_verdict = Verdict.API_BREAK
        result = _result(Verdict.API_BREAK, [c])
        cfg = resolve_severity_config("default", potential_breaking="error")

        annotations = collect_annotations(result, severity_config=cfg)
        assert len(annotations) == 1
        _sort_key, line = annotations[0]
        assert "title=API Break%3A symbol_version_required_added" in line
        assert "Deployment Risk" not in line

    def test_frozen_namespace_floor_honoured_under_policy_override(self):
        """A policy-file override that demotes FUNC_REMOVED to COMPATIBLE must
        not silently downgrade a finding tagged frozen_namespace_violation —
        the actual exit code (severity.compute_exit_code, given policy_file)
        keeps such a finding at its raw BREAKING severity, so the annotation
        must too, or a build that still fails CI would get no ::error at all.
        """
        from abicheck.checker_policy import Verdict as _Verdict
        from abicheck.policy_file import PolicyFile
        from abicheck.severity import compute_exit_code, resolve_severity_config

        c = Change(
            ChangeKind.FUNC_REMOVED, "_Z3foov", "removed: foo",
            frozen_namespace_violation="**::detail::r1::*",
        )
        pf = PolicyFile(overrides={ChangeKind.FUNC_REMOVED: _Verdict.COMPATIBLE})
        result = DiffResult(
            old_version="1.0", new_version="2.0", library="libtest.so.1",
            changes=[c], verdict=Verdict.BREAKING, policy_file=pf,
        )
        cfg = resolve_severity_config("default")  # abi_breaking=error

        # The actual gate: still fails at the frozen finding's raw severity.
        eff_sets = result._effective_kind_sets()
        exit_code = compute_exit_code(
            result.changes, cfg, kind_sets=eff_sets, policy_file=result.policy_file,
        )
        assert exit_code == 4

        annotations = collect_annotations(result, severity_config=cfg)
        assert len(annotations) == 1
        sort_key, line = annotations[0]
        assert sort_key == 0
        assert line.startswith("::error ")
        # CodeRabbit review: the title must agree with the level — the kind
        # itself was policy-demoted into compatible_set, so a title picked
        # from kind-set membership alone would misreport this ::error as
        # "Quality Issue" (or "ABI Addition") instead of "ABI Break".
        assert "title=ABI Break%3A func_removed" in line
        assert "Quality Issue" not in line
        assert "ABI Addition" not in line
