"""Tests for abi_check.reporter — JSON and Markdown output."""
import json

from abicheck.checker import Change, ChangeKind, DiffResult, Verdict
from abicheck.reporter import to_json, to_markdown, to_review_digest


class TestReviewDigest:
    def test_breaking_digest_has_verdict_and_recommendation(self):
        c = Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "Public function removed: foo")
        out = to_review_digest(_result(Verdict.BREAKING, changes=[c]))
        assert "ABI review" in out
        assert "`BREAKING`" in out
        assert "Release recommendation:" in out
        assert "_Z3foov" in out  # top impacted symbol

    def test_manual_review_banner_on_scope_fallback(self):
        r = _result(Verdict.BREAKING, changes=[
            Change(ChangeKind.FUNC_REMOVED, "x", "removed"),
        ])
        r.scope_to_public_surface = True
        r.scope_resolved = False
        out = to_review_digest(r)
        assert "Manual review required" in out
        assert "unconfirmed" in out.lower()

    def test_no_banner_when_scope_resolved(self):
        r = _result(Verdict.COMPATIBLE)
        r.scope_to_public_surface = True
        r.scope_resolved = True
        out = to_review_digest(r)
        assert "Manual review required" not in out
        # Scoped reports label additions as public and show the filtered row.
        assert "Public additions" in out
        assert "Filtered (internal/private)" in out


def _result(verdict: Verdict, changes=None) -> DiffResult:
    return DiffResult(
        old_version="1.0", new_version="2.0",
        library="libtest.so.1",
        changes=changes or [],
        verdict=verdict,
    )


class TestJsonReporter:
    def test_no_change_json(self):
        r = _result(Verdict.NO_CHANGE)
        d = json.loads(to_json(r))
        assert d["verdict"] == "NO_CHANGE"
        assert d["summary"]["total_changes"] == 0

    def test_breaking_json_has_changes(self):
        c = Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "Public function removed: foo")
        r = _result(Verdict.BREAKING, changes=[c])
        d = json.loads(to_json(r))
        assert d["verdict"] == "BREAKING"
        assert d["summary"]["breaking"] == 1
        assert d["changes"][0]["kind"] == "func_removed"


class TestMarkdownReporter:
    def test_no_change_contains_no_change(self):
        md = to_markdown(_result(Verdict.NO_CHANGE))
        assert "NO_CHANGE" in md
        assert "No ABI changes" in md

    def test_breaking_contains_section(self):
        c = Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "Public function removed: foo",
                   old_value="foo")
        md = to_markdown(_result(Verdict.BREAKING, [c]))
        assert "❌ Breaking Changes" in md
        assert "func_removed" in md

    def test_compatible_section(self):
        c = Change(ChangeKind.FUNC_ADDED, "_Z6newapiv", "New public function: new_api",
                   new_value="new_api")
        md = to_markdown(_result(Verdict.COMPATIBLE, [c]))
        assert "COMPATIBLE" in md
        assert "Additions" in md

    def test_noexcept_added_in_quality_section(self):
        c = Change(ChangeKind.FUNC_NOEXCEPT_ADDED, "_Z4swapv",
                   "noexcept specifier added: swap")
        md = to_markdown(_result(Verdict.COMPATIBLE, [c]))
        assert "COMPATIBLE" in md
        assert "Quality Issues" in md

    def test_legend_always_present(self):
        md = to_markdown(_result(Verdict.NO_CHANGE))
        assert "Legend" in md

    def test_risk_changes_in_json(self):
        """JSON summary must include risk_changes field with correct count."""
        c = Change(ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED, "libc.so.6",
                   "New GLIBC_2.34 version requirement added")
        r = _result(Verdict.COMPATIBLE_WITH_RISK, changes=[c])
        d = json.loads(to_json(r))
        assert d["verdict"] == "COMPATIBLE_WITH_RISK"
        assert "risk_changes" in d["summary"], "JSON summary must contain 'risk_changes' key"
        assert d["summary"]["risk_changes"] == 1
        assert d["summary"]["breaking"] == 0

    def test_risk_section_in_markdown(self):
        """Markdown must include Deployment Risk Changes section when risk > 0."""
        c = Change(ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED, "libc.so.6",
                   "New GLIBC_2.34 version requirement added")
        md = to_markdown(_result(Verdict.COMPATIBLE_WITH_RISK, [c]))
        assert "COMPATIBLE_WITH_RISK" in md
        assert "⚠️ Deployment Risk Changes" in md
        assert "binary-compatible" in md
        assert "symbol_version_required_added" in md

    def test_compatible_with_risk_emoji_in_markdown(self):
        """COMPATIBLE_WITH_RISK verdict uses ⚠️ emoji in header table."""
        c = Change(ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED, "libc.so.6",
                   "New GLIBC_2.34 version requirement added")
        md = to_markdown(_result(Verdict.COMPATIBLE_WITH_RISK, [c]))
        assert "⚠️ `COMPATIBLE_WITH_RISK`" in md


