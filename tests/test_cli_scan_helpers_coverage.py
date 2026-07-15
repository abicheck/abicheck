"""Coverage-closing unit tests for :mod:`abicheck.cli_scan_helpers`.

The render/resolve helpers in ``cli_scan_helpers`` are click-free, side-effect
free functions that ``cli_scan._render_text`` and ``run_scan_core`` compose.
Several of their branches were unexercised: the two advisory branches of
``l4_coverage_advisories`` (widened scope / uncovered public headers), the
``except``/success paths of ``resolve_effective_allow_query`` (malformed config
vs. a trusted config defining ``build.query``), the optional depth/timings
blocks of ``render_summary_lines``, the non-empty divergence/leak block of
``render_preprocessor_lines``, and the budget footer of ``render_verdict_lines``.

These tests construct real inputs (dicts, ``SimpleNamespace`` stand-ins for the
``ScanOutcome`` dataclass, real ``.abicheck.yml`` files, real ``SourceMethod``
enums) and assert on the exact text/return values the helpers produce.
"""

from __future__ import annotations

from types import SimpleNamespace

from abicheck.buildsource.scan_levels import SourceMethod
from abicheck.cli_scan_helpers import (
    l4_coverage_advisories,
    render_baseline_lines,
    render_coverage_lines,
    render_crosscheck_lines,
    render_pattern_lines,
    render_preprocessor_lines,
    render_summary_lines,
    render_verdict_lines,
    resolve_effective_allow_query,
    scan_pattern_roots,
)

# --- l4_coverage_advisories --------------------------------------------------


def test_l4_advisories_scope_widened_branch() -> None:
    """A widened-to-full replay yields the full-fanout advisory (line 66)."""
    notes = l4_coverage_advisories({"scope_widened_to_full": True})
    assert len(notes) == 1
    assert "widened to all compile units" in notes[0]
    assert "--since/--changed-path" in notes[0]


def test_l4_advisories_uncovered_public_headers_branch() -> None:
    """Uncovered public headers yield the partial-coverage advisory (line 74)."""
    notes = l4_coverage_advisories({"public_headers_uncovered": 3})
    assert len(notes) == 1
    assert "3 public header(s) were not reached" in notes[0]


def test_l4_advisories_all_three_stack() -> None:
    """All three independent conditions can fire together, in order."""
    notes = l4_coverage_advisories(
        {
            "scope_widened_to_full": True,
            "public_headers_uncovered": 2,
            "exported_symbols": 10,
            "matched_symbols": 0,
            "compile_units_parsed": 4,
        }
    )
    assert len(notes) == 3
    assert "widened to all compile units" in notes[0]
    assert "2 public header(s) were not reached" in notes[1]
    assert "matched 0/10" in notes[2]


def test_l4_advisories_empty_when_clean() -> None:
    """A clean coverage dict produces no advisories."""
    assert l4_coverage_advisories({}) == []
    # Falsy / zero values must not trip any branch.
    assert (
        l4_coverage_advisories(
            {
                "scope_widened_to_full": False,
                "public_headers_uncovered": 0,
                "exported_symbols": 5,
                "matched_symbols": 5,
                "compile_units_parsed": 2,
            }
        )
        == []
    )


# --- resolve_effective_allow_query -------------------------------------------


def test_resolve_query_gate_short_circuits_when_not_applicable() -> None:
    """When the D4 gate preconditions are unmet, the input is returned as-is."""
    # allow_build_query already True → no auto-enable needed.
    assert resolve_effective_allow_query(
        allow_build_query=True,
        build_config=None,
        collect_mode="s1",
        level_explicit=True,
        resolved=SourceMethod.S1,
    ) == (True, None)
    # No explicit --config → gate cannot fire.
    assert resolve_effective_allow_query(
        allow_build_query=False,
        build_config=None,
        collect_mode="s1",
        level_explicit=True,
        resolved=SourceMethod.S1,
    ) == (False, None)


