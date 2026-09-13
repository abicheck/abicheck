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

    def test_an_unrelated_module_stays_unclassified(self):
        # The extension must not widen to the point where every PR is
        # perf-sensitive. This used to assert `abicheck/model/fact.py` on the
        # reasoning that "model/ is mostly data shapes" -- which was the wrong
        # reason and is now the opposite of the contract: those shapes are
        # constructed, encoded, walked and projected by every measured command,
        # so the whole ring is classified (TestModelRingIsClassified). The guard
        # moved to a module no measured path imports.
        assert not classify.changed_files_are_perf_sensitive(["abicheck/errors.py"])


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

    def test_an_unrelated_module_is_still_not_sensitive(self):
        # Vacuity guard: a pattern broad enough to match everything under
        # abicheck/ would make the assertions above meaningless. (This used to
        # name `abicheck/model/fact.py`, which is now legitimately classified --
        # see TestModelRingIsClassified.)
        assert not classify.changed_files_are_perf_sensitive(["abicheck/errors.py"])


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


class TestModelRingIsClassified:
    """Every object the measured L2 pipeline passes between stages lives in `model/`.

    Extraction constructs them, storage encodes them, comparison walks them and
    reporting projects them, so a change to one of their layouts or to a shared
    normalization primitive can regress the whole full-CLI workload without
    touching any other classified path -- and every perf job was skipped for it.
    """

    @pytest.mark.parametrize(
        "path",
        [
            "abicheck/model/semantic_ir.py",
            "abicheck/model/snapshot.py",
            "abicheck/model/signature_normalization.py",
            "abicheck/model/fact.py",
            "abicheck/model/change_catalog/kinds.py",
        ],
    )
    def test_a_model_module_is_perf_sensitive(self, path):
        assert classify.changed_files_are_perf_sensitive([path])

    def test_the_whole_subtree_is_covered_not_a_file_list(self):
        # The invariant is on the ring, not on the modules today's pipeline
        # happens to touch: a file list here would go stale exactly the way the
        # pre-`extract/**` one did. Asserted with a name that exists nowhere.
        assert classify.changed_files_are_perf_sensitive(
            ["abicheck/model/a_module_added_tomorrow.py"]
        )
        assert classify.changed_files_are_perf_sensitive(
            ["abicheck/model/nested/deeper.py"]
        )

    def test_a_sibling_tree_is_not_swept_in(self):
        # Vacuity guard: `abicheck/model/**` must not be a stand-in for
        # `abicheck/**`.
        assert not classify.changed_files_are_perf_sensitive(
            ["abicheck/modelling_helpers.py", "abicheck/errors.py"]
        )