# ---------------------------------------------------------------------------
# Severity-aware reporter output
# ---------------------------------------------------------------------------

class TestSeverityMarkdown:
    """Tests for to_markdown with severity_config parameter."""

    def test_severity_badges_shown_when_config_provided(self):
        """Section header for breaking changes includes ERROR badge."""
        from abicheck.severity import PRESET_DEFAULT
        c = Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "Public function removed: foo",
                   old_value="foo")
        md = to_markdown(_result(Verdict.BREAKING, [c]), severity_config=PRESET_DEFAULT)
        # Exact section header produced by the reporter
        assert "## \u274c Breaking Changes \u274c `ERROR`" in md

    def test_severity_badges_absent_without_config(self):
        """Section headers do NOT include severity badges without severity_config."""
        c = Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "Public function removed: foo",
                   old_value="foo")
        md = to_markdown(_result(Verdict.BREAKING, [c]))
        # Without severity_config, the header has no badge suffix
        assert "## \u274c Breaking Changes\n" in md
        assert "`ERROR`" not in md
        assert "`WARNING`" not in md
        assert "`INFO`" not in md

    def test_severity_summary_table_in_markdown(self):
        """Markdown includes a severity configuration table when config is provided."""
        from abicheck.severity import PRESET_STRICT
        c = Change(ChangeKind.FUNC_ADDED, "_Z6newapiv", "New public function: new_api")
        md = to_markdown(_result(Verdict.COMPATIBLE, [c]), severity_config=PRESET_STRICT)
        assert "## Severity Configuration" in md
        # Exact table rows
        assert "| ABI/API Incompatibilities |" in md
        assert "| Additions |" in md

    def test_severity_summary_absent_without_config(self):
        """Markdown does NOT include severity table without config."""
        c = Change(ChangeKind.FUNC_ADDED, "_Z6newapiv", "New public function: new_api")
        md = to_markdown(_result(Verdict.COMPATIBLE, [c]))
        assert "Severity Configuration" not in md

    def test_quality_section_with_severity_label(self):
        """Quality section header includes WARNING badge."""
        from abicheck.severity import PRESET_DEFAULT
        c = Change(ChangeKind.FUNC_NOEXCEPT_ADDED, "_Z4swapv",
                   "noexcept specifier added: swap")
        md = to_markdown(_result(Verdict.COMPATIBLE, [c]), severity_config=PRESET_DEFAULT)
        # Exact section header
        assert "## \U0001f50d Quality Issues \u26a0\ufe0f `WARNING`" in md

    def test_additions_section_with_severity_label(self):
        """Additions section header includes INFO badge."""
        from abicheck.severity import PRESET_DEFAULT
        c = Change(ChangeKind.FUNC_ADDED, "_Z6newapiv", "New public function: new_api")
        md = to_markdown(_result(Verdict.COMPATIBLE, [c]), severity_config=PRESET_DEFAULT)
        # Exact section header
        assert "## \u2705 Additions \u2139\ufe0f `INFO`" in md


