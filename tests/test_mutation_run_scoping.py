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

"""Unit tests for scripts/check_mutation_score.py's run-scoping
(``--scope-run-to-diff``).

Split out of tests/test_mutation_score_gate.py (which was already past the
file-size soft limit) rather than grown further — see that file for the
gate's general parsing/drift-logic tests; this file is scoped to one
feature.

`mutmut run` always *generates* mutants for the whole `only_mutate` set —
only the test-execution phase can be scoped, via its own `MUTANT_NAMES`
positional argument (verified directly against a real, installed mutmut
3.7.0: `collect_source_file_mutation_data` fnmatches each given pattern
against every mutant key and filters `tests_for_mutant_names` to the
matches). These tests cover the pure pattern-building helpers and the
scope-aware unresolved-gating main() needs so an out-of-scope module's
deliberately-untested ("not checked") mutants never fail a scoped run.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_GATE_PATH = (
    Path(__file__).resolve().parent.parent / "scripts" / "check_mutation_score.py"
)
_spec = importlib.util.spec_from_file_location("check_mutation_score", _GATE_PATH)
assert _spec and _spec.loader
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)


def _write(tmp_path: Path, name: str, text: str) -> str:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


_DIFF = """\
diff --git a/abicheck/diff_types.py b/abicheck/diff_types.py
--- a/abicheck/diff_types.py
+++ b/abicheck/diff_types.py
@@ -1,0 +2,1 @@
+    pass
"""

_SOURCE = """\
def alpha():
    return 1


def untouched():
    return 2
"""


def _pyproject_with_only_mutate(tmp_path: Path, only_mutate: list[str]) -> None:
    # A minimal but real TOML document — hand-written rather than via
    # tomllib's write-side (stdlib has none), since only_mutate's own strings
    # are plain module paths with no character needing escaping.
    items = ",\n".join(f'  "{m}"' for m in only_mutate)
    (tmp_path / "pyproject.toml").write_text(
        f"[tool.mutmut]\nonly_mutate = [\n{items}\n]\n", encoding="utf-8"
    )


def test_mutant_scope_pattern_matches_mutmuts_dotted_key_format() -> None:
    assert gate.mutant_scope_pattern("abicheck/diff_symbols.py") == (
        "abicheck.diff_symbols.*"
    )
    assert gate.mutant_scope_pattern("abicheck/buildsource/inline.py") == (
        "abicheck.buildsource.inline.*"
    )


def test_load_only_mutate_globs_reads_the_real_pyproject_toml() -> None:
    """Sanity: the real config this repo ships parses and names real modules."""
    only_mutate = gate.load_only_mutate_globs()
    assert only_mutate is not None
    assert "abicheck/diff_symbols.py" in only_mutate
    assert "abicheck/checker_policy.py" in only_mutate


def test_load_only_mutate_globs_returns_none_when_unreadable(tmp_path: Path) -> None:
    assert gate.load_only_mutate_globs(tmp_path / "does-not-exist.toml") is None


def test_diff_touched_only_mutate_modules_uses_added_and_removed_lines() -> None:
    only_mutate = ["abicheck/diff_types.py", "abicheck/diff_symbols.py"]
    # diff_types.py: pure modification. diff_symbols.py: pure deletion (no
    # new-side hunk at all) — the case the module docstring calls out.
    diff = (
        "diff --git a/abicheck/diff_types.py b/abicheck/diff_types.py\n"
        "--- a/abicheck/diff_types.py\n"
        "+++ b/abicheck/diff_types.py\n"
        "@@ -1,0 +2,1 @@\n"
        "+    pass\n"
        "diff --git a/abicheck/diff_symbols.py b/abicheck/diff_symbols.py\n"
        "--- a/abicheck/diff_symbols.py\n"
        "+++ b/abicheck/diff_symbols.py\n"
        "@@ -5,1 +4,0 @@\n"
        "-    pass\n"
        "diff --git a/abicheck/service.py b/abicheck/service.py\n"
        "--- a/abicheck/service.py\n"
        "+++ b/abicheck/service.py\n"
        "@@ -1,0 +2,1 @@\n"
        "+    pass\n"
    )
    touched = gate.diff_touched_only_mutate_modules(diff, only_mutate)
    # service.py is real but not in only_mutate, and must not appear.
    assert touched == {"abicheck/diff_types.py", "abicheck/diff_symbols.py"}


def test_mutant_run_scope_is_none_without_a_diff_or_config() -> None:
    assert gate.mutant_run_scope(None, ["abicheck/diff_types.py"]) is None
    assert gate.mutant_run_scope("some diff", None) is None
    assert gate.mutant_run_scope("some diff", []) is None


def test_mutant_run_scope_is_none_when_nothing_in_scope_is_touched() -> None:
    diff = (
        "diff --git a/abicheck/service.py b/abicheck/service.py\n"
        "--- a/abicheck/service.py\n+++ b/abicheck/service.py\n"
        "@@ -1,0 +2,1 @@\n+    pass\n"
    )
    assert gate.mutant_run_scope(diff, ["abicheck/diff_types.py"]) is None


def test_mutant_run_scope_is_none_when_every_module_is_touched() -> None:
    """Scoping would filter nothing, so it's not worth the extra invocation shape."""
    diff = (
        "diff --git a/abicheck/diff_types.py b/abicheck/diff_types.py\n"
        "--- a/abicheck/diff_types.py\n+++ b/abicheck/diff_types.py\n"
        "@@ -1,0 +2,1 @@\n+    pass\n"
    )
    assert gate.mutant_run_scope(diff, ["abicheck/diff_types.py"]) is None


def test_mutant_run_scope_narrows_to_the_touched_module() -> None:
    diff = (
        "diff --git a/abicheck/diff_types.py b/abicheck/diff_types.py\n"
        "--- a/abicheck/diff_types.py\n+++ b/abicheck/diff_types.py\n"
        "@@ -1,0 +2,1 @@\n+    pass\n"
    )
    only_mutate = ["abicheck/diff_types.py", "abicheck/diff_symbols.py"]
    assert gate.mutant_run_scope(diff, only_mutate) == ["abicheck.diff_types.*"]


_ONLY_MUTATE_TWO = ["abicheck/diff_types.py", "abicheck/diff_symbols.py"]


