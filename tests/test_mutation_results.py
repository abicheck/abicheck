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

"""Primitive-level tests for the mutation-gate's parsing/attribution layer.

Per AGENTS.md's "Primitive-level property tests" guidance, the reusable
primitives (`split_mutant_key`, `functions_covering_lines`,
`survivors_by_module`) get their contract stated as invariants here, decoupled
from the gate's own drift logic.

The fixtures are **captured from real mutmut 3.7.0 output**, not invented. That
distinction is the whole point of this file: the previous suite's fixtures
encoded a format mutmut does not actually emit (`x_1: survived`, with no
`__mutmut_<n>` suffix) and a single-line summary that a real run never produces
in isolation — so every test passed against a parser that misread real output.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_MOD_PATH = Path(__file__).resolve().parent.parent / "scripts" / "mutation_results.py"
_spec = importlib.util.spec_from_file_location("mutation_results", _MOD_PATH)
assert _spec and _spec.loader
mr = importlib.util.module_from_spec(_spec)
# Register before exec: the module defines a dataclass, and dataclasses
# resolves field types via sys.modules[cls.__module__].
sys.modules["mutation_results"] = mr
_spec.loader.exec_module(mr)


# --- Captured verbatim from `mutmut results` (3.7.0) -----------------------
REAL_RESULTS = """\
    pkg.alpha.x_scale__mutmut_1: survived
    pkg.alpha.x_scale__mutmut_2: survived
    pkg.beta.x_clamp__mutmut_1: survived
    pkg.beta.x_clamp__mutmut_2: survived
"""

REAL_RESULTS_METHODS = """\
    pkg.gamma.xǁWidgetǁarea__mutmut_1: survived
    pkg.gamma.xǁWidgetǁbump__mutmut_1: survived
    pkg.gamma.xǁWidgetǁbump__mutmut_2: survived
    pkg.gamma.x_fetch__mutmut_1: no tests
    pkg.gamma.x_fetch__mutmut_2: no tests
    pkg.gamma.x_outer__mutmut_1: survived
    pkg.gamma.x_outer__mutmut_2: survived
