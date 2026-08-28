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

"""Unit tests for `scripts/check_bugfix_test_contract.py`'s structural
"does this diff carry evidence of a real, executable test" classification —
`touches_shipped_code`/`touches_tests`/`is_test_path`/`adds_or_modifies_a_test`
and the file-shape predicates underneath them
(`_STANDALONE_TEST_RUNNERS`/`_is_collected_python_test_module`).

Split out of `tests/test_bugfix_test_contract.py` (PR #885), which the
ADR-061 no-growth debt ledger (`architecture/debt.yaml`) already tracks at
a 1267-line baseline as a pre-existing oversized legacy test module —
several rounds of review findings on this exact classification logic
(a standalone CI-invoked runner that isn't `test_*.py`-named, a non-gating
script that looks like one, `conftest.py` at any depth) pushed the file
94 lines past that baseline, and the gate's own guidance is "move
responsibility instead of raising the baseline." This file is that move,
not a new obligation — see `test_bugfix_test_contract.py` for the
declared-half (PR-body requirement) and content-diff-parsing tests.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path, PurePosixPath

import pytest

_PATH = (
    Path(__file__).resolve().parent.parent / "scripts" / "check_bugfix_test_contract.py"
)
_spec = importlib.util.spec_from_file_location("check_bugfix_test_contract", _PATH)
assert _spec and _spec.loader
gate = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("check_bugfix_test_contract", gate)
_spec.loader.exec_module(gate)


def _content_diff(path: str, added: str = "    assert f(2) == 4") -> str:
    """One file with one added line, in `git diff --unified=0 -M` shape."""
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        "@@ -1,0 +2 @@\n"
        f"+{added}\n"
    )


def _deletion_diff(path: str) -> str:
    return (
        f"diff --git a/{path} b/{path}\n"
        "deleted file mode 100644\n"
        f"--- a/{path}\n"
        "+++ /dev/null\n"
        "@@ -1 +0,0 @@\n"
        "-    assert f(2) == 4\n"
    )


class TestStructuralRequirement:
    def test_shipped_code_without_a_test_is_detected(self) -> None:
        paths = ["abicheck/diff_types.py"]
        assert gate.touches_shipped_code(paths)
        assert not gate.touches_tests(paths)

    def test_a_test_change_satisfies_it(self) -> None:
        assert gate.touches_tests(["abicheck/diff_types.py", "tests/test_diff.py"])

    @pytest.mark.parametrize(
        "path",
        [
            "contrib/abicheck-clang-plugin/AbicheckFactsPlugin.cpp",
            "contrib/abicheck-clang-plugin/CMakeLists.txt",
        ],
    )
    def test_the_clang_plugin_runtime_is_shipped_code(self, path: str) -> None:
        """AGENTS.md classifies it as a surrounding first-party tree with its
        own tests; its runtime is C++/CMake, so no `.py`/`.sh`/`.yml` suffix
        matched and a fix confined to the plugin skipped the structural
        requirement (Codex review)."""
        assert gate.touches_shipped_code([path])

    def test_plugin_prose_is_not_shipped_code(self) -> None:
        assert not gate.touches_shipped_code(
            ["contrib/abicheck-clang-plugin/README.md"]
        )

    def test_a_deleted_test_is_not_evidence_of_a_regression_test(self) -> None:
        """Deleting a test is a change to a test path and the opposite of
        evidence — it let a fix satisfy the requirement by removing coverage
        (Codex review)."""
        assert not gate.adds_or_modifies_a_test(
            [("D", "tests/test_x.py")], _deletion_diff("tests/test_x.py")
        )

    @pytest.mark.parametrize("status", ["A", "M"])
    def test_an_added_or_modified_test_is_evidence(self, status: str) -> None:
        assert gate.adds_or_modifies_a_test(
            [(status, "tests/test_x.py")], _content_diff("tests/test_x.py")
        )

    def test_a_deleted_test_alongside_an_added_one_is_still_evidence(self) -> None:
        """Replacing a test is legitimate — only *deletion alone* is not."""
        assert gate.adds_or_modifies_a_test(
            [("D", "tests/test_old.py"), ("A", "tests/test_new.py")],
            _deletion_diff("tests/test_old.py") + _content_diff("tests/test_new.py"),
        )

    def test_a_modified_non_test_is_not_evidence(self) -> None:
        assert not gate.adds_or_modifies_a_test(
            [("M", "abicheck/diff_types.py")], _content_diff("abicheck/diff_types.py")
        )

    @pytest.mark.parametrize(
        "path",
        [
            # A registry/support module a real test imports, not one pytest
            # collects on its own — real files, not hypotheticals (Codex
            # review, PR #885): editing only tests/regressions/manifest.py
            # (e.g. adding a BugClass with no seed_tests path touched)
            # satisfied this gate with zero executable test evidence.
            "tests/regressions/manifest.py",
            "tests/canonical_identity_contract.py",
            "tests/_workflow_exec.py",
        ],
    )
    def test_a_modified_support_module_under_tests_is_not_evidence(
        self, path: str
    ) -> None:
        """`is_test_path()` alone credits any non-prose file under `tests/`
        — this is the narrower check that requires the file to actually be
        one pytest collects."""
        assert gate.is_test_path(path), f"{path} should still read as a test path"
        assert not gate.adds_or_modifies_a_test([("M", path)], _content_diff(path))

    def test_a_modified_support_module_alongside_a_real_test_is_still_evidence(
        self,
    ) -> None:
        """Negative control for the pair above: the new check must reject the
        support module specifically, not the presence of any `.py` file in
        the same diff."""
        diff = _content_diff("tests/regressions/manifest.py") + _content_diff(
            "tests/test_regressions_manifest.py"
        )
        assert gate.adds_or_modifies_a_test(
            [
                ("M", "tests/regressions/manifest.py"),
                ("M", "tests/test_regressions_manifest.py"),
            ],
            diff,
        )

    def test_a_type_change_is_not_test_evidence(self) -> None:
        """Retyping a test file is not writing one."""
        assert not gate.adds_or_modifies_a_test(
            [("T", "tests/test_x.py")], _content_diff("tests/test_x.py")
        )

    @pytest.mark.parametrize(
        "path",
        sorted(gate._STANDALONE_TEST_RUNNERS),
    )
    def test_a_modified_standalone_test_runner_is_evidence(self, path: str) -> None:
        """The naming-convention check alone is too narrow the other way:
        these files are real, CI-executed tests (invoked directly as
        `python <path> ...` by a workflow, never collected by pytest), so
        they must count as evidence despite not matching `test_*.py`/
        `*_test.py` (Codex review, PR #885 — the reported gap was
        `contrib/abicheck-clang-plugin/tests/conformance.py`/`scan_flow.py`
        specifically, blocking any fix confined to the clang facts plugin)."""
        assert not (
            PurePosixPath(path).name.startswith("test_") or path.endswith("_test.py")
        ), (
            f"{path} already matches the naming convention — remove it from the allowlist"
        )
        assert gate.adds_or_modifies_a_test([("M", path)], _content_diff(path))

    def test_a_non_gating_standalone_script_is_not_evidence(self) -> None:
        """Negative control for the pair above: not every `python <path> ...`
        invocation qualifies. `tests/summarize_validate_results.py`'s own two
        CI invocations both end in `|| true`, so its exit code can never fail
        the workflow — a fix confined to editing it proves nothing was
        actually tested (Codex review, PR #885)."""
        path = "tests/summarize_validate_results.py"
        assert path not in gate._STANDALONE_TEST_RUNNERS
        assert not gate.adds_or_modifies_a_test([("M", path)], _content_diff(path))

    @pytest.mark.parametrize(
        "path",
        [
            "tests/conftest.py",
            "tests/some_subdir/conftest.py",
        ],
    )
    def test_a_modified_conftest_is_evidence(self, path: str) -> None:
        """A `conftest.py` at any depth *under the root `tests/` tree* is
        accepted despite not matching `test_*.py`/`*_test.py` naming —
        pytest auto-discovers and applies every `conftest.py` under its
        `testpaths = ["tests"]` rootdir to every test in its own directory
        and below, unconditionally, so widening a parametrized fixture
        there can genuinely exercise new test runs with no `test_*.py`
        file touched at all (Codex review, PR #885, fifth round)."""
        assert gate.adds_or_modifies_a_test([("M", path)], _content_diff(path))

    def test_a_conftest_outside_the_root_tests_tree_is_not_evidence(self) -> None:
        """Negative control: `contrib/abicheck-clang-plugin/tests/` is a
        real, sibling `tests/` directory in this repo, but this
        repository's own `testpaths = ["tests"]` means pytest never
        collects it (its own tests are invoked directly with `python
        <path> ...`, never through pytest — see `_STANDALONE_TEST_RUNNERS`)
        — so a `conftest.py` placed there is never loaded by pytest at
        all, and crediting it as evidence would be exactly the same
        unearned credit `_is_collected_python_test_module` exists to
        reject (Codex review, PR #885, ninth round)."""
        path = "contrib/abicheck-clang-plugin/tests/conftest.py"
        assert gate.is_test_path(path), f"{path} should still read as a test path"
        assert not gate.adds_or_modifies_a_test([("M", path)], _content_diff(path))

    def test_conftest_is_still_not_credited_when_unchanged(self) -> None:
        """Negative control: naming `conftest.py` isn't a free pass on its
        own — the status/content requirements (added, modified — not
        deleted; real added content, not a comment/whitespace edit) still
        apply exactly as they do to any other recognized test file."""
        assert not gate.adds_or_modifies_a_test(
            [("D", "tests/conftest.py")], _deletion_diff("tests/conftest.py")
        )