def test_diff_touches_outside_only_mutate_detects_a_shared_test_fixture() -> None:
    diff_touching_a_shared_fixture = (
        "diff --git a/abicheck/diff_types.py b/abicheck/diff_types.py\n"
        "--- a/abicheck/diff_types.py\n+++ b/abicheck/diff_types.py\n"
        "@@ -1,0 +2,1 @@\n+    pass\n"
        "diff --git a/tests/conftest.py b/tests/conftest.py\n"
        "--- a/tests/conftest.py\n+++ b/tests/conftest.py\n"
        "@@ -1,0 +2,1 @@\n+    pass\n"
    )
    assert (
        gate.diff_touches_outside_only_mutate(
            diff_touching_a_shared_fixture, _ONLY_MUTATE_TWO
        )
        is True
    )

    diff_touching_only_production = (
        "diff --git a/abicheck/diff_types.py b/abicheck/diff_types.py\n"
        "--- a/abicheck/diff_types.py\n+++ b/abicheck/diff_types.py\n"
        "@@ -1,0 +2,1 @@\n+    pass\n"
    )
    assert (
        gate.diff_touches_outside_only_mutate(
            diff_touching_only_production, _ONLY_MUTATE_TWO
        )
        is False
    )


def test_diff_touches_outside_only_mutate_detects_a_shared_production_helper() -> None:
    """A non-`only_mutate` production module an untouched module imports
    (Codex review, PR #877) — a residual gap an earlier, `tests/`-only
    version of this check missed."""
    diff = (
        "diff --git a/abicheck/diff_types.py b/abicheck/diff_types.py\n"
        "--- a/abicheck/diff_types.py\n+++ b/abicheck/diff_types.py\n"
        "@@ -1,0 +2,1 @@\n+    pass\n"
        "diff --git a/abicheck/model.py b/abicheck/model.py\n"
        "--- a/abicheck/model.py\n+++ b/abicheck/model.py\n"
        "@@ -1,0 +2,1 @@\n+    pass\n"
    )
    assert gate.diff_touches_outside_only_mutate(diff, _ONLY_MUTATE_TWO) is True


def test_diff_touches_outside_only_mutate_detects_a_non_python_fixture_input() -> None:
    """A non-`.py` `also_copy` input (an `examples/**/*.json` fixture, say)
    read as test fixture/oracle data — a second residual gap an earlier,
    Python-only version of this check missed (Codex review, PR #877)."""
    diff = (
        "diff --git a/abicheck/diff_types.py b/abicheck/diff_types.py\n"
        "--- a/abicheck/diff_types.py\n+++ b/abicheck/diff_types.py\n"
        "@@ -1,0 +2,1 @@\n+    pass\n"
        "diff --git a/catalog/ground_truth.json b/catalog/ground_truth.json\n"
        "--- a/catalog/ground_truth.json\n+++ b/catalog/ground_truth.json\n"
        '@@ -1,0 +2,1 @@\n+  "x": 1,\n'
    )
    assert gate.diff_touches_outside_only_mutate(diff, _ONLY_MUTATE_TWO) is True


def test_diff_touches_outside_only_mutate_still_disables_on_a_changelog_fragment() -> (
    None
):
    """No allowlist survives at all now, deliberately — see this predicate's
    own docstring for why a changelog-fragment exemption (an earlier
    revision's allowance) was removed rather than patched a third time."""
    diff = (
        "diff --git a/abicheck/diff_types.py b/abicheck/diff_types.py\n"
        "--- a/abicheck/diff_types.py\n+++ b/abicheck/diff_types.py\n"
        "@@ -1,0 +2,1 @@\n+    pass\n"
        "diff --git a/changelog.d/foo.md b/changelog.d/foo.md\n"
        "--- a/changelog.d/foo.md\n+++ b/changelog.d/foo.md\n"
        "@@ -1,0 +2,1 @@\n+### Fixed\n"
    )
    assert gate.diff_touches_outside_only_mutate(diff, _ONLY_MUTATE_TWO) is True


def test_diff_touches_outside_only_mutate_detects_pyproject_toml() -> None:
    diff = (
        "diff --git a/abicheck/diff_types.py b/abicheck/diff_types.py\n"
        "--- a/abicheck/diff_types.py\n+++ b/abicheck/diff_types.py\n"
        "@@ -1,0 +2,1 @@\n+    pass\n"
        "diff --git a/pyproject.toml b/pyproject.toml\n"
        "--- a/pyproject.toml\n+++ b/pyproject.toml\n"
        "@@ -1,0 +2,1 @@\n+mutate_only_covered_lines = true\n"
    )
    assert gate.diff_touches_outside_only_mutate(diff, _ONLY_MUTATE_TWO) is True


def test_diff_touches_outside_only_mutate_detects_a_binary_file_diff() -> None:
    """A binary-file diff has no `@@` hunk at all — invisible to
    `parse_changed_lines`/`parse_removed_lines` (built on `_hunks()`), which
    the pre-fix predicate relied on exclusively (Codex review, PR #877,
    fourth round on this same check)."""
    diff = (
        "diff --git a/abicheck/diff_types.py b/abicheck/diff_types.py\n"
        "--- a/abicheck/diff_types.py\n+++ b/abicheck/diff_types.py\n"
        "@@ -1,0 +2,1 @@\n+    pass\n"
        "diff --git a/tests/fixtures/blob.bin b/tests/fixtures/blob.bin\n"
        "index abc123..def456 100644\n"
        "Binary files a/tests/fixtures/blob.bin and b/tests/fixtures/blob.bin differ\n"
    )
    assert gate.diff_touches_outside_only_mutate(diff, _ONLY_MUTATE_TWO) is True
    # And the pre-fix hunk-based reading really did miss it — pinning why
    # the fix had to change what feeds the check, not just its allowlist.
    hunk_based_touched = set(gate.parse_changed_lines(diff)) | set(
        gate.parse_removed_lines(diff)
    )
    assert "tests/fixtures/blob.bin" not in hunk_based_touched


def test_diff_touches_outside_only_mutate_detects_a_pure_rename() -> None:
    """A pure rename with no content change has no `@@` hunk either."""
    diff = (
        "diff --git a/abicheck/diff_types.py b/abicheck/diff_types.py\n"
        "--- a/abicheck/diff_types.py\n+++ b/abicheck/diff_types.py\n"
        "@@ -1,0 +2,1 @@\n+    pass\n"
        "diff --git a/tests/old_name.py b/tests/new_name.py\n"
        "similarity index 100%\n"
        "rename from tests/old_name.py\n"
        "rename to tests/new_name.py\n"
    )
    assert gate.diff_touches_outside_only_mutate(diff, _ONLY_MUTATE_TWO) is True