"""

# The tail of a real `mutmut run`, showing the progress line re-rendered from
# 🙁 0 upward. Reading the FIRST match here is the defect this gate had.
REAL_RUN_TAIL = (
    "Running mutation testing\n"
    "⠼ 0/6  🎉 0 🫥 0  ⏰ 0  🤔 0  🙁 0  🔇 0  🧙 0"
    "⠴ 0/6  🎉 0 🫥 0  ⏰ 0  🤔 0  🙁 0  🔇 0  🧙 0"
    "⠇ 1/6  🎉 0 🫥 0  ⏰ 0  🤔 0  🙁 1  🔇 0  🧙 0"
    "⠋ 6/6  🎉 2 🫥 0  ⏰ 0  🤔 0  🙁 4  🔇 0  🧙 0\n"
    "55.90 mutations/second\n"
)


class TestMutantKeySplitting:
    """`split_mutant_key` is the attribution primitive everything else rests on."""

    @pytest.mark.parametrize(
        "key, module, function",
        [
            ("pkg.alpha.x_scale__mutmut_1", "pkg.alpha", "scale"),
            ("pkg.gamma.xǁWidgetǁarea__mutmut_1", "pkg.gamma", "Widget.area"),
            ("a.b.c.x_deep__mutmut_12", "a.b.c", "deep"),
            # A module whose own name starts with the mangling prefix must not
            # confuse the split: the function is always the LAST component.
            ("pkg.x_helpers.x_run__mutmut_3", "pkg.x_helpers", "run"),
        ],
    )
    def test_splits_real_key_shapes(self, key: str, module: str, function: str) -> None:
        assert mr.split_mutant_key(key) == (module, function)

    @pytest.mark.parametrize(
        "key",
        [
            "not_a_mutant",
            "pkg.alpha.x_scale",  # no __mutmut_<n> suffix
            "pkg.alpha.x_scale__mutmut_x",  # non-numeric index
            "x_scale__mutmut_1",  # no module part
            "pkg.alpha.scale__mutmut_1",  # unmangled function name
            "__mutmut_1",
        ],
    )
    def test_refuses_to_guess(self, key: str) -> None:
        """Ambiguous input yields None, never a fabricated attribution."""
        assert mr.split_mutant_key(key) is None

    def test_module_path_is_posix_source_path(self) -> None:
        recs = mr.parse_mutant_records(REAL_RESULTS)
        assert {r.module_path for r in recs} == {"pkg/alpha.py", "pkg/beta.py"}


class TestStatusVocabulary:
    """Statuses come from mutmut's own `status_by_exit_code` table."""

    def test_survived_and_unresolved_are_disjoint(self) -> None:
        assert mr.STATUS_SURVIVED not in mr.UNRESOLVED_STATUSES
        assert not (mr.RESOLVED_OK_STATUSES & mr.UNRESOLVED_STATUSES)

    def test_no_tests_counts_as_unresolved_not_as_clean(self) -> None:
        """An uncovered mutant is a real gap, never a neutral outcome."""
        recs = mr.parse_mutant_records(REAL_RESULTS_METHODS)
        no_tests = [r for r in recs if r.status == "no tests"]
        assert no_tests, "fixture should carry a no-tests mutant"
        assert all(r.is_unresolved and not r.is_survivor for r in no_tests)
        assert mr.count_unresolved(REAL_RESULTS_METHODS) == 2

    def test_multiword_status_is_parsed_whole(self) -> None:
        text = "    pkg.a.x_f__mutmut_1: caught by type check\n"
        (rec,) = mr.parse_mutant_records(text)
        assert rec.status == "caught by type check"
        assert not rec.is_survivor and not rec.is_unresolved

    def test_an_unknown_status_fails_closed_as_unresolved(self) -> None:
        """A status from a future `mutmut>=3.7,<4` must not vanish.

        The pin admits any 3.x, so a new non-killed status is a real
        possibility. Classified as neither a survivor nor unresolved it would
        drop out of both halves of the gate, letting an outcome this parser
        does not understand pass a zero baseline (Codex review).
        """
        text = "    pkg.a.x_f__mutmut_1: escaped via warp core\n"
        (rec,) = mr.parse_mutant_records(text)
        assert rec.status not in mr.RESOLVED_OK_STATUSES
        assert rec.status not in mr.UNRESOLVED_STATUSES
        assert not rec.is_survivor
        assert rec.is_unresolved
        assert mr.count_unresolved(text) == 1

    @pytest.mark.parametrize("status", sorted(mr.RESOLVED_OK_STATUSES))
    def test_only_the_named_good_statuses_are_accepted(self, status: str) -> None:
        """Negative control for the fail-closed rule above.

        Failing closed must not degrade into "everything is unresolved": each
        status mutmut names as a resolved, acceptable outcome still resolves.
        """
        (rec,) = mr.parse_mutant_records(f"    pkg.a.x_f__mutmut_1: {status}\n")
        assert not rec.is_unresolved and not rec.is_survivor

    def test_every_documented_unresolved_status_is_still_unresolved(self) -> None:
        """The observed 3.7.0 vocabulary keeps its meaning under the complement
        rule — the frozenset stopped being the predicate, not the contract."""
        for status in sorted(mr.UNRESOLVED_STATUSES):
            (rec,) = mr.parse_mutant_records(f"    pkg.a.x_f__mutmut_1: {status}\n")
            assert rec.is_unresolved, status


class TestCompletionWitness:
    """`{total - not_checked}/{total}`, transcribed from mutmut 3.7.0."""

    @pytest.mark.parametrize(
        "text, complete",
        [
            ("12/12  🎉 12  🙁 0", True),
            ("0/100  🎉 0  🙁 0", False),
            ("309/464  🎉 300  🙁 0", False),
            ("🙁 0", False),
            ("", False),
            # Zero total is not a finished run, it is no run.
            ("0/0  🎉 0  🙁 0", False),
        ],
    )
    def test_only_a_matching_counter_means_finished(
        self, text: str, complete: bool
    ) -> None:
        assert mr.summary_run_is_complete(text) is complete

    def test_the_last_render_decides(self) -> None:
        """Same rule as the survivor counter: the stream re-renders, and only
        the final one describes the run that happened."""
        stream = "0/50  🙁 0\n25/50  🙁 1\n50/50  🎉 49  🙁 1\n"
        assert mr.summary_run_is_complete(stream)
        assert not mr.summary_run_is_complete("50/50  🙁 1\n0/50  🙁 0\n")

    def test_a_bare_fraction_in_prose_is_not_a_render(self) -> None:
        """The counter is only read from a line that also carries a status
        emoji, so an unrelated ratio cannot be mistaken for completion."""
        assert not mr.summary_run_is_complete("processed 7/7 files\n")


