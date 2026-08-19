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

"""Pure-logic tests for the field-eval runner's source tier (D1) + drift gate (D2).

The runner shells out to abicheck/git/cmake for the live scans (covered by the
scheduled CI lane, not here). These tests pin the *pure* halves — the embedded
`build_source` coverage parser, the binary-tier drift gate, the source-entry
filter, and the report renderers — so the regression guard and the source-tier
table cannot silently break. The runner lives in `eval/`, imported by adding
that directory to `sys.path`.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_EVAL_DIR = Path(__file__).resolve().parent.parent / "eval"
if str(_EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(_EVAL_DIR))

runner = pytest.importorskip("runner", reason="eval/runner.py importable")


def _snap() -> dict:
    return {
        "build_source": {
            "manifest": {
                "coverage": [
                    {"layer": "L3_build", "status": "present"},
                    {"layer": "L4_source_abi", "status": "partial"},
                    {"layer": "L5_source_graph", "status": "present"},
                ]
            },
            "build_evidence": {
                "compile_units": [1, 2, 3],
                "targets": [1],
                "build_options": [1, 2],
            },
            "source_abi": {
                "reachable_source_surface": {
                    "declarations": [1, 2, 3, 4],
                    "types": [1, 2],
                    "macros": [1],
                }
            },
            "source_graph": {"nodes": [1, 2, 3, 4, 5], "edges": [1, 2]},
        }
    }


def test_source_coverage_counts_each_layer() -> None:
    c = runner._source_coverage(_snap())
    assert c["l3_compile_units"] == 3
    assert c["l3_targets"] == 1
    assert c["l3_build_options"] == 2
    assert c["l4_declarations"] == 4
    assert c["l4_types"] == 2
    assert c["l4_macros"] == 1
    assert c["l5_nodes"] == 5
    assert c["l5_edges"] == 2
    assert c["coverage_status"]["L4_source_abi"] == "partial"


def test_source_coverage_defends_against_empty_payload() -> None:
    # A configure-only tree / no clang yields a missing-or-partial payload; the
    # parser must still return a zeroed row, never raise.
    for snap in ({}, {"build_source": {}}, {"build_source": {"source_abi": {}}}):
        c = runner._source_coverage(snap)
        assert c["l3_compile_units"] == 0
        assert c["l4_declarations"] == 0
        assert c["coverage_status"] == {}


def test_list_len_non_list_is_zero() -> None:
    assert runner._list_len([1, 2]) == 2
    assert runner._list_len(None) == 0
    assert runner._list_len("abc") == 0  # a string is not a fact list


def test_drift_rows_flags_mismatch_and_error_only() -> None:
    payload = {
        "results": [
            {"lib": "ok", "verdict": "BREAKING", "verdict_matches_expected": True},
            {
                "lib": "drift",
                "verdict": "COMPATIBLE",
                "verdict_matches_expected": False,
            },
            {"lib": "boom", "error": "dump failed"},
        ]
    }
    assert [r["lib"] for r in runner.drift_rows(payload)] == ["drift", "boom"]


def test_drift_rows_empty_when_all_match() -> None:
    payload = {"results": [{"lib": "a", "verdict_matches_expected": True}]}
    assert runner.drift_rows(payload) == []


def test_dump_sources_uses_depth_source_not_the_retired_full_rung(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Regression guard for the P0 false-green incident: `--depth full` was
    retired (ADR-043 D2, collapsed into `source`) and is now a hard CLI
    error, so every source-tier scan failed identically while the scheduled
    workflow still reported success (0/N scanned, tolerated as "some
    libraries failed to build"). Asserts the actual argv `_dump_sources()`
    shells out with (via `runner._run`), not its source text (CodeRabbit
    review — a source-text match passes even on unreachable/dead code).
    """
    captured: list[list[str]] = []

    def fake_run(cmd: list[str]) -> tuple[float, object]:
        captured.append(cmd)
        return 0.1, SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(runner, "_run", fake_run)
    runner._dump_sources(tmp_path / "tree", tmp_path / "build", tmp_path / "out.json")

    assert len(captured) == 1
    cmd = captured[0]
    assert "full" not in cmd
    depth_index = cmd.index("--depth")
    assert cmd[depth_index + 1] == "source"