def test_diff_touches_outside_only_mutate_detects_a_mode_only_change() -> None:
    """A mode-only change (e.g. `chmod +x`) has no `@@` hunk either."""
    diff = (
        "diff --git a/abicheck/diff_types.py b/abicheck/diff_types.py\n"
        "--- a/abicheck/diff_types.py\n+++ b/abicheck/diff_types.py\n"
        "@@ -1,0 +2,1 @@\n+    pass\n"
        "diff --git a/scripts/some_script.sh b/scripts/some_script.sh\n"
        "old mode 100644\n"
        "new mode 100755\n"
    )
    assert gate.diff_touches_outside_only_mutate(diff, _ONLY_MUTATE_TWO) is True


def test_diff_touched_paths_reads_both_sides_of_a_diff_git_header() -> None:
    diff = (
        "diff --git a/old_name.py b/new_name.py\n"
        "similarity index 100%\n"
        "rename from old_name.py\n"
        "rename to new_name.py\n"
    )
    assert gate.diff_touched_paths(diff) == {"old_name.py", "new_name.py"}


def test_diff_has_unparseable_git_header_is_false_for_ordinary_diffs() -> None:
    diff = (
        "diff --git a/abicheck/diff_types.py b/abicheck/diff_types.py\n"
        "--- a/abicheck/diff_types.py\n+++ b/abicheck/diff_types.py\n"
        "@@ -1,0 +2,1 @@\n+    pass\n"
        "diff --git a/tests/old_name.py b/tests/new_name.py\n"
        "similarity index 100%\n"
        "rename from tests/old_name.py\n"
        "rename to tests/new_name.py\n"
    )
    assert gate.diff_has_unparseable_git_header(diff) is False


def test_diff_has_unparseable_git_header_detects_a_quoted_path() -> None:
    """Git quotes a non-ASCII/space/special-char path by default
    (`core.quotepath`), emitting ``diff --git "a/..." "b/..."`` — a header
    `_DIFF_GIT_HEADER`'s plain ``a/... b/...`` pattern doesn't match, so the
    quoted path would otherwise silently vanish from `diff_touched_paths`
    (Codex review, PR #877, fifth round on this same check)."""
    diff = 'diff --git "a/tests/fixtures/caf\\303\\251.json" "b/tests/fixtures/caf\\303\\251.json"\n'
    assert gate.diff_has_unparseable_git_header(diff) is True


def test_diff_touches_outside_only_mutate_falls_back_on_a_quoted_path() -> None:
    """End-to-end: a diff editing an in-scope module plus a quoted-path file
    outside it must not be scoped, even though `diff_touched_paths` alone
    can't see the quoted path at all — the header-unparseable guard is what
    catches it, not the touched-paths set."""
    diff = (
        "diff --git a/abicheck/diff_types.py b/abicheck/diff_types.py\n"
        "--- a/abicheck/diff_types.py\n+++ b/abicheck/diff_types.py\n"
        "@@ -1,0 +2,1 @@\n+    pass\n"
        'diff --git "a/tests/fixtures/caf\\303\\251.json" "b/tests/fixtures/caf\\303\\251.json"\n'
        "index abc123..def456 100644\n"
        "Binary files differ\n"
    )
    assert gate.diff_touches_outside_only_mutate(diff, _ONLY_MUTATE_TWO) is True
    # Pin why: the touched-paths reader alone misses the quoted file entirely.
    assert gate.diff_touched_paths(diff) == {"abicheck/diff_types.py"}


def test_diff_lacks_git_headers_for_its_hunks_is_false_for_an_ordinary_diff() -> None:
    diff = (
        "diff --git a/abicheck/diff_types.py b/abicheck/diff_types.py\n"
        "--- a/abicheck/diff_types.py\n+++ b/abicheck/diff_types.py\n"
        "@@ -1,0 +2,1 @@\n+    pass\n"
    )
    assert gate.diff_lacks_git_headers_for_its_hunks(diff) is False


def test_diff_lacks_git_headers_for_its_hunks_is_false_for_no_hunks_at_all() -> None:
    """An empty/no-hunk diff has nothing this predicate needs to guard —
    `mutant_run_scope`'s own no-diff/nothing-touched fallbacks already
    handle it."""
    assert gate.diff_lacks_git_headers_for_its_hunks("") is False


def test_diff_lacks_git_headers_for_its_hunks_detects_a_headerless_diff() -> None:
    """A unified diff with real `---`/`+++`/`@@` hunks but no `diff --git`
    line at all — e.g. from plain `diff -u` rather than `git diff`, or a
    hand-assembled `--diff-file` — still parses fine under `_hunks()`, but
    `diff_touched_paths` (keyed on `diff --git` headers alone) sees nothing
    (Codex review, PR #877, sixth round on this same check)."""
    diff = (
        "--- a/abicheck/diff_types.py\n+++ b/abicheck/diff_types.py\n"
        "@@ -1,0 +2,1 @@\n+    pass\n"
    )
    assert gate.diff_lacks_git_headers_for_its_hunks(diff) is True
    # Pin why: the header-based reader really does see nothing here.
    assert gate.diff_touched_paths(diff) == set()


def test_diff_touches_outside_only_mutate_falls_back_on_a_headerless_diff() -> None:
    """End-to-end: a headerless diff editing an in-scope module plus a
    shared test fixture must not be scoped, even though `diff_touched_paths`
    sees nothing at all in either hunk — the hunk-based reader
    (`diff_touched_only_mutate_modules`) would have silently detected only
    the in-scope module, letting a scoped run through despite the fixture
    change, if this guard didn't fire first."""
    diff = (
        "--- a/abicheck/diff_types.py\n+++ b/abicheck/diff_types.py\n"
        "@@ -1,0 +2,1 @@\n+    pass\n"
        "--- a/tests/conftest.py\n+++ b/tests/conftest.py\n"
        "@@ -1,0 +2,1 @@\n+    pass\n"
    )
    assert gate.diff_touches_outside_only_mutate(diff, _ONLY_MUTATE_TWO) is True
    # Pin why: the header-based reader alone would have missed this entirely.
    assert gate.diff_touched_paths(diff) == set()
    assert gate.diff_touched_only_mutate_modules(diff, _ONLY_MUTATE_TWO) == {
        "abicheck/diff_types.py"
    }


def test_diff_lacks_git_headers_for_its_hunks_detects_a_mixed_diff() -> None:
    """A diff concatenating one ordinary `diff --git`-headed entry with a
    second, headerless unified-diff section (two files pasted together, or
    a hand-assembled `--diff-file`) has a `diff --git` line *somewhere* —
    checking mere presence isn't enough, since the headerless section's own
    file is still invisible to `diff_touched_paths` (Codex review, PR #877,
    seventh round on this same check)."""
    diff = (
        "diff --git a/abicheck/diff_types.py b/abicheck/diff_types.py\n"
        "--- a/abicheck/diff_types.py\n+++ b/abicheck/diff_types.py\n"
        "@@ -1,0 +2,1 @@\n+    pass\n"
        "--- a/tests/conftest.py\n+++ b/tests/conftest.py\n"
        "@@ -1,0 +2,1 @@\n+    pass\n"
    )
    assert gate.diff_lacks_git_headers_for_its_hunks(diff) is True
    # Pin why: a `diff --git` line exists, but doesn't cover the second hunk.
    assert gate.diff_touched_paths(diff) == {"abicheck/diff_types.py"}