def test_resolve_query_malformed_config_swallowed(tmp_path) -> None:
    """A config that fails to load is treated as no-query (lines 128-129)."""
    bad = tmp_path / ".abicheck.yml"
    # Unbalanced brackets → yaml.YAMLError → load_build_config raises ValueError.
    bad.write_text("build: {query: [unterminated\n", encoding="utf-8")
    effective, advisory = resolve_effective_allow_query(
        allow_build_query=False,
        build_config=bad,
        collect_mode="s1",
        level_explicit=True,
        resolved=SourceMethod.S1,
    )
    # Exception was swallowed; nothing auto-enabled.
    assert effective is False
    assert advisory is None


def test_resolve_query_trusted_config_auto_enables(tmp_path) -> None:
    """A trusted config defining build.query auto-enables it (lines 130-138)."""
    cfg = tmp_path / ".abicheck.yml"
    cfg.write_text(
        "build:\n  query: 'cmake -DCMAKE_EXPORT_COMPILE_COMMANDS=ON .'\n",
        encoding="utf-8",
    )
    effective, advisory = resolve_effective_allow_query(
        allow_build_query=False,
        build_config=cfg,
        collect_mode="s5",
        level_explicit=True,
        resolved=SourceMethod.S5,
    )
    assert effective is True
    assert advisory is not None
    assert "level s5" in advisory
    assert "auto-enabled the query" in advisory


def test_resolve_query_config_without_query_is_noop(tmp_path) -> None:
    """A trusted config that defines no build.query does not auto-enable."""
    cfg = tmp_path / ".abicheck.yml"
    cfg.write_text("sources:\n  public_headers: ['include/**']\n", encoding="utf-8")
    assert resolve_effective_allow_query(
        allow_build_query=False,
        build_config=cfg,
        collect_mode="s5",
        level_explicit=True,
        resolved=SourceMethod.S5,
    ) == (False, None)


# --- scan_pattern_roots ------------------------------------------------------


def test_scan_pattern_roots_excludes_sources_for_shallow_depth(tmp_path) -> None:
    """BINARY/HEADERS depth never walks the --sources tree."""
    from abicheck.buildsource.scan_levels import EvidenceDepth

    h = tmp_path / "inc"
    src = tmp_path / "src"
    assert scan_pattern_roots([h], src, EvidenceDepth.HEADERS) == [h]
    assert scan_pattern_roots([h], src, EvidenceDepth.BINARY) == [h]


def test_scan_pattern_roots_adds_sources_for_deep_depth(tmp_path) -> None:
    """SOURCE depth adds the --sources tree to the pattern roots."""
    from abicheck.buildsource.scan_levels import EvidenceDepth

    h = tmp_path / "inc"
    src = tmp_path / "src"
    assert scan_pattern_roots([h], src, EvidenceDepth.SOURCE) == [h, src]
    # No --sources → header roots only, regardless of depth.
    assert scan_pattern_roots([h], None, EvidenceDepth.SOURCE) == [h]


# --- render_summary_lines ----------------------------------------------------


def _risk(total: int = 0, recommended: str = "s0", matched: dict | None = None):
    return SimpleNamespace(
        total=total,
        recommended_method=recommended,
        matched=matched or {},
    )


