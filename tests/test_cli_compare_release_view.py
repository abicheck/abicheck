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
from abicheck.model import (
    AbiSnapshot,
    Function,
    Param,
    RecordType,
    TypeField,
    Visibility,
)
from abicheck.probe_harness import MatrixSnapshot
from abicheck.serialization import snapshot_to_json
from abicheck.workflows.findings import matrix_snapshot_to_json

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


def _write_removed_functions_pair(tmp_path: Path, *, count: int) -> tuple[Path, Path]:
    """One library, *count* removed public functions -- *count* independent
    BREAKING, function-kind findings, past `_MAX_RELEASE_FINDINGS_PER_
    LIBRARY`'s per-library display cap for a large enough *count*."""
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()
    functions = [_fn(f"api_{i}", f"_Z{len(f'api_{i}')}api_{i}v") for i in range(count)]
    old_snap = AbiSnapshot(
        library="libfoo.so", version="1.0", functions=functions, from_headers=True
    )
    new_snap = AbiSnapshot(
        library="libfoo.so", version="2.0", functions=[], from_headers=True
    )
    _write_snap(old_dir / "libfoo.json", old_snap)
    _write_snap(new_dir / "libfoo.json", new_snap)
    return old_dir, new_dir


def _write_struct_size_change_pair(tmp_path: Path) -> tuple[Path, Path]:
    """One library whose ``Point`` struct grows a field, with a function
    taking it by value -- a ``TYPE_SIZE_CHANGED`` root-type change whose
    ``affected_symbols``/``caused_count`` feed a real impact-summary table
    (``--view impact``'s aggregate counterpart, Codex review PR #1154
    follow-up)."""
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()
    point_v1 = RecordType(
        name="Point", kind="struct", size_bits=64,
        fields=[
            TypeField(name="x", type="int", offset_bits=0),
            TypeField(name="y", type="int", offset_bits=32),
        ],
    )
    point_v2 = RecordType(
        name="Point", kind="struct", size_bits=96,
        fields=[
            TypeField(name="x", type="int", offset_bits=0),
            TypeField(name="y", type="int", offset_bits=32),
            TypeField(name="z", type="int", offset_bits=64),
        ],
    )
    draw_point = Function(
        name="draw_point", mangled="_Z10draw_point5Point",
        return_type="void", params=[Param(name="p", type="Point")],
        visibility=Visibility.PUBLIC,
    )
    old_snap = AbiSnapshot(
        library="libfoo.so", version="1.0",
        functions=[draw_point], types=[point_v1], from_headers=True,
    )
    new_snap = AbiSnapshot(
        library="libfoo.so", version="2.0",
        functions=[draw_point], types=[point_v2], from_headers=True,
    )
    _write_snap(old_dir / "libfoo.json", old_snap)
    _write_snap(new_dir / "libfoo.json", new_snap)
    return old_dir, new_dir


def _write_removed_function_pair_multi(
    tmp_path: Path, names: tuple[str, ...]
) -> tuple[Path, Path]:
    """Several libraries, each with its own removed public function --
    exercises the parallel (``jobs=0`` default, > 1 matched library) release
    fan-out, not just the single-library-falls-back-to-sequential path."""
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()
    for name in names:
        old_snap = AbiSnapshot(
            library=f"{name}.so",
            version="1.0",
            functions=[_fn("api_b", _MANGLED)],
            from_headers=True,
        )
        new_snap = AbiSnapshot(
            library=f"{name}.so", version="2.0", functions=[], from_headers=True
        )
        _write_snap(old_dir / f"{name}.json", old_snap)
        _write_snap(new_dir / f"{name}.json", new_snap)
    return old_dir, new_dir


def _invoke(*args: str):
    return CliRunner().invoke(main, list(args))