def test_diff_touches_outside_only_mutate_falls_back_on_a_mixed_diff() -> None:
    """End-to-end: the mixed-format diff above must not be scoped, even
    though its lone `diff --git` header names only the in-scope module."""
    diff = (
        "diff --git a/abicheck/diff_types.py b/abicheck/diff_types.py\n"
        "--- a/abicheck/diff_types.py\n+++ b/abicheck/diff_types.py\n"
        "@@ -1,0 +2,1 @@\n+    pass\n"
        "--- a/tests/conftest.py\n+++ b/tests/conftest.py\n"
        "@@ -1,0 +2,1 @@\n+    pass\n"
    )
    assert gate.diff_touches_outside_only_mutate(diff, _ONLY_MUTATE_TWO) is True


def test_binary_marker_paths_reads_both_sides_of_a_git_style_marker() -> None:
    diff = "Binary files a/examples/oracle.bin and b/examples/oracle.bin differ\n"
    assert gate.diff_lacks_git_headers_for_its_hunks(diff) is True
    # Pin the extracted paths directly, a/b prefix stripped.
    assert gate._binary_marker_paths(diff) == {"examples/oracle.bin"}


def test_binary_marker_paths_reads_the_plain_diffutils_form() -> None:
    """GNU diffutils' own bare-path form (`diff file1 file2`, no a/b prefix)."""
    diff = "Binary files old.bin and new.bin differ\n"
    assert gate._binary_marker_paths(diff) == {"old.bin", "new.bin"}


def test_diff_lacks_git_headers_for_its_hunks_ignores_a_headed_binary_marker() -> None:
    """An ordinary git binary diff (marker immediately after a real
    `diff --git` header) must not be flagged — the marker's path is already
    covered by the header."""
    diff = (
        "diff --git a/tests/fixtures/blob.bin b/tests/fixtures/blob.bin\n"
        "index abc123..def456 100644\n"
        "Binary files a/tests/fixtures/blob.bin and b/tests/fixtures/blob.bin differ\n"
    )
    assert gate.diff_lacks_git_headers_for_its_hunks(diff) is False


def test_diff_lacks_git_headers_for_its_hunks_detects_a_headerless_binary_marker() -> (
    None
):
    """A diff mixing one properly-headed entry with a headerless GNU-diffutils
    binary marker — reachable via plain `diff`/`diff -u` on binary files,
    not just a hypothetical construction (Codex review, PR #877, eighth
    round on this same check)."""
    diff = (
        "diff --git a/abicheck/diff_types.py b/abicheck/diff_types.py\n"
        "--- a/abicheck/diff_types.py\n+++ b/abicheck/diff_types.py\n"
        "@@ -1,0 +2,1 @@\n+    pass\n"
        "Binary files a/examples/oracle.bin and b/examples/oracle.bin differ\n"
    )
    assert gate.diff_lacks_git_headers_for_its_hunks(diff) is True
    # Pin why: the header-based reader only ever saw the in-scope module.
    assert gate.diff_touched_paths(diff) == {"abicheck/diff_types.py"}


def test_diff_touches_outside_only_mutate_falls_back_on_a_headerless_binary_marker() -> (
    None
):
    diff = (
        "diff --git a/abicheck/diff_types.py b/abicheck/diff_types.py\n"
        "--- a/abicheck/diff_types.py\n+++ b/abicheck/diff_types.py\n"
        "@@ -1,0 +2,1 @@\n+    pass\n"
        "Binary files a/examples/oracle.bin and b/examples/oracle.bin differ\n"
    )
    assert gate.diff_touches_outside_only_mutate(diff, _ONLY_MUTATE_TWO) is True


def test_hunk_file_targets_reads_the_bare_path_form() -> None:
    """Plain `diff -u file1 file2` (and `git diff --no-prefix`) emit
    `--- file.py` / `+++ file.py` with no `a/`/`b/` prefix at all —
    `_hunks()` requires that prefix and yields nothing for this shape."""
    diff = "--- old/file.py\n+++ new/file.py\n@@ -1,0 +2,1 @@\n+    pass\n"
    assert gate._hunk_file_targets(diff) == {"old/file.py", "new/file.py"}
    # Pin why: _hunks() itself really does see nothing here.
    assert list(gate._hunks(diff)) == []


def test_hunk_file_targets_strips_a_gnu_diff_timestamp_suffix() -> None:
    """Real `diff -u` output tab-separates a timestamp after the path."""
    diff = (
        "--- old/file.py\t2024-01-01 10:00:00.000000000 +0000\n"
        "+++ new/file.py\t2024-01-01 10:00:01.000000000 +0000\n"
        "@@ -1,0 +2,1 @@\n+    pass\n"
    )
    assert gate._hunk_file_targets(diff) == {"old/file.py", "new/file.py"}


def test_hunk_file_targets_still_strips_the_a_b_prefix_when_present() -> None:
    diff = (
        "--- a/abicheck/diff_types.py\n+++ b/abicheck/diff_types.py\n"
        "@@ -1,0 +2,1 @@\n+    pass\n"
    )
    assert gate._hunk_file_targets(diff) == {"abicheck/diff_types.py"}


def test_diff_lacks_git_headers_for_its_hunks_detects_a_bare_path_mixed_diff() -> None:
    """A diff mixing one properly-headed entry with a bare-path (no `a/`/
    `b/` prefix) headerless section — real output of plain `diff -u` or
    `git diff --no-prefix`, invisible to `_hunks()` itself regardless of
    whether a `diff --git` header exists anywhere (Codex review, PR #877,
    ninth round on this same check)."""
    diff = (
        "diff --git a/abicheck/diff_types.py b/abicheck/diff_types.py\n"
        "--- a/abicheck/diff_types.py\n+++ b/abicheck/diff_types.py\n"
        "@@ -1,0 +2,1 @@\n+    pass\n"
        "--- tests/conftest.py\n+++ tests/conftest.py\n"
        "@@ -1,0 +2,1 @@\n+    pass\n"
    )
    assert gate.diff_lacks_git_headers_for_its_hunks(diff) is True
    # Pin why: _hunks() alone sees only the a/b-prefixed entry.
    assert {
        p for old, new, _, _ in gate._hunks(diff) for p in (old, new) if p is not None
    } == {"abicheck/diff_types.py"}