class TestSideHasLayerEvidence:
    """Direct tests of the primitive `_row_has_full_evidence` builds on —
    count AND status must both check out, independent of any one caller's
    row-shaped context (mirrors this repo's own "primitive-level property
    tests" convention for a reusable predicate)."""

    def test_positive_count_and_present_status_is_evidence(self) -> None:
        row = {
            "old_coverage": {
                "l5_nodes": 7,
                "coverage_status": {"L5_source_graph": "present"},
            }
        }
        assert runner._side_has_layer_evidence(
            row, "old_coverage", "l5_nodes", "L5_source_graph"
        )

    def test_zero_count_is_not_evidence_even_if_status_present(self) -> None:
        row = {
            "old_coverage": {
                "l5_nodes": 0,
                "coverage_status": {"L5_source_graph": "present"},
            }
        }
        assert not runner._side_has_layer_evidence(
            row, "old_coverage", "l5_nodes", "L5_source_graph"
        )

    def test_positive_count_with_partial_status_is_not_evidence(self) -> None:
        # The exact shape this fix closes: a positive count from fallback
        # nodes folded in from L3, while the real L5 pass degraded.
        row = {
            "old_coverage": {
                "l5_nodes": 7,
                "coverage_status": {"L5_source_graph": "partial"},
            }
        }
        assert not runner._side_has_layer_evidence(
            row, "old_coverage", "l5_nodes", "L5_source_graph"
        )

    def test_positive_count_with_missing_status_key_is_not_evidence(self) -> None:
        # No coverage_status entry for this layer at all -- conservative
        # default (not "present" unless explicitly recorded as such).
        row = {"old_coverage": {"l5_nodes": 7, "coverage_status": {}}}
        assert not runner._side_has_layer_evidence(
            row, "old_coverage", "l5_nodes", "L5_source_graph"
        )

    def test_missing_coverage_status_dict_entirely_is_not_evidence(self) -> None:
        row = {"old_coverage": {"l5_nodes": 7}}
        assert not runner._side_has_layer_evidence(
            row, "old_coverage", "l5_nodes", "L5_source_graph"
        )

    def test_missing_side_entirely_is_not_evidence(self) -> None:
        assert not runner._side_has_layer_evidence(
            {}, "old_coverage", "l5_nodes", "L5_source_graph"
        )