class TestSurvivorCounting:
    def test_counts_real_results_listing(self) -> None:
        assert mr.parse_survivors(REAL_RESULTS) == 4

    def test_progress_stream_does_not_mask_the_real_count(self) -> None:
        """Regression: the previous parser returned 0 for this exact input.

        `_gather_results` concatenated `mutmut run`'s stdout ahead of `mutmut
        results`, and a bare `re.search(r"🙁\\s*(\\d+)")` matched the FIRST
        progress render — which is always `🙁 0`, printed before any mutant has
        been classified. Reproduced against mutmut 3.7.0: 4 real survivors,
        reported as 0. With a baseline of 0 the gate would have passed green.
        """
        combined = REAL_RUN_TAIL + REAL_RESULTS
        assert mr.parse_survivors(combined) == 4

    def test_progress_only_text_reads_the_final_render(self) -> None:
        """With no per-mutant listing, the LAST summary is the settled one."""
        assert mr.parse_survivors(REAL_RUN_TAIL) == 4

    def test_unmeasurable_is_distinguishable_from_zero(self) -> None:
        for text in ("", "   ", "no useful signal here", "Killed all"):
            assert mr.parse_survivors(text) is None

    def test_killed_only_run_reports_zero_survivors(self) -> None:
        assert mr.parse_survivors("    pkg.a.x_f__mutmut_1: killed\n") == 0

    def test_by_module_groups_and_sorts(self) -> None:
        recs = mr.parse_mutant_records(REAL_RESULTS_METHODS)
        assert mr.survivors_by_module(recs) == {
            "pkg/gamma.py": [
                "pkg.gamma.x_outer__mutmut_1",
                "pkg.gamma.x_outer__mutmut_2",
                "pkg.gamma.xǁWidgetǁarea__mutmut_1",
                "pkg.gamma.xǁWidgetǁbump__mutmut_1",
                "pkg.gamma.xǁWidgetǁbump__mutmut_2",
            ]
        }

    def test_no_tests_mutants_are_not_counted_as_survivors(self) -> None:
        recs = mr.parse_mutant_records(REAL_RESULTS_METHODS)
        assert sum(1 for r in recs if r.is_survivor) == 5
        assert all(
            "fetch" not in k for k in mr.survivors_by_module(recs)["pkg/gamma.py"]
        )


class TestChangedFunctionAttribution:
    SOURCE = textwrap.dedent(
        """\
        class Widget:
            def area(self, w, h):
                return w * h

            class Inner:
                def deep(self, q):
                    return q - 1

        async def fetch(n):
            return n * 3

        def outer(a):
            def nested(b):
                return b + 5
            return nested(a)
        """
    )

    def test_maps_line_to_method_qualname(self) -> None:
        assert mr.functions_covering_lines(self.SOURCE, {3}) == {"Widget.area"}

    def test_nested_function_also_marks_its_enclosing_function(self) -> None:
        """mutmut attributes a nested function's mutants to the enclosing one.

        Verified against 3.7.0: mutating `nested` produced keys named
        `x_outer__mutmut_N`. If this returned only `outer.nested`, the
        diff-scoped gate would never match those survivors.
        """
        assert mr.functions_covering_lines(self.SOURCE, {14}) == {
            "outer",
            "outer.nested",
        }

    def test_async_def_is_covered(self) -> None:
        assert mr.functions_covering_lines(self.SOURCE, {10}) == {"fetch"}

    def test_a_class_statement_is_module_scope_not_a_function(self) -> None:
        """This previously asserted `set()`, which was correct about the
        *function* question and wrong about the gate: a `class` line belongs to
        no function body, yet changing it (its bases, its name) changes what
        its methods do. Since mutmut mutates only function bodies, module scope
        is the only attribution that lets the diff-scoped gate see it at all."""
        assert mr.functions_covering_lines(self.SOURCE, {1}) == {mr.MODULE_SCOPE}

    def test_empty_line_set_is_empty_result(self) -> None:
        assert mr.functions_covering_lines(self.SOURCE, set()) == set()

    def test_a_decorator_line_is_attributed_to_the_function_it_decorates(
        self,
    ) -> None:
        """Every detector in this codebase is registered by decorator, so a
        change confined to `@registry.detector("...")` is an ordinary edit —
        and `ast.FunctionDef.lineno` points at `def`, not the decorator, so
        anchoring there matched no function at all (Codex review)."""
        source = (
            "import x\n"
            "\n"
            "@registry.detector('foo')\n"
            "@another\n"
            "def alpha():\n"
            "    return 1\n"
        )
        assert mr.functions_covering_lines(source, {3}) == {"alpha"}
        assert mr.functions_covering_lines(source, {4}) == {"alpha"}
        assert mr.functions_covering_lines(source, {5}) == {"alpha"}
        # The import above the decorator belongs to no function — it is a
        # module-scope change, not "nothing changed" (see
        # test_an_import_change_is_module_scope_too).
        assert mr.functions_covering_lines(source, {1}) == {mr.MODULE_SCOPE}

    def test_a_decorated_method_keeps_its_qualname(self) -> None:
        source = "class W:\n    @property\n    def area(self):\n        return 1\n"
        assert mr.functions_covering_lines(source, {2}) == {"W.area"}

    MODULE_LEVEL_SOURCE = textwrap.dedent(
        """\
        import os

        # a comment at module scope
        _KINDS = frozenset({"a", "b"})

        def uses_it(x):
            return x in _KINDS
        """
    )

    def test_a_module_level_edit_is_attributed_to_module_scope(self) -> None:
        """Several mutated modules carry behaviour in top-level tables, and
        mutmut mutates only function bodies — so "no function contains this
        line" collapsed to "nothing changed" and the diff-scoped gate matched
        no survivor at all (Codex review)."""
        assert mr.functions_covering_lines(self.MODULE_LEVEL_SOURCE, {4}) == {
            mr.MODULE_SCOPE
        }

    def test_an_import_change_is_module_scope_too(self) -> None:
        assert mr.MODULE_SCOPE in mr.functions_covering_lines(
            self.MODULE_LEVEL_SOURCE, {1}
        )

    @pytest.mark.parametrize("line", [2, 3, 5])
    def test_blank_and_comment_lines_do_not_gate_a_module(self, line: int) -> None:
        """Otherwise reformatting or a comment edit would gate every survivor
        in the module."""
        assert mr.functions_covering_lines(self.MODULE_LEVEL_SOURCE, {line}) == set()

    def test_a_function_body_edit_is_not_module_scope(self) -> None:
        assert mr.functions_covering_lines(self.MODULE_LEVEL_SOURCE, {7}) == {"uses_it"}

    def test_syntax_error_degrades_instead_of_raising(self) -> None:
        """A half-edited file must not crash the gate."""
        assert mr.functions_covering_lines("def broken(:\n", {1}) == set()

    def test_attribution_is_order_independent(self) -> None:
        """The result depends on the line set, not on iteration order."""
        a = mr.functions_covering_lines(self.SOURCE, {3, 10, 14})
        b = mr.functions_covering_lines(self.SOURCE, {14, 3, 10})
        assert a == b == {"Widget.area", "fetch", "outer", "outer.nested"}


