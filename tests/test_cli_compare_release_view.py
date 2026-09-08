# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
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

"""Codex review (PR #1154 follow-up): a directory/package `compare`'s
`--view` handling used to silently drop every one of its four derived
values (`report_mode`/`show_only`/`demangle`/`explain_patterns`) at the
release fan-out's own dispatch (`_dispatch_release_compare` never forwarded
them to `compare_release_cmd`) -- `compare OLD_DIR NEW_DIR --view
show=breaking` or `--view root-cause` was accepted but silently rendered
the ordinary, full, unfiltered release report.

This module proves the fix: `show=...` actually filters the release's own
per-library findings (JSON and Markdown alike), `demangle`/`no-demangle`
actually changes the release Markdown's symbol spelling, `patterns` doesn't
crash the release path, and `leaf`/`root-cause` are rejected outright with
a clear usage error rather than being silently ignored -- the release
report is a genuinely different, already-aggregated document with no
single-`DiffResult` root-cause graph for those two view modes to
restructure (the same structural reason `--format sarif/html/review`
already reject a directory/package operand).
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import snapshot_to_json

# The real Itanium mangling of `api_b()` (matches test_cli_compare_fold_demangle.py's
# own fixture, confirmed against real `c++filt`).
_MANGLED = "_Z5api_bv"
_DEMANGLED = "api_b()"


def _fn(name: str, mangled: str, ret: str = "int") -> Function:
    return Function(
        name=name, mangled=mangled, return_type=ret, visibility=Visibility.PUBLIC
    )


def _write_snap(path: Path, snap: AbiSnapshot) -> Path:
    path.write_text(snapshot_to_json(snap), encoding="utf-8")
    return path


def _write_removed_function_pair(tmp_path: Path) -> tuple[Path, Path]:
    """One library, one removed public function -- a BREAKING, function-kind
    finding (bucket ``breaking``, element ``functions``)."""
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()
    old_snap = AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        functions=[_fn("api_b", _MANGLED)],
        from_headers=True,
    )
    new_snap = AbiSnapshot(
        library="libfoo.so", version="2.0", functions=[], from_headers=True
    )
    _write_snap(old_dir / "libfoo.json", old_snap)
    _write_snap(new_dir / "libfoo.json", new_snap)
    return old_dir, new_dir


def _invoke(*args: str):
    return CliRunner().invoke(main, list(args))


class TestReleaseViewShowOnly:
    """``--view show=...`` on a directory/package input."""

    def test_show_only_filters_release_markdown_findings(self, tmp_path: Path) -> None:
        old_dir, new_dir = _write_removed_function_pair(tmp_path)

        # The removed symbol is a *function*; asking to show only
        # *variable* findings must filter it out of the rendered findings
        # list even though the underlying breaking change (and thus the
        # exit code) is unaffected -- policy/view changes never alter what
        # was actually observed (AGENTS.md "Record before disposing").
        result = _invoke(
            "compare", str(old_dir), str(new_dir), "--jobs", "1",
            "--view", "show=variables",
        )
        assert result.exit_code == 4, result.output
        assert "api_b" not in result.output
        assert "## Per-Library Findings" not in result.output
        # The raw per-library counts (unaffected by show_only) still report
        # the real breaking count.
        assert "Breaking: 1" in result.output or "breaking" in result.output.lower()

    def test_show_only_does_not_filter_when_it_matches(self, tmp_path: Path) -> None:
        old_dir, new_dir = _write_removed_function_pair(tmp_path)

        result = _invoke(
            "compare", str(old_dir), str(new_dir), "--jobs", "1",
            "--view", "show=functions",
        )
        assert result.exit_code == 4, result.output
        assert "## Per-Library Findings" in result.output
        assert _DEMANGLED in result.output or _MANGLED in result.output

    def test_show_only_filters_release_json_findings(self, tmp_path: Path) -> None:
        old_dir, new_dir = _write_removed_function_pair(tmp_path)

        result = _invoke(
            "compare", str(old_dir), str(new_dir), "--jobs", "1",
            "--format", "json", "--view", "show=variables",
        )
        assert result.exit_code == 4, result.output
        doc = json.loads(result.output)
        lib_entries = doc["libraries"]
        assert len(lib_entries) == 1
        # The function-kind finding is filtered out of the projected list,
        # but the raw counted total (unaffected by show_only) is preserved.
        assert lib_entries[0].get("findings", []) == []
        assert lib_entries[0]["breaking"] == 1

    def test_show_only_does_not_change_exit_code_or_verdict(
        self, tmp_path: Path
    ) -> None:
        """View/rendering options are presentation-only (ADR-068 D4) --
        filtering every displayed finding away must not turn a real ABI
        break into a clean exit."""
        old_dir, new_dir = _write_removed_function_pair(tmp_path)

        baseline = _invoke("compare", str(old_dir), str(new_dir), "--jobs", "1")
        filtered = _invoke(
            "compare", str(old_dir), str(new_dir), "--jobs", "1",
            "--view", "show=variables",
        )
        assert baseline.exit_code == filtered.exit_code == 4


class TestReleaseViewDemangle:
    """``--view demangle``/``--view no-demangle`` on a directory/package input."""

    def test_demangled_by_default_in_release_markdown(self, tmp_path: Path) -> None:
        old_dir, new_dir = _write_removed_function_pair(tmp_path)

        result = _invoke("compare", str(old_dir), str(new_dir), "--jobs", "1")
        assert result.exit_code == 4, result.output
        assert _DEMANGLED in result.output
        assert _MANGLED not in result.output

    def test_no_demangle_keeps_mangled_name_in_release_markdown(
        self, tmp_path: Path
    ) -> None:
        old_dir, new_dir = _write_removed_function_pair(tmp_path)

        result = _invoke(
            "compare", str(old_dir), str(new_dir), "--jobs", "1",
            "--view", "no-demangle",
        )
        assert result.exit_code == 4, result.output
        assert _MANGLED in result.output
        assert _DEMANGLED not in result.output

    def test_demangle_is_a_no_op_for_release_json(self, tmp_path: Path) -> None:
        """JSON is a machine format -- symbols always stay raw/mangled,
        matching a single-pair `compare --format json`'s own behaviour."""
        old_dir, new_dir = _write_removed_function_pair(tmp_path)

        result = _invoke(
            "compare", str(old_dir), str(new_dir), "--jobs", "1",
            "--format", "json", "--view", "demangle",
        )
        assert result.exit_code == 4, result.output
        assert _MANGLED in result.output


