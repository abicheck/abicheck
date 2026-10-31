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

"""ADR-091 Phase 5 split of ``test_cli_compare_release_view.py``: the
directory/package ``--view`` impact aggregate, the JUnit parity of that view,
and the release-global findings and output-dir rendering under it. The
helpers are shared with the parent module, so the fixtures stay single-sourced.
"""

from __future__ import annotations

import json
from pathlib import Path

from test_cli_compare_release_view import (  # noqa: E402
    _invoke,
    _view_config,
    _write_matrix_pair,
    _write_removed_function_pair,
    _write_struct_size_change_pair,
)


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
            "compare",
            str(old_dir),
            str(new_dir),
            "-o",
            "json=-",
            "--config",
            _view_config(tmp_path, '["impact"]'),
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
            "compare",
            str(old_dir),
            str(new_dir),
            "-o",
            "json=-",
        )
        assert result.exit_code == 4, result.output
        data = json.loads(result.output)
        lib = data["libraries"][0]
        assert "impact_table" not in lib

    def test_markdown_renders_the_impact_section(self, tmp_path: Path) -> None:
        old_dir, new_dir = _write_struct_size_change_pair(tmp_path)

        result = _invoke(
            "compare",
            str(old_dir),
            str(new_dir),
            "--config",
            _view_config(tmp_path, '["impact"]'),
        )
        assert result.exit_code == 4, result.output
        assert "**Impact**" in result.output
        assert "Point" in result.output

    def test_impact_table_respects_show_only_in_every_export(
        self, tmp_path: Path
    ) -> None:
        """``--view impact`` plus ``--view show=...`` filters the impact
        table the way it filters ``findings`` -- and, since plan slice 7m,
        in every export rather than only the first (see
        ``TestReleaseViewShowOnlyAppliesToEveryExport``)."""
        old_dir, new_dir = _write_struct_size_change_pair(tmp_path)
        write_path = tmp_path / "full.json"

        result = _invoke(
            "compare",
            str(old_dir),
            str(new_dir),
            "-o",
            "json=-",
            "-o",
            f"json={write_path}",
            "--config",
            _view_config(tmp_path, '["impact", "show=variables"]'),
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
        assert "impact_table" not in secondary_lib
        # And the private view key never leaks into either rendered document.
        assert "impact_table_view" not in primary_lib
        assert "impact_table_view" not in secondary_lib


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
            "compare",
            str(old_dir),
            str(new_dir),
            "-o",
            "junit=-",
            "--config",
            _view_config(tmp_path, '["impact"]'),
        )
        without_impact = _invoke(
            "compare",
            str(old_dir),
            str(new_dir),
            "-o",
            "junit=-",
        )
        assert with_impact.exit_code == 4, with_impact.output
        assert without_impact.exit_code == 4, without_impact.output
        assert with_impact.output == without_impact.output

    def test_release_junit_with_view_impact_carries_no_impact_markup(
        self, tmp_path: Path
    ) -> None:
        old_dir, new_dir = _write_struct_size_change_pair(tmp_path)

        result = _invoke(
            "compare",
            str(old_dir),
            str(new_dir),
            "-o",
            "junit=-",
            "--config",
            _view_config(tmp_path, '["impact"]'),
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
            "compare",
            str(old_snap_path),
            str(new_snap_path),
            "-o",
            "junit=-",
            "--config",
            _view_config(tmp_path, '["impact"]'),
        )
        assert single_pair.exit_code == 4, single_pair.output
        assert "<?xml" in single_pair.output
        assert "impact" not in single_pair.output.lower()


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

        baseline = _invoke("compare", str(old_dir), str(new_dir), "-o", "junit=-")
        assert baseline.exit_code == 4, baseline.output
        assert 'failures="1"' in baseline.output

        filtered = _invoke(
            "compare",
            str(old_dir),
            str(new_dir),
            "-o",
            "junit=-",
            "--config",
            _view_config(tmp_path, '["show=variables"]'),
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
            "compare",
            str(old_dir),
            str(new_dir),
            "-o",
            "junit=-",
            "--config",
            _view_config(tmp_path, '["show=functions"]'),
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
            "compare",
            str(old_dir),
            str(new_dir),
            "--build-info",
            f"old={matrix_old}",
            "--build-info",
            f"new={matrix_new}",
            "-o",
            "json=-",
            "--config",
            _view_config(tmp_path, '["show=api-break"]'),
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
            "compare",
            str(old_dir),
            str(new_dir),
            "--build-info",
            f"old={matrix_old}",
            "--build-info",
            f"new={matrix_new}",
            "-o",
            "json=-",
            "--config",
            _view_config(tmp_path, '["show=breaking"]'),
        )
        assert json_result.exit_code == 4, json_result.output
        doc = json.loads(json_result.output)
        assert doc["matrix_findings"] == []
        # The real matrix verdict (what actually gated the exit code) is
        # unaffected by the display filter -- "record before disposing".
        assert doc["matrix_verdict"] == "API_BREAK"

        md_result = _invoke(
            "compare",
            str(old_dir),
            str(new_dir),
            "--build-info",
            f"old={matrix_old}",
            "--build-info",
            f"new={matrix_new}",
            "--config",
            _view_config(tmp_path, '["show=breaking"]'),
        )
        assert md_result.exit_code == 4, md_result.output
        assert "Build-Configuration (Matrix) Findings" not in md_result.output
        assert "cxx_standard_floor_raised" not in md_result.output

    def test_matrix_finding_unfiltered_by_default(self, tmp_path: Path) -> None:
        old_dir, new_dir = _write_removed_function_pair(tmp_path)
        matrix_old, matrix_new = _write_matrix_pair(tmp_path, old_std=17, new_std=20)

        result = _invoke(
            "compare",
            str(old_dir),
            str(new_dir),
            "--build-info",
            f"old={matrix_old}",
            "--build-info",
            f"new={matrix_new}",
            "-o",
            "json=-",
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
            "compare",
            str(old_dir),
            str(new_dir),
            "--config",
            _view_config(tmp_path, '["show=variables"]'),
            "-o",
            f"json={output_dir}/",
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
        lib_report = json.loads(
            (output_dir / "libfoo.json").read_text(encoding="utf-8")
        )
        kinds = {c["kind"] for c in lib_report["changes"]}
        assert {"func_removed", "public_surface_shrank"} <= kinds