@pytest.mark.slow
@pytest.mark.skipif(shutil.which("mutmut") is None, reason="mutmut not installed")
def test_parser_reads_a_real_mutmut_run_end_to_end(tmp_path: Path) -> None:
    """Third-party-boundary test: go through real mutmut, not a fixture.

    AGENTS.md's storage-boundary lesson (ADR-059 §12) applies here too — a
    parser whose job is "honor an external tool's output format" must be
    exercised against that tool's *actual* output at least once, or a format
    change (or a wrong belief about the format) passes every hand-written
    fixture. This builds a package with one deliberately under-verified
    function, runs mutmut for real, and asserts the parser attributes the
    survivor to the right module and function.

    The `mutation` CI lane installs mutmut, so this executes there; it is
    marked `slow` to stay out of the default inner loop.
    """
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "alpha.py").write_text(
        textwrap.dedent(
            """\
            def add(a, b):
                return a + b

            def scale(a):
                return a * 2
            """
        )
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_all.py").write_text(
        textwrap.dedent(
            """\
            from pkg.alpha import add, scale

            def test_add():
                assert add(1, 2) == 3

            def test_scale():
                scale(3)   # executed but NOT verified -> mutants survive
            """
        )
    )
    (tmp_path / "pyproject.toml").write_text(
        textwrap.dedent(
            """\
            [tool.mutmut]
            source_paths = ["pkg/alpha.py"]
            pytest_add_cli_args_test_selection = ["tests/"]
            pytest_add_cli_args = ["-q", "--tb=no"]
            use_git_change_detection = false
            """
        )
    )

    env = {"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(tmp_path)}
    subprocess.run(
        [sys.executable, "-m", "mutmut", "run"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=900,
        env=env,
    )
    results = subprocess.run(
        [sys.executable, "-m", "mutmut", "results"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
    ).stdout

    records = mr.parse_mutant_records(results)
    assert records, f"no mutants parsed from real output:\n{results}"

    survivors = mr.survivors_by_module(records)
    assert "pkg/alpha.py" in survivors, survivors
    # `scale` is executed but unverified, so its mutants survive; `add` is
    # asserted, so its mutants must not appear as survivors.
    surviving_funcs = {r.function for r in records if r.is_survivor}
    assert "scale" in surviving_funcs
    assert "add" not in surviving_funcs

    # The exported stats are the completeness witness the gate relies on.
    subprocess.run(
        [sys.executable, "-m", "mutmut", "export-cicd-stats"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
    )
    stats = mr.load_cicd_stats(tmp_path / "mutants")
    assert stats is not None and stats["total"] > 0
    assert stats["survived"] == sum(1 for r in records if r.is_survivor)
