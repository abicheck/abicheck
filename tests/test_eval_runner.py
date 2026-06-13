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
            {"lib": "drift", "verdict": "COMPATIBLE", "verdict_matches_expected": False},
            {"lib": "boom", "error": "dump failed"},
        ]
    }
    assert [r["lib"] for r in runner.drift_rows(payload)] == ["drift", "boom"]


def test_drift_rows_empty_when_all_match() -> None:
    payload = {"results": [{"lib": "a", "verdict_matches_expected": True}]}
    assert runner.drift_rows(payload) == []


def test_source_entries_filters_to_source_blocks_and_only() -> None:
    manifest = {
        "libraries": [
            {"lib": "zlib", "source": {"repo": "r", "tag_old": "a", "tag_new": "b"}},
            {"lib": "icu"},  # no source block → excluded
            {"lib": "zstd", "source": {"repo": "r2"}},
        ]
    }
    assert [e["lib"] for e in runner._source_entries(manifest, None)] == ["zlib", "zstd"]
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
                "lib": "zlib", "old": "v1.2.13", "new": "v1.3.1",
                "verdict": "COMPATIBLE",
                "new_coverage": runner._source_coverage(_snap()),
                "build_s": 5.0, "compare_s": 1.0,
            },
            {"lib": "broken", "old": "x", "new": "y", "error": "skipped: missing cmake"},
        ],
    }
    rep = runner.render_report(payload)
    assert "## Source tier" in rep
    assert "L4 decls" in rep
    assert "| zlib |" in rep
    assert "SKIP/ERR" in rep  # the errored entry is still rendered as a row


def test_render_report_binary_section_shows_verdict_distribution() -> None:
    payload = {
        "generated_utc": "t", "abicheck_version": "v",
        "host": {"platform": "linux", "python": "3.13"}, "tier": "binary",
        "results": [
            {"lib": "zstd", "old": "1.5.5", "new": "1.5.7", "verdict": "BREAKING",
             "verdict_matches_expected": True, "breaking": 3, "risk_changes": 0,
             "compatible_additions": 1, "total_changes": 4},
        ],
    }
    rep = runner.render_report(payload)
    assert "## Binary tier" in rep
    assert "BREAKING×1" in rep
    assert "| zstd |" in rep