class TestSeverityJson:
    """Tests for to_json with severity_config parameter."""

    def test_severity_section_in_json(self):
        """JSON output includes severity section when config is provided."""
        from abicheck.severity import PRESET_DEFAULT
        c = Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "Public function removed: foo")
        r = _result(Verdict.BREAKING, changes=[c])
        d = json.loads(to_json(r, severity_config=PRESET_DEFAULT))
        assert "severity" in d
        sev = d["severity"]
        assert "config" in sev
        assert sev["config"]["abi_breaking"] == "error"
        assert sev["config"]["addition"] == "info"
        assert "categories" in sev
        assert sev["categories"]["abi_breaking"]["count"] == 1

    def test_severity_absent_in_json_without_config(self):
        """JSON output does NOT include severity section without config."""
        c = Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "Public function removed: foo")
        r = _result(Verdict.BREAKING, changes=[c])
        d = json.loads(to_json(r))
        assert "severity" not in d

    def test_severity_exit_code_in_json(self):
        """JSON severity section includes computed exit_code."""
        from abicheck.severity import PRESET_STRICT
        c = Change(ChangeKind.FUNC_ADDED, "_Z6newapiv", "New public function: new_api")
        r = _result(Verdict.COMPATIBLE, changes=[c])
        d = json.loads(to_json(r, severity_config=PRESET_STRICT))
        assert d["severity"]["exit_code"] == 1

    def test_severity_category_counts(self):
        """JSON severity categories have correct counts for mixed changes."""
        from abicheck.severity import PRESET_DEFAULT
        changes = [
            Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "removed: foo"),
            Change(ChangeKind.FUNC_ADDED, "_Z3barv", "added: bar"),
            Change(ChangeKind.VISIBILITY_LEAK, "std::string", "std symbol exposed"),
        ]
        r = _result(Verdict.BREAKING, changes=changes)
        d = json.loads(to_json(r, severity_config=PRESET_DEFAULT))
        cats = d["severity"]["categories"]
        assert cats["abi_breaking"]["count"] == 1
        assert cats["addition"]["count"] == 1
        assert cats["quality_issues"]["count"] == 1
        assert cats["potential_breaking"]["count"] == 0


# ---------------------------------------------------------------------------
# Confidence, evidence tiers, coverage warnings, and policy in reports
# ---------------------------------------------------------------------------

class TestConfidenceInJson:
    """JSON report must include confidence, evidence_tiers, and coverage_warnings."""

    def test_default_confidence_high(self):
        r = _result(Verdict.NO_CHANGE)
        d = json.loads(to_json(r))
        assert d["confidence"] == "high"
        assert d["evidence_tiers"] == []
        assert "coverage_warnings" not in d  # omitted when empty

    def test_confidence_with_tiers(self):
        from abicheck.checker_policy import Confidence
        r = _result(Verdict.BREAKING, [
            Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "removed"),
        ])
        r.confidence = Confidence.MEDIUM
        r.evidence_tiers = ["elf", "header"]
        r.coverage_warnings = ["DWARF debug info not available"]
        d = json.loads(to_json(r))
        assert d["confidence"] == "medium"
        assert d["evidence_tiers"] == ["elf", "header"]
        assert d["coverage_warnings"] == ["DWARF debug info not available"]

    def test_policy_overrides_in_json(self):
        from abicheck.policy_file import PolicyFile
        pf = PolicyFile(
            base_policy="strict_abi",
            overrides={ChangeKind.FUNC_REMOVED: Verdict.COMPATIBLE},
        )
        r = _result(Verdict.COMPATIBLE)
        r.policy_file = pf
        d = json.loads(to_json(r))
        assert d["policy_overrides"] == {"func_removed": "COMPATIBLE"}

    def test_policy_overrides_absent_without_file(self):
        r = _result(Verdict.NO_CHANGE)
        d = json.loads(to_json(r))
        assert "policy_overrides" not in d