def test_diff_touches_outside_only_mutate_falls_back_on_a_bare_path_mixed_diff() -> (
    None
):
    diff = (
        "diff --git a/abicheck/diff_types.py b/abicheck/diff_types.py\n"
        "--- a/abicheck/diff_types.py\n+++ b/abicheck/diff_types.py\n"
        "@@ -1,0 +2,1 @@\n+    pass\n"
        "--- tests/conftest.py\n+++ tests/conftest.py\n"
        "@@ -1,0 +2,1 @@\n+    pass\n"
    )
    assert gate.diff_touches_outside_only_mutate(diff, _ONLY_MUTATE_TWO) is True


def test_only_in_marker_paths_reads_a_git_style_directory() -> None:
    diff = "Only in b/examples: oracle.json\n"
    assert gate._only_in_marker_paths(diff) == {"examples/oracle.json"}


def test_only_in_marker_paths_reads_the_plain_diffutils_form() -> None:
    diff = "Only in examples: oracle.json\n"
    assert gate._only_in_marker_paths(diff) == {"examples/oracle.json"}


def test_diff_lacks_git_headers_for_its_hunks_detects_an_only_in_marker() -> None:
    """`diff -r`/`diff -ur` reports a one-sided file this way — no hunk, no
    binary marker, no `diff --git` header — invisible to every path source
    checked before this one (Codex review, PR #877, tenth round on this
    same check)."""
    diff = (
        "diff --git a/abicheck/diff_types.py b/abicheck/diff_types.py\n"
        "--- a/abicheck/diff_types.py\n+++ b/abicheck/diff_types.py\n"
        "@@ -1,0 +2,1 @@\n+    pass\n"
        "Only in b/examples: oracle.json\n"
    )
    assert gate.diff_lacks_git_headers_for_its_hunks(diff) is True
    # Pin why: the header-based reader only ever saw the in-scope module.
    assert gate.diff_touched_paths(diff) == {"abicheck/diff_types.py"}


def test_diff_touches_outside_only_mutate_falls_back_on_an_only_in_marker() -> None:
    diff = (
        "diff --git a/abicheck/diff_types.py b/abicheck/diff_types.py\n"
        "--- a/abicheck/diff_types.py\n+++ b/abicheck/diff_types.py\n"
        "@@ -1,0 +2,1 @@\n+    pass\n"
        "Only in b/examples: oracle.json\n"
    )
    assert gate.diff_touches_outside_only_mutate(diff, _ONLY_MUTATE_TWO) is True


def test_diff_has_unrecognized_content_is_false_for_ordinary_diffs() -> None:
    diff = (
        "diff --git a/abicheck/diff_types.py b/abicheck/diff_types.py\n"
        "--- a/abicheck/diff_types.py\n+++ b/abicheck/diff_types.py\n"
        "@@ -1,0 +2,1 @@\n+    pass\n"
    )
    assert gate.diff_has_unrecognized_content(diff) is False


def test_diff_has_unrecognized_content_is_false_for_rename_and_mode_changes() -> None:
    """A rename/mode-only entry has no hunk at all — every line must still
    be recognized as git's own per-entry metadata, not flagged as unknown."""
    rename = (
        "diff --git a/tests/old_name.py b/tests/new_name.py\n"
        "similarity index 100%\n"
        "rename from tests/old_name.py\n"
        "rename to tests/new_name.py\n"
    )
    mode = (
        "diff --git a/scripts/some_script.sh b/scripts/some_script.sh\n"
        "old mode 100644\nnew mode 100755\n"
    )
    assert gate.diff_has_unrecognized_content(rename) is False
    assert gate.diff_has_unrecognized_content(mode) is False


def test_diff_has_unrecognized_content_is_false_for_a_headed_binary_diff() -> None:
    diff = (
        "diff --git a/tests/fixtures/blob.bin b/tests/fixtures/blob.bin\n"
        "index abc123..def456 100644\n"
        "Binary files a/tests/fixtures/blob.bin and b/tests/fixtures/blob.bin differ\n"
    )
    assert gate.diff_has_unrecognized_content(diff) is False


def test_diff_has_unrecognized_content_detects_a_brief_diff_marker() -> None:
    """GNU diffutils' `-q`/`--brief` mode reports a differing file as `Files
    X and Y differ` — no hunk, no `Binary files` marker, no `diff --git`
    header, and (unlike the binary/`Only in` markers) no dedicated path
    extractor anywhere in this module, so it must be caught by this
    function's general fallback rather than recognized as safe (Codex
    review, PR #877, eleventh round on this same predicate)."""
    diff = "Files a/examples/oracle.json and b/examples/oracle.json differ\n"
    assert gate.diff_has_unrecognized_content(diff) is True


def test_diff_lacks_git_headers_for_its_hunks_detects_a_brief_diff_marker() -> None:
    diff = (
        "diff --git a/abicheck/diff_types.py b/abicheck/diff_types.py\n"
        "--- a/abicheck/diff_types.py\n+++ b/abicheck/diff_types.py\n"
        "@@ -1,0 +2,1 @@\n+    pass\n"
        "Files a/examples/oracle.json and b/examples/oracle.json differ\n"
    )
    assert gate.diff_lacks_git_headers_for_its_hunks(diff) is True


def test_diff_touches_outside_only_mutate_falls_back_on_a_brief_diff_marker() -> None:
    diff = (
        "diff --git a/abicheck/diff_types.py b/abicheck/diff_types.py\n"
        "--- a/abicheck/diff_types.py\n+++ b/abicheck/diff_types.py\n"
        "@@ -1,0 +2,1 @@\n+    pass\n"
        "Files a/examples/oracle.json and b/examples/oracle.json differ\n"
    )
    assert gate.diff_touches_outside_only_mutate(diff, _ONLY_MUTATE_TWO) is True


def test_diff_has_unrecognized_content_catches_a_hypothetical_future_marker() -> None:
    """The whole point of the general fallback: a diff-tool output shape no
    round of review has reported yet still disables scoping, with no new
    code needed to recognize it specifically."""
    diff = (
        "diff --git a/abicheck/diff_types.py b/abicheck/diff_types.py\n"
        "--- a/abicheck/diff_types.py\n+++ b/abicheck/diff_types.py\n"
        "@@ -1,0 +2,1 @@\n+    pass\n"
        "Some entirely made-up marker nobody has reported yet: examples/x.json\n"
    )
    assert gate.diff_has_unrecognized_content(diff) is True
    assert gate.diff_touches_outside_only_mutate(diff, _ONLY_MUTATE_TWO) is True


