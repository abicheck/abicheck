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

"""``dump``/``compare``/``scan`` L2 compile-context forwarding parity in
``action/run.sh`` (AGENTS.md P0 "fix Action compile-context forwarding
parity").

The three CLI subcommands all share ``compile_context_options``
(the ``ast-frontend``/``gcc-path``/``gcc-prefix``/``gcc-options``/
``--sysroot``/``--nostdinc``, ADR-037 D3) — but ``action/run.sh`` used to
forward all six only in ``dump`` mode, only ``--ast-frontend`` in ``compare``
mode (behind a comment incorrectly claiming the rest were "dump-only flags...
not exposed on the compare CLI"), and none of them in ``scan`` mode.

**Phase 7b (PR #1153) update**: the CLI's own ``--ast-frontend``/
``--sysroot``/``--nostdinc`` flags were demoted to ``.abicheck.yml``'s
``compile:`` block entirely — every one of those flags now exits 64
(UsageError) on every command. ``action/run.sh`` kept forwarding them
literally for a while after that (a real regression, since fixed): these
three inputs are now folded, via ``_resolve_effective_build_config``, into a
scratch ``.abicheck.yml``'s ``compile:`` block and forwarded through
``--config`` instead. ``--compiler``/``--compiler-prefix``/
``--compiler-option`` remain literal flags (Phase 7b didn't touch them) and
this module's gcc-options tests are otherwise unchanged.

These tests extract each mode's compile-context region verbatim from run.sh
(the same "parse the real file, don't hand-copy it" discipline as
``test_action_run_sh_legacy_aliases.py``) and assert parity — including
running the real ``_resolve_effective_build_config`` merge helper (a real
Python/PyYAML dependency, present in this dev environment) rather than a
stub, so a regression in the merge logic itself would fail here too, not
only in a full-invocation test.

See ``tests/test_action_compile_context_end_to_end.py`` for the companion
module that runs the *complete, unmodified* ``action/run.sh`` against a real
compiled library and a real installed ``abicheck`` — proving these inputs
produce exit 0 (or a real verdict), not exit 64, which this file's own
extracted-fragment approach cannot by itself demonstrate.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest
import yaml

from abicheck._compiler_options import split_gcc_options

RUN_SH = Path(__file__).resolve().parents[1] / "action" / "run.sh"

_DUMP_MODE_MARKER = 'if [[ "$MODE" == "dump" ]]; then'
_COMPARE_MODE_MARKER = 'elif [[ "$MODE" == "compare" ]]; then'
_SCAN_MODE_MARKER = 'elif [[ "$MODE" == "scan" ]]; then'

# dump mode: --compiler/--compiler-prefix/--compiler-option (unaffected by
# Phase 7b) through the merged --config forward (the Phase 7b replacement for
# the removed --ast-frontend/--sysroot/--nostdinc flags) -- one contiguous
# region.
_DUMP_CONTEXT_START = 'add_single_flag "--compiler" "${INPUT_GCC_PATH:-}"'
_DUMP_CONTEXT_END = 'add_single_flag "--config" "$_EFFECTIVE_BUILD_CONFIG"'

# compare's region starts at the gating comment (Codex review: these inputs
# are gated to the single-pair path, since the release fan-out rejects them
# outright) and ends at the same merged --config forward, which applies
# unconditionally to both the release-style and single-pair paths.
_COMPARE_COMPILE_CONTEXT_START = (
    "# The L2 compile-context inputs (ast-frontend/gcc-*/sysroot/nostdinc)"
)
_COMPARE_COMPILE_CONTEXT_END = _DUMP_CONTEXT_END

# scan's region is structurally different again: the --config merge happens
# right after --build-info, well *before* --compiler/--compiler-prefix/
# --compiler-option (which sit near --lang, after --against) -- Phase 7b's
# fix folded the merge in at its own pre-existing --config call site rather
# than relocating it next to the cross-compiler flags. Split into two
# sub-regions so each can be tested without dragging in unrelated
# prerequisites ($SCAN_ARTIFACT_SET/$FORCE_AUDIT_ONLY sit between them and
# aren't needed by either).
_SCAN_MERGE_START = (
    'add_single_flag "--build-info" "${INPUT_BUILD_INFO:-${INPUT_COMPILE_DB:-}}"'
)
_SCAN_MERGE_END = _DUMP_CONTEXT_END
_SCAN_GCC_OPTIONS_START = 'add_single_flag "--compiler" "${INPUT_GCC_PATH:-}"'
_SCAN_GCC_OPTIONS_END = (
    'add_flag_shlex_split "--compiler-option" "${INPUT_GCC_OPTIONS:-}"'
)


# _is_release_style_operand is defined once, well before any mode branch;
# compare's extracted region calls it, so the harness needs its real
# definition rather than a hand-copied stub (same "parse the real file"
# discipline as the rest of this module).
_IS_RELEASE_STYLE_OPERAND_START = "_is_release_style_operand() {"
_IS_RELEASE_STYLE_OPERAND_END = "\n}\n"

# _is_path_already_qualified is defined near the very top of run.sh, well
# before $_PY_SAFE_DIR exists -- _resolve_effective_build_config (below)
# calls it to anchor a relative build-config path to $PWD before its own
# `cd "$_PY_SAFE_DIR"`, so the harness needs the real definition too.
_IS_PATH_ALREADY_QUALIFIED_START = "_is_path_already_qualified() {"
_IS_PATH_ALREADY_QUALIFIED_END = "\n}\n"

# _resolve_effective_build_config -- the Phase 7b merge helper itself. Its
# own body shells out to the real $_PY_BIN with real PyYAML, so extracting
# and running it verbatim (rather than stubbing it) actually exercises the
# merge logic, not just its call sites.
_RESOLVE_BUILD_CONFIG_START = (
    '_GENERATED_COMPILE_CONTEXT_CONFIG="$_PY_SAFE_DIR/'
    'generated-compile-context.abicheck.yml"'
)
_RESOLVE_BUILD_CONFIG_END = (
    "\n  printf '%s' \"$_GENERATED_COMPILE_CONTEXT_CONFIG\"\n}\n"
)


def _extract(start_marker: str, end_marker: str, after: str | None = None) -> str:
    text = RUN_SH.read_text(encoding="utf-8")
    base = text.index(after) if after is not None else 0
    start = text.index(start_marker, base)
    end = text.index(end_marker, start) + len(end_marker)
    return text[start:end]


def _is_release_style_operand_source() -> str:
    return _extract(_IS_RELEASE_STYLE_OPERAND_START, _IS_RELEASE_STYLE_OPERAND_END)


def _is_path_already_qualified_source() -> str:
    return _extract(_IS_PATH_ALREADY_QUALIFIED_START, _IS_PATH_ALREADY_QUALIFIED_END)


def _resolve_effective_build_config_source() -> str:
    return _extract(_RESOLVE_BUILD_CONFIG_START, _RESOLVE_BUILD_CONFIG_END)


# add_flag() (unlike add_single_flag, stubbed inline in each harness below) is
# extracted verbatim -- --compiler-option (CLI audit PR 5/5's --gcc-options
# migration) now goes through its real whitespace/newline-splitting logic,
# not a one-line stub, so a harness exercising it needs the real definition.
# add_flag_shlex_split() -- the --compiler-option-only sibling that
# shlex-splits a single-line value (Codex review: add_flag()'s own plain
# bash word-splitting broke a quoted gcc-options value into malformed
# tokens) -- is defined immediately after add_flag() and is extracted in
# the same slice, since it falls back to calling add_flag() itself.
# _split_legacy_value() -- add_flag()'s own shared legacy-split helper
# (Phase 8, PR #919: disables pathname/glob expansion for the unquoted
# `for item in $value` split) -- is defined immediately *before* add_flag()
# and must be captured in the same slice too: add_flag() calls it, and
# starting the extraction at "add_flag() {" itself silently excluded it,
# leaving the extracted region call an undefined function under `set -u`
# (no error, since the script has no `set -e` -- it just silently produced
# zero CMD entries instead of the real split, which is exactly how this
# extraction bug was caught: two tests in this file that assert a non-empty
# result started failing the moment _split_legacy_value existed).
_ADD_FLAG_START = "_split_legacy_value() {"
_ADD_FLAG_SHLEX_SPLIT_END = "\nadd_flag_shlex_split() {"
_ADD_FLAG_END = "\n}\n"


def _add_flag_source() -> str:
    text = RUN_SH.read_text(encoding="utf-8")
    start = text.index(_ADD_FLAG_START)
    # First closing brace is add_flag()'s own end; the second (searched from
    # just past add_flag_shlex_split's own opening) is that function's end.
    shlex_start = text.index(_ADD_FLAG_SHLEX_SPLIT_END, start)
    end = text.index(_ADD_FLAG_END, shlex_start) + len(_ADD_FLAG_END)
    return text[start:end]


# $_PY_SAFE_DIR is referenced (not redefined) inside add_flag_shlex_split's
# real body -- extracted verbatim (Codex review: a harness that silently left
# it unset would make every "(cd \"$_PY_SAFE_DIR\" && ...)" wrapper `cd` into
# an empty string, i.e. a no-op staying in the untrusted checkout, exercising
# *none* of the CWD-shadowing fix that variable exists for, while every test
# here kept passing regardless). Fails loud (exit 1) rather than falling
# back to a shared directory, so the extracted block is the whole
# if/fi -- not a single line.
_PY_SAFE_DIR_START = 'if ! _PY_SAFE_DIR="$(mktemp -d)"; then'
_PY_SAFE_DIR_END = "\ntrap 'rm -rf \"$_PY_SAFE_DIR\"' EXIT\n"

# $_PY_BIN_HAS_ABICHECK is referenced (not redefined) inside
# add_flag_shlex_split's real guard -- extracted verbatim for the identical
# reason as $_PY_SAFE_DIR above (Codex review: a harness silently leaving it
# unset/empty would always take the plain-whitespace-split fallback branch,
# exercising none of the real shlex-aware splitting any test here checks).
_PY_BIN_HAS_ABICHECK_START = '_PY_BIN_HAS_ABICHECK="false"'
_PY_BIN_HAS_ABICHECK_END = "\nfi\n"


def _py_safe_dir_source() -> str:
    text = RUN_SH.read_text(encoding="utf-8")
    start = text.index(_PY_SAFE_DIR_START)
    end = text.index(_PY_SAFE_DIR_END, start) + len(_PY_SAFE_DIR_END)
    return text[start:end]


def _py_bin_has_abicheck_source() -> str:
    text = RUN_SH.read_text(encoding="utf-8")
    start = text.index(_PY_BIN_HAS_ABICHECK_START)
    end = text.index(_PY_BIN_HAS_ABICHECK_END, start) + len(_PY_BIN_HAS_ABICHECK_END)
    return text[start:end]


def _compile_context_region(
    start_marker: str, end_marker: str, mode_marker: str
) -> str:
    """Extract one mode's compile-context flag-forwarding region verbatim,
    anchored to start searching only after *mode_marker* so an identical
    literal string in another mode's own region isn't matched instead."""
    text = RUN_SH.read_text(encoding="utf-8")
    mode_start = text.index(mode_marker)
    start = text.index(start_marker, mode_start)
    end = text.index(end_marker, start) + len(end_marker)
    return text[start:end]


def _bash_executable() -> str:
    """Resolve a real bash, bypassing Windows' WSL-launcher stub.

    See ``test_action_run_sh_helpers._bash_executable`` for the full
    rationale (GitHub windows-latest runners resolve a bare "bash" to a
    non-functional WSL stub ahead of Git for Windows' real bash).
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


_FULL_ENV = {
    "INPUT_AST_FRONTEND": "clang",
    "INPUT_GCC_PATH": "/opt/gcc-14/bin/g++",
    "INPUT_GCC_PREFIX": "aarch64-linux-gnu-",
    "INPUT_GCC_OPTIONS": "-DFOO=1",
    "INPUT_SYSROOT": "/opt/sysroot",
    "INPUT_NOSTDINC": "true",
}


def _run_bash_script(
    script: str,
    env: dict[str, str] | None = None,
    *,
    check: bool = True,
    text: bool = True,
    cwd: Path | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[Any]:
    """Run ``script`` via a real bash, from a temp file rather than as an
    inline ``-c`` argument (Codex review, fresh evidence: windows-latest CI
    failure, this module only). Windows processes have no real argv --
    Python's ``subprocess`` reconstructs one command-line string via
    ``list2cmdline`` (MSVC/CRT backslash-run-parity quoting), but Git Bash's
    MSYS runtime re-derives its own argv from that same string using a
    materially different convention. For a script this size, with many
    nested/embedded single and double quotes (the extracted
    ``add_flag_shlex_split`` region alone), the two conventions don't always
    round-trip losslessly -- confirmed on windows-latest: a script verified
    syntactically valid via ``bash -n`` on Linux produced a genuine bash
    *parse* error when passed this way (\"unexpected EOF while looking for
    matching `\"'\"), i.e. corruption in transit, not a real syntax defect
    in the script text itself. A path argument sidesteps command-line
    reconstruction entirely -- the same pattern already used by
    ``test_action_run_sh_severity_summary.py``'s ``_run()`` and
    ``test_action_run_sh_py_safe_path.py``."""
    with tempfile.NamedTemporaryFile(
        "w", suffix=".sh", delete=False, encoding="utf-8", newline="\n"
    ) as f:
        f.write(script)
        script_path = f.name
    try:
        return subprocess.run(
            [_bash_executable(), script_path],
            capture_output=True,
            text=text,
            env=env,
            check=check,
            cwd=cwd,
            timeout=timeout,
        )
    finally:
        os.unlink(script_path)


def _harness(*, needs_merge_helper: bool, needs_release_style: bool) -> str:
    parts = [
        'add_single_flag() { [[ -n "$2" ]] && CMD+=("$1" "$2"); }\n',
        # add_flag_shlex_split() (extracted as part of _add_flag_source())
        # needs _PY_BIN resolved, same as the real script does near its own
        # top -- otherwise the harness silently falls back to add_flag()'s
        # own naive splitting and a quoting regression would go undetected.
        '_PY_BIN="$(command -v python3 || command -v python || true)"\n',
        # Only needed by _is_path_already_qualified's Windows-only branch;
        # this harness always runs the non-Windows path (parity with every
        # other run.sh test file, which is exercised for real on the
        # windows-latest CI lane directly rather than simulated here).
        "_RUNNING_ON_WINDOWS=false\n",
        _py_safe_dir_source(),
        _py_bin_has_abicheck_source(),
        _add_flag_source(),
    ]
    if needs_release_style:
        parts.append(_is_release_style_operand_source())
    if needs_merge_helper:
        parts.append(_is_path_already_qualified_source())
        parts.append(_resolve_effective_build_config_source())
    parts.append("\nCMD=()\n")
    return "".join(parts)


_CONFIG_CONTENT_MARKER = "===GENERATED_CONFIG_CONTENT==="


def _run_region(
    mode_marker: str,
    env_extra: dict[str, str],
    start_marker: str,
    end_marker: str,
    *,
    needs_merge_helper: bool = True,
    needs_release_style: bool = False,
    cwd: Path | None = None,
) -> tuple[list[str], str, str]:
    script = (
        _harness(
            needs_merge_helper=needs_merge_helper,
            needs_release_style=needs_release_style,
        )
        + _compile_context_region(start_marker, end_marker, mode_marker)
        + "\nprintf '%s\\n' \"${CMD[@]}\"\n"
        # $_PY_SAFE_DIR (which the merged scratch config lives under) is
        # removed by this script's own EXIT trap the moment the process
        # exits -- before a caller reading the file from outside this
        # subprocess could ever see it. Emit its content here, still inside
        # the process, so _find_config_yaml can parse it from captured
        # stdout instead of re-opening a path that no longer exists by the
        # time subprocess.run() returns.
        + f"echo '{_CONFIG_CONTENT_MARKER}'\n"
        # `|| true`: a failed/false `[[ ]] && cat ...` as the script's own
        # last command would otherwise make bash exit non-zero even on the
        # ordinary "no config generated" case, which is not itself a
        # failure -- callers of this harness only care whether *bash itself*
        # ran cleanly, not whether a config happened to exist.
        + '[[ -n "${_EFFECTIVE_BUILD_CONFIG:-}" && -f "${_EFFECTIVE_BUILD_CONFIG:-}" ]] '
        '&& cat "$_EFFECTIVE_BUILD_CONFIG"\n' + "true\n"
    )
    env = {**os.environ, **env_extra}
    out = _run_bash_script(script, env, check=True, cwd=cwd)
    cmd_part, _, config_part = out.stdout.partition(_CONFIG_CONTENT_MARKER + "\n")
    return cmd_part.splitlines(), out.stderr, config_part


def _run_region_raw(
    mode_marker: str,
    env_extra: dict[str, str],
    start_marker: str,
    end_marker: str,
    *,
    needs_merge_helper: bool = True,
    needs_release_style: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Like :func:`_run_region`, but ``check=False`` and returns the raw
    result -- for a region that's now expected to ``exit 1``, where
    ``check=True`` would raise before the caller could inspect anything."""
    script = (
        _harness(
            needs_merge_helper=needs_merge_helper,
            needs_release_style=needs_release_style,
        )
        + _compile_context_region(start_marker, end_marker, mode_marker)
        + "\nprintf '%s\\n' \"${CMD[@]}\"\n"
    )
    env = {**os.environ, **env_extra}
    return _run_bash_script(script, env, check=False)


def _find_config_yaml(config_yaml: str) -> dict[str, Any]:
    """Parse the merged ``.abicheck.yml`` content ``_run_region`` captured
    (see its own docstring for why this can't just re-open the ``--config``
    path after the subprocess exits)."""
    return yaml.safe_load(config_yaml) or {}


class TestCompileContextForwardingParity:
    """dump/compare/scan must fold ast-frontend/sysroot/nostdinc into the
    same merged compile: block, and forward the unaffected cross-compiler
    flags identically."""

    def test_dump_forwards_compiler_flags_and_merges_the_rest(self) -> None:
        cmd, _, _config_yaml = _run_region(
            _DUMP_MODE_MARKER, _FULL_ENV, _DUMP_CONTEXT_START, _DUMP_CONTEXT_END
        )
        assert "--compiler" in cmd and "/opt/gcc-14/bin/g++" in cmd
        assert "--compiler-prefix" in cmd and "aarch64-linux-gnu-" in cmd
        assert "--compiler-option" in cmd and "-DFOO=1" in cmd
        # The removed flags never appear literally -- Phase 7b's whole point.
        assert "--ast-frontend" not in cmd
        assert "--sysroot" not in cmd
        assert "--nostdinc" not in cmd
        assert "--config" in cmd
        merged = _find_config_yaml(_config_yaml)
        assert merged["compile"]["frontend"] == "clang"
        assert merged["compile"]["sysroot"] == "/opt/sysroot"
        assert merged["compile"]["nostdinc"] is True

    def test_compare_single_pair_forwards_compiler_flags_and_merges_the_rest(
        self,
    ) -> None:
        """Regression: compare used to forward only --ast-frontend, behind a
        comment incorrectly claiming the rest are dump-only — the CLI's
        `compare` command has shared `compile_context_options` (ADR-037 D3)
        the whole time. A single-pair (non-directory/package) old/new-library
        is required here since compare's forwarding is now gated to that
        path (see the release-style tests below)."""
        env = {
            **_FULL_ENV,
            "INPUT_OLD_LIBRARY": "old.so",
            "INPUT_NEW_LIBRARY": "new.so",
        }
        cmd, _, _config_yaml = _run_region(
            _COMPARE_MODE_MARKER,
            env,
            _COMPARE_COMPILE_CONTEXT_START,
            _COMPARE_COMPILE_CONTEXT_END,
            needs_release_style=True,
        )
        assert "--compiler" in cmd and "/opt/gcc-14/bin/g++" in cmd
        assert "--compiler-prefix" in cmd and "aarch64-linux-gnu-" in cmd
        assert "--compiler-option" in cmd and "-DFOO=1" in cmd
        assert "--ast-frontend" not in cmd
        assert "--sysroot" not in cmd
        assert "--nostdinc" not in cmd
        assert "--config" in cmd
        merged = _find_config_yaml(_config_yaml)
        assert merged["compile"]["frontend"] == "clang"
        assert merged["compile"]["sysroot"] == "/opt/sysroot"
        assert merged["compile"]["nostdinc"] is True

    def test_scan_merges_ast_frontend_sysroot_nostdinc(self) -> None:
        """Regression: scan forwarded none of these, even though
        `cli_scan.py` shares the identical `compile_context_options`
        decorator with dump (ADR-037 D3 / ADR-035 amendment)."""
        cmd, _, _config_yaml = _run_region(
            _SCAN_MODE_MARKER, _FULL_ENV, _SCAN_MERGE_START, _SCAN_MERGE_END
        )
        assert "--ast-frontend" not in cmd
        assert "--sysroot" not in cmd
        assert "--nostdinc" not in cmd
        assert "--config" in cmd
        merged = _find_config_yaml(_config_yaml)
        assert merged["compile"]["frontend"] == "clang"
        assert merged["compile"]["sysroot"] == "/opt/sysroot"
        assert merged["compile"]["nostdinc"] is True

    def test_scan_forwards_compiler_flags_once_each(self) -> None:
        """Regression (Codex review, PR #757): scan's cross-compiler block
        used to appear twice in run.sh -- harmless duplication for the old
        scalar --gcc-options (last-of-two-identical-values wins), but
        --compiler-option is `multiple=True` and genuinely accumulates every
        occurrence, so the duplicate silently doubled each forwarded token
        once the mechanical --gcc-options -> --compiler-option migration
        landed."""
        cmd, _, _config_yaml = _run_region(
            _SCAN_MODE_MARKER,
            _FULL_ENV,
            _SCAN_GCC_OPTIONS_START,
            _SCAN_GCC_OPTIONS_END,
            needs_merge_helper=False,
        )
        assert "--compiler" in cmd and "/opt/gcc-14/bin/g++" in cmd
        assert "--compiler-prefix" in cmd and "aarch64-linux-gnu-" in cmd
        assert "--compiler-option" in cmd and "-DFOO=1" in cmd
        assert cmd.count("--compiler") == 1
        assert cmd.count("--compiler-prefix") == 1
        assert cmd.count("--compiler-option") == 1

    def test_gcc_options_quoted_value_stays_one_token(self) -> None:
        """Regression (Codex review, PR #757): routing gcc-options through
        add_flag()'s plain bash word-splitting broke a shell-quoted value
        into malformed tokens -- `-DMSG="hello world" -DOK=1` word-split to
        `-DMSG="hello`, `world"`, `-DOK=1` instead of the two real tokens
        abicheck's own server-side shlex.split() used to produce for the old
        --gcc-options flag. add_flag_shlex_split() must reproduce that
        shlex-aware splitting, not add_flag()'s own naive one."""
        env = {**_FULL_ENV, "INPUT_GCC_OPTIONS": '-DMSG="hello world" -DOK=1'}
        cmd, _, _config_yaml = _run_region(
            _SCAN_MODE_MARKER,
            env,
            _SCAN_GCC_OPTIONS_START,
            _SCAN_GCC_OPTIONS_END,
            needs_merge_helper=False,
        )
        assert cmd.count("--compiler-option") == 2
        assert "-DMSG=hello world" in cmd
        assert "-DOK=1" in cmd
        # The malformed tokens a naive word-split would produce must be
        # absent.
        assert '-DMSG="hello' not in cmd
        assert 'world"' not in cmd

    def test_gcc_options_hash_character_is_not_treated_as_a_comment(self) -> None:
        """Regression (Codex review, PR #774): an earlier revision of
        add_flag_shlex_split()'s inline Python lexer disabled backslash
        escaping via a hand-rolled shlex.shlex(escape=""), which left
        shlex's default #-starts-a-comment behavior active and silently
        truncated any token containing `#`, dropping every flag after it.
        Mirrors abicheck._compiler_options.split_gcc_options's own
        regression test for the identical Python-side fix."""
        env = {**_FULL_ENV, "INPUT_GCC_OPTIONS": "-I/build/#generated -DOK=1"}
        cmd, _, _config_yaml = _run_region(
            _SCAN_MODE_MARKER,
            env,
            _SCAN_GCC_OPTIONS_START,
            _SCAN_GCC_OPTIONS_END,
            needs_merge_helper=False,
        )
        assert cmd.count("--compiler-option") == 2
        assert "-I/build/#generated" in cmd
        assert "-DOK=1" in cmd

    def test_gcc_options_backslash_escaped_space_is_honored(self) -> None:
        """Sibling regression (Codex review, PR #774): the same hand-rolled
        lexer broke a real POSIX backslash-escaped space into two tokens
        instead of one.

        Like :func:`test_gcc_options_unquoted_backslash_matches_the_real_python_helper`
        below, a backslash-before-whitespace's correct handling is
        platform-dependent by design: real POSIX collapses it into one
        token, but the Windows-only tokenizer deliberately does not (see
        ``_split_gcc_options_windows``'s own docstring, item #5) -- a
        revision hardcoding the POSIX answer here failed on windows-latest
        for exactly that reason. Asserted dynamically against the real
        Python helper instead of one hardcoded platform's answer."""
        value = r"-DMSG=hello\ world"
        expected_tokens = split_gcc_options(value)
        env = {**_FULL_ENV, "INPUT_GCC_OPTIONS": value}
        cmd, _, _config_yaml = _run_region(
            _SCAN_MODE_MARKER,
            env,
            _SCAN_GCC_OPTIONS_START,
            _SCAN_GCC_OPTIONS_END,
            needs_merge_helper=False,
        )
        assert cmd.count("--compiler-option") == len(expected_tokens)
        for token in expected_tokens:
            assert token in cmd

    def test_gcc_options_unquoted_backslash_matches_the_real_python_helper(
        self,
    ) -> None:
        """Third- and fourth-round regression (Codex review, PR #774): an
        unquoted backslash's correct handling is platform-dependent by
        design (``abicheck._compiler_options.split_gcc_options`` dispatches
        on ``os.name`` -- see that function's own docstring), so this test
        cannot hardcode one platform's answer without failing on the other
        CI lanes (a revision doing exactly that failed here on
        ubuntu-latest/macos-latest for hardcoding the Windows answer).
        Instead it asserts run.sh's forwarding stays in lockstep with
        whatever the real Python helper actually resolves to on *this*
        host -- proving the delegation (added specifically so run.sh
        carries no second copy of the tokenizer to drift out of sync) is
        faithful, independent of which platform runs the test. The
        Windows-specific tokenizer behavior itself is covered directly,
        regardless of host OS, by
        ``tests/test_compiler_options.py::TestSplitGccOptionsWindows``."""
        value = r"-IC:\mypath\include -DFOO=bar"
        expected_tokens = split_gcc_options(value)
        env = {**_FULL_ENV, "INPUT_GCC_OPTIONS": value}
        cmd, _, _config_yaml = _run_region(
            _SCAN_MODE_MARKER,
            env,
            _SCAN_GCC_OPTIONS_START,
            _SCAN_GCC_OPTIONS_END,
            needs_merge_helper=False,
        )
        assert cmd.count("--compiler-option") == len(expected_tokens)
        for token in expected_tokens:
            assert token in cmd

    def _env_with_unusable_python(self, tmp_path: Path) -> dict[str, str]:
        """A fake ``python3`` on ``PATH`` that can run but can never import
        ``abicheck`` -- makes ``$_PY_BIN_HAS_ABICHECK`` resolve ``false``
        without needing a second, genuinely abicheck-less Python
        installation."""
        fake_bin = tmp_path / "fakebin"
        fake_bin.mkdir()
        fake_python3 = fake_bin / "python3"
        fake_python3.write_text("#!/bin/bash\nexit 1\n")
        fake_python3.chmod(0o755)
        return {"PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}"}

    def test_gcc_options_needing_real_parser_fails_loud_without_one(
        self, tmp_path: Path
    ) -> None:
        """Codex review, fresh evidence, second round: falling back to
        add_flag()'s naive whitespace split when the real Python parser is
        unavailable silently corrupted a quoted value
        (``-DMSG="hello world"``) into malformed tokens under a wrong
        compile context instead of failing. A value that actually needs
        real quote-aware parsing must fail the Action loud."""
        env = {
            **_FULL_ENV,
            **self._env_with_unusable_python(tmp_path),
            "INPUT_GCC_OPTIONS": '-DMSG="hello world" -DOK=1',
        }
        result = _run_region_raw(
            _SCAN_MODE_MARKER,
            env,
            _SCAN_GCC_OPTIONS_START,
            _SCAN_GCC_OPTIONS_END,
            needs_merge_helper=False,
        )
        assert result.returncode == 1
        assert "::error::" in result.stdout
        assert "quoting/escaping" in result.stdout

    def test_gcc_options_simple_value_still_falls_back_without_real_parser(
        self, tmp_path: Path
    ) -> None:
        """Companion to the test above: a value with no quoting/escaping at
        all is provably identical whether split by the real parser or by
        add_flag()'s naive whitespace split, so this must still succeed via
        the plain-whitespace-split fallback rather than fail unnecessarily."""
        env = {
            **_FULL_ENV,
            **self._env_with_unusable_python(tmp_path),
            "INPUT_GCC_OPTIONS": "-DFOO=1 -DBAR=2",
        }
        cmd, _, _config_yaml = _run_region(
            _SCAN_MODE_MARKER,
            env,
            _SCAN_GCC_OPTIONS_START,
            _SCAN_GCC_OPTIONS_END,
            needs_merge_helper=False,
        )
        assert cmd.count("--compiler-option") == 2
        assert "-DFOO=1" in cmd
        assert "-DBAR=2" in cmd

    def test_gcc_options_glob_metacharacters_fail_loud_without_real_parser(
        self, tmp_path: Path
    ) -> None:
        """Codex review, fresh evidence, third round: at the time this test
        was written, add_flag()'s own unquoted `for item in $value`
        performed pathname (glob) EXPANSION, not just whitespace splitting
        -- a value like `-DPATTERN=*` would silently rewrite to whatever
        filenames exist in the current directory (the analyzed, potentially
        PR-controlled checkout) at the time this fallback runs, so a value
        containing a glob metacharacter had to fail loud rather than be
        treated as safe to fall back on.

        add_flag() itself no longer glob-expands (Phase 8, PR #919 --
        `_split_legacy_value`'s `set -f`; see
        `test_add_flag_no_longer_glob_expands_after_the_fix` below for the
        direct proof), but this refusal is kept as-is: `--compiler-option`
        values have compiler-flag quoting semantics add_flag() was never
        meant to interpret (that's the whole reason the real shlex-aware
        parser exists), so refusing to guess here remains the conservative,
        correct choice independent of whether the specific glob-expansion
        vector is also closed one layer down."""
        env = {
            **_FULL_ENV,
            **self._env_with_unusable_python(tmp_path),
            "INPUT_GCC_OPTIONS": "-DPATTERN=*",
        }
        result = _run_region_raw(
            _SCAN_MODE_MARKER,
            env,
            _SCAN_GCC_OPTIONS_START,
            _SCAN_GCC_OPTIONS_END,
            needs_merge_helper=False,
        )
        assert result.returncode == 1
        assert "::error::" in result.stdout
        assert "glob metacharacters" in result.stdout

    def test_add_flag_no_longer_glob_expands_after_the_fix(
        self, tmp_path: Path
    ) -> None:
        """Direct regression pin for the Phase 8 fix (PR #919,
        `_split_legacy_value`'s `set -f` in `action/run.sh`).

        Until that fix, this same scenario -- a real file planted with the
        exact glob pattern as its name, in add_flag()'s own working
        directory -- demonstrated the opposite of what's asserted below:
        add_flag()'s unquoted `for item in $value`, with no glob-
        metacharacter guard of its own, really did expand the pattern
        against a file present in the current directory rather than
        treating it as a literal value (bash word-splits on whitespace
        first, then glob-expands each resulting word, so the whole token is
        the glob pattern here). That was the reproduction this test file's
        sibling test above exists to defend a *different* code path
        (`add_flag_shlex_split`'s fallback) against; this one exercises
        add_flag() itself, which had no equivalent guard until this fix."""
        (tmp_path / "-DPATTERN=PLANTED_FILE").write_text("")
        script = (
            "CMD=()\n"
            + _add_flag_source()
            + 'add_flag "--compiler-option" "-DPATTERN=*"\n'
            + "printf '%s\\n' \"${CMD[@]}\"\n"
        )
        result = _run_bash_script(script, check=False, cwd=tmp_path, timeout=30)
        assert result.returncode == 0, result.stderr
        assert "-DPATTERN=PLANTED_FILE" not in result.stdout.splitlines()
        assert "-DPATTERN=*" in result.stdout.splitlines()

    def test_gcc_options_malformed_quoting_fails_loud(self) -> None:
        """Codex review, fresh evidence, second round: split_gcc_options()
        raises ValueError on malformed quoting (e.g. an unbalanced quote);
        without checking the command substitution's exit status (this
        script deliberately has no `set -e`), execution silently continued
        with an empty $split, dropping every requested compiler option
        instead of failing on the invalid input."""
        env = {**_FULL_ENV, "INPUT_GCC_OPTIONS": '-DMSG="unterminated'}
        result = _run_region_raw(
            _SCAN_MODE_MARKER,
            env,
            _SCAN_GCC_OPTIONS_START,
            _SCAN_GCC_OPTIONS_END,
            needs_merge_helper=False,
        )
        assert result.returncode == 1
        assert "::error::" in result.stdout
        assert "could not be parsed" in result.stdout

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason=(
            "The CRLF transport is simulated with a bash `python3` wrapper; a "
            "Windows interpreter path embedded in that script would itself be "
            "corrupted by backslash escaping, and the scenario this test "
            "reproduces (fake python3 standing in for python.exe) is POSIX-only "
            "by its own premise -- a real windows-latest run already exercises "
            "the real CRLF transport directly."
        ),
    )
    def test_gcc_options_strips_crlf_from_windows_python_output(
        self, tmp_path: Path
    ) -> None:
        """Codex review, seventh round: on windows-latest, ``$_PY_BIN``
        resolves to native ``python.exe``, whose ``print()`` writes CRLF
        line endings by default (Python's text-mode stdout translates
        ``"\\n"`` to ``os.linesep`` on write, regardless of whether stdout
        is a console or -- as here -- a pipe). bash's ``read`` only splits
        on LF, so without stripping it, every forwarded token would gain a
        trailing ``\\r`` (e.g. ``-DFOO=1`` arriving as ``-DFOO=1\\r``),
        corrupting every downstream compiler invocation. This can't be
        reproduced with the real ``python3`` on this (POSIX) host --
        ``os.linesep`` is already ``"\\n"`` here -- so a fake ``python3`` on
        ``PATH`` stands in for ``python.exe``, wrapping the real
        interpreter's output with a CRLF-emitting filter, the same
        transport shape a real Windows run would produce.

        Deliberately does NOT go through :func:`_run_region` -- its own
        ``printf '%s\\n' "${CMD[@]}"`` capture, read back via
        ``subprocess.run(text=True)`` /``.splitlines()``, treats a
        corrupted ``token\\r`` immediately followed by that printf's own
        ``\\n`` as one ordinary CRLF line ending and silently normalizes it
        away -- exactly the masking Codex's review comment named, and
        confirmed here by first writing this test against the *unfixed*
        code: ``_run_region``-based assertions kept passing even with
        ``action/run.sh``'s CR-stripping line deleted. A NUL-delimited,
        raw-bytes capture (no ``text=True``, no intervening ``\\n`` anywhere
        near the ``\\r``) is the one shape that can't launder the bug away
        the same way.
        """
        real_python3 = sys.executable
        fake_python3 = tmp_path / "python3"
        fake_python3.write_text(
            # Plain `sed` (not GNU-only `sed -u`): the test reads the full
            # output only after the process exits, so line buffering buys
            # nothing here, and `-u` is unsupported by macOS's stock `sed`
            # (Codex review, fresh evidence).
            f'#!/bin/bash\nexec "{real_python3}" "$@" | sed $\'s/$/\\r/\'\n'
        )
        fake_python3.chmod(0o755)
        script = (
            _harness(needs_merge_helper=False, needs_release_style=False)
            + _compile_context_region(
                _SCAN_GCC_OPTIONS_START, _SCAN_GCC_OPTIONS_END, _SCAN_MODE_MARKER
            )
            + "\nprintf '%s\\0' \"${CMD[@]}\"\n"
        )
        env = {
            **os.environ,
            **_FULL_ENV,
            "INPUT_GCC_OPTIONS": "-DFOO=1 -DBAR=2",
            "PATH": f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}",
        }
        result = _run_bash_script(script, env, check=True, text=False)
        cmd = [tok.decode("utf-8") for tok in result.stdout.split(b"\0") if tok]
        assert cmd.count("--compiler-option") == 2
        assert "-DFOO=1" in cmd
        assert "-DBAR=2" in cmd
        assert not any("\r" in token for token in cmd)

    def test_compare_omits_unset_flags(self) -> None:
        cmd, _, _config_yaml = _run_region(
            _COMPARE_MODE_MARKER,
            {"INPUT_OLD_LIBRARY": "old.so", "INPUT_NEW_LIBRARY": "new.so"},
            _COMPARE_COMPILE_CONTEXT_START,
            _COMPARE_COMPILE_CONTEXT_END,
            needs_release_style=True,
        )
        assert "--compiler" not in cmd
        assert "--ast-frontend" not in cmd
        assert "--sysroot" not in cmd
        assert "--nostdinc" not in cmd
        # No build-config and no compile-context inputs set at all: no merge
        # needed, so --config is entirely absent (not even an empty value).
        assert "--config" not in cmd

    def test_scan_omits_unset_flags(self) -> None:
        cmd, _, _config_yaml = _run_region(
            _SCAN_MODE_MARKER, {}, _SCAN_MERGE_START, _SCAN_MERGE_END
        )
        assert "--ast-frontend" not in cmd
        assert "--sysroot" not in cmd
        assert "--nostdinc" not in cmd
        assert "--config" not in cmd

    def test_compare_fails_loud_for_compile_context_against_release_style_operand(
        self,
    ) -> None:
        """Regression (Codex review): the CLI hard-rejects these flags for
        directory/package operands (a UsageError, exit 64) since the
        per-library release fan-out never threads a CompileContext to each
        pair's header dump. A prior fix gated them to the single-pair path
        but only warned and continued for a directory operand — silently
        running the comparison with headers parsed under the wrong
        macros/sysroot/frontend instead of the intended cross-compile
        context. Must fail loud instead (a second Codex round), matching
        the evidence-flags guard's already-established treatment of the
        same "explicitly-configured input the fan-out can't honor" shape."""
        env = {
            **_FULL_ENV,
            "INPUT_OLD_LIBRARY": str(RUN_SH.parent),  # any real directory
            "INPUT_NEW_LIBRARY": "new.so",
        }
        result = _run_region_raw(
            _COMPARE_MODE_MARKER,
            env,
            _COMPARE_COMPILE_CONTEXT_START,
            _COMPARE_COMPILE_CONTEXT_END,
            needs_release_style=True,
        )
        assert result.returncode != 0
        assert "not support" in result.stdout

    def test_compare_release_style_succeeds_when_context_unset(self) -> None:
        """Companion: a plain directory/package compare with no compile-
        context inputs configured must still succeed (only fails when a
        flag was actually configured and would be dropped)."""
        cmd, stderr, _config_yaml = _run_region(
            _COMPARE_MODE_MARKER,
            {
                "INPUT_OLD_LIBRARY": str(RUN_SH.parent),
                "INPUT_NEW_LIBRARY": "new.so",
            },
            _COMPARE_COMPILE_CONTEXT_START,
            _COMPARE_COMPILE_CONTEXT_END,
            needs_release_style=True,
        )
        assert "not support" not in stderr

    def test_compare_release_style_succeeds_with_ast_frontend_auto(self) -> None:
        """Regression (Codex review, second round): "auto" is the
        documented no-op spelling of ast-frontend -- it resolves to the
        same default castxml selection as leaving the input unset entirely
        (see the input's description in action.yml), so a workflow that
        spells it out explicitly requests nothing the release fan-out
        could actually drop. Must not trip the fail-loud guard, unlike a
        real frontend choice such as "clang"."""
        cmd, stderr, _config_yaml = _run_region(
            _COMPARE_MODE_MARKER,
            {
                "INPUT_OLD_LIBRARY": str(RUN_SH.parent),
                "INPUT_NEW_LIBRARY": "new.so",
                "INPUT_AST_FRONTEND": "auto",
            },
            _COMPARE_COMPILE_CONTEXT_START,
            _COMPARE_COMPILE_CONTEXT_END,
            needs_release_style=True,
        )
        assert "not support" not in stderr
        assert "--ast-frontend" not in cmd

    def test_compare_release_style_fails_with_ast_frontend_clang(self) -> None:
        """Companion: an actual, non-"auto" frontend choice still trips
        the guard -- only the documented no-op spelling is exempt."""
        result = _run_region_raw(
            _COMPARE_MODE_MARKER,
            {
                "INPUT_OLD_LIBRARY": str(RUN_SH.parent),
                "INPUT_NEW_LIBRARY": "new.so",
                "INPUT_AST_FRONTEND": "clang",
            },
            _COMPARE_COMPILE_CONTEXT_START,
            _COMPARE_COMPILE_CONTEXT_END,
            needs_release_style=True,
        )
        assert result.returncode != 0
        assert "not support" in result.stdout

    def test_ast_frontend_input_wins_over_a_conflicting_build_config_value(
        self, tmp_path: Path
    ) -> None:
        """Precedence rule this fix must reproduce (see
        _resolve_effective_build_config's own docstring in run.sh): an
        Action input wins over a same-key value already in the caller's own
        build-config -- mirroring cli_options.py's pre-Phase-7b "CLI flag >
        config" rule, and ADR-049 D7's explicit_cli/api_request > ...  >
        project_config precedence."""
        build_config = tmp_path / "conflict.abicheck.yml"
        build_config.write_text(
            "compile:\n  frontend: castxml\n  sysroot: /old/sysroot\n"
            "  std: c++17\nseverity:\n  preset: strict\n",
            encoding="utf-8",
        )
        env = {
            **_FULL_ENV,
            "INPUT_BUILD_CONFIG": str(build_config),
        }
        cmd, _, _config_yaml = _run_region(
            _DUMP_MODE_MARKER, env, _DUMP_CONTEXT_START, _DUMP_CONTEXT_END
        )
        merged = _find_config_yaml(_config_yaml)
        assert merged["compile"]["frontend"] == "clang"
        assert merged["compile"]["sysroot"] == "/opt/sysroot"
        assert merged["compile"]["nostdinc"] is True
        # Untouched blocks/keys survive the merge.
        assert merged["compile"]["std"] == "c++17"
        assert merged["severity"]["preset"] == "strict"
        # The caller's own file on disk is never mutated in place.
        assert "castxml" in build_config.read_text(encoding="utf-8")

    def test_no_merge_needed_passes_the_build_config_through_unchanged(self) -> None:
        """When none of ast-frontend/sysroot/nostdinc are set, --config
        forwards the caller's own build-config path verbatim -- no scratch
        file, no Python merge step, same as before this fix existed."""
        env = {"INPUT_BUILD_CONFIG": "my.abicheck.yml"}
        cmd, _, _config_yaml = _run_region(
            _DUMP_MODE_MARKER, env, _DUMP_CONTEXT_START, _DUMP_CONTEXT_END
        )
        idx = cmd.index("--config")
        assert cmd[idx + 1] == "my.abicheck.yml"

    def test_relative_build_config_is_anchored_before_the_python_cd(
        self, tmp_path: Path
    ) -> None:
        """Regression pin: _resolve_effective_build_config's Python merge
        step runs from inside $_PY_SAFE_DIR (a *different* CWD, for
        import-shadowing safety) -- a relative build-config path must be
        anchored to the caller's own $PWD first, or the merge can't find a
        file that genuinely exists there."""
        (tmp_path / "relative.abicheck.yml").write_text(
            "severity:\n  preset: strict\n", encoding="utf-8"
        )
        env = {**_FULL_ENV, "INPUT_BUILD_CONFIG": "relative.abicheck.yml"}
        _cmd, _, config_yaml = _run_region(
            _DUMP_MODE_MARKER, env, _DUMP_CONTEXT_START, _DUMP_CONTEXT_END, cwd=tmp_path
        )
        merged = _find_config_yaml(config_yaml)
        assert merged["severity"]["preset"] == "strict"
        assert merged["compile"]["frontend"] == "clang"

    def test_missing_build_config_fails_loud(self, tmp_path: Path) -> None:
        env = {
            **_FULL_ENV,
            "INPUT_BUILD_CONFIG": str(tmp_path / "does-not-exist.abicheck.yml"),
        }
        result = _run_region_raw(
            _DUMP_MODE_MARKER, env, _DUMP_CONTEXT_START, _DUMP_CONTEXT_END
        )
        assert result.returncode == 1
        # _resolve_effective_build_config's own errors go to stderr, not
        # stdout -- its callers capture stdout via `$(...)` as the merged
        # config *path*, so an unredirected echo would be silently
        # swallowed into that variable instead of ever surfacing.
        assert "::error::" in result.stderr
        assert "does not exist" in result.stderr

    def test_merge_needs_python_with_abicheck_importable(self, tmp_path: Path) -> None:
        """Without a working Python that can import abicheck, the merge
        cannot run -- must fail loud (not silently drop ast-frontend/
        sysroot/nostdinc, which would revert to the wrong compile context
        with no signal)."""
        env = {**_FULL_ENV, **self._env_with_unusable_python(tmp_path)}
        result = _run_region_raw(
            _DUMP_MODE_MARKER, env, _DUMP_CONTEXT_START, _DUMP_CONTEXT_END
        )
        assert result.returncode == 1
        assert "::error::" in result.stderr
        assert "no working Python interpreter" in result.stderr