class TestSourceTierBroken:
    def _row(
        self,
        lib: str,
        *,
        error: str | None = None,
        l3: int = 3,
        l4: int = 5,
        l5: int = 7,
        old_l3: int | None = None,
        old_l4: int | None = None,
        old_l5: int | None = None,
        l3_status: str = "present",
        l4_status: str = "present",
        l5_status: str = "present",
        old_l3_status: str | None = None,
        old_l4_status: str | None = None,
        old_l5_status: str | None = None,
    ) -> dict:
        """A source-tier row shaped like `scan_source_one()`'s real output —
        both `old_coverage` and `new_coverage` present, with all three
        `_EVIDENCE_LAYERS` fact counts *and* their `coverage_status` entries
        (`abicheck.buildsource.model.CoverageStatus` — "present"/"partial"/
        "not_collected"). Every `old_*` defaults to its `l3`/`l4`/`l5`
        counterpart (both sides "healthy"); pass one explicitly to build a
        one-sided row (only one snapshot actually captured that layer's
        evidence, or only one side's status is degraded).
        """
        if error is not None:
            return {"lib": lib, "error": error}
        return {
            "lib": lib,
            "verdict": "COMPATIBLE",
            "old_coverage": {
                "l3_compile_units": l3 if old_l3 is None else old_l3,
                "l4_declarations": l4 if old_l4 is None else old_l4,
                "l5_nodes": l5 if old_l5 is None else old_l5,
                "coverage_status": {
                    "L3_build": l3_status if old_l3_status is None else old_l3_status,
                    "L4_source_abi": l4_status
                    if old_l4_status is None
                    else old_l4_status,
                    "L5_source_graph": l5_status
                    if old_l5_status is None
                    else old_l5_status,
                },
            },
            "new_coverage": {
                "l3_compile_units": l3,
                "l4_declarations": l4,
                "l5_nodes": l5,
                "coverage_status": {
                    "L3_build": l3_status,
                    "L4_source_abi": l4_status,
                    "L5_source_graph": l5_status,
                },
            },
        }

    def test_none_when_no_source_entries_requested(self) -> None:
        # Binary-only run (or a manifest with no `source:` blocks) — nothing
        # to be broken about.
        assert runner.source_tier_broken({"source_results": []}) is None
        assert runner.source_tier_broken({}) is None

    def test_none_when_every_entry_scanned_with_real_evidence(self) -> None:
        payload = {"source_results": [self._row("zlib"), self._row("zstd")]}
        assert runner.source_tier_broken(payload) is None

    def test_none_on_ordinary_partial_per_library_failure(self) -> None:
        # One library's own build/network hiccup is tolerated — the tier as
        # a whole is still healthy.
        payload = {
            "source_results": [
                self._row("zlib"),
                self._row("snappy", error="git clone timed out"),
            ]
        }
        assert runner.source_tier_broken(payload) is None

    def test_none_on_single_library_run_that_fails_ordinarily(self) -> None:
        # Regression guard (Codex review): a `workflow_dispatch --only zlib`
        # debugging run where that one library's own build/network hiccup
        # fails is indistinguishable, with only one entry, from the tool
        # itself being systemically broken -- there's no second entry to
        # compare against. `total == 1` must not be treated as "every entry
        # failed identically" the way `total > 1` legitimately is.
        payload = {"source_results": [self._row("zlib", error="git clone timed out")]}
        assert runner.source_tier_broken(payload) is None

    def test_flags_single_library_success_with_zero_evidence(self) -> None:
        # Unlike the "ordinary failure" case above, a single library that
        # reports SUCCESS (no `error`) but captured zero L3 evidence is an
        # internal inconsistency in that one row already -- no second entry
        # needed to know something's wrong, so this stays gated at total == 1.
        payload = {"source_results": [self._row("zlib", l3=0)]}
        reason = runner.source_tier_broken(payload)
        assert reason is not None
        assert "1/1" in reason

    def test_flags_zero_scanned_as_systemic_failure(self) -> None:
        # The exact shape of the `--depth full` incident: every entry fails
        # identically (the same CLI usage error), not a per-library issue.
        payload = {
            "source_results": [
                self._row("zlib", error="Invalid value for '--depth': ..."),
                self._row("zstd", error="Invalid value for '--depth': ..."),
                self._row("snappy", error="Invalid value for '--depth': ..."),
            ]
        }
        reason = runner.source_tier_broken(payload)
        assert reason is not None
        assert "0/3" in reason

    def test_flags_zero_evidence_despite_reported_success(self) -> None:
        # Every scan "succeeds" (no `error` key) but none actually collected
        # L3 build evidence — a quieter false-green than a hard CLI error.
        payload = {"source_results": [self._row("zlib", l3=0), self._row("zstd", l3=0)]}
        reason = runner.source_tier_broken(payload)
        assert reason is not None
        assert "2/2" in reason
        assert "L3" in reason

    def test_flags_zero_l4_evidence_despite_success(self) -> None:
        # The L3/L4/L5 check is real for every layer, not just L3 -- a scan
        # that captured compile units but zero declarations (e.g. a clang
        # invocation that silently failed mid-replay) must gate the same way.
        payload = {"source_results": [self._row("zlib", l4=0)]}
        reason = runner.source_tier_broken(payload)
        assert reason is not None
        assert "1/1" in reason

    def test_flags_zero_l5_evidence_despite_success(self) -> None:
        payload = {"source_results": [self._row("zlib", l5=0)]}
        reason = runner.source_tier_broken(payload)
        assert reason is not None
        assert "1/1" in reason

    def test_flags_partial_l5_status_despite_nonzero_node_count(self) -> None:
        # Codex review, fresh evidence: when source replay's L5 call/type
        # pass degrades, source_graph.nodes can still be populated by
        # fallback nodes folded in from L3 targets/compile units/files, so
        # l5_nodes stays positive while the manifest's own coverage table
        # correctly records L5_source_graph: partial. A count-only check
        # would misread this as full L5 evidence.
        payload = {"source_results": [self._row("zlib", l5_status="partial")]}
        reason = runner.source_tier_broken(payload)
        assert reason is not None
        assert "1/1" in reason

    def test_flags_not_collected_l3_status_despite_nonzero_count(self) -> None:
        # The same reasoning applies to every layer, not just L5.
        payload = {"source_results": [self._row("zlib", l3_status="not_collected")]}
        reason = runner.source_tier_broken(payload)
        assert reason is not None

    def test_flags_partial_status_on_only_one_side(self) -> None:
        # A degraded status on just the OLD side (new side fully "present")
        # must still fail -- both-sides-complete is required, mirroring the
        # existing one-sided-count regression guard below.
        payload = {"source_results": [self._row("zlib", old_l5_status="partial")]}
        reason = runner.source_tier_broken(payload)
        assert reason is not None

    def test_none_when_every_layer_status_is_present(self) -> None:
        # Sanity check: the default _row() (all "present") must still pass,
        # so this fix doesn't turn a genuinely healthy run into a false red.
        payload = {"source_results": [self._row("zlib")]}
        assert runner.source_tier_broken(payload) is None

    def test_flags_evidence_present_on_only_one_side(self) -> None:
        # Regression guard (Codex review): a row whose OLD snapshot silently
        # captured zero L3 units while the NEW one succeeded must NOT count
        # as "with evidence" -- that comparison is one-sided and already
        # misleading, even though *a* snapshot has real data.
        payload = {
            "source_results": [
                self._row("zlib", l3=5, old_l3=0),
                self._row("zstd", l3=5, old_l3=0),
            ]
        }
        reason = runner.source_tier_broken(payload)
        assert reason is not None
        assert "2/2" in reason

    def test_source_scan_summary_counts(self) -> None:
        payload = {
            "source_results": [
                self._row("zlib"),
                self._row("zstd", l3=0),
                self._row("snappy", error="boom"),
            ]
        }
        summary = runner.source_scan_summary(payload)
        assert summary == {"total": 3, "scanned": 2, "with_evidence": 1}

    def test_source_scan_summary_requires_evidence_on_both_sides(self) -> None:
        payload = {
            "source_results": [
                self._row("zlib", l3=5, old_l3=0),  # new has evidence, old doesn't
                self._row("zstd"),  # both sides healthy
            ]
        }
        summary = runner.source_scan_summary(payload)
        assert summary == {"total": 2, "scanned": 2, "with_evidence": 1}


