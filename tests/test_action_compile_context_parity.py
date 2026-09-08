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

The three CLI subcommands used to share ``compile_context_options`` (the
``ast-frontend``/``gcc-path``/``gcc-prefix``/``gcc-options``/``--sysroot``/
``--nostdinc``, ADR-037 D3) as CLI flags. Phase 7
(one-comparison-product.md §4.1/§4.2, ADR-037 D8.1) removed all of those
(plus ``--lang``) from ``compare``/``dump`` entirely -- CONFIG class, no
surviving CLI override -- so ``action/run.sh`` now synthesizes a
``.abicheck.yml`` ``compile:`` block from its own cross-compilation inputs
and forwards it via ``--config`` instead
(:func:`add_compile_context_flags`, extracted verbatim below). ``scan``
is unaffected: it keeps every one of these flags, so its own region below
is untouched and still asserts literal flag forwarding.

``add_compile_context_flags`` no longer writes its synthesized overlay
directly (Codex review, PR #1159 P1): an explicit ``--config`` fully
replaces ``_resolve_compare_config``'s own auto-discovery of the
repository's ``.abicheck.yml``, so it now routes through the shared
``_merge_config_overlay_with_discovered_project_config`` helper (also
extracted below), which discovers and merges in the real project config
before writing the file ``--config`` points at -- see
``test_action_release_topology_config.py``'s module docstring for the full
account (that module carries the dedicated merge-behavior regression
tests; this one stays focused on flag-forwarding parity, isolating its own
harness's working directory from any real ``.abicheck.yml`` purely so the
merge step degrades to its pre-merge no-op base case here).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

from abicheck._compiler_options import split_gcc_options

RUN_SH = Path(__file__).resolve().parents[1] / "action" / "run.sh"

_DUMP_MODE_MARKER = 'if [[ "$MODE" == "dump" ]]; then'
_COMPARE_MODE_MARKER = 'elif [[ "$MODE" == "compare" ]]; then'
_SCAN_MODE_MARKER = 'elif [[ "$MODE" == "scan" ]]; then'

_COMPILE_CONTEXT_START = 'add_single_flag "--ast-frontend" "${INPUT_AST_FRONTEND:-}"'
# scan has no release fan-out, so its region ends at the nostdinc if-block;
# anchor past its closing "fi" so the extracted fragment is syntactically
# complete.
_COMPILE_CONTEXT_END = (
    'if [[ "${INPUT_NOSTDINC:-false}" == "true" ]]; then\n    CMD+=(--nostdinc)\n  fi'
)

# dump's region (Phase 7) is now the single call to the shared helper.
_DUMP_COMPILE_CONTEXT_START = "add_compile_context_flags true"
_DUMP_COMPILE_CONTEXT_END = "add_compile_context_flags true"

# compare's region (Phase 7) starts at the gating comment (these inputs are
# gated to the single-pair path, since the release fan-out can't thread a
# CompileContext to each pair's header dump) and ends at the single-pair
# branch's call to the shared helper.
_COMPARE_COMPILE_CONTEXT_START = (
    "# The L2 compile-context inputs (ast-frontend/gcc-*/sysroot/nostdinc/lang)"
)
_COMPARE_COMPILE_CONTEXT_END = "else\n    add_compile_context_flags true\n  fi"

# add_compile_context_flags() itself (Phase 7): extracted verbatim, since
# dump's and compare's regions both call it now instead of forwarding flags
# directly -- same "parse the real file, don't hand-copy it" discipline as
# the rest of this module. Its Python heredoc body contains no line
# matching either boundary marker, so a plain substring search is safe.
_COMPILE_CONTEXT_FN_START = '_COMPILE_CONTEXT_CONFIG_OVERLAY=""'
_COMPILE_CONTEXT_FN_END = '\n  CMD+=(--config "$_COMPILE_CONTEXT_CONFIG_OVERLAY")\n}\n'


def _add_compile_context_flags_source() -> str:
    text = RUN_SH.read_text(encoding="utf-8")
    start = text.index(_COMPILE_CONTEXT_FN_START)
    end = text.index(_COMPILE_CONTEXT_FN_END, start) + len(_COMPILE_CONTEXT_FN_END)
    return text[start:end]


# _is_release_style_operand is defined once, well before any mode branch;
# compare's extracted region calls it, so the harness needs its real
# definition rather than a hand-copied stub (same "parse the real file"
# discipline as the rest of this module).
_IS_RELEASE_STYLE_OPERAND_START = "_is_release_style_operand() {"
_IS_RELEASE_STYLE_OPERAND_END = "\n}\n"


def _is_release_style_operand_source() -> str:
    text = RUN_SH.read_text(encoding="utf-8")
    start = text.index(_IS_RELEASE_STYLE_OPERAND_START)
    end = text.index(_IS_RELEASE_STYLE_OPERAND_END, start) + len(
        _IS_RELEASE_STYLE_OPERAND_END
    )
    return text[start:end]


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


# The merge helper add_compile_context_flags now routes its overlay through
# (module docstring) -- extracted verbatim, same markers
# test_action_release_topology_config.py's own
# `_merge_config_overlay_fn_source` uses.
_MERGE_FN_START = "_merge_config_overlay_with_discovered_project_config() {"
_MERGE_FN_END = "\n_COMPILE_CONTEXT_CONFIG_OVERLAY="


def _merge_config_overlay_fn_source() -> str:
    text = RUN_SH.read_text(encoding="utf-8")
    start = text.index(_MERGE_FN_START)
    end = text.index(_MERGE_FN_END, start)
    return text[start:end]


# `_merge_config_overlay_with_discovered_project_config`'s own
# `base_source` absolutization (Codex review, PR #1159, fourth round) now
# delegates to `_is_path_already_qualified` (a real Windows drive/UNC/
# root-relative path must not get a `$PWD/` prefix) rather than a
# POSIX-only `!= /*` test -- so any harness including the merge function
# must also define this helper (and the `$OSTYPE`-derived
# `$_RUNNING_ON_WINDOWS` it reads), the same verbatim-extraction discipline
# `test_action_run_sh_py_safe_path.py`'s own
# `_path_qualified_helper_source` already established, and
# `test_action_release_topology_config.py`'s own sibling copy mirrors.
_PATH_QUALIFIED_HELPER_START = 'case "$OSTYPE" in'
_PATH_QUALIFIED_HELPER_END = "\n}\n"


def _path_qualified_helper_source() -> str:
    text = RUN_SH.read_text(encoding="utf-8")
    start = text.index(_PATH_QUALIFIED_HELPER_START)
    end = text.index(_PATH_QUALIFIED_HELPER_END, start) + len(
        _PATH_QUALIFIED_HELPER_END
    )
    return text[start:end]


# add_compile_context_flags's overlay `mktemp` result is now canonicalized
# via `_mktemp_canonical` (relative $TMPDIR broke the merge subprocess,
# Codex review PR #1159 sixth round); mirrors the sibling topology module.
_MKTEMP_CANONICAL_START = "_mktemp_canonical() {"
_MKTEMP_CANONICAL_END = "\n}\n"


def _mktemp_canonical_source() -> str:
    text = RUN_SH.read_text(encoding="utf-8")
    start = text.index(_MKTEMP_CANONICAL_START)
    end = text.index(_MKTEMP_CANONICAL_END, start) + len(_MKTEMP_CANONICAL_END)
    return text[start:end]


_END_MARKER_FOR_START: dict[str, str] = {
    _COMPARE_COMPILE_CONTEXT_START: _COMPARE_COMPILE_CONTEXT_END,
    _DUMP_COMPILE_CONTEXT_START: _DUMP_COMPILE_CONTEXT_END,
}


def _compile_context_region(
    mode_marker: str, start_marker: str = _COMPILE_CONTEXT_START
) -> str:
    """Extract one mode's compile-context flag-forwarding block verbatim."""
    text = RUN_SH.read_text(encoding="utf-8")
    mode_start = text.index(mode_marker)
    start = text.index(start_marker, mode_start)
    end_marker = _END_MARKER_FOR_START.get(start_marker, _COMPILE_CONTEXT_END)
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


def _mode_value_for_marker(mode_marker: str) -> str:
    return {
        _DUMP_MODE_MARKER: "dump",
        _COMPARE_MODE_MARKER: "compare",
        _SCAN_MODE_MARKER: "scan",
    }[mode_marker]


def _run_region(
    mode_marker: str,
    env_extra: dict[str, str],
    start_marker: str = _COMPILE_CONTEXT_START,
) -> tuple[list[str], str]:
    # add_single_flag is defined earlier in run.sh; redefine a minimal
    # equivalent here since only the compile-context region is extracted,
    # not the whole file (keeps the harness self-contained and fast).
    # _is_release_style_operand is extracted from the real file (only
    # compare's region calls it, but defining it unconditionally is
    # harmless for dump/scan). add_compile_context_flags (Phase 7) is
    # extracted from the real file too -- dump's and compare's regions both
    # call it now instead of forwarding flags directly.
    harness = (
        f'MODE="{_mode_value_for_marker(mode_marker)}"\n'
        'add_single_flag() { [[ -n "$2" ]] && CMD+=("$1" "$2"); }\n'
        # add_flag_shlex_split() (extracted as part of _add_flag_source())
        # needs _PY_BIN resolved, same as the real script does near its own
        # top -- otherwise the harness silently falls back to add_flag()'s
        # own naive splitting and a quoting regression would go undetected.
        # $_PY_SAFE_DIR (extracted verbatim, not redefined -- see its own
        # extraction function's docstring) is the second such prerequisite.
        '_PY_BIN="$(command -v python3 || command -v python || true)"\n'
        + _py_safe_dir_source()
        + _py_bin_has_abicheck_source()
        + _path_qualified_helper_source()
        + _mktemp_canonical_source()
        + _merge_config_overlay_fn_source()
        + _add_flag_source()
        + _is_release_style_operand_source()
        + _add_compile_context_flags_source()
        + "\nCMD=()\n"
    )
    script = (
        harness
        + _compile_context_region(mode_marker, start_marker)
        + "\nprintf '%s\\n' \"${CMD[@]}\"\n"
    )
    env = {**os.environ, **env_extra}
    # add_compile_context_flags now discovers a project .abicheck.yml from
    # its caller's own working directory and merges it into the synthesized
    # overlay (module docstring) -- an isolated, freshly created directory
    # keeps every parity test here exercising the pre-merge "no project
    # config found" base case, regardless of what happens to exist above
    # wherever this test process's own CWD is.
    with tempfile.TemporaryDirectory() as isolated_cwd:
        out = _run_bash_script(script, env, check=True, cwd=Path(isolated_cwd))
    return out.stdout.splitlines(), out.stderr


def _run_region_raw(
    mode_marker: str,
    env_extra: dict[str, str],
    start_marker: str = _COMPILE_CONTEXT_START,
) -> subprocess.CompletedProcess[str]:
    """Like :func:`_run_region`, but ``check=False`` and returns the raw
    result -- for a region that's now expected to ``exit 1``, where
    ``check=True`` would raise before the caller could inspect anything."""
    harness = (
        f'MODE="{_mode_value_for_marker(mode_marker)}"\n'
        'add_single_flag() { [[ -n "$2" ]] && CMD+=("$1" "$2"); }\n'
        # add_flag_shlex_split() (extracted as part of _add_flag_source())
        # needs _PY_BIN resolved, same as the real script does near its own
        # top -- otherwise the harness silently falls back to add_flag()'s
        # own naive splitting and a quoting regression would go undetected.
        # $_PY_SAFE_DIR (extracted verbatim, not redefined -- see its own
        # extraction function's docstring) is the second such prerequisite.
        '_PY_BIN="$(command -v python3 || command -v python || true)"\n'
        + _py_safe_dir_source()
        + _py_bin_has_abicheck_source()
        + _path_qualified_helper_source()
        + _mktemp_canonical_source()
        + _merge_config_overlay_fn_source()
        + _add_flag_source()
        + _is_release_style_operand_source()
        + _add_compile_context_flags_source()
        + "\nCMD=()\n"
    )
    script = (
        harness
        + _compile_context_region(mode_marker, start_marker)
        + "\nprintf '%s\\n' \"${CMD[@]}\"\n"
    )
    env = {**os.environ, **env_extra}
    # See _run_region's identical isolation comment above.
    with tempfile.TemporaryDirectory() as isolated_cwd:
        return _run_bash_script(script, env, check=False, cwd=Path(isolated_cwd))


def _read_compile_config_overlay(cmd: list[str]) -> dict[str, Any]:
    """Read back the synthesized ``compile:`` block a ``--config <path>``
    entry in *cmd* points at (Phase 7's replacement for individually
    forwarded flags on dump/compare)."""
    assert "--config" in cmd, cmd
    path = cmd[cmd.index("--config") + 1]
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    return doc.get("compile", {})


class TestCompileContextForwardingParity:
    """scan forwards the six flags directly; dump/compare (Phase 7) forward
    the identical settings via a synthesized --config compile: block."""

    def test_dump_forwards_all_six_flags(self) -> None:
        cmd, _ = _run_region(_DUMP_MODE_MARKER, _FULL_ENV, _DUMP_COMPILE_CONTEXT_START)
        compile_blk = _read_compile_config_overlay(cmd)
        assert compile_blk["frontend"] == "clang"
        # gcc_path wins over gcc_prefix when both are given (the merged
        # compile.compiler field can only hold one) -- see
        # add_compile_context_flags's own docstring/comment.
        assert compile_blk["compiler"] == "/opt/gcc-14/bin/g++"
        assert compile_blk["options"] == ["-DFOO=1"]
        assert compile_blk["sysroot"] == "/opt/sysroot"
        assert compile_blk["nostdinc"] is True

    def test_compare_forwards_all_six_flags(self) -> None:
        """Regression: compare used to forward only --ast-frontend, behind a
        comment incorrectly claiming the rest are dump-only — the CLI's
        `compare` command has shared `compile_context_options` (ADR-037 D3)
        the whole time. Phase 7 then removed all of them from the CLI, so
        this now asserts the synthesized --config overlay instead. A
        single-pair (non-directory/package) old/new-library is required
        here since compare's forwarding is gated to that path (see the
        release-style tests below)."""
        env = {
            **_FULL_ENV,
            "INPUT_OLD_LIBRARY": "old.so",
            "INPUT_NEW_LIBRARY": "new.so",
        }
        cmd, _ = _run_region(_COMPARE_MODE_MARKER, env, _COMPARE_COMPILE_CONTEXT_START)
        compile_blk = _read_compile_config_overlay(cmd)
        assert compile_blk["frontend"] == "clang"
        assert compile_blk["compiler"] == "/opt/gcc-14/bin/g++"
        assert compile_blk["options"] == ["-DFOO=1"]
        assert compile_blk["sysroot"] == "/opt/sysroot"
        assert compile_blk["nostdinc"] is True

    def test_dump_gcc_options_multiline_is_one_token_per_line(self) -> None:
        """CodeRabbit review, PR #1146, finding #7: a multi-line gcc-options
        value must become one ``compile.options`` list entry per nonempty
        line, matching how scan's own equivalent flag path
        (``add_flag_shlex_split``) treats a multi-line value -- one line is
        already one complete, space-safe token, never shlex-split further.
        Unconditional ``shlex.split()`` previously re-tokenized on every
        whitespace character regardless of line breaks."""
        env = {**_FULL_ENV, "INPUT_GCC_OPTIONS": "-march=armv8-a\n-DFOO=1\n"}
        cmd, _ = _run_region(_DUMP_MODE_MARKER, env, _DUMP_COMPILE_CONTEXT_START)
        compile_blk = _read_compile_config_overlay(cmd)
        assert compile_blk["options"] == ["-march=armv8-a", "-DFOO=1"]

    def test_dump_gcc_options_multiline_preserves_a_spaced_line_verbatim(
        self,
    ) -> None:
        """A multi-line value carrying a deliberately-spaced line is passed
        through as one (whitespace-bearing) atom, exactly as scan's own
        multi-line handling would forward it as one ``--compiler-option``
        occurrence -- consistently rejected downstream by
        ``BuildConfig.from_dict()``'s per-atom whitespace rule (a clear
        error at abicheck invocation time), rather than silently accepted
        by a `shlex.split()` that would have torn it into multiple tokens
        and diverged from scan's own token boundaries for the identical
        input."""
        env = {**_FULL_ENV, "INPUT_GCC_OPTIONS": "-Xclang -load\n./evil.so\n"}
        cmd, _ = _run_region(_DUMP_MODE_MARKER, env, _DUMP_COMPILE_CONTEXT_START)
        compile_blk = _read_compile_config_overlay(cmd)
        assert compile_blk["options"] == ["-Xclang -load", "./evil.so"]

    def test_compare_gcc_options_multiline_is_one_token_per_line(self) -> None:
        """Same fix, compare's own synthesis call site."""
        env = {
            **_FULL_ENV,
            "INPUT_OLD_LIBRARY": "old.so",
            "INPUT_NEW_LIBRARY": "new.so",
            "INPUT_GCC_OPTIONS": "-march=armv8-a\n-DFOO=1\n",
        }
        cmd, _ = _run_region(_COMPARE_MODE_MARKER, env, _COMPARE_COMPILE_CONTEXT_START)
        compile_blk = _read_compile_config_overlay(cmd)
        assert compile_blk["options"] == ["-march=armv8-a", "-DFOO=1"]

    def test_dump_gcc_options_single_line_still_shlex_splits(self) -> None:
        """Single-line gcc-options keeps its existing shell-quoting-aware
        splitting (the direct config-key replacement for the old scalar
        --gcc-options flag) -- only the multi-line handling changed."""
        env = {**_FULL_ENV, "INPUT_GCC_OPTIONS": '-DMSG="hello world" -DOK=1'}
        cmd, _ = _run_region(_DUMP_MODE_MARKER, env, _DUMP_COMPILE_CONTEXT_START)
        compile_blk = _read_compile_config_overlay(cmd)
        assert compile_blk["options"] == ["-DMSG=hello world", "-DOK=1"]

    def test_scan_forwards_all_six_flags(self) -> None:
        """Regression: scan forwarded none of these, even though
        `cli_scan.py` shares the identical `compile_context_options`
        decorator with dump (ADR-037 D3 / ADR-035 amendment)."""
        cmd, _ = _run_region(_SCAN_MODE_MARKER, _FULL_ENV)
        assert "--ast-frontend" in cmd and "clang" in cmd
        assert "--compiler" in cmd and "/opt/gcc-14/bin/g++" in cmd
        assert "--compiler-prefix" in cmd and "aarch64-linux-gnu-" in cmd
        assert "--compiler-option" in cmd and "-DFOO=1" in cmd
        assert "--sysroot" in cmd and "/opt/sysroot" in cmd
        assert "--nostdinc" in cmd
        # Regression (Codex review, PR #757): scan's cross-compiler block used
        # to appear twice in run.sh -- harmless duplication for the old
        # scalar --gcc-options (last-of-two-identical-values wins), but
        # --compiler-option is `multiple=True` and genuinely accumulates every
        # occurrence, so the duplicate silently doubled every forwarded
        # --compiler/--compiler-prefix/--compiler-option/--sysroot token.
        assert cmd.count("--compiler") == 1
        assert cmd.count("--compiler-prefix") == 1
        assert cmd.count("--compiler-option") == 1
        assert cmd.count("--sysroot") == 1

    def test_gcc_options_quoted_value_stays_one_token(self) -> None:
        """Regression (Codex review, PR #757): routing gcc-options through
        add_flag()'s plain bash word-splitting broke a shell-quoted value
        into malformed tokens -- `-DMSG="hello world" -DOK=1` word-split to
        `-DMSG="hello`, `world"`, `-DOK=1` instead of the two real tokens
        abicheck's own server-side shlex.split() used to produce for the old
        --gcc-options flag. add_flag_shlex_split() must reproduce that
        shlex-aware splitting, not add_flag()'s own naive one."""
        env = {**_FULL_ENV, "INPUT_GCC_OPTIONS": '-DMSG="hello world" -DOK=1'}
        cmd, _ = _run_region(_SCAN_MODE_MARKER, env)
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
        cmd, _ = _run_region(_SCAN_MODE_MARKER, env)
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
        cmd, _ = _run_region(_SCAN_MODE_MARKER, env)
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
        cmd, _ = _run_region(_SCAN_MODE_MARKER, env)
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
        result = _run_region_raw(_SCAN_MODE_MARKER, env)
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
        cmd, _ = _run_region(_SCAN_MODE_MARKER, env)
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
        result = _run_region_raw(_SCAN_MODE_MARKER, env)
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
        result = _run_region_raw(_SCAN_MODE_MARKER, env)
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
        harness = (
            'add_single_flag() { [[ -n "$2" ]] && CMD+=("$1" "$2"); }\n'
            '_PY_BIN="$(command -v python3 || command -v python || true)"\n'
            + _py_safe_dir_source()
            + _py_bin_has_abicheck_source()
            + _add_flag_source()
            + _is_release_style_operand_source()
            + "\nCMD=()\n"
        )
        script = (
            harness
            + _compile_context_region(_SCAN_MODE_MARKER)
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
        # Phase 7: with none of the cross-compilation inputs set,
        # add_compile_context_flags is a no-op -- no --config overlay at
        # all, not one with an empty compile: block.
        cmd, _ = _run_region(
            _COMPARE_MODE_MARKER,
            {"INPUT_OLD_LIBRARY": "old.so", "INPUT_NEW_LIBRARY": "new.so"},
            _COMPARE_COMPILE_CONTEXT_START,
        )
        assert "--config" not in cmd
        assert "--compiler" not in cmd
        assert "--sysroot" not in cmd
        assert "--nostdinc" not in cmd

    def test_scan_omits_unset_flags(self) -> None:
        cmd, _ = _run_region(_SCAN_MODE_MARKER, {})
        assert "--compiler" not in cmd
        assert "--sysroot" not in cmd
        assert "--nostdinc" not in cmd

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
            _COMPARE_MODE_MARKER, env, _COMPARE_COMPILE_CONTEXT_START
        )
        assert result.returncode != 0
        assert "not support" in result.stdout

    def test_compare_release_style_succeeds_when_context_unset(self) -> None:
        """Companion: a plain directory/package compare with no compile-
        context inputs configured must still succeed (only fails when a
        flag was actually configured and would be dropped)."""
        cmd, stderr = _run_region(
            _COMPARE_MODE_MARKER,
            {
                "INPUT_OLD_LIBRARY": str(RUN_SH.parent),
                "INPUT_NEW_LIBRARY": "new.so",
            },
            _COMPARE_COMPILE_CONTEXT_START,
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
        cmd, stderr = _run_region(
            _COMPARE_MODE_MARKER,
            {
                "INPUT_OLD_LIBRARY": str(RUN_SH.parent),
                "INPUT_NEW_LIBRARY": "new.so",
                "INPUT_AST_FRONTEND": "auto",
            },
            _COMPARE_COMPILE_CONTEXT_START,
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
        )
        assert result.returncode != 0
        assert "not support" in result.stdout

    def test_dump_default_lang_does_not_synthesize_an_overlay(self) -> None:
        """CodeRabbit review, PR #1146, finding #6: action.yml maps an
        omitted `lang` Action input to INPUT_LANG=c++ -- that is the
        *default*, not a user override. A prior predicate treated any
        non-empty INPUT_LANG (including this default) as "lang was
        explicitly requested," so a dump/compare run configuring nothing
        at all still synthesized a --config overlay just to carry
        compile.lang: c++ (a no-op value, but a needless overlay/config
        interaction all the same)."""
        cmd, _ = _run_region(
            _DUMP_MODE_MARKER,
            {"INPUT_LANG": "c++"},
            _DUMP_COMPILE_CONTEXT_START,
        )
        assert "--config" not in cmd

    def test_dump_non_default_lang_still_synthesizes_an_overlay(self) -> None:
        """Companion: an actual, non-default lang choice still triggers the
        overlay -- only the documented default value is exempt."""
        cmd, _ = _run_region(
            _DUMP_MODE_MARKER,
            {"INPUT_LANG": "c"},
            _DUMP_COMPILE_CONTEXT_START,
        )
        compile_blk = _read_compile_config_overlay(cmd)
        assert compile_blk["lang"] == "c"

    def test_compare_release_style_succeeds_with_default_lang(self) -> None:
        """Companion to test_compare_release_style_succeeds_with_ast_frontend_
        auto above, for the release-operand rejection predicate: the
        default INPUT_LANG=c++ must not by itself reject a directory/
        package compare -- only an actual override (a non-"c++" value)
        should."""
        cmd, stderr = _run_region(
            _COMPARE_MODE_MARKER,
            {
                "INPUT_OLD_LIBRARY": str(RUN_SH.parent),
                "INPUT_NEW_LIBRARY": "new.so",
                "INPUT_LANG": "c++",
            },
            _COMPARE_COMPILE_CONTEXT_START,
        )
        assert "not support" not in stderr

    def test_compare_release_style_fails_with_non_default_lang(self) -> None:
        """Companion: an actual, non-default lang choice still trips the
        release-operand guard."""
        result = _run_region_raw(
            _COMPARE_MODE_MARKER,
            {
                "INPUT_OLD_LIBRARY": str(RUN_SH.parent),
                "INPUT_NEW_LIBRARY": "new.so",
                "INPUT_LANG": "c",
            },
            _COMPARE_COMPILE_CONTEXT_START,
        )
        assert result.returncode != 0
        assert "not support" in result.stdout


class TestCompileContextMergesWithExplicitBuildConfig:
    """Codex review, PR #1159 (P1, second round): combining an explicit
    ``build-config`` input with a compile-context input (``ast-frontend``/
    ``gcc-path``/``gcc-prefix``/``gcc-options``/``sysroot``/``nostdinc``/
    ``lang``) used to be a hard rejection ("cannot combine ... with
    build-config") -- the identical regression
    ``test_action_release_topology_config.py`` found and fixed for
    ``add_release_topology_config_flags``, confirmed present here too since
    the two functions have shared every bug found this session. The fix
    merges the synthesized ``compile:`` overlay into a COPY of the user's
    own explicit build-config instead, Action input winning on a genuine
    conflict -- and, since an explicit build-config is a deliberate operator
    action (not passively discovered, untrusted content), the merge must
    NOT strip ``build.query``/``compile.compiler`` from it."""

    def test_explicit_build_config_settings_survive_alongside_compile_context(
        self, tmp_path: Path
    ) -> None:
        build_config = tmp_path / "my-build-config.yml"
        build_config.write_text(
            "severity:\n  abi_breaking: error\ncompile:\n  std: c++17\n",
            encoding="utf-8",
        )
        cmd, _ = _run_region(
            _DUMP_MODE_MARKER,
            {"INPUT_GCC_PATH": "/opt/gcc-14/bin/g++", "INPUT_BUILD_CONFIG": str(build_config)},
            _DUMP_COMPILE_CONTEXT_START,
        )
        # Exactly one --config -- no leftover unmerged build-config entry.
        assert cmd.count("--config") == 1
        path = cmd[cmd.index("--config") + 1]
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        assert doc["severity"] == {"abi_breaking": "error"}
        # The user's own compile.std passes through, alongside the
        # synthesized compile.compiler -- not replaced by it.
        assert doc["compile"]["std"] == "c++17"
        assert doc["compile"]["compiler"] == "/opt/gcc-14/bin/g++"

    def test_compile_context_input_wins_on_a_genuine_conflict(
        self, tmp_path: Path
    ) -> None:
        build_config = tmp_path / "my-build-config.yml"
        build_config.write_text(
            "compile:\n  sysroot: /old/sysroot\n  std: c++17\n",
            encoding="utf-8",
        )
        cmd, _ = _run_region(
            _DUMP_MODE_MARKER,
            {"INPUT_SYSROOT": "/opt/sysroot", "INPUT_BUILD_CONFIG": str(build_config)},
            _DUMP_COMPILE_CONTEXT_START,
        )
        doc = json.loads(
            Path(cmd[cmd.index("--config") + 1]).read_text(encoding="utf-8")
        )
        # sysroot: the Action input wins over the explicit build-config's own.
        assert doc["compile"]["sysroot"] == "/opt/sysroot"
        # std: not named by any compile-context input, so it passes through.
        assert doc["compile"]["std"] == "c++17"

    def test_explicit_build_config_query_and_compiler_are_not_stripped(
        self, tmp_path: Path
    ) -> None:
        """Unlike the discovered-config merge, an explicit build-config is a
        deliberate operator action -- the same trust an explicit
        ``--config`` already carries for ``cli_options.py``'s own
        ``compile.compiler``/``build.query`` gates -- so neither key is
        stripped here."""
        build_config = tmp_path / "my-build-config.yml"
        build_config.write_text(
            "build:\n  query: 'cmake --build .'\n  system: cmake\n",
            encoding="utf-8",
        )
        cmd, stderr = _run_region(
            _DUMP_MODE_MARKER,
            {"INPUT_SYSROOT": "/opt/sysroot", "INPUT_BUILD_CONFIG": str(build_config)},
            _DUMP_COMPILE_CONTEXT_START,
        )
        doc = json.loads(
            Path(cmd[cmd.index("--config") + 1]).read_text(encoding="utf-8")
        )
        assert doc["build"] == {"query": "cmake --build .", "system": "cmake"}
        assert "build.query" not in stderr


def _run_region_with_cwd(
    mode_marker: str,
    env_extra: dict[str, str],
    cwd: Path,
    start_marker: str = _COMPILE_CONTEXT_START,
) -> tuple[list[str], str]:
    """Like :func:`_run_region`, but the caller supplies the working
    directory instead of a fresh, isolated one -- needed to exercise a
    ``build-config`` path that is relative to the Action's real working
    directory rather than ``$_PY_SAFE_DIR`` (see
    ``TestCompileContextMergesWithRelativeBuildConfig`` below)."""
    harness = (
        f'MODE="{_mode_value_for_marker(mode_marker)}"\n'
        'add_single_flag() { [[ -n "$2" ]] && CMD+=("$1" "$2"); }\n'
        '_PY_BIN="$(command -v python3 || command -v python || true)"\n'
        + _py_safe_dir_source()
        + _py_bin_has_abicheck_source()
        + _path_qualified_helper_source()
        + _mktemp_canonical_source()
        + _merge_config_overlay_fn_source()
        + _add_flag_source()
        + _is_release_style_operand_source()
        + _add_compile_context_flags_source()
        + "\nCMD=()\n"
    )
    script = (
        harness
        + _compile_context_region(mode_marker, start_marker)
        + "\nprintf '%s\\n' \"${CMD[@]}\"\n"
    )
    env = {**os.environ, **env_extra}
    out = _run_bash_script(script, env, check=True, cwd=cwd)
    return out.stdout.splitlines(), out.stderr


class TestCompileContextMergesWithRelativeBuildConfig:
    """Codex review, PR #1159 (P1, third round): ``build-config`` is
    normally a checkout-relative path (``build-config: .abicheck.yml``), and
    the merge helper's Python invocation runs inside ``(cd "$_PY_SAFE_DIR"
    && ...)`` -- an unrelated scratch directory -- so a relative
    ``base_source`` handed straight into that subprocess used to resolve
    against ``$_PY_SAFE_DIR`` instead of the real Action working directory,
    failing with "does not exist" even though the file is right there.
    ``add_compile_context_flags`` shares ``_merge_config_overlay_with_
    discovered_project_config`` with ``add_release_topology_config_flags``
    (``test_action_release_topology_config.py`` carries the same regression
    class for that function), so this confirms the shared fix covers this
    call site too. Deliberately does NOT use ``_run_region``'s own isolated
    ``TemporaryDirectory`` cwd -- the whole point is a real mismatch between
    the Action's working directory (here, the caller-supplied ``cwd``) and
    ``$_PY_SAFE_DIR`` (a distinct ``mktemp -d`` directory), the same
    mismatch a real Action step has between its checkout and this script's
    isolation directory."""

    def test_relative_build_config_resolves_against_action_cwd_not_py_safe_dir(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / ".abicheck.yml").write_text(
            "severity:\n  abi_breaking: error\n", encoding="utf-8"
        )
        cmd, stderr = _run_region_with_cwd(
            _DUMP_MODE_MARKER,
            {
                "INPUT_GCC_PATH": "/opt/gcc-14/bin/g++",
                "INPUT_BUILD_CONFIG": ".abicheck.yml",
            },
            tmp_path,
            _DUMP_COMPILE_CONTEXT_START,
        )
        assert "does not exist" not in stderr
        assert cmd.count("--config") == 1
        doc = json.loads(
            Path(cmd[cmd.index("--config") + 1]).read_text(encoding="utf-8")
        )
        assert doc["severity"] == {"abi_breaking": "error"}
        assert doc["compile"]["compiler"] == "/opt/gcc-14/bin/g++"

    def test_nested_relative_build_config_resolves_against_action_cwd(
        self, tmp_path: Path
    ) -> None:
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "ci.yml").write_text(
            "compile:\n  std: c++17\n", encoding="utf-8"
        )
        cmd, stderr = _run_region_with_cwd(
            _DUMP_MODE_MARKER,
            {
                "INPUT_SYSROOT": "/opt/sysroot",
                "INPUT_BUILD_CONFIG": "config/ci.yml",
            },
            tmp_path,
            _DUMP_COMPILE_CONTEXT_START,
        )
        assert "does not exist" not in stderr
        doc = json.loads(
            Path(cmd[cmd.index("--config") + 1]).read_text(encoding="utf-8")
        )
        assert doc["compile"]["std"] == "c++17"
        assert doc["compile"]["sysroot"] == "/opt/sysroot"


class TestCompileContextOverlayGenerationIsIsolated:
    """Codex review, PR #1159 (P1, fourth round): confirmed
    ``add_compile_context_flags`` had the identical bare-``python3``
    isolation gap ``add_release_topology_config_flags`` did (see
    ``test_action_release_topology_config.py``'s sibling test class of the
    same name) -- both were fixed the same way, routing through
    ``$_PY_BIN``/``$_PY_SAFE_DIR`` with ``PYTHONPATH`` cleared instead of a
    bare same-directory ``python3``."""

    def test_source_uses_isolated_interpreter_not_bare_python3(self) -> None:
        fn_source = _add_compile_context_flags_source()
        assert '(cd "$_PY_SAFE_DIR"' in fn_source
        assert 'PYTHONPATH= "$_PY_BIN" -' in fn_source
        for line in fn_source.splitlines():
            stripped = line.strip()
            assert not stripped.startswith("python3 ") and stripped != "python3"
            assert not stripped.startswith("python ") and stripped != "python"

    def test_overlay_generation_ignores_a_poisoned_pythonpath(
        self, tmp_path: Path
    ) -> None:
        poison_dir = tmp_path / "poison"
        poison_dir.mkdir()
        # A `json.py` shadowing the stdlib module: if this function's own
        # Python invocation inherited $PYTHONPATH instead of clearing it,
        # `import json` would pick this up and crash instead of the real
        # stdlib module the overlay-generation script needs.
        (poison_dir / "json.py").write_text(
            "raise ImportError('POISONED: PYTHONPATH leaked into an "
            "isolated invocation')\n",
            encoding="utf-8",
        )
        env = {**_FULL_ENV, "PYTHONPATH": str(poison_dir)}
        cmd, stderr = _run_region(_DUMP_MODE_MARKER, env, _DUMP_COMPILE_CONTEXT_START)
        assert "POISONED" not in stderr
        doc = json.loads(
            Path(cmd[cmd.index("--config") + 1]).read_text(encoding="utf-8")
        )
        assert doc["compile"]["frontend"] == "clang"