def _summary_outcome(**overrides):
    base = dict(
        mode="review",
        resolved_method="s3",
        depth=None,
        collect_mode="s3",
        auto=False,
        risk=_risk(),
        changed_path_count=0,
        changed_path_source="git",
        advisories=[],
        stage_timings={},
        poi={},
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_render_summary_optional_depth_and_timings_and_poi() -> None:
    """Depth, auto flag, timings, and POI blocks all render (lines 149-179)."""
    out = _summary_outcome(
        depth="source",
        auto=True,
        risk=_risk(total=7, recommended="s5", matched={"abi_header": 2, "vtable": 1}),
        changed_path_count=4,
        stage_timings={"pattern": 0.5, "l4": 1.25},
        advisories=["watch out"],
        poi={
            "counts_by_reason": {"changed": 3, "risk": 1},
            "total": 4,
            "changed_paths": ["a.cpp", "b.cpp"],
            "symbols": ["_Z3foov"],
        },
    )
    lines = render_summary_lines(out)
    text = "\n".join(lines)
    # Optional depth segment (line 150) + collect-mode + auto marker.
    assert "depth=source" in text
    assert "collect-mode=s3" in text
    assert "(auto)" in text
    # matched risk breakdown rendered in sorted order.
    assert "[abi_header×2, vtable×1]" in text
    assert "risk score=7 (auto→s5)" in text
    assert "changed paths: 4 (git)" in text
    assert "  note: watch out" in text
    # timings block (lines 165-170), sorted by name, 2-dp seconds.
    assert "timings: l4=1.25s, pattern=0.50s" in text
    # POI focus block (lines 172-179).
    assert "focus (POI): 4 point(s)" in text
    assert "[changed×3, risk×1]" in text
    assert "2 path(s), 1 symbol(s)" in text


def test_render_summary_minimal_omits_optional_blocks() -> None:
    """With no depth/timings/poi/matched, those lines are skipped."""
    out = _summary_outcome()
    text = "\n".join(render_summary_lines(out))
    assert "depth=" not in text
    assert "(auto)" not in text
    assert "timings:" not in text
    assert "focus (POI):" not in text
    # No matched map → no trailing bracket on the risk line.
    assert "risk score=0 (auto→s0)" in text
    assert "[" not in text.split("changed paths")[0].split("risk score")[1]


# --- render_preprocessor_lines -----------------------------------------------


def test_render_preprocessor_empty_when_no_facts() -> None:
    """No divergences and no leaks → empty block (early return)."""
    assert render_preprocessor_lines(SimpleNamespace(preprocessor={})) == []
    assert (
        render_preprocessor_lines(
            SimpleNamespace(preprocessor={"divergences": [], "leaks": []})
        )
        == []
    )


def test_render_preprocessor_renders_divergences_and_leaks() -> None:
    """Non-empty divergence + leak facts render both loops (lines 226-236)."""
    out = SimpleNamespace(
        preprocessor={
            "divergences": [{"macro": "NDEBUG", "n_values": 2}],
            "leaks": [
                {
                    "leak_class": "private",
                    "public_header": "api.h",
                    "leaked_header": "internal/impl.h",
                }
            ],
        }
    )
    lines = render_preprocessor_lines(out)
    text = "\n".join(lines)
    assert "Preprocessor pre-scan facts (S2, advisory)" in text
    assert "macro divergence: NDEBUG (2 values across TUs)" in text
    assert "private-header leak: api.h → internal/impl.h" in text


def test_render_preprocessor_leak_only() -> None:
    """A leak with no divergence still triggers the block (line 224 branch)."""
    out = SimpleNamespace(
        preprocessor={
            "leaks": [
                {
                    "leak_class": "generated",
                    "public_header": "cfg.h",
                    "leaked_header": "build/config.h",
                }
            ]
        }
    )
    text = "\n".join(render_preprocessor_lines(out))
    assert "generated-header leak: cfg.h → build/config.h" in text
    assert "macro divergence" not in text


# --- render_verdict_lines ----------------------------------------------------


def test_render_verdict_without_budget() -> None:
    """No budget → verdict line only, no Elapsed footer."""
    lines = render_verdict_lines(
        SimpleNamespace(verdict="COMPATIBLE", budget_s=None, elapsed_s=0.0)
    )
    assert lines == ["", "Verdict: COMPATIBLE"]


def test_render_verdict_with_budget_appends_elapsed() -> None:
    """A budget renders the Elapsed/budget footer (lines 256-257)."""
    lines = render_verdict_lines(
        SimpleNamespace(verdict="BREAKING", budget_s=30.0, elapsed_s=2.5)
    )
    assert lines[-1] == "Elapsed: 2.50s / budget 30s"
    assert "Verdict: BREAKING" in lines


# --- render_coverage_lines ---------------------------------------------------


def test_render_coverage_table() -> None:
    """The coverage table renders one padded row per layer (lines 185-190)."""
    out = SimpleNamespace(
        coverage=[
            {"layer": "L0_binary", "status": "present", "detail": "12 function(s)"},
            {"layer": "L2_header", "status": "skipped"},  # no 'detail' → default ''
        ]
    )
    lines = render_coverage_lines(out)
    assert lines[0] == ""
    assert lines[1] == "Coverage"
    assert (
        "L0_binary" in lines[2]
        and "present" in lines[2]
        and "12 function(s)" in lines[2]
    )
    # Missing 'detail' key falls back to empty string without KeyError.
    assert lines[3].startswith("  L2_header")
    assert "skipped" in lines[3]


# --- render_crosscheck_lines -------------------------------------------------


def test_render_crosscheck_empty_when_no_counts() -> None:
    """No cross-check counts → empty block."""
    assert render_crosscheck_lines(SimpleNamespace(crosscheck={})) == []


def test_render_crosscheck_audit_and_cross_source_headers() -> None:
    """Audit vs. non-audit selects the header; severities default to warning."""
    cc = {"counts_by_check": {"private_header_leak": 2, "odr_type_variant": 1}}
    audit_out = SimpleNamespace(
        crosscheck=cc,
        crosscheck_severities={"odr_type_variant": "error"},
        audit=True,
    )
    text = "\n".join(render_crosscheck_lines(audit_out))
    assert "ABI-hygiene catalog (intra-version, advisory)" in text
    # Explicit severity honored; missing one defaults to 'warning'.
    assert "[error] odr_type_variant: 1" in text
    assert "[warning] private_header_leak: 2" in text

    cross_out = SimpleNamespace(crosscheck=cc, crosscheck_severities={}, audit=False)
    assert "Cross-source findings (advisory)" in "\n".join(
        render_crosscheck_lines(cross_out)
    )


# --- render_pattern_lines ----------------------------------------------------


def test_render_pattern_empty_when_no_counts() -> None:
    """No pattern counts → empty block."""
    assert render_pattern_lines(SimpleNamespace(pattern={})) == []
    assert render_pattern_lines(SimpleNamespace(pattern={"counts_by_kind": {}})) == []


def test_render_pattern_facts_rendered_sorted() -> None:
    """Pattern facts render one sorted row per kind (lines 214-217)."""
    out = SimpleNamespace(
        pattern={"counts_by_kind": {"virtual_method": 3, "bitfield": 1}}
    )
    lines = render_pattern_lines(out)
    assert lines[:2] == ["", "Pattern pre-scan facts (advisory)"]
    # Sorted alphabetically: bitfield before virtual_method.
    assert lines[2] == "  bitfield: 1"
    assert lines[3] == "  virtual_method: 3"


# --- render_baseline_lines (guard the None short-circuit) --------------------


def test_render_baseline_none_and_populated() -> None:
    """No baseline diff → empty; a diff summary → the counts line."""
    assert render_baseline_lines(SimpleNamespace(diff_summary=None)) == []
    out = SimpleNamespace(
        diff_summary={"breaking": 1, "api_break": 2, "risk": 3, "compatible": 4}
    )
    text = "\n".join(render_baseline_lines(out))
    assert "Baseline comparison" in text
    assert "breaking=1 api_break=2 risk=3 compatible=4" in text


def test_render_baseline_lines_lists_findings_not_just_counts() -> None:
    """A failing baseline compare must name the broken symbol, not just count it."""
    out = SimpleNamespace(
        diff_summary={
            "breaking": 1,
            "api_break": 0,
            "risk": 0,
            "compatible": 0,
            "findings": [
                {
                    "bucket": "breaking",
                    "kind": "func_removed",
                    "symbol": "_Z3foov",
                    "description": "Public function removed: foo",
                    "source_location": "foo.h:42",
                }
            ],
        }
    )
    lines = render_baseline_lines(out)
    text = "\n".join(lines)
    assert "[breaking] func_removed: _Z3foov (foo.h:42)" in text


def test_render_baseline_lines_notes_truncation() -> None:
    out = SimpleNamespace(
        diff_summary={
            "breaking": 25,
            "api_break": 0,
            "risk": 0,
            "compatible": 0,
            "findings": [
                {
                    "bucket": "breaking",
                    "kind": "func_removed",
                    "symbol": "sym",
                    "description": None,
                    "source_location": None,
                }
            ],
            "findings_truncated": True,
        }
    )
    text = "\n".join(render_baseline_lines(out))
    assert "additional findings omitted" in text