def test_diff_has_unrecognized_content_requires_a_header_before_entry_metadata() -> (
    None
):
    """Git entry metadata (`rename from`/`rename to`, `old mode`/`new mode`,
    `index `, ...) carries no path extractor anywhere in this module — the
    owning `diff --git` header already names the path. Recognizing a
    metadata-shaped line *regardless of parser state* (the first version of
    this function) left a headerless `rename from`/`rename to` pair pasted
    after an already-open, properly-headed hunk invisible to both this
    function and the path-set comparison at once — the exact
    "recognized but never path-extracted" mistake this function's own
    docstring warns against, reproduced by this function's own first draft
    (Codex review, PR #877, thirteenth round on this same predicate)."""
    diff = (
        "diff --git a/abicheck/diff_types.py b/abicheck/diff_types.py\n"
        "--- a/abicheck/diff_types.py\n+++ b/abicheck/diff_types.py\n"
        "@@ -1,0 +2,1 @@\n+    pass\n"
        "rename from tests/shared_fixture.py\n"
        "rename to tests/renamed_fixture.py\n"
    )
    assert gate.diff_has_unrecognized_content(diff) is True


def test_diff_touches_outside_only_mutate_falls_back_on_a_headerless_rename() -> None:
    diff = (
        "diff --git a/abicheck/diff_types.py b/abicheck/diff_types.py\n"
        "--- a/abicheck/diff_types.py\n+++ b/abicheck/diff_types.py\n"
        "@@ -1,0 +2,1 @@\n+    pass\n"
        "rename from tests/shared_fixture.py\n"
        "rename to tests/renamed_fixture.py\n"
    )
    assert gate.diff_touches_outside_only_mutate(diff, _ONLY_MUTATE_TWO) is True


def test_diff_has_unrecognized_content_still_accepts_a_real_headed_rename() -> None:
    """A legitimate git rename (metadata immediately after its own
    `diff --git` header, no content hunk) must still pass — the fix is
    about *state*, not about rejecting rename metadata outright."""
    diff = (
        "diff --git a/tests/old_name.py b/tests/new_name.py\n"
        "similarity index 100%\n"
        "rename from tests/old_name.py\n"
        "rename to tests/new_name.py\n"
    )
    assert gate.diff_has_unrecognized_content(diff) is False


def test_mutant_run_scope_is_none_when_a_shared_test_fixture_is_touched() -> None:
    """The scenario `--require-baseline` alone cannot rule out: a production
    module and a *shared* test fixture both change, but the fixture doesn't
    match any of mutation.yml's per-module `mutated_tests` globs, so
    `require_baseline` stays false — scoping must refuse on its own rather
    than trust that upstream signal here (CodeRabbit review, PR #877)."""
    diff = (
        "diff --git a/abicheck/diff_types.py b/abicheck/diff_types.py\n"
        "--- a/abicheck/diff_types.py\n+++ b/abicheck/diff_types.py\n"
        "@@ -1,0 +2,1 @@\n+    pass\n"
        "diff --git a/tests/conftest.py b/tests/conftest.py\n"
        "--- a/tests/conftest.py\n+++ b/tests/conftest.py\n"
        "@@ -1,0 +2,1 @@\n+    pass\n"
    )
    only_mutate = ["abicheck/diff_types.py", "abicheck/diff_symbols.py"]
    assert gate.mutant_run_scope(diff, only_mutate) is None


def test_run_mode_falls_back_to_full_run_when_a_test_file_is_also_touched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end version of the shared-fixture case above, through main()."""
    (tmp_path / "abicheck").mkdir()
    (tmp_path / "abicheck" / "diff_types.py").write_text(_SOURCE, encoding="utf-8")
    _pyproject_with_only_mutate(
        tmp_path, ["abicheck/diff_types.py", "abicheck/diff_symbols.py"]
    )
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(gate.shutil, "which", lambda name: "/usr/bin/mutmut")
    diff = _write(
        tmp_path,
        "d.diff",
        _DIFF + "diff --git a/tests/conftest.py b/tests/conftest.py\n"
        "--- a/tests/conftest.py\n+++ b/tests/conftest.py\n"
        "@@ -1,0 +2,1 @@\n+    pass\n",
    )

    seen_cmds: list[list[str]] = []

    def fake_run_mutmut(cmd: list[str]) -> tuple[str, int]:
        seen_cmds.append(cmd)
        if cmd[:2] == ["mutmut", "run"]:
            return "1/1  🎉 1  🙁 0", 0
        return "    abicheck.diff_types.x_alpha__mutmut_1: killed\n", 0

    monkeypatch.setattr(gate, "_run_mutmut", fake_run_mutmut)
    rc = gate.main(
        ["--run", "--diff-scoped", "--scope-run-to-diff", "--diff-file", diff]
    )
    assert rc == 0
    assert seen_cmds[0] == ["mutmut", "run"]


def test_run_mode_passes_the_scope_patterns_to_mutmut_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--scope-run-to-diff`` reaches the actual `mutmut run` argv."""
    (tmp_path / "abicheck").mkdir()
    (tmp_path / "abicheck" / "diff_types.py").write_text(_SOURCE, encoding="utf-8")
    _pyproject_with_only_mutate(
        tmp_path, ["abicheck/diff_types.py", "abicheck/diff_symbols.py"]
    )
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(gate.shutil, "which", lambda name: "/usr/bin/mutmut")
    diff = _write(tmp_path, "d.diff", _DIFF)

    seen_cmds: list[list[str]] = []

    def fake_run_mutmut(cmd: list[str]) -> tuple[str, int]:
        seen_cmds.append(cmd)
        if cmd[:2] == ["mutmut", "run"]:
            return "1/1  🎉 1  🙁 0", 0
        return "    abicheck.diff_types.x_alpha__mutmut_1: killed\n", 0

    monkeypatch.setattr(gate, "_run_mutmut", fake_run_mutmut)
    rc = gate.main(
        [
            "--run",
            "--diff-scoped",
            "--scope-run-to-diff",
            "--diff-file",
            diff,
        ]
    )
    assert rc == 0
    run_cmd = seen_cmds[0]
    assert run_cmd[:2] == ["mutmut", "run"]
    assert run_cmd[2:] == ["abicheck.diff_types.*"]