def _write_matrix_pair(tmp_path: Path, old_std: int, new_std: int) -> tuple[Path, Path]:
    """A release-global (not per-library) probe-matrix pair whose only
    finding is a ``CXX_STANDARD_FLOOR_RAISED`` (default verdict API_BREAK,
    show-only severity label ``api-break``) -- release-global bundle/matrix
    findings have no per-library home, which is what makes them a distinct
    axis from the per-library findings the other test classes cover."""
    old_matrix = MatrixSnapshot(
        library="libfoo.so", version="1.0", spec_name="std-probe",
        cxx_stds={"cfg": old_std},
    )
    new_matrix = MatrixSnapshot(
        library="libfoo.so", version="2.0", spec_name="std-probe",
        cxx_stds={"cfg": new_std},
    )
    old_path = tmp_path / "matrix_old.json"
    new_path = tmp_path / "matrix_new.json"
    old_path.write_text(matrix_snapshot_to_json(old_matrix), encoding="utf-8")
    new_path.write_text(matrix_snapshot_to_json(new_matrix), encoding="utf-8")
    return old_path, new_path


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
            "compare", str(old_dir), str(new_dir),
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
            "compare", str(old_dir), str(new_dir),
            "--view", "show=functions",
        )
        assert result.exit_code == 4, result.output
        assert "## Per-Library Findings" in result.output
        assert _DEMANGLED in result.output or _MANGLED in result.output

    def test_show_only_filters_release_json_findings(self, tmp_path: Path) -> None:
        old_dir, new_dir = _write_removed_function_pair(tmp_path)

        result = _invoke(
            "compare", str(old_dir), str(new_dir),
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

        baseline = _invoke("compare", str(old_dir), str(new_dir))
        filtered = _invoke(
            "compare", str(old_dir), str(new_dir),
            "--view", "show=variables",
        )
        assert baseline.exit_code == filtered.exit_code == 4

    def test_release_json_records_the_active_filter_and_counts(
        self, tmp_path: Path
    ) -> None:
        """Codex review, fresh evidence ("Record the active filter in
        release JSON"): the release JSON used to substitute the filtered
        library projection with no trace of the selector or the pre/post
        counts, leaving a filtered-to-empty ``findings`` list next to
        ``verdict: BREAKING`` indistinguishable from missing/truncated
        detail -- unlike scalar `compare` JSON, which has always carried
        `show_only_filter`/`filtered_summary` for exactly this reason.
        `release_filtered_summary` (not `filtered_summary` -- Codex review,
        fresh evidence, second round: "Preserve the scalar filtered_summary
        schema") is a deliberately separately-named/-shaped structure, since
        the release-level aggregate has no per-severity-bucket breakdown
        the way one scalar `DiffResult` does."""
        old_dir, new_dir = _write_removed_function_pair(tmp_path)

        result = _invoke(
            "compare", str(old_dir), str(new_dir),
            "--format", "json", "--view", "show=variables",
        )
        assert result.exit_code == 4, result.output
        doc = json.loads(result.output)
        assert doc["libraries"][0].get("findings", []) == []
        assert doc["show_only_filter"] == "variables"
        # Two real findings (the removed function plus the resulting
        # public-surface-shrank note) existed before the filter; neither
        # is a variable-element finding, so none survive it.
        assert doc["release_filtered_summary"] == {"displayed": 0, "total": 2}
        # The scalar schema's own field name/shape is untouched -- no
        # `filtered_summary` key is added to the release document at all,
        # so a consumer expecting that name/shape off a release report
        # gets neither a wrong shape nor a silent collision.
        assert "filtered_summary" not in doc

    def test_release_json_omits_the_filter_fields_without_view_show(
        self, tmp_path: Path
    ) -> None:
        """The identical comparison with no ``--view show=`` never adds
        either key -- both are opt-in, matching scalar `compare` JSON's own
        `show_only_filter` contract."""
        old_dir, new_dir = _write_removed_function_pair(tmp_path)

        result = _invoke(
            "compare", str(old_dir), str(new_dir), "--format", "json",
        )
        assert result.exit_code == 4, result.output
        doc = json.loads(result.output)
        assert "show_only_filter" not in doc
        assert "release_filtered_summary" not in doc

    def test_release_json_filtered_summary_counts_bundle_and_matrix_too(
        self, tmp_path: Path
    ) -> None:
        """A ``--view show=...`` selection that keeps the per-library
        finding (a real BREAKING function removal, severity ``breaking``)
        but drops both the library's own compatible
        ``public_surface_shrank`` note and the release-global matrix
        finding (``API_BREAK`` severity) is reflected in the aggregate
        counts: 3 total (2 per-library + 1 matrix-global), 1 displayed
        (only the breaking function removal matches ``show=breaking``)."""
        old_dir, new_dir = _write_removed_function_pair(tmp_path)
        matrix_old, matrix_new = _write_matrix_pair(tmp_path, old_std=17, new_std=20)

        result = _invoke(
            "compare", str(old_dir), str(new_dir),
            "--probe-matrix", f"old={matrix_old}",
            "--probe-matrix", f"new={matrix_new}",
            "--format", "json", "--view", "show=breaking",
        )
        assert result.exit_code == 4, result.output
        doc = json.loads(result.output)
        assert doc["show_only_filter"] == "breaking"
        assert doc["matrix_findings"] == []
        assert doc["libraries"][0]["findings"] != []
        assert doc["release_filtered_summary"] == {"displayed": 1, "total": 3}

    def test_release_json_filtered_summary_counts_findings_past_the_display_cap(
        self, tmp_path: Path
    ) -> None:
        """Codex review, fresh evidence, third round ("Count uncapped
        findings in release filter totals"): a library with more than
        `_MAX_RELEASE_FINDINGS_PER_LIBRARY` (10) real findings has its
        `findings` display list capped at 10, but `release_filtered_
        summary`'s `total` must still report the true, uncapped count --
        summing the already-capped display list under-reports past the
        cap (25 real findings would read as 10)."""
        old_dir, new_dir = _write_removed_functions_pair(tmp_path, count=25)

        result = _invoke(
            "compare", str(old_dir), str(new_dir),
            "--format", "json", "--view", "show=functions",
        )
        assert result.exit_code == 4, result.output
        doc = json.loads(result.output)
        lib = doc["libraries"][0]
        assert lib.get("findings_truncated") is True
        assert len(lib["findings"]) <= 10
        # 25 real removed-function findings (plus 1 compatible
        # public-surface-shrank note, element `surface` -- not `functions`,
        # so it doesn't match `show=functions`) existed before the filter;
        # `displayed` (25) and `total` (26) both reflect the true, uncapped
        # pool -- neither is capped at the per-library display limit the
        # raw `findings` list itself is capped to.
        assert doc["release_filtered_summary"] == {"displayed": 25, "total": 26}

    def test_output_dir_summary_json_never_leaks_the_internal_accounting_keys(
        self, tmp_path: Path
    ) -> None:
        """Codex review, fresh evidence (discovered while fixing the
        adjacent "uncapped release filter totals" finding): the
        `--output-dir` `summary.json` sidecar serializes `library_results`
        directly, unlike the primary `--format` render, which always routes
        through `_release_findings_for_render`'s private-key stripping --
        so the internal `findings_view`/`findings_total_count`/
        `findings_total_count_view`/`impact_table_view` accounting keys
        `_strip_diff_results_and_adjust_verdict` stashes on each entry used
        to leak straight into this sidecar's per-library JSON."""
        old_dir, new_dir = _write_removed_function_pair(tmp_path)
        out_dir = tmp_path / "out"

        result = _invoke(
            "compare", str(old_dir), str(new_dir),
            "--view", "show=functions", "--view", "impact",
            "--output-dir", str(out_dir),
        )
        assert result.exit_code == 4, result.output
        summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
        lib_keys = set(summary["libraries"][0].keys())
        leaked = lib_keys & {
            "findings_view",
            "findings_view_truncated",
            "findings_total_count",
            "findings_total_count_view",
            "impact_table_view",
        }
        assert not leaked, lib_keys


class TestReleaseViewDemangle:
    """``--view demangle``/``--view no-demangle`` on a directory/package input."""

    def test_demangled_by_default_in_release_markdown(self, tmp_path: Path) -> None:
        old_dir, new_dir = _write_removed_function_pair(tmp_path)

        result = _invoke("compare", str(old_dir), str(new_dir))
        assert result.exit_code == 4, result.output
        assert _DEMANGLED in result.output
        assert _MANGLED not in result.output

    def test_no_demangle_keeps_mangled_name_in_release_markdown(
        self, tmp_path: Path
    ) -> None:
        old_dir, new_dir = _write_removed_function_pair(tmp_path)

        result = _invoke(
            "compare", str(old_dir), str(new_dir),
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
            "compare", str(old_dir), str(new_dir),
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

        baseline = _invoke("compare", str(old_dir), str(new_dir))
        with_patterns = _invoke(
            "compare", str(old_dir), str(new_dir),
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


class TestReleaseViewPatternsMultiLibraryOrdering:
    """Codex review, fresh evidence ("Serialize pattern-ledger output after
    parallel comparison"): with multiple matched libraries, the default
    parallel (``jobs=0``) fan-out used to echo each library's pattern
    ledger directly from inside its own ``ThreadPoolExecutor`` worker
    thread -- several independent ``click.echo`` writes per library, free
    to interleave nondeterministically with a sibling library's under real
    thread scheduling. Proves each library's ledger block is now complete
    and appears in ``matched_keys`` order, not interleaved."""

    def test_pattern_ledger_blocks_appear_complete_and_in_order(
        self, tmp_path: Path
    ) -> None:
        names = ("liba", "libb", "libc", "libd")
        old_dir, new_dir = _write_removed_function_pair_multi(tmp_path, names)

        result = _invoke(
            "compare", str(old_dir), str(new_dir),
            "--view", "patterns",
        )
        assert result.exit_code == 4, result.output

        # Each library's own "== <name>.json ==" header (old_path.name --
        # these are stored snapshot pairs, not real .so binaries) appears
        # exactly once, and in matched_keys (alphabetical) order -- proves
        # the echo was collected and replayed by the single-threaded
        # post-processing loop, not interleaved by several worker threads
        # racing to write.
        headers = [f"== {name}.json ==" for name in names]
        positions = [result.output.index(h) for h in headers]
        assert positions == sorted(positions), result.output
        for header in headers:
            assert result.output.count(header) == 1, result.output

        # Each header is immediately followed by its own complete
        # "No pattern-aware modulations applied." line before the next
        # library's header starts -- an interleaved write would instead
        # show one library's header followed by a foreign line.
        for i, header in enumerate(headers):
            start = result.output.index(header)
            end = (
                result.output.index(headers[i + 1])
                if i + 1 < len(headers)
                else len(result.output)
            )
            block = result.output[start:end]
            assert "No pattern-aware modulations applied." in block, block


class TestReleaseViewReportModeRejected:
    """``--view leaf``/``--view root-cause`` restructure a single
    `DiffResult`'s own root-cause graph -- the release summary has no such
    graph, so both are rejected with a clear usage error instead of being
    silently ignored."""

    def test_leaf_is_rejected_for_a_directory_operand(self, tmp_path: Path) -> None:
        old_dir, new_dir = _write_removed_function_pair(tmp_path)

        result = _invoke(
            "compare", str(old_dir), str(new_dir),
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
            "compare", str(old_dir), str(new_dir),
            "--view", "root-cause",
        )
        assert result.exit_code == 64, result.output
        assert "--view root-cause is not available" in result.output

    def test_full_and_impact_are_accepted_for_a_directory_operand(
        self, tmp_path: Path
    ) -> None:
        """'full' (the default) and 'impact' (a real per-library aggregate,
        Codex review PR #1154 second follow-up: "Reject unsupported impact
        views instead of silently dropping them") must not be rejected --
        only leaf/root-cause restructure the document shape."""
        old_dir, new_dir = _write_removed_function_pair(tmp_path)

        for token in ("full", "impact"):
            result = _invoke(
                "compare", str(old_dir), str(new_dir),
                "--view", token,
            )
            assert result.exit_code == 4, (token, result.output)


class TestReleaseViewReportModeRejectedUnderDryRun:
    """Codex review, fresh evidence ("Validate release-only view
    restrictions before dry-run exit"): the rejection above lives inside
    ``_dispatch_release_compare``, which ``--dry-run`` never reaches
    (``emit_dry_run`` raises ``SystemExit`` before dispatch runs) -- so
    ``compare --dry-run --view leaf`` on a directory/package operand used
    to exit 0 (the dry-run report rendered "ok") while the identical
    non-dry-run invocation exits 64. A dry run must never validate an
    invocation the real run would then reject."""

    def test_leaf_is_rejected_under_dry_run_for_a_directory_operand(
        self, tmp_path: Path
    ) -> None:
        old_dir, new_dir = _write_removed_function_pair(tmp_path)

        result = _invoke(
            "compare", str(old_dir), str(new_dir),
            "--dry-run", "--view", "leaf",
        )
        assert result.exit_code == 64, result.output
        assert "--view leaf is not available" in result.output

    def test_root_cause_is_rejected_under_dry_run_for_a_directory_operand(
        self, tmp_path: Path
    ) -> None:
        old_dir, new_dir = _write_removed_function_pair(tmp_path)

        result = _invoke(
            "compare", str(old_dir), str(new_dir),
            "--dry-run", "--view", "root-cause",
        )
        assert result.exit_code == 64, result.output
        assert "--view root-cause is not available" in result.output

    def test_full_and_impact_still_succeed_under_dry_run(
        self, tmp_path: Path
    ) -> None:
        """Companion: the fix must not reject the two view modes a
        directory/package release fan-out genuinely supports."""
        old_dir, new_dir = _write_removed_function_pair(tmp_path)

        for token in ("full", "impact"):
            result = _invoke(
                "compare", str(old_dir), str(new_dir),
                "--dry-run", "--view", token,
            )
            assert result.exit_code == 0, (token, result.output)


class TestReleaseViewImpactAggregate:
    """``--view impact`` on a directory/package operand (Codex review, PR
    #1154 second follow-up): unlike ``leaf``/``root-cause`` -- which
    restructure a single ``DiffResult``'s own root-cause graph, something a
    multi-library release report has no equivalent of -- an impact summary
    is naturally per-library (each library already has its own
    ``DiffResult``), so it is threaded through as a real per-library
    aggregate rather than silently dropped (the pre-fix behaviour) or
    rejected as a usage error."""

    def test_json_embeds_a_per_library_impact_table(self, tmp_path: Path) -> None:
        old_dir, new_dir = _write_struct_size_change_pair(tmp_path)

        result = _invoke(
            "compare", str(old_dir), str(new_dir),
            "--format", "json", "--view", "impact",
        )
        assert result.exit_code == 4, result.output
        data = json.loads(result.output)
        lib = data["libraries"][0]
        impact = lib["impact_table"]
        assert impact["root_entries"], impact
        entry = impact["root_entries"][0]
        assert entry["symbol"] == "Point"
        assert entry["kind"] == "type_size_changed"
        assert entry["iface_count"] >= 1

    def test_json_omits_impact_table_without_view_impact(self, tmp_path: Path) -> None:
        """The identical comparison with no ``--view impact`` never adds the
        key at all -- the flag is opt-in, matching single-pair `compare`'s
        own ``show_impact`` default."""
        old_dir, new_dir = _write_struct_size_change_pair(tmp_path)

        result = _invoke(
            "compare", str(old_dir), str(new_dir), "--format", "json",
        )
        assert result.exit_code == 4, result.output
        data = json.loads(result.output)
        lib = data["libraries"][0]
        assert "impact_table" not in lib

    def test_markdown_renders_the_impact_section(self, tmp_path: Path) -> None:
        old_dir, new_dir = _write_struct_size_change_pair(tmp_path)

        result = _invoke(
            "compare", str(old_dir), str(new_dir), "--view", "impact",
        )
        assert result.exit_code == 4, result.output
        assert "**Impact**" in result.output
        assert "Point" in result.output

    def test_impact_table_respects_show_only_and_write_stays_full(
        self, tmp_path: Path
    ) -> None:
        """Combining ``--view impact`` with ``--view show=...`` filters the
        primary render's impact table the same way it filters ``findings``,
        while a secondary ``--write`` stays the full, unfiltered table --
        mirroring the existing findings/findings_view contract exactly."""
        old_dir, new_dir = _write_struct_size_change_pair(tmp_path)
        write_path = tmp_path / "full.json"

        result = _invoke(
            "compare", str(old_dir), str(new_dir),
            "--format", "json",
            "--view", "impact",
            "--view", "show=variables",
            "--write", f"json={write_path}",
        )
        assert result.exit_code == 4, result.output
        assert result.output.startswith("Report written to")
        _, _, primary_json_text = result.output.partition("\n")
        primary = json.loads(primary_json_text)
        primary_lib = primary["libraries"][0]
        # "show=variables" matches no variable-element finding (the root
        # type change is a types-element finding), so the filtered primary
        # render carries no impact table for this library.
        assert "impact_table" not in primary_lib

        secondary = json.loads(write_path.read_text(encoding="utf-8"))
        secondary_lib = secondary["libraries"][0]
        assert secondary_lib["impact_table"]["root_entries"]


class TestReleaseViewImpactJUnitParity:
    """``--format junit --view impact`` on a directory/package operand
    (Codex review, PR #1154 third follow-up): a release JUnit render never
    receives ``show_impact`` at all, unlike the JSON/Markdown renders proven
    above. This is not a release-only gap -- single-pair ``compare --format
    junit --view impact`` already renders ordinary JUnit XML with no impact
    representation, silently, since ``service_render.render_output``'s own
    ``fmt == "junit"`` branch never forwards ``show_impact`` either. This
    class pins that parity (not a bug fix, a regression guard): the release
    JUnit render must keep behaving exactly like the single-pair one for the
    identical flag combination -- it must not start rejecting the
    combination (which would make the release path *more* restrictive than
    single-pair `compare` for the same flags) and it must not start
    injecting some release-only impact representation into JUnit XML that
    single-pair `compare` has no equivalent of."""

    def test_release_junit_with_view_impact_matches_release_junit_without_it(
        self, tmp_path: Path
    ) -> None:
        old_dir, new_dir = _write_struct_size_change_pair(tmp_path)

        with_impact = _invoke(
            "compare", str(old_dir), str(new_dir),
            "--format", "junit", "--view", "impact",
        )
        without_impact = _invoke(
            "compare", str(old_dir), str(new_dir), "--format", "junit",
        )
        assert with_impact.exit_code == 4, with_impact.output
        assert without_impact.exit_code == 4, without_impact.output
        assert with_impact.output == without_impact.output

    def test_release_junit_with_view_impact_carries_no_impact_markup(
        self, tmp_path: Path
    ) -> None:
        old_dir, new_dir = _write_struct_size_change_pair(tmp_path)

        result = _invoke(
            "compare", str(old_dir), str(new_dir),
            "--format", "junit", "--view", "impact",
        )
        assert result.exit_code == 4, result.output
        assert "<?xml" in result.output
        assert "impact" not in result.output.lower()

    def test_single_pair_junit_with_view_impact_also_carries_no_impact_markup(
        self, tmp_path: Path
    ) -> None:
        """Same struct-size-change library, compared as a single old/new
        ``.json`` pair rather than a directory -- confirms the release
        fan-out's silent no-op (asserted above) matches the single-pair
        path it exists to mirror (ADR-037 D1/D7), not just a release-side
        coincidence. Doesn't assert byte-identical output: a release render
        also carries its own ``comparison_scope`` testsuite the single-pair
        path never emits, which is an orthogonal, already-covered
        difference, not part of what this test pins."""
        old_dir, new_dir = _write_struct_size_change_pair(tmp_path)
        old_snap_path = old_dir / "libfoo.json"
        new_snap_path = new_dir / "libfoo.json"

        single_pair = _invoke(
            "compare", str(old_snap_path), str(new_snap_path),
            "--format", "junit", "--view", "impact",
        )
        assert single_pair.exit_code == 4, single_pair.output
        assert "<?xml" in single_pair.output
        assert "impact" not in single_pair.output.lower()


class TestReleaseViewShowOnlySecondaryWriteStaysFull:
    """Codex review, PR #1154 second follow-up ("Apply release show filters
    inside each renderer"): a secondary ``--write`` report is documented/
    contracted to always be full and unfiltered -- the *previous* fix
    filtered the shared ``library_results`` projection once, upstream of
    both the primary ``--format`` render and a secondary ``--write`` render,
    so ``--write`` incorrectly inherited the primary's own ``--view show=``
    selection. This proves ``--write`` stays full even when the primary
    render is filtered down to nothing."""

    def test_write_json_is_full_while_primary_markdown_is_filtered(
        self, tmp_path: Path
    ) -> None:
        old_dir, new_dir = _write_removed_function_pair(tmp_path)
        write_path = tmp_path / "secondary.json"

        result = _invoke(
            "compare", str(old_dir), str(new_dir),
            "--view", "show=variables",
            "--write", f"json={write_path}",
        )
        assert result.exit_code == 4, result.output

        # Primary (markdown, the default format) is filtered: the function
        # finding is a "functions"-element kind, and `show=variables` keeps
        # only variable-element kinds.
        assert "## Per-Library Findings" not in result.output
        assert "api_b" not in result.output

        # Secondary --write is full/unfiltered: the same function finding
        # a `--view show=variables` filter removed from the primary render
        # must still be present here. Two findings, not one, since Codex
        # review (PR #1154 follow-up: "Filter the complete release finding
        # set") widened the full/unfiltered pool to every category
        # (including the unconditional surface-metrics compatible finding),
        # not only the legacy breaking/api_break/risk buckets.
        secondary_doc = json.loads(write_path.read_text(encoding="utf-8"))
        lib_entries = secondary_doc["libraries"]
        assert len(lib_entries) == 1
        findings = lib_entries[0].get("findings", [])
        assert {f["kind"] for f in findings} == {"func_removed", "public_surface_shrank"}
        assert len(findings) == 2

    def test_write_json_is_full_even_when_primary_is_also_json(
        self, tmp_path: Path
    ) -> None:
        """The same invariant holds when the *primary* format is JSON too --
        the private ``findings_view`` transport key the primary render
        consumes must never leak into either document, and a secondary
        ``--write`` must never see it either."""
        old_dir, new_dir = _write_removed_function_pair(tmp_path)
        write_path = tmp_path / "secondary.json"

        result = _invoke(
            "compare", str(old_dir), str(new_dir),
            "--format", "json",
            "--view", "show=variables",
            "--write", f"markdown={write_path}",
        )
        assert result.exit_code == 4, result.output

        # A secondary --write to a *file* (unlike stdout) prints a "Report
        # written to ..." notice ahead of the primary JSON on stdout.
        primary_json_text = result.output[result.output.index("{") :]
        primary_doc = json.loads(primary_json_text)
        assert primary_doc["libraries"][0].get("findings", []) == []
        assert "findings_view" not in primary_doc["libraries"][0]

        secondary_text = write_path.read_text(encoding="utf-8")
        assert "api_b" in secondary_text
        assert "findings_view" not in secondary_text


class TestReleaseViewShowOnlyJUnit:
    """Codex review, PR #1154 second follow-up: JUnit was the one primary
    format that silently ignored the release's own ``--view show=``
    selection entirely -- it read the un-filtered per-library ``DiffResult``
    (``diff_pairs``), never the ``show_only``-filtered projection JSON/
    Markdown already used."""

    def test_junit_respects_show_only_when_it_filters_out_the_finding(
        self, tmp_path: Path
    ) -> None:
        old_dir, new_dir = _write_removed_function_pair(tmp_path)

        baseline = _invoke("compare", str(old_dir), str(new_dir), "--format", "junit")
        assert baseline.exit_code == 4, baseline.output
        assert 'failures="1"' in baseline.output

        filtered = _invoke(
            "compare", str(old_dir), str(new_dir),
            "--format", "junit", "--view", "show=variables",
        )
        # show_only is presentation-only -- the exit code (computed from the
        # real, unfiltered DiffResult) is unaffected even though the JUnit
        # report itself shows no failing testcase.
        assert filtered.exit_code == 4, filtered.output
        assert 'failures="0"' in filtered.output
        assert "api_b" not in filtered.output

    def test_junit_keeps_the_finding_when_show_only_matches(
        self, tmp_path: Path
    ) -> None:
        old_dir, new_dir = _write_removed_function_pair(tmp_path)

        result = _invoke(
            "compare", str(old_dir), str(new_dir),
            "--format", "junit", "--view", "show=functions",
        )
        assert result.exit_code == 4, result.output
        assert 'failures="1"' in result.output


class TestReleaseViewShowOnlyReleaseGlobalFindings:
    """Codex review, PR #1154 second follow-up: release-global bundle/matrix
    findings (not tied to any one library) previously bypassed ``--view
    show=`` entirely in every format. This exercises the matrix axis --
    ``CXX_STANDARD_FLOOR_RAISED``'s default verdict is API_BREAK, so
    ``show=api-break`` keeps it and ``show=breaking`` filters it out, in
    both JSON and Markdown."""

    def test_matrix_finding_kept_when_show_only_matches_its_severity(
        self, tmp_path: Path
    ) -> None:
        old_dir, new_dir = _write_removed_function_pair(tmp_path)
        matrix_old, matrix_new = _write_matrix_pair(tmp_path, old_std=17, new_std=20)

        result = _invoke(
            "compare", str(old_dir), str(new_dir),
            "--probe-matrix", f"old={matrix_old}",
            "--probe-matrix", f"new={matrix_new}",
            "--format", "json", "--view", "show=api-break",
        )
        assert result.exit_code == 4, result.output
        doc = json.loads(result.output)
        assert len(doc["matrix_findings"]) == 1
        assert doc["matrix_findings"][0]["kind"] == "cxx_standard_floor_raised"
        # The real matrix verdict is unaffected by the display filter.
        assert doc["matrix_verdict"] == "API_BREAK"

    def test_matrix_finding_filtered_in_json_and_markdown_when_show_only_excludes_it(
        self, tmp_path: Path
    ) -> None:
        old_dir, new_dir = _write_removed_function_pair(tmp_path)
        matrix_old, matrix_new = _write_matrix_pair(tmp_path, old_std=17, new_std=20)

        json_result = _invoke(
            "compare", str(old_dir), str(new_dir),
            "--probe-matrix", f"old={matrix_old}",
            "--probe-matrix", f"new={matrix_new}",
            "--format", "json", "--view", "show=breaking",
        )
        assert json_result.exit_code == 4, json_result.output
        doc = json.loads(json_result.output)
        assert doc["matrix_findings"] == []
        # The real matrix verdict (what actually gated the exit code) is
        # unaffected by the display filter -- "record before disposing".
        assert doc["matrix_verdict"] == "API_BREAK"

        md_result = _invoke(
            "compare", str(old_dir), str(new_dir),
            "--probe-matrix", f"old={matrix_old}",
            "--probe-matrix", f"new={matrix_new}",
            "--view", "show=breaking",
        )
        assert md_result.exit_code == 4, md_result.output
        assert "Build-Configuration (Matrix) Findings" not in md_result.output
        assert "cxx_standard_floor_raised" not in md_result.output

    def test_matrix_finding_unfiltered_by_default(self, tmp_path: Path) -> None:
        old_dir, new_dir = _write_removed_function_pair(tmp_path)
        matrix_old, matrix_new = _write_matrix_pair(tmp_path, old_std=17, new_std=20)

        result = _invoke(
            "compare", str(old_dir), str(new_dir),
            "--probe-matrix", f"old={matrix_old}",
            "--probe-matrix", f"new={matrix_new}",
            "--format", "json",
        )
        assert result.exit_code == 4, result.output
        doc = json.loads(result.output)
        assert len(doc["matrix_findings"]) == 1


class TestReleaseViewShowOnlyOutputDirStaysFull:
    """Codex review, fresh evidence (PR #1154 follow-up: "Keep per-library
    output-dir reports unfiltered"). ``_release_md_library_findings``
    directs a reader to ``--output-dir`` as the one uncapped, *complete*
    per-library source when the aggregate report's own findings list was
    truncated -- the same "always full" contract a secondary ``--write``
    already gets (:class:`TestReleaseViewShowOnlySecondaryWriteStaysFull`
    above). ``--view show=...`` used to be forwarded into that per-library
    JSON write too, so a filter that excluded the very finding a reader was
    told to go find there made it disappear from the "complete" file as
    well."""

    def test_output_dir_json_is_full_while_primary_markdown_is_filtered(
        self, tmp_path: Path
    ) -> None:
        old_dir, new_dir = _write_removed_function_pair(tmp_path)
        output_dir = tmp_path / "out"

        result = _invoke(
            "compare", str(old_dir), str(new_dir),
            "--view", "show=variables",
            "--output-dir", str(output_dir),
        )
        assert result.exit_code == 4, result.output

        # Primary (markdown, the default format) is filtered: the function
        # finding is a "functions"-element kind, and `show=variables` keeps
        # only variable-element kinds.
        assert "## Per-Library Findings" not in result.output
        assert "api_b" not in result.output

        # --output-dir's own per-library JSON is a real single-pair `compare`
        # report (its own `changes` array, not the release summary's capped
        # `findings` dicts) -- full/unfiltered: the same function finding a
        # `--view show=variables` filter removed from the primary render
        # must still be present here.
        lib_report = json.loads((output_dir / "libfoo.json").read_text(encoding="utf-8"))
        kinds = {c["kind"] for c in lib_report["changes"]}
        assert {"func_removed", "public_surface_shrank"} <= kinds
