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
import subprocess
import tempfile
from pathlib import Path

import pytest
from _workflow_exec import HOSTILE_SCALAR_CORPUS

RUN_SH = Path(__file__).resolve().parents[1] / "action" / "run.sh"
_MARKER = "# Build the abicheck command"


def _helpers_region() -> str:
    """The function-definitions header of run.sh, up to the assembly marker."""
    text = RUN_SH.read_text(encoding="utf-8")
    idx = text.index(_MARKER)
    return text[:idx]


def _bash_executable() -> str:
    """Resolve a real bash, bypassing Windows' WSL-launcher stub.

    On GitHub's windows-latest runners, ``%SystemRoot%\\System32\\bash.exe``
    is the WSL launcher stub — present even with no distro installed — and a
    bare ``["bash", ...]`` subprocess call can resolve to it ahead of Git for
    Windows' real bash depending on the calling process's inherited PATH
    order. The stub exits immediately (non-zero) without running anything,
    which looks identical to every helper-function test failing at once with
    no bash-level diagnostic (Codex/CI investigation, PR #551). Prefer Git for
    Windows' own bash explicitly on that platform; every other platform keeps
    using whatever "bash" already resolves to on PATH.
    """
    if os.name != "nt":
        return "bash"
    for candidate in (
        os.environ.get("GIT_BASH_PATH"),
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
    ):
        if candidate and Path(candidate).is_file():
            return candidate
    return "bash"


def _run_harness(harness: str, *, cwd: Path | None = None) -> str:
    """Source the real helper functions + *harness*, return CMD joined by '\\x1f'.

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
    """
    script = (
        _helpers_region()
        + "\nCMD=()\n"
        + harness
        # ${CMD[@]+"${CMD[@]}"} (not plain "${CMD[@]}"): pre-4.4 bash — macOS's
        # stock 3.2 included — treats an empty array subscripted with [@]
        # under `set -u` as an unbound-variable error and aborts the script
        # (the same bug run.sh itself works around at its PR-comment loop).
        + "\nprintf '%s\\x1f' ${CMD[@]+\"${CMD[@]}\"}\n"
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
            [_bash_executable(), script_path],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=str(cwd) if cwd is not None else None,
        )
    finally:
        os.unlink(script_path)
    if result.returncode != 0:
        raise AssertionError(
            f"harness script failed (exit {result.returncode})\n"
            f"--- stdout ---\n{result.stdout}\n"
            f"--- stderr ---\n{result.stderr}"
        )
    return result.stdout


def _cmd_items(stdout: str) -> list[str]:
    return [item for item in stdout.split("\x1f") if item]


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
    script = _helpers_region() + f"\nif {call}; then exit 0; else exit 1; fi\n"
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
            [_bash_executable(), script_path],
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
    def test_no_corpus_value_glob_expands_to_a_decoy_file(
        self, tmp_path, value: str
    ) -> None:
        (tmp_path / "decoy_one.txt").write_text("x")
        (tmp_path / "decoy_two.txt").write_text("x")
        literal = _bash_ansi_c_quote(value)
        out = _run_harness(f'add_flag "-H" {literal}', cwd=tmp_path)
        items = _cmd_items(out)
        assert "decoy_one.txt" not in items
        assert "decoy_two.txt" not in items


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
    def test_no_corpus_value_glob_expands_to_a_decoy_file(
        self, tmp_path, value: str
    ) -> None:
        (tmp_path / "decoy_one.txt").write_text("x")
        (tmp_path / "decoy_two.txt").write_text("x")
        literal = _bash_ansi_c_quote(value)
        out = _run_harness(f'add_sided_flag "--header" "old" {literal}', cwd=tmp_path)
        items = _cmd_items(out)
        assert "old=decoy_one.txt" not in items
        assert "old=decoy_two.txt" not in items


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