class TestReleaseViewPatterns:
    """``--view patterns`` on a directory/package input: a per-library
    stderr echo, never part of the rendered report body."""

    def test_patterns_does_not_crash_and_does_not_change_the_report_body(
        self, tmp_path: Path
    ) -> None:
        old_dir, new_dir = _write_removed_function_pair(tmp_path)

        baseline = _invoke("compare", str(old_dir), str(new_dir), "--jobs", "1")
        with_patterns = _invoke(
            "compare", str(old_dir), str(new_dir), "--jobs", "1",
            "--view", "patterns",
        )
        assert with_patterns.exit_code == baseline.exit_code == 4
        # `--view patterns`'s only visible effect is the per-library stderr
        # echo (CliRunner's `.output` merges stdout+stderr, so the raw
        # report body -- from the "# ABI Release Comparison" header onward
        # -- is what must stay byte-identical, not the whole captured
        # stream).
        assert "No pattern-aware modulations applied." in with_patterns.output
        report_marker = "# ABI Release Comparison"
        assert baseline.output[baseline.output.index(report_marker) :] == (
            with_patterns.output[with_patterns.output.index(report_marker) :]
        )


class TestReleaseViewReportModeRejected:
    """``--view leaf``/``--view root-cause`` restructure a single
    `DiffResult`'s own root-cause graph -- the release summary has no such
    graph, so both are rejected with a clear usage error instead of being
    silently ignored."""

    def test_leaf_is_rejected_for_a_directory_operand(self, tmp_path: Path) -> None:
        old_dir, new_dir = _write_removed_function_pair(tmp_path)

        result = _invoke(
            "compare", str(old_dir), str(new_dir), "--jobs", "1",
            "--view", "leaf",
        )
        assert result.exit_code == 64, result.output
        assert "--view leaf is not available" in result.output
        assert "directories or packages" in result.output

    def test_root_cause_is_rejected_for_a_directory_operand(
        self, tmp_path: Path
    ) -> None:
        old_dir, new_dir = _write_removed_function_pair(tmp_path)

        result = _invoke(
            "compare", str(old_dir), str(new_dir), "--jobs", "1",
            "--view", "root-cause",
        )
        assert result.exit_code == 64, result.output
        assert "--view root-cause is not available" in result.output

    def test_full_and_impact_are_accepted_for_a_directory_operand(
        self, tmp_path: Path
    ) -> None:
        """'full' (the default) and 'impact' (sugar for full + an additive
        JSON flag with no release-summary equivalent) must not be rejected
        -- only leaf/root-cause restructure the document shape."""
        old_dir, new_dir = _write_removed_function_pair(tmp_path)

        for token in ("full", "impact"):
            result = _invoke(
                "compare", str(old_dir), str(new_dir), "--jobs", "1",
                "--view", token,
            )
            assert result.exit_code == 4, (token, result.output)
