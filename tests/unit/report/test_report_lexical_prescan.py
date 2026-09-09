# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Tests for the pattern/preprocessor pre-scan report sections (schema 3.12,
plan §3 rows 6/8, §6 Phase 2b)."""

from __future__ import annotations

import json

from abicheck.checker_types import DiffResult
from abicheck.report.dispatch_markdown import to_markdown
from abicheck.report.lexical_prescan import (
    compute_pattern_prescan_summary,
    compute_preprocessor_prescan_summary,
    pattern_prescan_review_warnings,
    preprocessor_prescan_review_warnings,
    render_pattern_prescan_json,
    render_pattern_prescan_markdown,
    render_preprocessor_prescan_json,
    render_preprocessor_prescan_markdown,
)
from abicheck.reporter import to_json


def test_compute_pattern_summary_none_when_never_folded():
    assert compute_pattern_prescan_summary(None) is None
    assert render_pattern_prescan_json(None) is None


def test_compute_preprocessor_summary_none_when_never_folded():
    assert compute_preprocessor_prescan_summary(None) is None
    assert render_preprocessor_prescan_json(None) is None


def test_compute_pattern_summary_round_trips_verbatim():
    old = {"files_scanned": 1, "facts": []}
    new = {"files_scanned": 2, "facts": []}
    summary = compute_pattern_prescan_summary({"old": old, "new": new})
    assert summary is not None
    assert render_pattern_prescan_json(summary) == {"old": old, "new": new}


def test_compute_preprocessor_summary_round_trips_verbatim():
    old = {"ran": False, "skipped_reason": "no L3 build evidence"}
    new = {"ran": True, "skipped_reason": ""}
    summary = compute_preprocessor_prescan_summary({"old": old, "new": new})
    assert summary is not None
    assert render_preprocessor_prescan_json(summary) == {"old": old, "new": new}


def test_json_report_carries_both_blocks_when_folded():
    result = DiffResult(
        old_version="1.0",
        new_version="1.1",
        library="libfoo.so",
        pattern_prescan={"old": {"files_scanned": 0}, "new": {"files_scanned": 1}},
        preprocessor_prescan={
            "old": {"ran": False, "skipped_reason": "x"},
            "new": {"ran": False, "skipped_reason": "x"},
        },
    )
    doc = json.loads(to_json(result))
    assert doc["pattern_prescan"] == {
        "old": {"files_scanned": 0},
        "new": {"files_scanned": 1},
    }
    assert doc["preprocessor_prescan"] == {
        "old": {"ran": False, "skipped_reason": "x"},
        "new": {"ran": False, "skipped_reason": "x"},
    }


def test_json_report_omits_both_blocks_when_never_folded():
    """A `DiffResult` built by a caller that never runs `fold_lexical_prescan`
    (e.g. a direct `checker.compare()` unit test) omits both blocks, rather
    than rendering an empty/null placeholder."""
    result = DiffResult(old_version="1.0", new_version="1.1", library="libfoo.so")
    doc = json.loads(to_json(result))
    assert "pattern_prescan" not in doc
    assert "preprocessor_prescan" not in doc


def test_both_blocks_stable_across_report_modes():
    """ADR-068 D4: presentation never changes analysis -- the same
    pre-scan attachment appears identically across every JSON report_mode."""
    result = DiffResult(
        old_version="1.0",
        new_version="1.1",
        library="libfoo.so",
        pattern_prescan={"old": {"files_scanned": 0}, "new": {"files_scanned": 1}},
        preprocessor_prescan={
            "old": {"ran": False, "skipped_reason": "x"},
            "new": {"ran": False, "skipped_reason": "x"},
        },
    )
    for mode in ("full", "leaf", "root-cause"):
        doc = json.loads(to_json(result, report_mode=mode))
        assert doc["pattern_prescan"]["new"]["files_scanned"] == 1
        assert doc["preprocessor_prescan"]["old"]["ran"] is False


def test_render_pattern_prescan_markdown_none_renders_nothing():
    assert render_pattern_prescan_markdown(None) == []


def test_render_preprocessor_prescan_markdown_none_renders_nothing():
    assert render_preprocessor_prescan_markdown(None) == []


def test_render_pattern_prescan_markdown_shows_per_side_status():
    summary = compute_pattern_prescan_summary(
        {
            "old": {
                "files_scanned": 0,
                "facts": [],
                "coverage": {"status": "not_collected"},
            },
            "new": {
                "files_scanned": 2,
                "facts": [{"kind": "virtual_method"}],
                "escalation_triggers": [{"kind": "virtual_method"}],
                "coverage": {"status": "present"},
            },
        }
    )
    lines = render_pattern_prescan_markdown(summary)
    text = "\n".join(lines)
    assert "Pattern Pre-Scan" in text
    assert "OLD" in text and "not evaluated" in text
    assert "NEW" in text and "2 file(s) scanned" in text and "1 construct(s)" in text


def test_render_pattern_prescan_markdown_flags_partial_coverage():
    """Codex review, PR #1169, fifth round, fresh evidence: a side with
    `coverage.status == "partial"` (a missing sibling root even though
    another root scanned successfully -- `scope_reason` itself is `None`
    since real coverage exists) previously rendered identically to a
    fully-covered scan in full/leaf/root-cause Markdown, mirroring the
    exact gap `_preprocessor_side_markdown_line` was already fixed for."""
    summary = compute_pattern_prescan_summary(
        {
            "old": {
                "files_scanned": 1,
                "facts": [],
                "escalation_triggers": [],
                "coverage": {
                    "status": "partial",
                    "detail": "1 file(s), 1 unreadable skipped",
                },
            },
            "new": {
                "files_scanned": 1,
                "facts": [],
                "escalation_triggers": [],
                "coverage": {"status": "present"},
            },
        }
    )
    lines = render_pattern_prescan_markdown(summary)
    old_line = next(line for line in lines if line.startswith("- **OLD**"))
    assert "partial coverage" in old_line
    assert "1 unreadable skipped" in old_line
    new_line = next(line for line in lines if line.startswith("- **NEW**"))
    assert "partial coverage" not in new_line


def test_render_preprocessor_prescan_markdown_shows_per_side_status():
    summary = compute_preprocessor_prescan_summary(
        {
            "old": {"ran": False, "skipped_reason": "no L3 build evidence"},
            "new": {"ran": True, "divergences": [], "leaks": [{"x": 1}]},
        }
    )
    lines = render_preprocessor_prescan_markdown(summary)
    text = "\n".join(lines)
    assert "Preprocessor Pre-Scan" in text
    assert "OLD" in text and "no L3 build evidence" in text
    assert "NEW" in text and "1 header leak(s)" in text


def test_full_markdown_report_shows_both_pre_scan_sections():
    """Codex review (P2, finding #5): `scan`'s own text output surfaced
    each pre-scan's coverage status; `compare`'s Markdown report must too,
    not just its JSON. Exercises the real `to_markdown` entry point, not
    just the section renderer in isolation."""
    result = DiffResult(
        old_version="1.0",
        new_version="1.1",
        library="libfoo.so",
        pattern_prescan={
            "old": {
                "files_scanned": 0,
                "facts": [],
                "coverage": {"status": "not_collected"},
            },
            "new": {
                "files_scanned": 1,
                "facts": [{"kind": "virtual_method"}],
                "escalation_triggers": [],
                "coverage": {"status": "present"},
            },
        },
        preprocessor_prescan={
            "old": {"ran": False, "skipped_reason": "no L3 build evidence"},
            "new": {"ran": False, "skipped_reason": "no L3 build evidence"},
        },
    )
    md = to_markdown(result)
    assert "Pattern Pre-Scan" in md
    assert "Preprocessor Pre-Scan" in md
    assert "no L3 build evidence" in md


def test_full_markdown_report_omits_pre_scan_sections_when_never_folded():
    """A pre-Phase-2b `DiffResult` (e.g. a direct `checker.compare()` test)
    renders no pre-scan section at all -- proves existing golden output
    stays byte-for-byte unchanged."""
    result = DiffResult(old_version="1.0", new_version="1.1", library="libfoo.so")
    md = to_markdown(result)
    assert "Pattern Pre-Scan" not in md
    assert "Preprocessor Pre-Scan" not in md


# ---------------------------------------------------------------------------
# Codex review (PR #1151), finding #2: a `partial` preprocessor-scan side
# must not read as a clean, fully-scanned one.
# ---------------------------------------------------------------------------


def test_render_preprocessor_prescan_markdown_flags_partial_coverage():
    """A side with ``coverage.status == "partial"`` (some `clang -E` probes
    failed, or the probe cap truncated the scan) must not read identically
    to a genuinely clean, fully-scanned side reporting the same zero
    divergences/leaks -- the rendered line must carry a visible partial/
    incomplete indicator."""
    summary = compute_preprocessor_prescan_summary(
        {
            "old": {
                "ran": True,
                "divergences": [],
                "leaks": [],
                "coverage": {
                    "status": "partial",
                    "detail": "2 clang run(s) failed",
                },
            },
            "new": {
                "ran": True,
                "divergences": [],
                "leaks": [],
                "coverage": {"status": "present", "detail": "clean"},
            },
        }
    )
    lines = render_preprocessor_prescan_markdown(summary)
    text = "\n".join(lines)
    old_line = next(line for line in lines if line.startswith("- **OLD**"))
    new_line = next(line for line in lines if line.startswith("- **NEW**"))
    assert "partial" in old_line.lower()
    assert "2 clang run(s) failed" in old_line
    # The clean side must NOT be flagged, and the two lines must differ even
    # though both report identical divergence/leak counts.
    assert "partial" not in new_line.lower()
    assert old_line != new_line
    assert "0 macro divergence(s)" in text


def test_full_markdown_report_distinguishes_partial_from_clean_preprocessor_scan():
    """Same invariant, through the real `to_markdown` entry point: a run
    where only some probes succeeded must never render identically to an
    all-clean run reporting the same zero findings."""
    partial_result = DiffResult(
        old_version="1.0",
        new_version="1.1",
        library="libfoo.so",
        preprocessor_prescan={
            "old": {
                "ran": True,
                "divergences": [],
                "leaks": [],
                "coverage": {"status": "partial", "detail": "1 clang run(s) failed"},
            },
            "new": {
                "ran": True,
                "divergences": [],
                "leaks": [],
                "coverage": {"status": "present", "detail": "clean"},
            },
        },
    )
    clean_result = DiffResult(
        old_version="1.0",
        new_version="1.1",
        library="libfoo.so",
        preprocessor_prescan={
            "old": {
                "ran": True,
                "divergences": [],
                "leaks": [],
                "coverage": {"status": "present", "detail": "clean"},
            },
            "new": {
                "ran": True,
                "divergences": [],
                "leaks": [],
                "coverage": {"status": "present", "detail": "clean"},
            },
        },
    )
    partial_md = to_markdown(partial_result)
    clean_md = to_markdown(clean_result)
    assert partial_md != clean_md
    assert "partial" in partial_md.lower()
    assert "partial" not in clean_md.lower()


# ---------------------------------------------------------------------------
# Codex review (PR #1151), finding #3: `--report-mode leaf`/`root-cause`
# must render both pre-scan sections too, not just full mode.
# ---------------------------------------------------------------------------


def _prescan_result() -> DiffResult:
    return DiffResult(
        old_version="1.0",
        new_version="1.1",
        library="libfoo.so",
        pattern_prescan={
            "old": {
                "files_scanned": 0,
                "facts": [],
                "coverage": {"status": "not_collected"},
                "scope_reason": "no_inputs",
            },
            "new": {
                "files_scanned": 1,
                "facts": [{"kind": "virtual_method"}],
                "escalation_triggers": [],
                "coverage": {"status": "present"},
                "scope_reason": None,
            },
        },
        preprocessor_prescan={
            "old": {"ran": False, "skipped_reason": "no L3 build evidence"},
            "new": {"ran": False, "skipped_reason": "no L3 build evidence"},
        },
    )


def test_leaf_mode_markdown_report_shows_both_pre_scan_sections():
    md = to_markdown(_prescan_result(), report_mode="leaf")
    assert "Pattern Pre-Scan" in md
    assert "Preprocessor Pre-Scan" in md
    assert "no L3 build evidence" in md


def test_root_cause_mode_markdown_report_shows_both_pre_scan_sections():
    md = to_markdown(_prescan_result(), report_mode="root-cause")
    assert "Pattern Pre-Scan" in md
    assert "Preprocessor Pre-Scan" in md
    assert "no L3 build evidence" in md


# ---------------------------------------------------------------------------
# Codex review (PR #1151), finding #4: distinguish "no inputs supplied" /
# "a valid empty --since seed" / "all supplied inputs unreadable" in the
# rendered message -- three different situations a single "not evaluated"
# message previously collapsed.
# ---------------------------------------------------------------------------


def test_pattern_prescan_markdown_distinguishes_no_inputs_from_empty_seed():
    summary = compute_pattern_prescan_summary(
        {
            "old": {
                "files_scanned": 0,
                "facts": [],
                "coverage": {"status": "not_collected"},
                "scope_reason": "no_inputs",
            },
            "new": {
                "files_scanned": 0,
                "facts": [],
                "coverage": {"status": "not_collected"},
                "scope_reason": "empty_seed",
            },
        }
    )
    lines = render_pattern_prescan_markdown(summary)
    old_line = next(line for line in lines if line.startswith("- **OLD**"))
    new_line = next(line for line in lines if line.startswith("- **NEW**"))
    assert old_line != new_line
    assert "no headers" in old_line
    assert "empty scope" in new_line
    assert "by design" in new_line
    # The empty-seed side must not be described as having no inputs at all.
    assert "no headers" not in new_line


def test_pattern_prescan_markdown_distinguishes_empty_seed_from_unreadable_inputs():
    summary = compute_pattern_prescan_summary(
        {
            "old": {
                "files_scanned": 0,
                "facts": [],
                "coverage": {"status": "not_collected"},
                "scope_reason": "empty_seed",
            },
            "new": {
                "files_scanned": 0,
                "facts": [],
                "coverage": {"status": "not_collected"},
                "scope_reason": "unreadable_inputs",
            },
        }
    )
    lines = render_pattern_prescan_markdown(summary)
    old_line = next(line for line in lines if line.startswith("- **OLD**"))
    new_line = next(line for line in lines if line.startswith("- **NEW**"))
    assert old_line != new_line
    assert "by design" in old_line
    assert "unreadable" in new_line
    assert "by design" not in new_line


def test_pattern_prescan_markdown_falls_back_when_scope_reason_absent():
    """A report built before schema 3.13 (no `scope_reason` key at all) must
    still render the old, coarser message rather than fail to render."""
    summary = compute_pattern_prescan_summary(
        {
            "old": {
                "files_scanned": 0,
                "facts": [],
                "coverage": {"status": "not_collected"},
            },
            "new": {
                "files_scanned": 1,
                "facts": [],
                "coverage": {"status": "present"},
            },
        }
    )
    lines = render_pattern_prescan_markdown(summary)
    old_line = next(line for line in lines if line.startswith("- **OLD**"))
    assert "not evaluated" in old_line


def test_workflows_pattern_scan_scope_reason_distinguishes_all_three_cases(
    tmp_path,
):
    """Direct unit coverage for
    `workflows.lexical_prescan._pattern_scan_scope_reason` -- the primitive
    the fold populates `pattern_prescan[...]["scope_reason"]` from."""
    from abicheck.buildsource.pattern_scan import PatternScanResult
    from abicheck.workflows.lexical_prescan import (
        _pattern_scan_scope_reason,
        compute_pattern_prescan_side,
    )

    # (a) no headers/--sources supplied at all.
    empty_roots: list = []
    no_input_result = compute_pattern_prescan_side(empty_roots, None, None, None)
    assert (
        _pattern_scan_scope_reason(empty_roots, False, no_input_result) == "no_inputs"
    )

    # (b) a real, valid, empty --since/--changed-path seed.
    header = tmp_path / "risky.hpp"
    header.write_text("virtual void f();\n", encoding="utf-8")
    seeded_result = compute_pattern_prescan_side([header], None, None, (), seeded=True)
    assert seeded_result.files_scanned == 0
    assert _pattern_scan_scope_reason([header], True, seeded_result) == "empty_seed"

    # (b2) a seeded run whose seed *did* select candidate files, but every
    # one was unreadable (`files_skipped > 0`) -- a genuine acquisition
    # failure, not the seed's own empty-by-design scope (Codex review,
    # fresh evidence: `seeded` alone used to collapse this onto
    # `empty_seed`, masking the failure).
    seeded_unreadable_result = PatternScanResult(
        facts=[], files_scanned=0, files_skipped=1
    )
    assert (
        _pattern_scan_scope_reason([header], True, seeded_unreadable_result)
        == "unreadable_inputs"
    )

    # (c) supplied inputs that are all unreadable (not a directory, not a
    # real file the scanner can open).
    ghost = tmp_path / "does-not-exist.hpp"
    unreadable_result = PatternScanResult(facts=[], files_scanned=0, files_skipped=1)
    assert (
        _pattern_scan_scope_reason([ghost], False, unreadable_result)
        == "unreadable_inputs"
    )

    # (d) a seeded run whose selected root doesn't exist on disk at all --
    # Codex review, third round, fresh evidence: `pattern_scan.
    # iter_source_files` silently drops a root that is neither a file nor
    # a directory with no accounting of its own. Fixed at the producer
    # (`scan_files`, fourth round): a missing root is now counted as
    # skipped there, so this side's own `files_skipped` already reflects
    # it by the time it reaches `_pattern_scan_scope_reason` -- exercised
    # through the REAL `compute_pattern_prescan_side` producer (not a
    # hand-built `PatternScanResult`).
    missing_root = tmp_path / "deleted-header.hpp"
    seeded_missing_root_result = compute_pattern_prescan_side(
        [missing_root], None, None, (), seeded=True
    )
    assert seeded_missing_root_result.files_scanned == 0
    assert seeded_missing_root_result.files_skipped == 1
    assert (
        _pattern_scan_scope_reason([missing_root], True, seeded_missing_root_result)
        == "unreadable_inputs"
    )

    # A side that actually scanned something needs no reason at all.
    scanned_result = compute_pattern_prescan_side([header], None, None, None)
    assert scanned_result.files_scanned == 1
    assert _pattern_scan_scope_reason([header], False, scanned_result) is None


def test_pattern_prescan_review_warnings_none_when_never_folded():
    assert pattern_prescan_review_warnings(None) == []


def test_pattern_prescan_review_warnings_silent_only_for_no_inputs():
    """`no_inputs` is the ordinary, structural "this evidence tier does not
    apply" case every binary-only comparison hits -- the review digest must
    not repeat it as a warning. `empty_seed` (Codex review, second round,
    fresh evidence) is NOT silent: even though it's a real, valid,
    by-design scope, a reviewer approving this PR still benefits from
    knowing the lexical scan didn't see the diff's changes."""
    summary = compute_pattern_prescan_summary(
        {
            "old": {"files_scanned": 0, "scope_reason": "no_inputs"},
            "new": {"files_scanned": 3, "scope_reason": None},
        }
    )
    assert pattern_prescan_review_warnings(summary) == []


def test_pattern_prescan_review_warnings_surfaces_unreadable_inputs():
    """Codex review, PR #1169: `build_review_digest_document` never called
    `render_pattern_prescan_markdown` at all, so an `unreadable_inputs` side
    -- a genuine acquisition failure -- silently vanished from the one
    GitHub-facing summary a reviewer approves a merge from."""
    summary = compute_pattern_prescan_summary(
        {
            "old": {"files_scanned": 0, "scope_reason": "unreadable_inputs"},
            "new": {"files_scanned": 3, "scope_reason": None},
        }
    )
    warnings = pattern_prescan_review_warnings(summary)
    assert len(warnings) == 1
    assert warnings[0].startswith("OLD pattern pre-scan:")
    assert "unreadable" in warnings[0]


def test_pattern_prescan_review_warnings_surfaces_empty_seed():
    """Codex review, PR #1169, second round, fresh evidence: a valid
    `empty_seed` scope previously produced no warning at all -- the digest
    could still read as an unqualified "safe to merge" despite the lexical
    scan having covered none of this diff's own changes."""
    summary = compute_pattern_prescan_summary(
        {
            "old": {"files_scanned": 2, "scope_reason": None},
            "new": {"files_scanned": 0, "scope_reason": "empty_seed"},
        }
    )
    warnings = pattern_prescan_review_warnings(summary)
    assert len(warnings) == 1
    assert warnings[0].startswith("NEW pattern pre-scan:")
    assert "0 files" in warnings[0]


def test_pattern_prescan_review_warnings_surfaces_partial_coverage():
    """Codex review, PR #1169, second round, fresh evidence: a side with
    `files_scanned > 0` AND `files_skipped > 0` has `scope_reason is None`
    (real coverage exists) but `coverage.status == "partial"` -- previously
    unexamined by this helper entirely."""
    summary = compute_pattern_prescan_summary(
        {
            "old": {"files_scanned": 2, "scope_reason": None},
            "new": {
                "files_scanned": 2,
                "scope_reason": None,
                "coverage": {
                    "status": "partial",
                    "detail": "2 file(s), 1 unreadable skipped",
                },
            },
        }
    )
    warnings = pattern_prescan_review_warnings(summary)
    assert len(warnings) == 1
    assert (
        warnings[0]
        == "NEW pattern pre-scan: partial coverage (2 file(s), 1 unreadable skipped)"
    )


def test_preprocessor_prescan_review_warnings_none_when_never_folded():
    assert preprocessor_prescan_review_warnings(None) == []


def test_preprocessor_prescan_review_warnings_silent_when_fully_covered():
    summary = compute_preprocessor_prescan_summary(
        {
            "old": {"ran": True, "coverage": {"status": "present"}},
            "new": {"ran": True, "coverage": {"status": "present"}},
        }
    )
    assert preprocessor_prescan_review_warnings(summary) == []


def test_preprocessor_prescan_review_warnings_silent_when_never_attempted():
    """The ordinary `ran: False` skip (no L3 build evidence / no clang at
    all) is `not_collected` but never attempted -- must stay silent, same
    as `pattern_prescan`'s `no_inputs`."""
    summary = compute_preprocessor_prescan_summary(
        {
            "old": {"ran": False, "coverage": {"status": "not_collected"}},
            "new": {"ran": True, "coverage": {"status": "present"}},
        }
    )
    assert preprocessor_prescan_review_warnings(summary) == []


def test_preprocessor_prescan_review_warnings_surfaces_partial_coverage():
    """Codex review, PR #1169: a partially-inspected build (some `clang -E`
    probes failed, or the probe cap truncated it) reported its divergence/
    leak counts in the review digest exactly like a clean, fully-scanned
    one."""
    summary = compute_preprocessor_prescan_summary(
        {
            "old": {"ran": True, "coverage": {"status": "present"}},
            "new": {
                "ran": True,
                "coverage": {"status": "partial", "detail": "3/5 probes failed"},
            },
        }
    )
    warnings = preprocessor_prescan_review_warnings(summary)
    assert len(warnings) == 1
    assert (
        warnings[0] == "NEW preprocessor pre-scan: partial coverage (3/5 probes failed)"
    )


def test_preprocessor_prescan_review_warnings_surfaces_all_failed():
    """Codex review, PR #1169, second round, fresh evidence: when clang and
    build evidence are both available but every `clang -E` invocation
    failed, `PreprocessorScanResult.coverage()` returns `status:
    "not_collected"` with `ran: True` -- this condition previously only
    recognized `partial`, so an all-failed scan (0 divergences/leaks,
    because nothing was actually inspected) emitted no warning at all."""
    summary = compute_preprocessor_prescan_summary(
        {
            "old": {"ran": True, "coverage": {"status": "present"}},
            "new": {
                "ran": True,
                "coverage": {
                    "status": "not_collected",
                    "detail": "clang -E ran but every invocation failed (2 attempt(s))",
                },
            },
        }
    )
    warnings = preprocessor_prescan_review_warnings(summary)
    assert len(warnings) == 1
    assert warnings[0].startswith("NEW preprocessor pre-scan:")
    assert "every invocation failed" in warnings[0]


def test_compute_review_digest_surfaces_pattern_prescan_scope_warning():
    """End-to-end: `reporter_markdown.compute_review_digest` -- what
    `build_review_digest_document` actually calls for `--format review` --
    now folds the pattern-prescan warning into `coverage_warnings` instead
    of dropping it (Codex review, PR #1169)."""
    from abicheck.reporter_markdown import compute_review_digest

    result = DiffResult(
        library="libfoo",
        old_version="1.0",
        new_version="1.1",
        changes=[],
        pattern_prescan={
            "old": {"files_scanned": 0, "scope_reason": "unreadable_inputs"},
            "new": {"files_scanned": 2, "scope_reason": None},
        },
    )
    digest = compute_review_digest(result)
    assert any(
        "pattern pre-scan" in w and "unreadable" in w for w in digest.coverage_warnings
    )
