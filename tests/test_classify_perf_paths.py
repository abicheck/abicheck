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
import re
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
            "abicheck/detectors.py",
            "abicheck/detector_registry.py",
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

    def test_force_label_in_current_labels_forces_run_true(
        self, capsys, monkeypatch, tmp_path
    ) -> None:
        # The real bug this closes: the workflow's own comment claimed
        # adding a "performance" label re-triggers the lane, but nothing
        # ever checked the label name -- a "performance" label on a PR
        # touching no listed path silently did not force a run.
        #
        # A `test_force_label_survives_a_later_push_with_no_event_label` with
        # a byte-identical body used to sit below this one, for the other
        # half of that finding: a synchronize push carries no
        # `github.event.label` (that field exists only on labeled/unlabeled
        # events) while `pull_request.labels` still lists it. That half is
        # not observable here at all -- this CLI has no event-label input,
        # so `--current-labels` alone IS the no-event-label case and the two
        # tests were one test. It is a *workflow-wiring* claim, and it is now
        # asserted as one by
        # `TestWorkflowWiring::test_current_labels_comes_from_pull_request_labels`
        # below, which nothing covered before.
        gh_output = tmp_path / "gh_output.txt"
        rc, _ = self._run(
            ["--force-label", "performance", "--current-labels", "performance"],
            stdin="README.md\n",
            env={"GITHUB_OUTPUT": str(gh_output)},
            capsys=capsys,
            monkeypatch=monkeypatch,
        )
        assert rc == 0
        assert gh_output.read_text() == "run=true\n"

    def test_force_label_among_several_current_labels_forces_run_true(
        self, capsys, monkeypatch, tmp_path
    ) -> None:
        # --current-labels carries the PR's whole current label set (e.g.
        # from github.event.pull_request.labels.*.name), comma-joined --
        # the force label may be any one of several, not the only one.
        gh_output = tmp_path / "gh_output.txt"
        rc, _ = self._run(
            [
                "--force-label",
                "performance",
                "--current-labels",
                "bug, performance ,needs-review",
            ],
            stdin="README.md\n",
            env={"GITHUB_OUTPUT": str(gh_output)},
            capsys=capsys,
            monkeypatch=monkeypatch,
        )
        assert rc == 0
        assert gh_output.read_text() == "run=true\n"

    def test_force_label_not_in_current_labels_falls_through_to_classification(
        self, capsys, monkeypatch, tmp_path
    ) -> None:
        gh_output = tmp_path / "gh_output.txt"
        rc, _ = self._run(
            ["--force-label", "performance", "--current-labels", "bug"],
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


class TestRenameDetectionInvocation:
    """Real-git regression guard for the workflow's own `git diff --no-renames
    --name-only` invocation (Codex review, fresh evidence): with rename
    detection on (git's default), `--name-only` prints only a renamed file's
    DESTINATION path, silently dropping the source -- a perf-sensitive file
    renamed to a non-matching destination would then be invisible to
    classification even though the change is exactly as perf-sensitive as an
    in-place edit. This exercises the real `git diff` command the workflow
    step runs, not just the pure Python classifier, since the bug was in the
    git invocation, not in `changed_files_are_perf_sensitive` itself.
    """

    def _git(self, cwd: Path, *args: str) -> str:
        import subprocess

        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout

    def _init_repo_with_rename(self, tmp_path: Path) -> tuple[Path, str, str]:
        """A repo whose single commit renames abicheck/checker.py to a
        non-sensitive destination, with enough of a content change to
        actually trigger git's rename heuristic (a byte-identical rename
        still reports 100% similarity and renames either way, but a real
        PR's rename is rarely byte-identical). Returns (repo, base_sha, head_sha).
        """
        repo = tmp_path / "repo"
        repo.mkdir()
        self._git(repo, "init", "-q")
        self._git(repo, "config", "user.email", "test@example.com")
        self._git(repo, "config", "user.name", "Test")
        (repo / "abicheck").mkdir()
        original = repo / "abicheck" / "checker.py"
        original.write_text("def f():\n    pass\n" * 5, encoding="utf-8")
        self._git(repo, "add", "-A")
        self._git(repo, "commit", "-q", "-m", "base")
        base_sha = self._git(repo, "rev-parse", "HEAD").strip()

        (repo / "abicheck" / "core").mkdir()
        renamed = repo / "abicheck" / "core" / "checker.py"
        original.rename(renamed)
        with renamed.open("a", encoding="utf-8") as f:
            f.write("# a small addition\n")
        self._git(repo, "add", "-A")
        self._git(repo, "commit", "-q", "-m", "rename")
        head_sha = self._git(repo, "rev-parse", "HEAD").strip()
        return repo, base_sha, head_sha

    def test_default_rename_detection_hides_the_source_path(
        self, tmp_path: Path
    ) -> None:
        # Confirms the bug this fix closes actually reproduces with real git
        # -- if this ever stops reproducing (a git behavior change), the
        # --no-renames fix below would need re-justifying.
        repo, base_sha, head_sha = self._init_repo_with_rename(tmp_path)
        out = self._git(repo, "diff", "--name-only", f"{base_sha}...{head_sha}")
        changed = [line for line in out.splitlines() if line]
        assert "abicheck/checker.py" not in changed

    def test_no_renames_reports_both_paths_and_classifies_sensitive(
        self, tmp_path: Path
    ) -> None:
        repo, base_sha, head_sha = self._init_repo_with_rename(tmp_path)
        out = self._git(
            repo, "diff", "--no-renames", "--name-only", f"{base_sha}...{head_sha}"
        )
        changed = [line for line in out.splitlines() if line]
        assert "abicheck/checker.py" in changed
        assert "abicheck/core/checker.py" in changed
        assert classify.changed_files_are_perf_sensitive(changed) is True


class TestWorkflowWiring:
    """`performance.yml` must feed the classifier the PR's *current* label
    set on every pull_request sub-event.

    The CLI half of Codex's force-label finding has unit coverage above, but
    the half that actually caused it -- where the label set comes from -- had
    none: a workflow that sourced labels from `github.event.label.name` would
    pass the unit tests unchanged and still stop force-running on every push
    after the labeling one. Asserted against the workflow text because that
    wiring has no other executable surface here.
    """

    @staticmethod
    def _workflow() -> str:
        root = Path(__file__).resolve().parent.parent
        return (root / ".github/workflows/performance.yml").read_text(encoding="utf-8")

    @classmethod
    def _effective_lines(cls) -> list[str]:
        """Workflow lines with comments stripped.

        Load-bearing: this file *documents* the rejected
        `github.event.label.name` spelling in a comment, so a naive substring
        search over the raw text reports the very thing it is checking for
        absent. Caught by the assertion below failing on its first run --
        which is the useful kind of test failure, and the reason this helper
        exists rather than a looser assertion.
        """
        out = []
        for raw in cls._workflow().splitlines():
            body = raw.split("#", 1)[0]
            if body.strip():
                out.append(body)
        return out

    def test_current_labels_comes_from_pull_request_labels(self) -> None:
        lines = self._effective_lines()
        # The PR's whole current label set, not the single delivered event's.
        assert any("github.event.pull_request.labels.*.name" in ln for ln in lines)
        # `github.event.label` exists only on labeled/unlabeled events, so
        # sourcing from it is what made the force-run stop after the first
        # push. It must not appear in any executed expression.
        offenders = [ln.strip() for ln in lines if "github.event.label" in ln]
        assert offenders == [], offenders

    def test_classifier_is_invoked_with_current_labels(self) -> None:
        text = self._workflow()
        assert "--current-labels" in text
        assert "--force-label performance" in text

    def test_current_labels_is_not_gated_on_a_labeled_event(self) -> None:
        """The env value must be assigned unconditionally -- a conditional
        expression keyed on the event name is how it would silently go empty
        on a synchronize push."""
        line = next(
            ln for ln in self._workflow().splitlines() if "CURRENT_LABELS:" in ln
        )
        assert "labeled" not in line
        assert "github.event_name" not in line
        assert "&&" not in line and "||" not in line


class TestL2ExtractionPathsAreClassified:
    """The stage that produces L2 evidence must mark a PR perf-sensitive.

    The first extension of this list covered the CLI, renderer, orchestration and
    storage paths but not the extraction layer -- so a PR touching only
    `extract/semantic_normalizer.py` or `dumper_manifest.py` classified as
    not-sensitive, and every perf job (including the full-CLI L2 gate, whose whole
    subject is that path) was skipped for it.
    """

    @pytest.mark.parametrize(
        "path",
        [
            "abicheck/extract/semantic_normalizer.py",
            "abicheck/extract/header_ast_fields.py",
            "abicheck/extract/header_ast_backend.py",
            "abicheck/extract/export_symbol_identity.py",
            "abicheck/dumper_manifest.py",
            "abicheck/dumper_hybrid.py",
        ],
    )
    def test_an_l2_extraction_module_is_perf_sensitive(self, path):
        assert classify.changed_files_are_perf_sensitive([path])

    def test_every_real_dumper_import_target_is_covered(self):
        # Derived from dumper.py's actual imports rather than a hand-listed set,
        # so a module it starts importing later is caught here instead of silently
        # dropping out of perf coverage.
        source = (_PATH.resolve().parent.parent / "abicheck" / "dumper.py").read_text(
            encoding="utf-8"
        )
        targets = set(
            re.findall(r"from \.(extract\.[a-z_]+|dumper_[a-z_]+) import", source)
        )
        targets |= set(re.findall(r"from \.(dumper_[a-z_]+) import", source))
        assert targets, "dumper.py should import at least one such module"
        uncovered = [
            t
            for t in sorted(targets)
            if not classify.changed_files_are_perf_sensitive(
                [f"abicheck/{t.replace('.', '/')}.py"]
            )
        ]
        assert uncovered == [], uncovered

    def test_an_unrelated_model_module_stays_unclassified(self):
        # The extension must not widen to the point where every PR is
        # perf-sensitive; model/ is mostly data shapes.
        assert not classify.changed_files_are_perf_sensitive(["abicheck/model/fact.py"])


class TestEntryPointModulesAreClassified:
    """The process entry point owns measured startup, so it must be sensitive.

    ``abicheck/__main__.py`` and ``abicheck/__init__.py`` run on every single
    invocation and together dominate the startup phase the full-CLI harness
    measures (~0.6s of a ~1.2s stored/stored compare). Both were unclassified:
    adding an import to either could slow every command with no perf job run.
    """

    @pytest.mark.parametrize("path", ["abicheck/__main__.py", "abicheck/__init__.py"])
    def test_an_entry_point_module_is_perf_sensitive(self, path):
        assert classify.changed_files_are_perf_sensitive([path])

    def test_an_ordinary_leaf_model_module_is_still_not_sensitive(self):
        # Vacuity guard: a pattern broad enough to match everything under
        # abicheck/ would make the two assertions above meaningless.
        assert not classify.changed_files_are_perf_sensitive(["abicheck/model/fact.py"])


class TestL2JobCheckoutHardening:
    """The two L2 jobs check out and then *run* pull-request code.

    With `actions/checkout`'s default `persist-credentials: true`, its GitHub
    credential stays in the workspace while the job builds a fixture and executes
    the CLI from that same checkout. `contents: read` does not disable
    persistence, so it has to be stated. Asserted over the parsed YAML rather
    than the text, so a reordering or a comment cannot satisfy it.
    """

    @staticmethod
    def _jobs() -> dict:
        yaml = pytest.importorskip("yaml")
        root = Path(__file__).resolve().parent.parent
        doc = yaml.safe_load(
            (root / ".github/workflows/performance.yml").read_text(encoding="utf-8")
        )
        return doc["jobs"]

    @pytest.mark.parametrize("job", ["l2-cli-perf", "l2-cli-extended"])
    def test_checkout_does_not_persist_credentials(self, job):
        steps = self._jobs()[job]["steps"]
        checkouts = [
            s for s in steps if str(s.get("uses", "")).startswith("actions/checkout")
        ]
        assert checkouts, f"{job} must check the repository out"
        for step in checkouts:
            assert step.get("with", {}).get("persist-credentials") is False, step

    @pytest.mark.parametrize("job", ["l2-cli-perf", "l2-cli-extended"])
    def test_every_remote_action_is_pinned_to_a_commit(self, job):
        steps = self._jobs()[job]["steps"]
        remote = [
            str(s["uses"])
            for s in steps
            if "uses" in s and not str(s["uses"]).startswith("./")
        ]
        assert remote, f"{job} uses at least one remote action"
        for uses in remote:
            ref = uses.split("@", 1)[1]
            assert re.fullmatch(r"[0-9a-f]{40}", ref), uses
