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

"""Unit tests for ``scripts/classify_perf_paths.py``.

Pure-stdlib and fast: pins the classification contract the Performance
workflow's ``classify`` job relies on to decide whether its downstream jobs
run at all.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parent.parent / "scripts" / "classify_perf_paths.py"
_spec = importlib.util.spec_from_file_location("classify_perf_paths", _PATH)
assert _spec and _spec.loader
classify = importlib.util.module_from_spec(_spec)
sys.modules["classify_perf_paths"] = classify
_spec.loader.exec_module(classify)


class TestChangedFilesArePerfSensitive:
    def test_empty_changeset_is_not_sensitive(self) -> None:
        assert classify.changed_files_are_perf_sensitive([]) is False

    def test_unrelated_file_is_not_sensitive(self) -> None:
        assert (
            classify.changed_files_are_perf_sensitive(
                ["docs/learn/quickstart.md", "README.md"]
            )
            is False
        )

    @pytest.mark.parametrize(
        "path",
        [
            "abicheck/checker.py",
            "abicheck/diff_symbols.py",  # abicheck/diff_*.py glob
            "abicheck/diff_types.py",
            "scripts/benchmark_scaling.py",
            "scripts/perf_measurement.py",
            "tests/test_benchmark_scaling.py",
            ".github/workflows/performance.yml",
        ],
    )
    def test_exact_and_glob_listed_paths_are_sensitive(self, path: str) -> None:
        assert classify.changed_files_are_perf_sensitive([path]) is True

    def test_buildsource_directory_glob_matches_any_depth(self) -> None:
        # abicheck/buildsource/** must match nested paths, not just direct
        # children -- Python's fnmatch doesn't special-case "/", so a single
        # "*" (and therefore "**") already matches across path separators.
        assert classify.changed_files_are_perf_sensitive(
            ["abicheck/buildsource/header_graph.py"]
        )
        assert classify.changed_files_are_perf_sensitive(
            ["abicheck/buildsource/adapters/bazel.py"]
        )

    def test_one_sensitive_file_among_many_unrelated_is_enough(self) -> None:
        assert classify.changed_files_are_perf_sensitive(
            ["README.md", "docs/x.md", "abicheck/checker.py", "CHANGELOG.md"]
        )

    def test_sibling_similarly_named_file_does_not_false_positive(self) -> None:
        # abicheck/diff_*.py should not match a file merely containing
        # "diff" in an unrelated position, nor a file in a different dir.
        assert not classify.changed_files_are_perf_sensitive(
            ["abicheck/mydiff_helpers.py"]
        )
        assert not classify.changed_files_are_perf_sensitive(
            ["tests/test_diff_symbols.py"]
        )


class TestMatchedPatterns:
    def test_maps_each_sensitive_file_to_its_pattern(self) -> None:
        hits = classify.matched_patterns(["abicheck/checker.py", "README.md"])
        assert hits == {"abicheck/checker.py": "abicheck/checker.py"}

    def test_glob_pattern_reported_for_glob_match(self) -> None:
        hits = classify.matched_patterns(["abicheck/diff_symbols.py"])
        assert hits == {"abicheck/diff_symbols.py": "abicheck/diff_*.py"}

    def test_no_matches_is_empty(self) -> None:
        assert classify.matched_patterns(["README.md"]) == {}


class TestMain:
    def _run(
        self,
        args: list[str],
        *,
        stdin: str = "",
        env: dict[str, str] | None = None,
        capsys,
        monkeypatch,
    ) -> tuple[int, str]:
        import io

        monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
        for k, v in (env or {}).items():
            monkeypatch.setenv(k, v)
        rc = classify.main(args)
        out = capsys.readouterr()
        return rc, out.out

    def test_stdin_with_sensitive_path_writes_run_true(
        self, capsys, monkeypatch, tmp_path
    ) -> None:
        gh_output = tmp_path / "gh_output.txt"
        rc, _ = self._run(
            [],
            stdin="abicheck/checker.py\n",
            env={"GITHUB_OUTPUT": str(gh_output)},
            capsys=capsys,
            monkeypatch=monkeypatch,
        )
        assert rc == 0
        assert gh_output.read_text() == "run=true\n"

    def test_stdin_with_only_unrelated_paths_writes_run_false(
        self, capsys, monkeypatch, tmp_path
    ) -> None:
        gh_output = tmp_path / "gh_output.txt"
        rc, _ = self._run(
            [],
            stdin="README.md\ndocs/x.md\n",
            env={"GITHUB_OUTPUT": str(gh_output)},
            capsys=capsys,
            monkeypatch=monkeypatch,
        )
        assert rc == 0
        assert gh_output.read_text() == "run=false\n"

    def test_always_flag_ignores_stdin(self, capsys, monkeypatch, tmp_path) -> None:
        gh_output = tmp_path / "gh_output.txt"
        rc, _ = self._run(
            ["--always"],
            stdin="README.md\n",
            env={"GITHUB_OUTPUT": str(gh_output)},
            capsys=capsys,
            monkeypatch=monkeypatch,
        )
        assert rc == 0
        assert gh_output.read_text() == "run=true\n"

    def test_force_label_matching_event_label_forces_run_true(
        self, capsys, monkeypatch, tmp_path
    ) -> None:
        # The real bug this closes: the workflow's own comment claimed
        # adding a "performance" label re-triggers the lane, but nothing
        # ever checked the label name -- a "performance" label on a PR
        # touching no listed path silently did not force a run.
        gh_output = tmp_path / "gh_output.txt"
        rc, _ = self._run(
            ["--force-label", "performance", "--event-label", "performance"],
            stdin="README.md\n",
            env={"GITHUB_OUTPUT": str(gh_output)},
            capsys=capsys,
            monkeypatch=monkeypatch,
        )
        assert rc == 0
        assert gh_output.read_text() == "run=true\n"

    def test_force_label_not_matching_event_label_falls_through_to_classification(
        self, capsys, monkeypatch, tmp_path
    ) -> None:
        gh_output = tmp_path / "gh_output.txt"
        rc, _ = self._run(
            ["--force-label", "performance", "--event-label", "bug"],
            stdin="README.md\n",
            env={"GITHUB_OUTPUT": str(gh_output)},
            capsys=capsys,
            monkeypatch=monkeypatch,
        )
        assert rc == 0
        assert gh_output.read_text() == "run=false\n"

    def test_files_from_file_is_read_instead_of_stdin(
        self, capsys, monkeypatch, tmp_path
    ) -> None:
        files_file = tmp_path / "changed.txt"
        files_file.write_text("abicheck/checker.py\n")
        gh_output = tmp_path / "gh_output.txt"
        rc, _ = self._run(
            ["--files-from", str(files_file)],
            stdin="README.md\n",  # must be ignored in favor of --files-from
            env={"GITHUB_OUTPUT": str(gh_output)},
            capsys=capsys,
            monkeypatch=monkeypatch,
        )
        assert rc == 0
        assert gh_output.read_text() == "run=true\n"

    def test_no_github_output_env_prints_to_stdout(self, capsys, monkeypatch) -> None:
        monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
        rc, out = self._run(
            ["--always"], stdin="", capsys=capsys, monkeypatch=monkeypatch
        )
        assert rc == 0
        assert "run=true" in out