def test_source_entries_filters_to_source_blocks_and_only() -> None:
    manifest = {
        "libraries": [
            {"lib": "zlib", "source": {"repo": "r", "tag_old": "a", "tag_new": "b"}},
            {"lib": "icu"},  # no source block → excluded
            {"lib": "zstd", "source": {"repo": "r2"}},
        ]
    }
    assert [e["lib"] for e in runner._source_entries(manifest, None)] == [
        "zlib",
        "zstd",
    ]
    assert [e["lib"] for e in runner._source_entries(manifest, {"zstd"})] == ["zstd"]


def test_checkout_key_distinguishes_repo_and_tag() -> None:
    # A manifest tag or repo bump must land in a different cache dir so a stale
    # checkout is never silently reused for a different revision (Codex review).
    repo = "https://github.com/madler/zlib.git"
    k_old = runner._checkout_key(repo, "v1.2.13")
    k_new = runner._checkout_key(repo, "v1.3.1")
    k_other_repo = runner._checkout_key("https://example.com/fork.git", "v1.2.13")
    assert k_old != k_new
    assert k_old != k_other_repo
    assert runner._checkout_key(repo, "v1.2.13") == k_old  # stable


def test_render_report_has_source_section_with_coverage() -> None:
    payload = {
        "generated_utc": "2026-01-01T00:00:00+00:00",
        "abicheck_version": "9.9",
        "host": {"platform": "linux", "python": "3.13"},
        "tier": "source",
        "source_results": [
            {
                "lib": "zlib",
                "old": "v1.2.13",
                "new": "v1.3.1",
                "verdict": "COMPATIBLE",
                "new_coverage": runner._source_coverage(_snap()),
                "build_s": 5.0,
                "compare_s": 1.0,
            },
            {
                "lib": "broken",
                "old": "x",
                "new": "y",
                "error": "skipped: missing cmake",
            },
        ],
    }
    rep = runner.render_report(payload)
    assert "## Source tier" in rep
    assert "L4 decls" in rep
    assert "| zlib |" in rep
    assert "SKIP/ERR" in rep  # the errored entry is still rendered as a row


def test_render_report_binary_section_shows_verdict_distribution() -> None:
    payload = {
        "generated_utc": "t",
        "abicheck_version": "v",
        "host": {"platform": "linux", "python": "3.13"},
        "tier": "binary",
        "results": [
            {
                "lib": "zstd",
                "old": "1.5.5",
                "new": "1.5.7",
                "verdict": "BREAKING",
                "verdict_matches_expected": True,
                "breaking": 3,
                "risk_changes": 0,
                "compatible_additions": 1,
                "total_changes": 4,
            },
        ],
    }
    rep = runner.render_report(payload)
    assert "## Binary tier" in rep
    assert "BREAKING×1" in rep
    assert "| zstd |" in rep