def test_run_mode_scoped_run_does_not_fail_on_out_of_scope_unresolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The dominant case this feature exists for: everything outside the
    touched module reads "not checked" (never test-executed), which must not
    be gated as an unresolved measurement — only what was actually in scope."""
    (tmp_path / "abicheck").mkdir()
    (tmp_path / "abicheck" / "diff_types.py").write_text(_SOURCE, encoding="utf-8")
    _pyproject_with_only_mutate(
        tmp_path, ["abicheck/diff_types.py", "abicheck/diff_symbols.py"]
    )
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(gate.shutil, "which", lambda name: "/usr/bin/mutmut")
    diff = _write(tmp_path, "d.diff", _DIFF)

    def fake_run_mutmut(cmd: list[str]) -> tuple[str, int]:
        if cmd[:2] == ["mutmut", "run"]:
            return "2/2  🎉 1  🙁 0  🫥 0  ⏰ 0  🤔 0", 0
        # In scope: killed. Out of scope (diff_symbols.py, never touched by
        # this diff): "not checked" — the real status an untested mutant gets.
        return (
            "    abicheck.diff_types.x_alpha__mutmut_1: killed\n"
            "    abicheck.diff_symbols.x_beta__mutmut_1: not checked\n"
        ), 0

    monkeypatch.setattr(gate, "_run_mutmut", fake_run_mutmut)
    monkeypatch.setattr(
        gate,
        "load_cicd_stats",
        lambda _dir: {"total": 2, "survived": 0, "killed": 1, "not_checked": 1},
    )
    rc = gate.main(
        ["--run", "--diff-scoped", "--scope-run-to-diff", "--diff-file", diff]
    )
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "did not resolve" not in out


def test_run_mode_unscoped_run_still_fails_on_unresolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative control for the test above: without --scope-run-to-diff, an
    unresolved mutant anywhere still fails a diff-scoped run, exactly as
    before this feature existed."""
    (tmp_path / "abicheck").mkdir()
    (tmp_path / "abicheck" / "diff_types.py").write_text(_SOURCE, encoding="utf-8")
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(gate.shutil, "which", lambda name: "/usr/bin/mutmut")
    diff = _write(tmp_path, "d.diff", _DIFF)
    monkeypatch.setattr(
        gate,
        "_run_mutmut",
        lambda cmd: (
            ("2/2  🎉 1  🙁 0  🫥 0  ⏰ 1  🤔 0", 0)
            if cmd[:2] == ["mutmut", "run"]
            else (
                "    abicheck.diff_types.x_alpha__mutmut_1: killed\n"
                "    abicheck.diff_symbols.x_beta__mutmut_1: timeout\n",
                0,
            )
        ),
    )
    rc = gate.main(["--run", "--diff-scoped", "--diff-file", diff])
    assert rc == 1


def test_write_baseline_never_scopes_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--write-baseline must always see the full population, never a subset."""
    (tmp_path / "abicheck").mkdir()
    (tmp_path / "abicheck" / "diff_types.py").write_text(_SOURCE, encoding="utf-8")
    _pyproject_with_only_mutate(tmp_path, ["abicheck/diff_types.py"])
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(gate.shutil, "which", lambda name: "/usr/bin/mutmut")
    diff = _write(tmp_path, "d.diff", _DIFF)

    seen_cmds: list[list[str]] = []

    def fake_run_mutmut(cmd: list[str]) -> tuple[str, int]:
        seen_cmds.append(cmd)
        if cmd[:2] == ["mutmut", "run"]:
            return "1/1  🎉 1  🙁 0", 0
        return "    abicheck.diff_types.x_alpha__mutmut_1: killed\n", 0

    monkeypatch.setattr(gate, "_run_mutmut", fake_run_mutmut)
    monkeypatch.setattr(
        gate, "load_cicd_stats", lambda _dir: {"total": 1, "survived": 0}
    )
    rc = gate.main(
        [
            "--run",
            "--diff-scoped",
            "--scope-run-to-diff",
            "--diff-file",
            diff,
            "--write-baseline",
            "--baseline-file",
            str(tmp_path / "baseline.json"),
        ]
    )
    assert rc == 0
    assert seen_cmds[0] == ["mutmut", "run"]


def test_write_baseline_never_loads_the_diff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--write-baseline (with --diff-scoped also given) must not fail on a
    diff it never actually consumes: that branch returns before the
    diff-scoped gate runs, and run-scoping is separately disabled under
    --write-baseline already — so nothing downstream ever reads diff_text in
    this mode. Point --diff-file at a path that does not exist at all: if the
    diff were still being loaded, this would raise (Codex review)."""
    (tmp_path / "abicheck").mkdir()
    (tmp_path / "abicheck" / "diff_types.py").write_text(_SOURCE, encoding="utf-8")
    _pyproject_with_only_mutate(tmp_path, ["abicheck/diff_types.py"])
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(gate.shutil, "which", lambda name: "/usr/bin/mutmut")
    monkeypatch.setattr(
        gate,
        "_run_mutmut",
        lambda cmd: (
            ("1/1  🎉 1  🙁 0", 0)
            if cmd[:2] == ["mutmut", "run"]
            else ("    abicheck.diff_types.x_alpha__mutmut_1: killed\n", 0)
        ),
    )
    monkeypatch.setattr(
        gate, "load_cicd_stats", lambda _dir: {"total": 1, "survived": 0}
    )
    rc = gate.main(
        [
            "--run",
            "--diff-scoped",
            "--diff-file",
            str(tmp_path / "does-not-exist.diff"),
            "--write-baseline",
            "--baseline-file",
            str(tmp_path / "baseline.json"),
        ]
    )
    assert rc == 0
    assert (tmp_path / "baseline.json").exists()


def test_receipt_records_run_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "abicheck").mkdir()
    (tmp_path / "abicheck" / "diff_types.py").write_text(_SOURCE, encoding="utf-8")
    _pyproject_with_only_mutate(
        tmp_path, ["abicheck/diff_types.py", "abicheck/diff_symbols.py"]
    )
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(gate.shutil, "which", lambda name: "/usr/bin/mutmut")
    diff = _write(tmp_path, "d.diff", _DIFF)
    monkeypatch.setattr(
        gate,
        "_run_mutmut",
        lambda cmd: (
            ("1/1  🎉 1  🙁 0", 0)
            if cmd[:2] == ["mutmut", "run"]
            else ("    abicheck.diff_types.x_alpha__mutmut_1: killed\n", 0)
        ),
    )
    monkeypatch.setattr(
        gate, "load_cicd_stats", lambda _dir: {"total": 1, "survived": 0}
    )
    receipt = tmp_path / "receipt.json"
    rc = gate.main(
        [
            "--run",
            "--diff-scoped",
            "--scope-run-to-diff",
            "--diff-file",
            diff,
            "--json",
            str(receipt),
        ]
    )
    assert rc == 0
    doc = json.loads(receipt.read_text())
    assert doc["run_scope"]["mode"] == "diff"
    assert doc["run_scope"]["modules"] == ["abicheck/diff_types.py"]
    assert doc["run_scope"]["requested"] is True