class TestConfidenceInMarkdown:
    """Markdown report must include Analysis Confidence section."""

    def test_confidence_section_present(self):
        from abicheck.checker_policy import Confidence
        r = _result(Verdict.COMPATIBLE, [
            Change(ChangeKind.FUNC_ADDED, "_Z3barv", "added: bar"),
        ])
        r.confidence = Confidence.LOW
        r.evidence_tiers = ["elf"]
        r.coverage_warnings = ["DWARF stripped"]
        md = to_markdown(r)
        assert "## Analysis Confidence" in md
        assert "LOW" in md
        assert "`elf`" in md
        assert "DWARF stripped" in md

    def test_policy_shown_in_markdown(self):
        r = _result(Verdict.NO_CHANGE)
        md = to_markdown(r)
        assert "**Policy**: `strict_abi`" in md

    def test_policy_overrides_shown(self):
        from abicheck.policy_file import PolicyFile
        pf = PolicyFile(
            base_policy="strict_abi",
            overrides={ChangeKind.FUNC_REMOVED: Verdict.COMPATIBLE},
        )
        r = _result(Verdict.NO_CHANGE)
        r.policy_file = pf
        md = to_markdown(r)
        assert "**Policy overrides**" in md
        assert "`func_removed`" in md


# ---------------------------------------------------------------------------
# AppCompat report traceability (file metadata + confidence)
# ---------------------------------------------------------------------------

class TestAppCompatTraceability:
    """AppCompat JSON/Markdown include file metadata and confidence when available."""

    def _appcompat_result(self):
        from types import SimpleNamespace

        from abicheck.checker_policy import Confidence
        diff = _result(Verdict.COMPATIBLE)
        diff.old_metadata = SimpleNamespace(path="/old/lib.so", sha256="aabb" * 8, size_bytes=4096)
        diff.new_metadata = SimpleNamespace(path="/new/lib.so", sha256="ccdd" * 8, size_bytes=8192)
        diff.confidence = Confidence.MEDIUM
        diff.evidence_tiers = ["elf", "header"]
        diff.coverage_warnings = []
        return SimpleNamespace(
            app_path="/bin/app",
            old_lib_path="/old/lib.so",
            new_lib_path="/new/lib.so",
            verdict=Verdict.COMPATIBLE,
            symbol_coverage=100.0,
            required_symbol_count=10,
            missing_symbols=[],
            missing_versions=[],
            breaking_for_app=[],
            irrelevant_for_app=[],
            full_diff=diff,
        )

    def test_appcompat_json_includes_file_metadata(self):
        from abicheck.reporter import appcompat_to_json
        r = self._appcompat_result()
        d = json.loads(appcompat_to_json(r))
        assert d["old_file"]["path"] == "/old/lib.so"
        assert d["new_file"]["path"] == "/new/lib.so"
        assert d["old_file"]["size_bytes"] == 4096
        assert d["confidence"] == "medium"
        assert d["evidence_tiers"] == ["elf", "header"]

    def test_appcompat_markdown_includes_file_metadata(self):
        from abicheck.reporter import appcompat_to_markdown
        r = self._appcompat_result()
        md = appcompat_to_markdown(r)
        assert "Library Files" in md
        assert "/old/lib.so" in md
        assert "**Confidence**" in md

    def test_appcompat_markdown_includes_policy(self):
        from abicheck.reporter import appcompat_to_markdown
        r = self._appcompat_result()
        md = appcompat_to_markdown(r)
        assert "**Policy**" in md
        assert "`strict_abi`" in md


class TestStatJsonConfidence:
    """Stat JSON must include confidence and evidence_tiers."""

    def test_stat_json_default_confidence(self):
        r = _result(Verdict.NO_CHANGE)
        d = json.loads(to_json(r, stat=True))
        assert d["confidence"] == "high"
        assert d["evidence_tiers"] == []
        assert "coverage_warnings" not in d

    def test_stat_json_with_confidence(self):
        from abicheck.checker_policy import Confidence
        r = _result(Verdict.BREAKING, [
            Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "removed"),
        ])
        r.confidence = Confidence.LOW
        r.evidence_tiers = ["elf"]
        r.coverage_warnings = ["DWARF stripped"]
        d = json.loads(to_json(r, stat=True))
        assert d["confidence"] == "low"
        assert d["evidence_tiers"] == ["elf"]
        assert d["coverage_warnings"] == ["DWARF stripped"]
