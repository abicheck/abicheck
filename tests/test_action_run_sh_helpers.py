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

"""Behavioral tests for ``action/run.sh``'s multi-value input splitting (P2.2).

``run.sh`` runs the actual ``abicheck`` invocation at the bottom of the file
(reading ``INPUT_*`` env vars and exiting with the tool's exit code), so it
cannot be sourced wholesale in a unit test. Instead this extracts just the
helper-function region (``_split_multi_value``/``add_flag``/``add_sided_flag``/
``add_single_flag``, everything before the "Build the abicheck command"
marker) and sources *that* alongside a small harness — the same "parse the
real file, don't hand-copy it" discipline as ``test_action_run_contract.py``,
so a future edit to the real functions is exercised here too, not a stale copy.

``add_flag``/``add_sided_flag`` used unquoted ``for item in $value`` word-
splitting, which explicitly could not support a path containing a space (a
Codex/report finding, P2.2). The fix prefers newline-separated items (a YAML
block-scalar Action input, e.g. ``headers: |``), which preserves embedded
spaces, and falls back to legacy whitespace-splitting only for a single-line
value (the documented back-compat form).

``TestAddFlagHostileScalarCorpus`` below closes a second, more severe
instance of that same unquoted-expansion class (bug-class-regression-
testing.md Phase 8, Codex review PR #919): unquoted ``for item in $value``
performs pathname expansion (globbing) as well as word-splitting, so a
caller-controlled single-line value of exactly ``"*"`` silently expanded to
every file in the runner's own working directory instead of staying
literal -- confirmed by direct execution before the fix in ``action/run.sh``
(``_split_legacy_value``'s ``set -f``). This module's own hostile corpus is
shared with the workflow-execution harness's (``tests/_workflow_exec.py``'s
``HOSTILE_SCALAR_CORPUS``) rather than kept as a second, independently-
drifting copy.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from _workflow_exec import HOSTILE_SCALAR_CORPUS, bash_executable, require_bash

RUN_SH = Path(__file__).resolve().parents[1] / "action" / "run.sh"
_MARKER = "# Build the abicheck command"


def _helpers_region() -> str:
    """The function-definitions header of run.sh, up to the assembly marker."""
    text = RUN_SH.read_text(encoding="utf-8")
    idx = text.index(_MARKER)
    return text[:idx]


def _cli_introspection_prelude(py_bin: str | None = None) -> str:
    """Shell establishing the three variables `_cli_value_options_init` needs.

    `_helpers_region()` stops at run.sh's "Build the abicheck command" marker,
    which is *before* the real script resolves `$_PY_BIN`, creates
    `$_PY_SAFE_DIR` and sets `$_PY_BIN_HAS_ABICHECK`. Since ADR-070 D3 replaced
    the hand-maintained value-option `case` list with a live query against the
    installed CLI, a harness that omits those three would silently exercise
    `_cli_value_options_init`'s *fallback* path -- where nothing is
    value-taking -- so every tokenizer test would assert the behaviour of the
    degraded mode while appearing to test the real one. That is precisely the
    "test goes through a shortcut into the dependency" anti-pattern root
    `AGENTS.md` warns about, so the harness supplies them instead.

    Uses this interpreter (`sys.executable`), which is by construction the one
    with abicheck importable when the test suite is running at all.

    *py_bin* overrides that interpreter. Its one use is
    ``TestDerivedOptionTableIsLineEndingAgnostic`` below, which needs an
    interpreter whose stdout line endings differ from this platform's.
    """
    return (
        f"\n_PY_BIN={shlex.quote(py_bin or sys.executable)}\n"
        '_PY_SAFE_DIR="$(mktemp -d)"\n'
        "_PY_BIN_HAS_ABICHECK=true\n"
        "trap 'rm -rf \"$_PY_SAFE_DIR\"' EXIT\n"
    )


def _run_harness(harness: str, *, cwd: Path | None = None) -> str:
    """Source the real helper functions + *harness*, return CMD joined by NUL.

    Writes the assembled script to a real file (UTF-8, explicit ``\\n`` line
    endings) and runs ``bash <path>`` rather than ``bash -c <string>``: passing
    a script containing non-ASCII characters (run.sh's comments use em-dashes)
    as a subprocess argv string hits Windows console/argv-encoding mangling
    and was flaky under macOS's stock bash 3.2 (exit 127) — a file sidesteps
    both, and matches how run.sh is actually invoked in production.

    ``cwd`` lets a caller control the working directory the harness runs in
    — needed to prove a value like ``"*"`` stays literal regardless of what
    files happen to exist there, rather than relying on whatever the pytest
    process's own cwd contains.

    Two byte-fidelity fixes over an earlier revision of this helper (Codex
    review, PR #919, fresh evidence -- found by actually deriving an
    independent expected-argv oracle for the hostile corpus and discovering
    two cases where the *harness itself*, not add_flag()/add_sided_flag(),
    silently altered what the test observed):

    - The item separator was ``\\x1f`` (unit separator), which collides with
      ``HOSTILE_SCALAR_CORPUS``'s own ``"unit-separator"`` entry -- a CMD
      item genuinely containing that byte was indistinguishable from a
      record boundary, truncating the observed item at the embedded byte.
      NUL (``\\0``) cannot appear in a bash string at all (the C string ABI
      bash variables are built on has no representation for it), so it is
      the only byte no corpus value could ever collide with.
    - ``subprocess.run(..., text=True)`` decodes stdout via a universal-
      newlines text wrapper, which silently rewrites a lone ``\\r`` (the
      corpus's own ``"carriage-return"`` entry) to ``\\n`` before the test
      ever sees it. Capturing raw bytes and decoding them directly (no
      ``text=True``) preserves every byte exactly.
    """
    require_bash()
    script = (
        _helpers_region()
        + _cli_introspection_prelude()
        + "\nCMD=()\n"
        + harness
        # ${CMD[@]+"${CMD[@]}"} (not plain "${CMD[@]}"): pre-4.4 bash — macOS's
        # stock 3.2 included — treats an empty array subscripted with [@]
        # under `set -u` as an unbound-variable error and aborts the script
        # (the same bug run.sh itself works around at its PR-comment loop).
        + "\nprintf '%s\\0' ${CMD[@]+\"${CMD[@]}\"}\n"
    )
    with tempfile.NamedTemporaryFile(
        "w",
        suffix=".sh",
        delete=False,
        encoding="utf-8",
        newline="\n",
    ) as f:
        f.write(script)
        script_path = f.name
    try:
        result = subprocess.run(
            [bash_executable(), script_path],
            capture_output=True,
            cwd=str(cwd) if cwd is not None else None,
        )
    finally:
        os.unlink(script_path)
    if result.returncode != 0:
        raise AssertionError(
            f"harness script failed (exit {result.returncode})\n"
            f"--- stdout ---\n{result.stdout!r}\n"
            f"--- stderr ---\n{result.stderr!r}"
        )
    return result.stdout.decode("utf-8")


def _cmd_items(stdout: str) -> list[str]:
    return [item for item in stdout.split("\0") if item]


def _expected_legacy_split_items(value: str) -> list[str]:
    """Independently derive what add_flag()'s/add_sided_flag()'s legacy
    single-line path SHOULD produce for *value*, without calling into
    real.sh at all -- so a test comparing the real output against this
    can't pass merely because both sides share the same (possibly buggy)
    formula (Codex review, PR #919, fresh evidence: an earlier revision of
    this test only checked that a decoy filename was absent, which would
    still pass if add_flag() dropped, mutated, or reordered a value).

    Reproduces bash's *default-IFS* (``<space><tab><newline>``) word-
    splitting exactly -- not Python's ``str.split()``, which also treats
    ``\\r`` and other whitespace bash's default IFS does not as a
    separator (verified against real bash: a lone ``\\r`` with no
    space/tab/newline present does NOT split). A value containing an
    embedded newline never reaches this path at all -- add_flag() routes
    it through the newline-preserving branch instead, one line per item.
    """
    if "\n" in value:
        return [line for line in value.split("\n") if line != ""]
    return re.findall(r"[^ \t\n]+", value)


def _bash_ansi_c_quote(value: str) -> str:
    """Render *value* as a bash ``$'...'`` (ANSI-C quoted) literal, safe for
    any byte ``HOSTILE_SCALAR_CORPUS`` carries -- backslash and single-quote
    are escaped, and every control character is rendered as ``\\xHH`` so the
    resulting literal is unambiguous regardless of the corpus entry."""
    out = []
    for ch in value:
        if ch == "\\":
            out.append("\\\\")
        elif ch == "'":
            out.append("\\'")
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\x{ord(ch):02x}")
        else:
            out.append(ch)
    return "$'" + "".join(out) + "'"


def _run_predicate(call: str) -> bool:
    """Source the real helper functions and evaluate a boolean-returning call
    (e.g. an ``_is_release_style_operand "path"`` invocation), returning
    whether it exited zero (true) or non-zero (false)."""
    require_bash()
    script = (
        _helpers_region()
        + _cli_introspection_prelude()
        + f"\nif {call}; then exit 0; else exit 1; fi\n"
    )
    with tempfile.NamedTemporaryFile(
        "w",
        suffix=".sh",
        delete=False,
        encoding="utf-8",
        newline="\n",
    ) as f:
        f.write(script)
        script_path = f.name
    try:
        result = subprocess.run(
            [bash_executable(), script_path],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    finally:
        os.unlink(script_path)
    return result.returncode == 0


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestAddFlagSplitting:
    def test_legacy_space_separated_single_line(self) -> None:
        # Back-compat: the documented single-line "space-separated" form.
        out = _run_harness('add_flag "-H" "inc/a inc/b"')
        assert _cmd_items(out) == ["-H", "inc/a", "-H", "inc/b"]

    def test_newline_separated_preserves_spaces(self) -> None:
        # A YAML block scalar (`headers: |`) input — one path per line,
        # including a path containing a space.
        out = _run_harness("add_flag \"-H\" $'inc/a\\npath with spaces/inc\\ninc/c'")
        assert _cmd_items(out) == [
            "-H",
            "inc/a",
            "-H",
            "path with spaces/inc",
            "-H",
            "inc/c",
        ]

    def test_empty_value_adds_nothing(self) -> None:
        out = _run_harness('add_flag "-H" ""')
        assert _cmd_items(out) == []

    def test_single_value_no_separator(self) -> None:
        out = _run_harness('add_flag "-H" "inc/only"')
        assert _cmd_items(out) == ["-H", "inc/only"]


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestAddFlagHostileScalarCorpus:
    """``add_flag``'s legacy single-line path against the shared hostile
    corpus (bug-class-regression-testing.md Phase 8, Codex review PR #919).

    Run with real decoy files present in the harness's own working
    directory, so an unfixed regression shows up as an extra CMD entry
    (the decoy's filename), not merely as a passing test that never
    actually exercised the vulnerable condition.
    """

    def test_a_glob_value_stays_literal(self, tmp_path) -> None:
        """Direct regression pin for the fix: the legacy path's unquoted
        ``for item in $value`` performed pathname expansion as well as
        word-splitting, so a value of exactly ``"*"`` silently expanded to
        every file in the runner's own working directory instead of
        staying literal -- confirmed via direct execution against this
        exact scenario before the fix."""
        (tmp_path / "decoy_one.txt").write_text("x")
        (tmp_path / "decoy_two.txt").write_text("x")
        out = _run_harness('add_flag "-H" "*"', cwd=tmp_path)
        assert _cmd_items(out) == ["-H", "*"]

    @pytest.mark.parametrize("value", HOSTILE_SCALAR_CORPUS)
    def test_argv_exactly_matches_the_independent_oracle(
        self, tmp_path, value: str
    ) -> None:
        """Compares the *complete* captured CMD against an independently
        derived expectation (Codex review, PR #919, fresh evidence: an
        earlier revision only checked that a planted decoy filename was
        absent, which would still pass if add_flag() dropped, mutated,
        reordered, or added extra items for a non-glob value)."""
        (tmp_path / "decoy_one.txt").write_text("x")
        (tmp_path / "decoy_two.txt").write_text("x")
        literal = _bash_ansi_c_quote(value)
        out = _run_harness(f'add_flag "-H" {literal}', cwd=tmp_path)
        items = _cmd_items(out)
        expected: list[str] = []
        for word in _expected_legacy_split_items(value):
            expected += ["-H", word]
        assert items == expected


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestAddSidedFlagSplitting:
    def test_legacy_space_separated_single_line(self) -> None:
        out = _run_harness('add_sided_flag "--header" "old" "inc/a inc/b"')
        assert _cmd_items(out) == [
            "--header",
            "old=inc/a",
            "--header",
            "old=inc/b",
        ]

    def test_newline_separated_preserves_spaces(self) -> None:
        out = _run_harness(
            'add_sided_flag "--header" "new" $\'inc/a\\npath with spaces/inc\''
        )
        assert _cmd_items(out) == [
            "--header",
            "new=inc/a",
            "--header",
            "new=path with spaces/inc",
        ]


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestAddSidedFlagHostileScalarCorpus:
    """``add_sided_flag``'s legacy single-line path against the shared
    hostile corpus -- the sibling of ``TestAddFlagHostileScalarCorpus``
    above, since it shares the identical unquoted-splitting helper."""

    def test_a_glob_value_stays_literal(self, tmp_path) -> None:
        (tmp_path / "decoy_one.txt").write_text("x")
        out = _run_harness('add_sided_flag "--header" "old" "*"', cwd=tmp_path)
        assert _cmd_items(out) == ["--header", "old=*"]

    @pytest.mark.parametrize("value", HOSTILE_SCALAR_CORPUS)
    def test_argv_exactly_matches_the_independent_oracle(
        self, tmp_path, value: str
    ) -> None:
        (tmp_path / "decoy_one.txt").write_text("x")
        (tmp_path / "decoy_two.txt").write_text("x")
        literal = _bash_ansi_c_quote(value)
        out = _run_harness(f'add_sided_flag "--header" "old" {literal}', cwd=tmp_path)
        items = _cmd_items(out)
        expected: list[str] = []
        for word in _expected_legacy_split_items(value):
            expected += ["--header", f"old={word}"]
        assert items == expected


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestAddSidedScalarFlag:
    """A scalar sided flag (``--version``) must pass its value through
    unsplit -- a single opaque string, not a list. Regression for a real
    bug: ``old-version: '1.0 (release build)'`` word-split through
    ``add_sided_flag`` into three repeated ``--version old=...`` flags
    (``old=1.0``, ``old=(release``, ``old=build)``); the CLI kept only the
    last one, so the report rendered ``(release build)`` and the real
    version, ``1.0``, was silently lost."""

    def test_space_separated_value_is_not_split(self) -> None:
        out = _run_harness(
            'add_sided_scalar_flag "--version" "old" "1.0 (release build)"'
        )
        assert _cmd_items(out) == ["--version", "old=1.0 (release build)"]

    def test_single_word_value(self) -> None:
        out = _run_harness('add_sided_scalar_flag "--version" "new" "pr-1"')
        assert _cmd_items(out) == ["--version", "new=pr-1"]

    def test_empty_value_adds_nothing(self) -> None:
        out = _run_harness('add_sided_scalar_flag "--version" "old" ""')
        assert _cmd_items(out) == []

    def test_embedded_newline_is_kept_verbatim(self) -> None:
        # Unlike add_sided_flag, a scalar flag never treats an embedded
        # newline as an item separator either -- it is still one opaque
        # value.
        out = _run_harness(
            'add_sided_scalar_flag "--version" "old" $\'line one\\nline two\''
        )
        assert _cmd_items(out) == ["--version", "old=line one\nline two"]


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestIsReleaseStyleOperand:
    """``compare`` mode now skips its --write optimization for
    directory/package operands, since the release fan-out engine rejects
    that flag — verified defect: it previously hard-failed a working
    directory/package compare under MODE=compare (Codex review, PR #557)."""

    def test_directory_is_release_style(self, tmp_path) -> None:
        d = tmp_path / "libdir"
        d.mkdir()
        assert _run_predicate(f'_is_release_style_operand "{d}"')

    def test_plain_file_is_not_release_style(self, tmp_path) -> None:
        f = tmp_path / "libfoo.so.1"
        f.write_text("", encoding="utf-8")
        assert not _run_predicate(f'_is_release_style_operand "{f}"')

    def test_json_snapshot_is_not_release_style(self, tmp_path) -> None:
        f = tmp_path / "snapshot.json"
        f.write_text("{}", encoding="utf-8")
        assert not _run_predicate(f'_is_release_style_operand "{f}"')

    @pytest.mark.parametrize(
        "suffix",
        [
            ".rpm",
            ".deb",
            ".tar",
            ".tar.gz",
            ".tar.xz",
            ".tar.bz2",
            ".tar.zst",
            ".tgz",
            ".conda",
            ".whl",
        ],
    )
    def test_package_extensions_are_release_style(self, tmp_path, suffix) -> None:
        f = tmp_path / f"libfoo{suffix}"
        f.write_text("", encoding="utf-8")
        assert _run_predicate(f'_is_release_style_operand "{f}"')

    def test_package_extension_matched_case_insensitively(self, tmp_path) -> None:
        f = tmp_path / "libfoo.RPM"
        f.write_text("", encoding="utf-8")
        assert _run_predicate(f'_is_release_style_operand "{f}"')

    def test_missing_path_is_not_release_style(self) -> None:
        # A nonexistent path isn't a directory and doesn't match a package
        # extension by name — the required-args guard in run.sh catches a
        # genuinely missing operand before this check ever runs.
        assert not _run_predicate('_is_release_style_operand "/no/such/path.so"')

    def test_extensionless_rpm_detected_by_magic_bytes(self, tmp_path) -> None:
        # abicheck/package.py:is_package() classifies an extensionless RPM by
        # its lead magic (0xedabeedb) regardless of filename — the Action's
        # name-suffix-only precheck missed this, so it would still add
        # --write for an operand the CLI goes on to reject
        # (Codex review, PR #557).
        f = tmp_path / "libfoo-release"
        f.write_bytes(b"\xed\xab\xee\xdb\x00\x00\x03\x00" + b"\x00" * 90)
        assert _run_predicate(f'_is_release_style_operand "{f}"')

    def test_extensionless_deb_detected_by_magic_bytes(self, tmp_path) -> None:
        # Deb packages are ar archives ("!<arch>\n" magic) — also detected
        # without a .deb extension by package.py's is_package().
        f = tmp_path / "libfoo-release"
        f.write_bytes(b"!<arch>\n" + b"\x00" * 90)
        assert _run_predicate(f'_is_release_style_operand "{f}"')

    def test_extensionless_plain_binary_not_release_style(self, tmp_path) -> None:
        # A real shared library's ELF magic (0x7f 'ELF') must not be
        # mistaken for RPM/Deb.
        f = tmp_path / "libfoo.so.1"
        f.write_bytes(b"\x7fELF" + b"\x00" * 92)
        assert not _run_predicate(f'_is_release_style_operand "{f}"')


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestExtraArgsHasWriteFlag:
    """Codex review: injecting the Action's own internal ``--write`` ahead of
    the user's ``extra-args`` passthrough is unsafe when the user's own
    ``extra-args`` already requests one -- Click applies both and the *last*
    wins, so the real run would silently honor the user's value instead of
    the Action's, leaving the internal sidecar file empty and triggering an
    unnecessary (and, for ``scan --depth build/source``, potentially
    expensive) rerun anyway. ``_extra_args_has_write_flag`` detects that case
    so the caller can skip its own injection instead.
    """

    def _predicate(self, extra_args: str) -> bool:
        return _run_predicate(
            f"INPUT_EXTRA_ARGS={extra_args!r} _extra_args_has_write_flag"
        )

    def test_absent_extra_args(self) -> None:
        assert not self._predicate("")

    def test_unrelated_extra_args(self) -> None:
        assert not self._predicate("--verbose --gate-api-break")

    def test_write_space_separated(self) -> None:
        assert self._predicate("--write text=out.txt")

    def test_write_equals_form(self) -> None:
        assert self._predicate("--write=text=out.txt")

    def test_write_flag_at_the_end_of_extra_args(self) -> None:
        assert self._predicate("--verbose --write text=out.txt")

    def _predicate_ansi_c(self, escaped: str) -> bool:
        """Like :meth:`_predicate`, but *escaped* is passed through bash's
        ``$'...'`` quoting so ``\\n``/``\\t`` become real whitespace.

        Python's ``!r`` renders a newline as the two characters backslash-n,
        and inside bash single quotes that stays two characters -- so the
        plain helper cannot express the very input these cases are about.
        """
        return _run_predicate(
            f"INPUT_EXTRA_ARGS=$'{escaped}' _extra_args_has_write_flag"
        )

    def test_write_after_a_newline(self) -> None:
        # `extra-args: |` (a YAML literal block) is ordinary Action usage and
        # puts a newline between arguments. `CMD+=($INPUT_EXTRA_ARGS)` splits
        # on IFS -- space, tab AND newline -- so this really is a `--write`
        # token on the command line; a literal-space substring check did not
        # see it, injected ours anyway, and lost to the user's (Codex review).
        assert self._predicate_ansi_c(r"--verbose\n--write text=out.txt")

    def test_write_after_a_tab(self) -> None:
        assert self._predicate_ansi_c(r"--verbose\t--write text=out.txt")

    def test_write_as_the_only_arg_with_surrounding_newlines(self) -> None:
        # A literal block usually ends with a trailing newline too.
        assert self._predicate_ansi_c(r"\n--write text=out.txt\n")

    def test_newline_separated_without_a_write_is_still_false(self) -> None:
        # The negative control for the same splitting: newlines must not make
        # the guard fire on their own.
        assert not self._predicate_ansi_c(r"--verbose\n--gate-api-break")

    def test_does_not_false_positive_on_a_substring(self) -> None:
        # A flag merely containing "write" as a substring (not a real
        # standalone token) must not trip the detector.
        assert not self._predicate("--not-a-write-flag")

    def test_write_consumed_as_an_output_option_value_is_not_a_flag(self) -> None:
        # A fourth Codex review round (fresh evidence): `extra-args:
        # --output --write` means "write a file literally named --write"
        # -- `--output` is the value-taking option here, so it consumes
        # the literal token "--write" as its own filename, and there is no
        # real `--write` flag in this invocation at all. Injecting the
        # internal JSON sidecar on top of a false "the user already has a
        # --write" belief would have left `_coverage_gated`/
        # `_assurance_gated`/`_severity_gate_categories` without evidence
        # for no reason.
        assert not self._predicate("--output --write")

    def test_write_consumed_as_a_clustered_short_options_value_is_not_a_flag(
        self,
    ) -> None:
        # A fifth Codex review round (P1, fresh evidence): `extra-args:
        # -vH --write` means "-v, then -H with a header literally named
        # --write" -- Click parses a clustered bare short option (`-vH`)
        # exactly like `-v -H`, and `-H` is the value-taking option here,
        # consuming the literal token "--write" as its own value. There is
        # no real `--write` flag in this invocation at all; failing to
        # recognize the cluster left the literal "--write" unconsumed and
        # wrongly classified as a real flag, silently suppressing the
        # internal JSON sidecar injection (the unsafe direction, unlike the
        # sibling false-positive class this file documents as accepted).
        assert not self._predicate("-vH --write")

    def test_write_after_a_clustered_bare_boolean_short_option_is_still_a_flag(
        self,
    ) -> None:
        # The negative control: `-vv` is a cluster of two boolean flags
        # only (no value-taking option at the end), so it consumes nothing
        # from the following token -- a real `--write` right after it is
        # still a real flag.
        assert self._predicate("-vv --write")

    def test_write_attached_to_a_clustered_short_option_value_is_not_a_flag(
        self,
    ) -> None:
        # `-vHabc` is `-v` plus `-H` with an *attached* value ("abc") --
        # Click does not consume a following token for this form at all,
        # so a real `--write` right after it is unaffected either way; this
        # pins that the attached-value form is left opaque rather than
        # mis-expanded into consuming the next token.
        assert self._predicate("-vHabc --write")

    def test_a_preceding_options_value_that_looks_like_a_cluster_is_not_expanded(
        self,
    ) -> None:
        # A sixth Codex review round (P1, fresh evidence): `-H -vH --write`
        # means "-H with a header literally named -vH", then a real
        # `--write` -- `-vH` here is `-H`'s own already-claimed value, not a
        # cluster to expand. Expanding every raw token up front (rather than
        # only once confirmed not already claimed as a preceding value)
        # corrupted this ordinary value into extra synthetic options and
        # could flip the following real `--write` either way; this pins
        # that a value token is left untouched regardless of its shape.
        assert self._predicate("-H -vH --write")


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestExtraArgsHasDryRunFlag:
    """Codex review, P2, fresh evidence: an effective dry run reached only
    through ``extra-args --dry-run`` (the dedicated ``INPUT_DRY_RUN`` input
    left false) must be recognized too, so the command-assembly branches
    that only check ``INPUT_DRY_RUN`` don't inject `-o`/`--write` alongside
    it -- a combination the CLI itself rejects.
    """

    def _predicate(self, extra_args: str) -> bool:
        return _run_predicate(
            f"INPUT_EXTRA_ARGS={extra_args!r} _extra_args_has_dry_run_flag"
        )

    def test_absent_extra_args(self) -> None:
        assert not self._predicate("")

    def test_unrelated_extra_args(self) -> None:
        assert not self._predicate("--verbose --gate-api-break")

    def test_bare_dry_run(self) -> None:
        assert self._predicate("--dry-run")

    def test_dry_run_after_another_flag(self) -> None:
        assert self._predicate("--verbose --dry-run")

    def test_does_not_false_positive_on_a_substring(self) -> None:
        assert not self._predicate("--not-a-dry-run-flag")

    def test_dry_run_consumed_as_a_bare_output_option_value_is_not_a_flag(
        self,
    ) -> None:
        # A second Codex review round (fresh evidence): `extra-args:
        # --output --dry-run` means "write to a file literally named
        # --dry-run" -- Click's `-o`/`--output PATH` (two-token form)
        # consumes the next token as its value, never parses it as a flag.
        assert not self._predicate("--output --dry-run")

    def test_dry_run_consumed_as_a_short_output_option_value_is_not_a_flag(
        self,
    ) -> None:
        assert not self._predicate("-o --dry-run")

    def test_a_real_dry_run_after_an_output_option_value_is_still_a_flag(
        self,
    ) -> None:
        # The negative control: only the token immediately after `-o`/
        # `--output` is exempt. A `--dry-run` anywhere else, including
        # right after a real (non-flag-shaped) output path, is a real flag.
        assert self._predicate("--output out.json --dry-run")

    def test_dry_run_after_an_unrelated_option_value_that_looks_like_o_is_still_a_flag(
        self,
    ) -> None:
        # A third Codex review round (fresh evidence): the earlier fix's
        # "skip the token right after -o/--output" rule was itself too
        # naive -- it can't tell a real `-o` flag from some *other* option's
        # value that happens to be spelled "-o" (e.g. a suppression file
        # named "-o"). `--suppress` is the value-taking option here, so it
        # consumes the literal "-o" as its own value, and the following
        # `--dry-run` is a real, unconsumed flag -- exactly what Click
        # itself would parse. The shared `_extra_args_options` tokenizer
        # (rather than a bare "was the previous token -o?" check) is what
        # gets this right.
        assert self._predicate("--suppress -o --dry-run")


def _run_value(call: str) -> str:
    """Source the real helper functions and return a value-printing call's
    stdout (e.g. an ``_effective_format`` invocation), stripped of the
    trailing newline `echo`/`printf` conventions may or may not add."""
    require_bash()
    script = _helpers_region() + _cli_introspection_prelude() + f"\n{call}\n"
    with tempfile.NamedTemporaryFile(
        "w",
        suffix=".sh",
        delete=False,
        encoding="utf-8",
        newline="\n",
    ) as f:
        f.write(script)
        script_path = f.name
    try:
        result = subprocess.run(
            [bash_executable(), script_path],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    finally:
        os.unlink(script_path)
    return result.stdout


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestEffectiveFormat:
    """ADR-064's "effective-format-override" gap: `extra-args: --format json`
    under `format: text`/`markdown` makes the real `abicheck` invocation emit
    JSON, since Click keeps only the *last* `--format` occurrence -- but every
    JSON-detection site that checked the Action's own nominal `$FORMAT`
    variable instead of what the command actually ran with silently missed
    that override. `_effective_format` resolves the real value the same way
    `_extra_args_has_write_flag`/`_extra_args_write_json_path` resolve their
    own `extra-args` overrides: by splitting `INPUT_EXTRA_ARGS` the same way
    the real command line is built and keeping the last match.
    """

    def _value(self, format_: str, extra_args: str) -> str:
        return _run_value(
            f"FORMAT={format_!r} INPUT_EXTRA_ARGS={extra_args!r} _effective_format"
        )

    def test_no_extra_args_falls_back_to_nominal_format(self) -> None:
        assert self._value("text", "") == "text"
        assert self._value("json", "") == "json"

    def test_unrelated_extra_args_falls_back_to_nominal_format(self) -> None:
        assert self._value("markdown", "--verbose --gate-api-break") == "markdown"

    def test_extra_args_overrides_to_json_space_separated(self) -> None:
        assert self._value("text", "--format json") == "json"

    def test_extra_args_overrides_to_json_equals_form(self) -> None:
        assert self._value("text", "--format=json") == "json"

    def test_extra_args_overrides_away_from_json(self) -> None:
        # The reverse direction matters too: a `format: json` step whose own
        # extra-args forces text must not still be treated as JSON.
        assert self._value("json", "--format text") == "text"

    def test_last_format_occurrence_wins(self) -> None:
        # Click keeps only the last repeated option -- this helper must
        # agree, not the first.
        assert self._value("text", "--format json --format markdown") == "markdown"

    def test_format_after_a_newline(self) -> None:
        # Same YAML-literal-block splitting concern as
        # `_extra_args_has_write_flag`'s own newline test.
        assert (
            _run_value(
                r"FORMAT=text INPUT_EXTRA_ARGS=$'--verbose\n--format json' "
                r"_effective_format"
            )
            == "json"
        )

    def test_does_not_false_positive_on_a_substring(self) -> None:
        assert self._value("text", "--not-a-format-flag") == "text"

    def test_format_consumed_as_an_output_option_value_is_not_an_override(
        self,
    ) -> None:
        # Same tokenizer, same class of bug as the sibling write/dry-run
        # helpers: `--output --format` means "write a file literally named
        # --format", not a `--format` override -- `--output` is the
        # value-taking option here and consumes the literal token.
        assert self._value("markdown", "--output --format") == "markdown"

    def test_format_after_an_unrelated_option_value_still_overrides(self) -> None:
        assert self._value("markdown", "--output out.md --format json") == "json"


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestExtraArgsWriteJsonPath:
    """``--write`` is **repeatable**, so every ``json=`` occurrence is real.

    This class previously asserted the opposite -- that ``--write`` is a scalar
    Click option resolving to the last occurrence whatever its format, so that
    a later non-JSON ``--write`` "un-discovers" an earlier JSON path (Codex
    review, P2, PR #1071). That premise is false for ``compare``, which
    declares the option with ``multiple=True``
    (``frontends/cli/options/secondary_output.py``'s ``deco_multi``, ADR-068
    D4's "one analysis, several artifacts"), with
    ``--write json=a.json --write markdown=b.md`` as its own help text's
    example. Both artifacts are written.

    The tree carried both claims at once -- this class and
    ``test_a_non_json_user_write_no_longer_suppresses_the_internal_one``
    contradicted each other -- and the contradiction was settled against the
    option declaration rather than either comment. The clearing it pinned was a
    real defect: the combination above reported no requested JSON path, so a
    missing ``a.json`` left an exit-0 run publishing COMPATIBLE (Codex review,
    P2, a later round).

    ``_extra_args_write_json_paths`` now answers all of them, newline-separated;
    the singular ``_extra_args_write_json_path`` answers the first, which is all
    ``_json_report_src`` needs (it wants *a* readable report, not every one).
    """

    def _value(self, extra_args: str) -> str:
        return _run_value(
            f"INPUT_EXTRA_ARGS={extra_args!r} _extra_args_write_json_path"
        )

    def test_absent_extra_args(self) -> None:
        assert self._value("") == ""

    def test_single_write_json(self) -> None:
        assert self._value("--write json=out.json") == "out.json"

    def _all(self, extra_args: str) -> str:
        return _run_value(
            f"INPUT_EXTRA_ARGS={extra_args!r} _extra_args_write_json_paths"
        )

    def test_two_json_writes_both_count(self) -> None:
        # Repeatable: both artifacts are written, so both are requested. The
        # singular helper answers the first; the plural one answers both.
        both = "--write json=first.json --write json=second.json"
        assert self._value(both) == "first.json"
        assert self._all(both).split() == ["first.json", "second.json"]

    def test_a_later_non_json_write_does_not_erase_an_earlier_json_one(self) -> None:
        # The defect this class used to pin: `markdown=` following `json=` does
        # not un-write the JSON artifact, so the path stays discoverable.
        combo = "--write json=out.json --write markdown=out.md"
        assert self._value(combo) == "out.json"
        assert self._all(combo).split() == ["out.json"]

    def test_a_later_json_write_after_a_non_json_one_counts(self) -> None:
        assert self._value("--write text=out.txt --write json=out.json") == "out.json"

    def test_no_json_write_answers_nothing(self) -> None:
        assert self._value("--write markdown=out.md") == ""
        assert self._all("--write markdown=out.md") == ""

    def test_unrelated_extra_args(self) -> None:
        assert self._value("--verbose --gate-api-break") == ""

    def test_equals_form(self) -> None:
        assert self._value("--write=json=out.json") == "out.json"

    def test_write_consumed_as_an_output_option_value_is_not_a_flag(self) -> None:
        # Same tokenizer, same class of bug as the sibling helpers: `--output
        # --write` means "write a file literally named --write", not a real
        # `--write` flag.
        assert self._value("--output --write") == ""


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestExtraArgsExpandShortClusters:
    """Direct unit tests for `_extra_args_expand_short_clusters`, the helper
    behind the fifth Codex review round (P1, fresh evidence): a clustered
    bare short option (`-vH` for Click's own `-v -H`) was an unrecognized
    token to `_extra_args_is_value_option`'s exact-string match, so a
    following literal `--write` (a header path spelled that way) was
    misclassified as a real flag instead of `-H`'s consumed value.
    """

    def _expand(self, token: str) -> str:
        return _run_value(f"_extra_args_expand_short_clusters {token!r} || true")

    def test_not_a_cluster_returns_nothing(self) -> None:
        assert self._expand("--write") == ""
        assert self._expand("-H") == ""
        assert self._expand("plain") == ""

    def test_bool_then_value_char_expands_and_marks_the_value_option(self) -> None:
        assert self._expand("-vH") == "-v\n-H\n"

    def test_multiple_bool_flags_then_value_char(self) -> None:
        assert self._expand("-vvH") == "-v\n-v\n-H\n"

    def test_every_known_value_char_expands(self) -> None:
        # Derived from the real Click parameter tables rather than hand-listed
        # (Action-vs-CLI surface audit, docs/contribute/plans/
        # action-cli-surface-drift.md): this case pinned a fourth char `j`,
        # which `compare` has never had since `jobs`/`-j` was retired with
        # ADR-068 D5 -- so it asserted the expander invented an option Click
        # itself rejects. `tests/test_extra_args_is_value_option_completeness
        # .py`'s TestShortClusterTerminalsMatchTheCli owns the same invariant
        # on the shell source; this is its behavioural half.
        from test_extra_args_is_value_option_completeness import (
            _value_taking_options,
        )

        chars = sorted(
            opt.lstrip("-")
            for opt in _value_taking_options(("compare",))
            if not opt.startswith("--")
        )
        assert chars, "no short value-taking options found via introspection"
        for char in chars:
            assert self._expand(f"-v{char}") == f"-v\n-{char}\n"

    def test_a_retired_short_option_is_not_a_cluster_terminal(self) -> None:
        """`-j` is gone from the CLI; expanding `-vj` would synthesize an
        option the real parser rejects."""
        assert self._expand("-vj") == ""

    def test_a_pure_boolean_cluster_is_not_expanded(self) -> None:
        # `-vv` has no trailing value-taking option, so there is nothing
        # that needs to consume a following token -- leaving it as one
        # opaque token (no expansion) is safe and correct, unlike a cluster
        # that ends in a real value-taking option.
        assert self._expand("-vv") == ""

    def test_an_attached_value_is_not_expanded(self) -> None:
        # `-vHabc` is `-v -Habc` (an attached value for -H) in real Click
        # parsing -- it does not consume a following token, so leaving it
        # as a single opaque token (no expansion) is the correct outcome.
        assert self._expand("-vHabc") == ""

    def test_an_unknown_char_is_not_expanded(self) -> None:
        assert self._expand("-vz") == ""

    def test_a_single_short_option_is_not_a_cluster(self) -> None:
        assert self._expand("-v") == ""


# `_text_report_content` (and its `TestTextReportContentEffectiveFormat`
# tests) was retired by ADR-063 Track T8: it existed solely to feed
# `_severity_gate_categories`'/`_severity_gate_exit`'s rendered-text
# fallbacks, both of which the track removed as prose reconstruction of a
# real gate decision. With no caller left, the function itself was deleted
# rather than kept dead.


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestExtraArgsHasConfigFlag:
    """Codex review, PR #1159, round 12: a synthesized ``--config`` (from a
    dso-only/fail-on-removed-library/compile-context input) already in
    ``$CMD`` collides with the user's own ``extra-args --config``, and
    Click keeps the *last* repeated flag -- silently dropping whichever one
    lost, exactly the ``_extra_args_has_write_flag`` shape above.
    ``_extra_args_has_config_flag`` detects the user's half of that
    collision so the caller can fail loud instead.
    """

    def _predicate(self, extra_args: str) -> bool:
        return _run_predicate(
            f"INPUT_EXTRA_ARGS={extra_args!r} _extra_args_has_config_flag"
        )

    def test_absent_extra_args(self) -> None:
        assert not self._predicate("")

    def test_unrelated_extra_args(self) -> None:
        assert not self._predicate("--verbose --gate-api-break")

    def test_config_space_separated(self) -> None:
        assert self._predicate("--config ci.yml")

    def test_config_equals_form(self) -> None:
        assert self._predicate("--config=ci.yml")

    def test_config_flag_at_the_end_of_extra_args(self) -> None:
        assert self._predicate("--verbose --config ci.yml")


_EXTRA_ARGS_CONFIG_GUARD_START = "# Append extra-args (pass-through CLI arguments)"
# A trailing, unindented "\nfi\n" used to be unique to this block's own
# closing `fi` -- but the round-18 `--pattern-verdicts`-stripping fix
# (Codex review, PR #1172, fresh evidence) nested a second `if/else/fi`
# *inside* it, so the naive first-match search now cuts the block off at
# that inner `fi` instead, truncating the sourced script mid-statement.
# Anchored on the next block's own distinctive comment instead, which is
# unique in the file and immediately follows this block's real end.
_EXTRA_ARGS_CONFIG_GUARD_END = (
    "# Recomputed here (idempotently -- the compare branch already computed it"
)


def _extra_args_config_guard_source() -> str:
    """The real, shipped ``extra-args`` append block, including the
    ``--config`` collision guard just above it -- not just the two
    predicate helpers it calls (see ``TestExtraArgsConfigCollisionGuard``
    below)."""
    text = RUN_SH.read_text(encoding="utf-8")
    start = text.index(_EXTRA_ARGS_CONFIG_GUARD_START)
    end = text.index(_EXTRA_ARGS_CONFIG_GUARD_END, start)
    return text[start:end]


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestExtraArgsConfigCollisionGuard:
    """End-to-end proof for the real, shipped guard block (not just its two
    predicate helpers in isolation): a synthesized ``--config`` already in
    ``$CMD`` combined with the user's own ``extra-args --config`` must fail
    loud, and every other combination must pass through unaffected."""

    def _run(
        self, cmd_has_config: bool, extra_args: str
    ) -> subprocess.CompletedProcess[str]:
        # `MODE`/`_CLI_MODE` (round 18, fresh evidence): the extra-args
        # append block now branches on both to decide whether to strip
        # `--pattern-verdicts` (see `TestPatternVerdictsFlagStrippedBefore
        # CompareTranslation` in test_action_run_sh_scan_routing_edge_
        # cases.py for that behavior itself) -- this narrower harness
        # sources only the block, never the full mode-dispatch chain that
        # normally sets both, so under the real script's own `set -u` they
        # would be unbound here. Seeded to "compare" -- consistent with
        # this test's own `CMD=(compare ...)` seed -- so the new branch
        # deterministically takes the plain-append `else` path, which is
        # what this collision-guard test class is actually about.
        require_bash()
        cmd_seed = "MODE=compare\n_CLI_MODE=compare\n" + (
            "CMD=(compare --config /tmp/overlay.yml)"
            if cmd_has_config
            else "CMD=(compare)"
        )
        script = (
            _helpers_region()
            + _cli_introspection_prelude()
            + f"\n{cmd_seed}\n"
            + _extra_args_config_guard_source()
            + '\nprintf "%s\\n" "${CMD[@]}"\n'
        )
        with tempfile.NamedTemporaryFile(
            "w", suffix=".sh", delete=False, encoding="utf-8", newline="\n"
        ) as f:
            f.write(script)
            script_path = f.name
        env = {**os.environ, "INPUT_EXTRA_ARGS": extra_args}
        try:
            return subprocess.run(
                [bash_executable(), script_path],
                capture_output=True,
                text=True,
                env=env,
            )
        finally:
            os.unlink(script_path)

    def test_synthesized_config_plus_extra_args_config_fails_loud(self) -> None:
        result = self._run(cmd_has_config=True, extra_args="--config ci.yml")
        assert result.returncode == 1, result.stdout
        assert "::error::" in result.stdout
        assert "extra-args" in result.stdout and "--config" in result.stdout

    def test_synthesized_config_alone_is_unaffected(self) -> None:
        result = self._run(cmd_has_config=True, extra_args="--verbose")
        assert result.returncode == 0, result.stdout
        assert "--verbose" in result.stdout.splitlines()

    def test_extra_args_config_alone_is_unaffected(self) -> None:
        # No synthesized --config in $CMD -- extra-args's own --config is
        # the only one, so there is no collision to guard against.
        result = self._run(cmd_has_config=False, extra_args="--config ci.yml")
        assert result.returncode == 0, result.stdout
        assert "ci.yml" in result.stdout

    def test_neither_config_is_unaffected(self) -> None:
        result = self._run(cmd_has_config=False, extra_args="--verbose")
        assert result.returncode == 0, result.stdout