def test_scoping_never_applies_over_a_saved_results_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--run --results-file ... --diff-scoped --scope-run-to-diff`: `_gather()`
    checks `--results-file` before `--run` and returns those saved results
    unconditionally (a pre-existing quirk, unrelated to this feature) — so
    `--run` being set does not mean mutmut is about to be re-executed
    scoped. Nothing here proves the saved file reflects a scoped run, so an
    out-of-scope unresolved record in it must still fail the gate exactly as
    it would without --scope-run-to-diff (Codex review, PR #877)."""
    (tmp_path / "abicheck").mkdir()
    (tmp_path / "abicheck" / "diff_types.py").write_text(_SOURCE, encoding="utf-8")
    _pyproject_with_only_mutate(
        tmp_path, ["abicheck/diff_types.py", "abicheck/diff_symbols.py"]
    )
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
    diff = _write(tmp_path, "d.diff", _DIFF)
    results = _write(
        tmp_path,
        "r.txt",
        "    abicheck.diff_types.x_alpha__mutmut_1: killed\n"
        # Out of scope (diff_symbols.py, never touched by this diff) *and*
        # genuinely unresolved — must not be exempted just because
        # --scope-run-to-diff was passed.
        "    abicheck.diff_symbols.x_beta__mutmut_1: timeout\n",
    )
    monkeypatch.setattr(
        gate,
        "load_cicd_stats",
        lambda _dir: {"total": 2, "survived": 0, "killed": 1, "timeout": 1},
    )
    rc = gate.main(
        [
            "--run",
            "--results-file",
            results,
            "--diff-scoped",
            "--scope-run-to-diff",
            "--diff-file",
            diff,
        ]
    )
    assert rc == 1


def test_run_seconds_is_none_for_a_saved_results_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--run --results-file ...`: `_gather()` returns the saved file without
    ever invoking mutmut, so `run_seconds`/`mutants_per_second` must read
    `None` rather than the near-zero file-read duration and the implausible
    rate derived from it — a receipt consumer (a future budget/trend tool)
    would otherwise mistake an offline replay for a fast live run (Codex
    review, PR #877, sixth round)."""
    (tmp_path / "abicheck").mkdir()
    (tmp_path / "abicheck" / "diff_types.py").write_text(_SOURCE, encoding="utf-8")
    _pyproject_with_only_mutate(tmp_path, ["abicheck/diff_types.py"])
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
    results = _write(
        tmp_path,
        "r.txt",
        "    abicheck.diff_types.x_alpha__mutmut_1: killed\n",
    )
    monkeypatch.setattr(
        gate, "load_cicd_stats", lambda _dir: {"total": 1, "survived": 0, "killed": 1}
    )
    receipt = tmp_path / "receipt.json"
    rc = gate.main(
        [
            "--run",
            "--results-file",
            results,
            "--json",
            str(receipt),
        ]
    )
    assert rc == 0
    doc = json.loads(receipt.read_text())
    assert doc["run_seconds"] is None
    assert doc["mutants_per_second"] is None
    # `load_cicd_stats` above is mocked to return a real, non-None dict — the
    # same shape a stale/unrelated mutants/mutmut-cicd-stats.json left over
    # from another run would produce, since --results-file's own results
    # text has no relationship to whatever happens to be in args.mutants_dir.
    # `mutants_measured` must not be derived from it either (Codex review,
    # PR #877, sixteenth round).
    assert doc["mutants_measured"] is None


def test_run_scope_mode_is_unknown_for_a_saved_results_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--run --results-file ...` never scopes (see the sixth-round test
    above), so `scope_modules` stays empty and the pre-fix code labeled the
    receipt's `run_scope.mode` "full" — asserting the saved measurement was
    a live full-population run, which this invocation has no way to know:
    the results file itself carries no record of whether *it* was produced
    by a scoped run. Must read "unknown" instead (Codex review, PR #877,
    eleventh round)."""
    (tmp_path / "abicheck").mkdir()
    (tmp_path / "abicheck" / "diff_types.py").write_text(_SOURCE, encoding="utf-8")
    _pyproject_with_only_mutate(tmp_path, ["abicheck/diff_types.py"])
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
    results = _write(
        tmp_path,
        "r.txt",
        "    abicheck.diff_types.x_alpha__mutmut_1: killed\n",
    )
    monkeypatch.setattr(
        gate, "load_cicd_stats", lambda _dir: {"total": 1, "survived": 0, "killed": 1}
    )
    receipt = tmp_path / "receipt.json"
    rc = gate.main(
        [
            "--run",
            "--results-file",
            results,
            "--json",
            str(receipt),
        ]
    )
    assert rc == 0
    doc = json.loads(receipt.read_text())
    assert doc["run_scope"]["mode"] == "unknown"


def test_run_scope_mode_is_unknown_for_a_bare_database_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Neither `--run` nor `--results-file`: `_gather()` reads the existing
    mutmut database as-is (the `mutmut results` path) without executing
    anything itself. That database may have been left by an earlier
    *scoped* run, so labeling this receipt's `run_scope.mode` "full" would
    be the identical unverifiable assertion the `--results-file` fix above
    already closed for a different input shape (Codex review, PR #877,
    twelfth round)."""
    (tmp_path / "abicheck").mkdir()
    (tmp_path / "abicheck" / "diff_types.py").write_text(_SOURCE, encoding="utf-8")
    _pyproject_with_only_mutate(tmp_path, ["abicheck/diff_types.py"])
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(gate.shutil, "which", lambda name: "/usr/bin/mutmut")
    monkeypatch.setattr(
        gate,
        "_run_mutmut",
        lambda cmd: ("    abicheck.diff_types.x_alpha__mutmut_1: killed\n", 0),
    )
    monkeypatch.setattr(
        gate, "load_cicd_stats", lambda _dir: {"total": 1, "survived": 0, "killed": 1}
    )
    receipt = tmp_path / "receipt.json"
    rc = gate.main(["--json", str(receipt)])
    assert rc == 0
    doc = json.loads(receipt.read_text())
    assert doc["run_scope"]["mode"] == "unknown"
    assert doc["run_seconds"] is None
    # `load_cicd_stats` above is mocked to return a real, non-None dict — a
    # bare database read must not publish mutants_measured from it either:
    # the local mutmut database it reflects could be arbitrarily old or
    # itself the product of an earlier scoped run, and this invocation
    # never executed anything to measure (Codex review, PR #877,
    # seventeenth round).
    assert doc["mutants_measured"] is None