class TestDerivedFromRealImports:
    """The classifier's set is checked against `dumper.py`'s own imports.

    A hand-listed set goes stale silently, which is how it came to cover
    `pe_metadata`/`macho_metadata` but not `elf_metadata` -- the first thing an
    ELF dump calls, and the format the full-CLI fixture is built in. So the
    expectation is *derived* here, including the function-local (lazy) imports
    that the earlier module-scope-only derivation could not see.
    """

    @staticmethod
    def _dumper_source() -> str:
        root = _PATH.resolve().parent.parent
        return (root / "abicheck" / "dumper.py").read_text(encoding="utf-8")

    def test_every_parser_dumper_imports_is_covered(self):
        # Matches both `from .x import y` at module scope and the indented, lazy
        # form inside a function (`    from .elf_metadata import ...`), which is
        # how dumper.py reaches most of the parsers.
        source = self._dumper_source()
        targets = set(re.findall(r"from \.((?:extract\.)?[a-z0-9_]+) import", source))
        parsers = {
            t
            for t in targets
            if t.endswith(("_metadata", "_parser", "_utils", "_snapshot"))
            or t.startswith(("dwarf", "elf", "pe_", "macho", "pdb", "btf", "ctf"))
        }
        assert parsers, "dumper.py must import at least one parser module"
        uncovered = sorted(
            t
            for t in parsers
            if not classify.changed_files_are_perf_sensitive(
                [f"abicheck/{t.replace('.', '/')}.py"]
            )
        )
        assert uncovered == [], uncovered

    def test_the_derivation_sees_lazy_function_local_imports(self):
        # Guard on the test itself: the previous version's regex only matched
        # module-scope imports, so it reported full coverage while every lazily
        # imported parser was unclassified. `elf_metadata` is imported lazily
        # inside `_dump_elf`, so finding it proves the regex reaches that form.
        source = self._dumper_source()
        targets = set(re.findall(r"from \.((?:extract\.)?[a-z0-9_]+) import", source))
        assert "elf_metadata" in targets, sorted(targets)[:20]
        assert re.search(r"^\s+from \.elf_metadata import", source, re.M), (
            "elf_metadata should be a lazy, indented import in dumper.py"
        )

    @pytest.mark.parametrize(
        "path",
        [
            "scripts/check_l2_cli_perf.py",
            "scripts/l2_cli_validation.py",
            "scripts/l2_cli_gating.py",
            "scripts/l2_cli_fixture.py",
            "scripts/perf_receipt.py",
            "scripts/perf_measurement.py",
            "scripts/perf_baseline.py",
            "scripts/check_header_graph_perf.py",
        ],
    )
    def test_every_harness_module_is_perf_sensitive(self, path):
        # A harness split that moves validation or gating into an unclassified
        # file stops a PR weakening it from being measured at all.
        assert classify.changed_files_are_perf_sensitive([path])

    def test_every_module_the_harness_imports_is_covered(self):
        root = _PATH.resolve().parent.parent
        source = (root / "scripts" / "check_l2_cli_perf.py").read_text(encoding="utf-8")
        siblings = set(re.findall(r"^(?:from|import) ([a-z0-9_]+)", source, re.M))
        local = sorted(
            name for name in siblings if (root / "scripts" / f"{name}.py").is_file()
        )
        assert local, "the harness imports at least one sibling script"
        uncovered = [
            name
            for name in local
            if not classify.changed_files_are_perf_sensitive([f"scripts/{name}.py"])
        ]
        assert uncovered == [], uncovered

    def test_an_unrelated_script_is_not_swept_in(self):
        # Vacuity guard: `scripts/l2_cli_*.py` must not behave like `scripts/*`.
        assert not classify.changed_files_are_perf_sensitive(
            ["scripts/gen_cli_reference.py", "scripts/check_fp_rate.py"]
        )


class TestTheHeaderGraphGateRequiresAllMetrics:
    """The PR lane must gate every metric its own summary claims to cover.

    `--require-all-metrics` was omitted on the argument that the base branch
    writes its report with its own older copy of the script, so a base report
    could legitimately lack `total_ms`. That reasoning was wrong about this job:
    the base measurement runs *head's* harness against base's installed package
    — deliberately, since one harness measuring two products is the only way the
    numbers are comparable — so the base report always carries all three metrics,
    and omitting the flag only let a missing or non-gateable metric degrade to a
    printed note while the job still exited 0.
    """

    @staticmethod
    def _workflow_text() -> str:
        root = _PATH.resolve().parent.parent
        return (root / ".github/workflows/performance.yml").read_text(encoding="utf-8")

    @staticmethod
    def _gating_job_steps() -> list[dict]:
        yaml = pytest.importorskip("yaml")
        root = _PATH.resolve().parent.parent
        doc = yaml.safe_load(
            (root / ".github/workflows/performance.yml").read_text(encoding="utf-8")
        )
        return doc["jobs"]["header-graph-regression"]["steps"]

    def test_the_baseline_gated_invocation_requires_all_metrics(self):
        # Asserted on the step that actually passes `--baseline`: a run with no
        # baseline is report-only, where the flag would mean nothing.
        gating = [
            step
            for step in self._gating_job_steps()
            if "--baseline base_header_graph.json" in str(step.get("run", ""))
        ]
        assert gating, "the job must gate head against a base measurement"
        for step in gating:
            body = step["run"]
            assert "--require-all-metrics" in body, body

    def test_the_base_measurement_really_uses_heads_harness(self):
        # The premise the fix rests on. If this ever stops being true, the flag's
        # justification changes and this test is where that surfaces.
        text = self._workflow_text()
        assert "./base_env/bin/python head/scripts/check_header_graph_perf.py" in text

    def test_the_script_supports_the_flag(self):
        root = _PATH.resolve().parent.parent
        source = (root / "scripts/check_header_graph_perf.py").read_text(
            encoding="utf-8"
        )
        assert '"--require-all-metrics"' in source
        assert "args.require_all_metrics and ungated" in source
