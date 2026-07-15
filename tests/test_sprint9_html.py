# pylint: disable=too-many-branches,too-many-statements,too-many-locals,too-many-arguments,too-many-return-statements
"""Sprint 9: Tests for ABICC-compatible HTML report generator."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from abicheck.html_report import generate_html_report, write_html_report

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ch(kind: str, symbol: str = "foo", desc: str = "", old: str = "", new: str = "",
        demangled: str = "") -> object:
    """Minimal Change-like object."""
    from enum import Enum

    class K(str, Enum):
        V = kind

    return SimpleNamespace(
        kind=K.V,
        symbol=symbol,
        demangled_symbol=demangled or symbol,
        description=desc,
        old_value=old,
        new_value=new,
    )


def _result(
    verdict: str = "COMPATIBLE",
    changes: list | None = None,
    suppressed: list | None = None,
    suppressed_count: int = 0,
    old_version: str = "1.0",
    new_version: str = "2.0",
    library: str = "libtest.so",
) -> object:
    v = SimpleNamespace(value=verdict)
    return SimpleNamespace(
        verdict=v,
        changes=changes or [],
        suppressed_changes=suppressed or [],
        suppressed_count=suppressed_count,
        old_version=old_version,
        new_version=new_version,
        library=library,
        suppression_file_provided=bool(suppressed_count),
    )


# ---------------------------------------------------------------------------
# Basic structure tests
# ---------------------------------------------------------------------------

def test_html_is_valid_document() -> None:
    r = _result()
    out = generate_html_report(r)
    assert out.startswith("<!DOCTYPE html>")
    assert "</html>" in out
    assert "<head>" in out
    assert "<body>" in out


def test_html_contains_verdict() -> None:
    r = _result(verdict="BREAKING")
    out = generate_html_report(r)
    assert "BREAKING" in out


def test_html_contains_library_and_versions() -> None:
    r = _result()
    out = generate_html_report(r, lib_name="libfoo", old_version="2025.0", new_version="2025.3")
    assert "libfoo" in out
    assert "2025.0" in out
    assert "2025.3" in out


def test_html_title_contains_versions() -> None:
    r = _result()
    out = generate_html_report(r, lib_name="libfoo", old_version="1.0", new_version="2.0")
    assert "<title>" in out
    assert "1.0" in out
    assert "2.0" in out


# ---------------------------------------------------------------------------
# BC% calculation
# ---------------------------------------------------------------------------

def test_bc_100_when_no_breaking() -> None:
    r = _result(verdict="COMPATIBLE", changes=[_ch("func_added")])
    out = generate_html_report(r)
    assert "100.0%" in out


def test_bc_0_when_all_breaking_no_symbol_count() -> None:
    r = _result(verdict="BREAKING", changes=[_ch("func_removed"), _ch("func_removed", "bar")])
    out = generate_html_report(r)
    assert "0.0%" in out


def test_bc_uses_old_symbol_count_when_provided() -> None:
    """BC% = (100 - 1) / 100 * 100 = 99.0%."""
    r = _result(verdict="BREAKING", changes=[_ch("func_removed")])
    out = generate_html_report(r, old_symbol_count=100)
    assert "99.0%" in out


def test_bc_no_change_is_100() -> None:
    r = _result(verdict="NO_CHANGE")
    out = generate_html_report(r)
    assert "100.0%" in out


# ---------------------------------------------------------------------------
# Sectioned layout
# ---------------------------------------------------------------------------

def test_removed_section_present_when_removals_exist() -> None:
    r = _result(verdict="BREAKING", changes=[_ch("func_removed", "myfunc")])
    out = generate_html_report(r)
    assert "id='removed'" in out
    assert "Removed Symbols" in out


def test_added_section_present_when_additions_exist() -> None:
    r = _result(verdict="COMPATIBLE", changes=[_ch("func_added", "newfunc")])
    out = generate_html_report(r)
    assert "id='added'" in out
    assert "Added Symbols" in out


def test_breaking_field_addition_not_in_green_added_section() -> None:
    """A structurally-additive but ABI-breaking finding (type_field_added -
    appending a field to a non-final/polymorphic type can break binary
    layout) must not render under the green Added section, or it reads as
    safe when it is not. Verified defect (source assessment): confirmed
    ChangeKind.TYPE_FIELD_ADDED is in both ADDED_KINDS and BREAKING_KINDS."""
    r = _result(verdict="BREAKING", changes=[_ch("type_field_added", "Config")])
    out = generate_html_report(r)
    assert "id='added'" not in out
    assert "id='changed'" in out


def test_policy_demoted_removal_does_not_contradict_compatible_verdict() -> None:
    """A policy-file override that demotes a removal to COMPATIBLE must not
    leave the page reporting "0.0% binary compatibility" next to a
    COMPATIBLE verdict banner — verified defect (source assessment): the
    binary-compatibility % previously counted breaking_count by raw kind
    membership, ignoring the policy override the verdict itself honours."""
    from abicheck.checker import Change, ChangeKind, DiffResult, Verdict
    from abicheck.policy_file import PolicyFile

    c = Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "removed: foo")
    pf = PolicyFile(overrides={ChangeKind.FUNC_REMOVED: Verdict.COMPATIBLE})
    result = DiffResult(
        old_version="1.0", new_version="2.0", library="libtest.so",
        changes=[c], verdict=Verdict.COMPATIBLE, policy_file=pf,
    )
    out = generate_html_report(result)
    assert "Verdict: COMPATIBLE" in out
    assert "Binary Compatibility: <strong>100.0%</strong>" in out
    assert "Binary Compatibility: <strong>0.0%</strong>" not in out
    assert "(0 breaking change(s))" in out


@pytest.mark.parametrize(
    "escalated_verdict",
    ["BREAKING", "API_BREAK", "COMPATIBLE_WITH_RISK"],
)
def test_policy_escalated_addition_not_in_green_added_section(escalated_verdict: str) -> None:
    """Codex review on #549 (two rounds): a policy file that escalates an
    inherently additive kind away from COMPATIBLE — to BREAKING, API_BREAK,
    *or* COMPATIBLE_WITH_RISK — must not leave the finding in the green
    "Added" section. `_change_bucket`'s canonical `BREAKING_KINDS`
    membership check can never see a policy-file escalation since it only
    looks at the kind's own default classification. The first fix only
    diverted the `== Verdict.BREAKING` case; a non-BREAKING escalation
    (API_BREAK, COMPATIBLE_WITH_RISK) still rendered under "Added" even
    though it needs review/recompilation like any other non-COMPATIBLE
    finding — the guard must check "not COMPATIBLE", not "is BREAKING"."""
    from abicheck.checker import Change, ChangeKind, DiffResult, Verdict
    from abicheck.policy_file import PolicyFile

    c = Change(ChangeKind.FUNC_ADDED, "_Z3barv", "new function: bar")
    pf = PolicyFile(overrides={ChangeKind.FUNC_ADDED: Verdict[escalated_verdict]})
    result = DiffResult(
        old_version="1.0", new_version="2.0", library="libtest.so",
        changes=[c], verdict=Verdict[escalated_verdict], policy_file=pf,
    )
    out = generate_html_report(result)
    assert "id='added'" not in out
    assert "id='changed'" in out


def test_changed_section_present_for_changed_kinds() -> None:
    r = _result(verdict="BREAKING", changes=[_ch("func_return_changed", "myfunc")])
    out = generate_html_report(r)
    assert "id='changed'" in out
    assert "Changed Symbols" in out


def test_no_empty_sections_when_no_changes() -> None:
    r = _result(verdict="NO_CHANGE")
    out = generate_html_report(r)
    # None of the section anchors should appear when there are no changes
    assert "id='removed'" not in out
    assert "id='added'" not in out
    assert "id='changed'" not in out


def test_no_change_fallback_message() -> None:
    r = _result(verdict="NO_CHANGE")
    out = generate_html_report(r)
    assert "No ABI changes detected" in out


# ---------------------------------------------------------------------------
# Summary table
# ---------------------------------------------------------------------------

def test_summary_table_present() -> None:
    r = _result(changes=[_ch("func_removed"), _ch("func_added")])
    out = generate_html_report(r)
    assert "Change Summary" in out


def test_summary_table_categories() -> None:
    r = _result(changes=[
        _ch("func_removed"),
        _ch("type_size_changed"),
        _ch("enum_member_removed"),
        _ch("soname_changed"),
    ])
    out = generate_html_report(r)
    assert "Functions" in out
    assert "Types" in out
    assert "Enums" in out
    assert "ELF" in out


# ---------------------------------------------------------------------------
# Navigation bar
# ---------------------------------------------------------------------------

def test_nav_links_to_sections() -> None:
    r = _result(changes=[_ch("func_removed"), _ch("func_added")])
    out = generate_html_report(r)
    assert "href='#removed'" in out
    assert "href='#added'" in out


def test_nav_absent_when_no_changes() -> None:
    r = _result(verdict="NO_CHANGE")
    out = generate_html_report(r)
    assert "href='#removed'" not in out
    assert "href='#added'" not in out


# ---------------------------------------------------------------------------
# Suppressed changes
# ---------------------------------------------------------------------------

def test_suppressed_section_shown() -> None:
    sup = [_ch("func_removed", "hidden_func")]
    r = _result(suppressed=sup, suppressed_count=1)
    out = generate_html_report(r)
    assert "id='suppressed'" in out
    assert "Suppressed" in out


def test_suppressed_count_only_shown_when_no_details() -> None:
    r = _result(suppressed_count=3)
    out = generate_html_report(r)
    assert "Suppressed" in out


# ---------------------------------------------------------------------------
# Demangled symbol display
# ---------------------------------------------------------------------------

def test_demangled_symbol_shown_as_text() -> None:
    ch = _ch("func_removed", symbol="_ZN3FooC1Ev",
             demangled="Foo::Foo()")
    r = _result(verdict="BREAKING", changes=[ch])
    out = generate_html_report(r)
    assert "Foo::Foo()" in out


def test_mangled_symbol_in_tooltip() -> None:
    ch = _ch("func_removed", symbol="_ZN3FooC1Ev",
             demangled="Foo::Foo()")
    r = _result(verdict="BREAKING", changes=[ch])
    out = generate_html_report(r)
    assert "_ZN3FooC1Ev" in out  # mangled in abbr title


# ---------------------------------------------------------------------------
# XSS safety
# ---------------------------------------------------------------------------

def test_xss_escape_lib_name() -> None:
    r = _result()
    out = generate_html_report(r, lib_name="<script>alert(1)</script>")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_xss_escape_symbol_name() -> None:
    ch = _ch("func_removed", symbol="<evil>")
    r = _result(verdict="BREAKING", changes=[ch])
    out = generate_html_report(r)
    assert "<evil>" not in out


def test_xss_escape_description() -> None:
    ch = _ch("func_removed", desc='<img src=x onerror="alert(1)">')
    r = _result(verdict="BREAKING", changes=[ch])
    out = generate_html_report(r)
    assert "<img" not in out


def test_xss_escape_old_value() -> None:
    ch = _ch("func_params_changed", old='<script>x()</script>', new="int")
    r = _result(verdict="BREAKING", changes=[ch])
    out = generate_html_report(r)
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_xss_escape_new_value() -> None:
    ch = _ch("func_params_changed", old="int", new='<img src=x onerror="alert(1)">')
    r = _result(verdict="BREAKING", changes=[ch])
    out = generate_html_report(r)
    assert "<img" not in out


# ---------------------------------------------------------------------------
# write_html_report
# ---------------------------------------------------------------------------

def test_write_creates_dirs_and_file(tmp_path: Path) -> None:
    r = _result()
    out = tmp_path / "deep" / "nested" / "report.html"
    write_html_report(r, out)
    assert out.exists()
    assert out.stat().st_size > 500


def test_write_passes_old_symbol_count(tmp_path: Path) -> None:
    r = _result(verdict="BREAKING", changes=[_ch("func_removed")])
    out = tmp_path / "report.html"
    write_html_report(r, out, lib_name="libfoo", old_version="1.0", new_version="2.0",
                      old_symbol_count=50)
    content = out.read_text()
    assert "98.0%" in content  # (50-1)/50 * 100


# ---------------------------------------------------------------------------
# Verdict colours
# ---------------------------------------------------------------------------

def test_breaking_verdict_red_color() -> None:
    r = _result(verdict="BREAKING")
    out = generate_html_report(r)
    assert "#b71c1c" in out  # BREAKING foreground


def test_compatible_verdict_green_color() -> None:
    r = _result(verdict="COMPATIBLE")
    out = generate_html_report(r)
    assert "#1b5e20" in out  # COMPATIBLE foreground


def test_no_change_verdict_blue_color() -> None:
    r = _result(verdict="NO_CHANGE")
    out = generate_html_report(r)
    assert "#0d47a1" in out  # NO_CHANGE foreground


# ---------------------------------------------------------------------------
# BC% edge cases (review feedback)
# ---------------------------------------------------------------------------

def test_bc_old_symbol_count_zero_uses_legacy_fallback() -> None:
    """old_symbol_count=0 → falls back to change-ratio: 1 breaking / 1 total = 0%."""
    r = _result(verdict="BREAKING", changes=[_ch("func_removed")])
    out = generate_html_report(r, old_symbol_count=0)
    assert "0.0%" in out


def test_bc_clamped_to_zero_when_breaking_exceeds_symbol_count() -> None:
    """breaking > old_symbol_count → clamped to 0% (stale snapshot edge case)."""
    r = _result(verdict="BREAKING", changes=[_ch("func_removed")] * 5)
    out = generate_html_report(r, old_symbol_count=3)
    assert "0.0%" in out


# ---------------------------------------------------------------------------
# enum_member_removed is breaking (review feedback)
# ---------------------------------------------------------------------------

def test_enum_member_removed_is_breaking() -> None:
    """enum_member_removed must count as breaking and appear in Removed section."""
    r = _result(verdict="BREAKING", changes=[_ch("enum_member_removed", "MY_VAL")])
    out = generate_html_report(r, old_symbol_count=10)
    # Must NOT be 100% — it's a breaking removal
    assert "100.0%" not in out
    # Must appear in removed section
    assert "id='removed'" in out


def test_enum_member_removed_bucket_is_removed() -> None:
    from abicheck.html_report import _change_bucket
    ch = _ch("enum_member_removed", "SOME_VAL")
    assert _change_bucket(ch) == "removed"


# ---------------------------------------------------------------------------
# _BREAKING_KINDS drift guard (review feedback)
# ---------------------------------------------------------------------------

def test_changed_breaking_kinds_subset_of_breaking_kinds() -> None:
    """CHANGED_BREAKING_KINDS must be a strict subset of BREAKING_KINDS."""
    from abicheck.report_classifications import BREAKING_KINDS, CHANGED_BREAKING_KINDS
    assert CHANGED_BREAKING_KINDS <= BREAKING_KINDS, (
        "CHANGED_BREAKING_KINDS has entries not in BREAKING_KINDS: "
        f"{CHANGED_BREAKING_KINDS - BREAKING_KINDS}"
    )


def test_removed_kinds_subset_of_breaking_kinds() -> None:
    from abicheck.report_classifications import BREAKING_KINDS, REMOVED_KINDS
    assert REMOVED_KINDS <= BREAKING_KINDS


# ---------------------------------------------------------------------------
# Confidence / evidence tiers / policy in HTML report
# ---------------------------------------------------------------------------

def test_confidence_section_present() -> None:
    """HTML report includes Analysis Confidence section."""
    from enum import Enum

    class Conf(str, Enum):
        LOW = "low"

    r = _result()
    r.confidence = Conf.LOW
    r.evidence_tiers = ["elf"]
    r.coverage_warnings = ["DWARF stripped"]
    r.policy = "strict_abi"
    r.policy_file = None
    out = generate_html_report(r)
    assert "Analysis Confidence" in out
    assert "LOW" in out
    assert "elf" in out
    assert "DWARF stripped" in out


def test_policy_shown_in_html() -> None:
    """HTML report shows the active policy."""
    r = _result()
    r.confidence = SimpleNamespace(value="high")
    r.evidence_tiers = []
    r.coverage_warnings = []
    r.policy = "sdk_vendor"
    r.policy_file = None
    out = generate_html_report(r)
    assert "sdk_vendor" in out


def test_confidence_absent_without_attribute() -> None:
    """HTML report works without confidence attribute (backward compat)."""
    r = _result()
    # No confidence attribute set — _result() uses SimpleNamespace
    out = generate_html_report(r)
    assert "<!DOCTYPE html>" in out
    # Should NOT crash — just skip the section
