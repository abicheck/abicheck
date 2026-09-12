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

"""``action/run.sh``'s ``_merge_config_overlay_with_discovered_project_config``
now validates its base document (discovered OR explicit) against the real
``BuildConfig`` schema BEFORE its own
``strip_untrusted_execution_keys``/``rebase_relative_config_paths``
mutation runs (PR #1222 Codex review, P2 finding).

Without this, a schema-invalid value in a field this function goes on to
strip unconditionally (``build.query: 7``, ``compile.compiler: []``) was
previously silently deleted as part of ordinary stripping -- before the
nested engine ever got a chance to parse and reject it -- turning a config a
direct ``compare --config <file>`` invocation would refuse outright into a
silently-accepted run. Fixed via the shared
``abicheck.action_config_overlay.validate_base_config`` -- also covered
directly, function-level, in
``tests/test_action_config_overlay.py::TestValidateBaseConfig``, and at the
``actions/check-target/action.yml`` "Generate assurance-overlay config"
step's own equivalent call site in
``tests/test_reusable_workflows_require_complete_analysis.py::
TestAssuranceOverlayValidatesBaseConfigBeforeStripping``.

Split into its own module rather than added to
``tests/test_action_compile_context_parity.py`` (a ``debt.yaml``
``no_growth``-tracked module already at its adoption baseline) -- this
module re-extracts the small set of shared bash-harness primitives
(``RUN_SH``, the dump-mode compile-context region/merge-helper/isolation
extractors, ``_run_bash_script``, ``_run_region_with_cwd``) verbatim rather
than importing them from its sibling, mirroring
``test_action_compile_context_old_library_liveness.py``'s own identical
split (and that module's docstring, which explains why: every bash-harness
test module in this directory duplicates this plumbing rather than
cross-importing test code).
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from _workflow_exec import bash_executable

RUN_SH = Path(__file__).resolve().parents[1] / "action" / "run.sh"

_DUMP_MODE_MARKER = 'if [[ "$MODE" == "dump" ]]; then'
_COMPILE_CONTEXT_START = 'add_single_flag "--ast-frontend" "${INPUT_AST_FRONTEND:-}"'
_DUMP_COMPILE_CONTEXT_START = "add_compile_context_flags true"
_DUMP_COMPILE_CONTEXT_END = "add_compile_context_flags true"

_END_MARKER_FOR_START: dict[str, str] = {
    _DUMP_COMPILE_CONTEXT_START: _DUMP_COMPILE_CONTEXT_END,
}

_MODE_VALUE_FOR_MARKER: dict[str, str] = {
    _DUMP_MODE_MARKER: "dump",
}


def _mode_value_for_marker(mode_marker: str) -> str:
    return _MODE_VALUE_FOR_MARKER[mode_marker]


def _compile_context_region(
    mode_marker: str, start_marker: str = _COMPILE_CONTEXT_START
) -> str:
    """Extract one mode's compile-context flag-forwarding block verbatim."""
    text = RUN_SH.read_text(encoding="utf-8")
    mode_start = text.index(mode_marker)
    start = text.index(start_marker, mode_start)
    end_marker = _END_MARKER_FOR_START[start_marker]
    end = text.index(end_marker, start) + len(end_marker)
    return text[start:end]


# add_compile_context_flags() itself (Phase 7): extracted verbatim, since
# dump's region calls it instead of forwarding flags directly.
_COMPILE_CONTEXT_FN_START = '_COMPILE_CONTEXT_CONFIG_OVERLAY=""'
_COMPILE_CONTEXT_FN_END = '\n  CMD+=(--config "$_COMPILE_CONTEXT_CONFIG_OVERLAY")\n}\n'


def _add_compile_context_flags_source() -> str:
    text = RUN_SH.read_text(encoding="utf-8")
    start = text.index(_COMPILE_CONTEXT_FN_START)
    end = text.index(_COMPILE_CONTEXT_FN_END, start) + len(_COMPILE_CONTEXT_FN_END)
    return text[start:end]


# _is_release_style_operand is defined once, well before any mode branch;
# add_compile_context_flags's own region needs its real definition.
_IS_RELEASE_STYLE_OPERAND_START = "_is_release_style_operand() {"
_IS_RELEASE_STYLE_OPERAND_END = "\n}\n"


def _is_release_style_operand_source() -> str:
    text = RUN_SH.read_text(encoding="utf-8")
    start = text.index(_IS_RELEASE_STYLE_OPERAND_START)
    end = text.index(_IS_RELEASE_STYLE_OPERAND_END, start) + len(
        _IS_RELEASE_STYLE_OPERAND_END
    )
    return text[start:end]


# add_flag()/add_flag_shlex_split() -- extracted as one region, since the
# latter falls back to calling the former.
_ADD_FLAG_START = "_split_legacy_value() {"
_ADD_FLAG_SHLEX_SPLIT_END = "\nadd_flag_shlex_split() {"
_ADD_FLAG_END = "\n}\n"


def _add_flag_source() -> str:
    text = RUN_SH.read_text(encoding="utf-8")
    start = text.index(_ADD_FLAG_START)
    shlex_start = text.index(_ADD_FLAG_SHLEX_SPLIT_END, start)
    end = text.index(_ADD_FLAG_END, shlex_start) + len(_ADD_FLAG_END)
    return text[start:end]


# $_PY_SAFE_DIR/$_PY_BIN_HAS_ABICHECK: add_flag_shlex_split's own
# prerequisites -- extracted verbatim.
_PY_SAFE_DIR_START = 'if ! _PY_SAFE_DIR="$(mktemp -d)"; then'
_PY_SAFE_DIR_END = "\ntrap 'rm -rf \"$_PY_SAFE_DIR\"' EXIT\n"
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


# The shared merge helper add_compile_context_flags routes its overlay
# through -- extracted verbatim, same markers every sibling module uses.
_MERGE_FN_START = "_merge_config_overlay_with_discovered_project_config() {"
_MERGE_FN_END = "\n_COMPILE_CONTEXT_CONFIG_OVERLAY="


def _merge_config_overlay_fn_source() -> str:
    text = RUN_SH.read_text(encoding="utf-8")
    start = text.index(_MERGE_FN_START)
    end = text.index(_MERGE_FN_END, start)
    return text[start:end]


# `_merge_config_overlay_with_discovered_project_config`'s own
# `base_source` absolutization delegates to `_is_path_already_qualified`,
# so any harness including the merge function must also define this
# helper (and the `$OSTYPE`-derived `$_RUNNING_ON_WINDOWS` it reads).
_PATH_QUALIFIED_HELPER_START = 'case "$OSTYPE" in'
_PATH_QUALIFIED_HELPER_END = "\n}\n"


def _path_qualified_helper_source() -> str:
    text = RUN_SH.read_text(encoding="utf-8")
    start = text.index(_PATH_QUALIFIED_HELPER_START)
    end = text.index(_PATH_QUALIFIED_HELPER_END, start) + len(
        _PATH_QUALIFIED_HELPER_END
    )
    return text[start:end]


# add_compile_context_flags's overlay `mktemp` result is canonicalized via
# `_mktemp_canonical` -- extracted verbatim.
_MKTEMP_CANONICAL_START = "_mktemp_canonical() {"
_MKTEMP_CANONICAL_END = "\n}\n"


def _mktemp_canonical_source() -> str:
    text = RUN_SH.read_text(encoding="utf-8")
    start = text.index(_MKTEMP_CANONICAL_START)
    end = text.index(_MKTEMP_CANONICAL_END, start) + len(_MKTEMP_CANONICAL_END)
    return text[start:end]


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
    inline ``-c`` argument -- see
    ``test_action_compile_context_parity._run_bash_script``'s identical
    docstring for the full windows-latest rationale this mirrors."""
    with tempfile.NamedTemporaryFile(
        "w", suffix=".sh", delete=False, encoding="utf-8", newline="\n"
    ) as f:
        f.write(script)
        script_path = f.name
    try:
        return subprocess.run(
            [bash_executable(), script_path],
            capture_output=True,
            text=text,
            env=env,
            check=check,
            cwd=cwd,
            timeout=timeout,
        )
    finally:
        os.unlink(script_path)


def _harness(mode_marker: str) -> str:
    return (
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


def _run_region_with_cwd(
    mode_marker: str,
    env_extra: dict[str, str],
    cwd: Path,
    start_marker: str = _COMPILE_CONTEXT_START,
) -> tuple[list[str], str]:
    """Run one mode's compile-context region with a caller-supplied working
    directory (needed so a planted ``.abicheck.yml`` is where discovery
    will find it)."""
    script = (
        _harness(mode_marker)
        + _compile_context_region(mode_marker, start_marker)
        + "\nprintf '%s\\n' \"${CMD[@]}\"\n"
    )
    env = {**os.environ, **env_extra}
    out = _run_bash_script(script, env, check=True, cwd=cwd)
    return out.stdout.splitlines(), out.stderr


def _run_region_raw_with_cwd(
    mode_marker: str,
    env_extra: dict[str, str],
    cwd: Path,
    start_marker: str = _COMPILE_CONTEXT_START,
) -> subprocess.CompletedProcess[str]:
    """Like :func:`_run_region_with_cwd`, but ``check=False`` and returns
    the raw result -- for a region expected to ``exit 1``, where
    ``check=True`` would raise before the caller could inspect anything."""
    script = (
        _harness(mode_marker)
        + _compile_context_region(mode_marker, start_marker)
        + "\nprintf '%s\\n' \"${CMD[@]}\"\n"
    )
    env = {**os.environ, **env_extra}
    return _run_bash_script(script, env, check=False, cwd=cwd)


class TestMergeConfigOverlayValidatesBaseConfigBeforeStripping:
    """P2 finding (PR #1222 Codex review, this commit): the base document
    ``_merge_config_overlay_with_discovered_project_config`` reads (a
    discovered or explicit ``.abicheck.yml``) was never validated against
    the real ``BuildConfig`` schema before this function's own
    ``strip_untrusted_execution_keys``/``rebase_relative_config_paths``
    mutation ran -- an invalid value in a field the discover-mode branch
    strips unconditionally (``build.query``/``compile.compiler``) was
    silently deleted as part of ordinary stripping, before the nested
    engine ever got a chance to reject it."""

    def test_discovered_invalid_build_query_type_fails_loud(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / ".abicheck.yml").write_text(
            "build:\n  query: 7\n", encoding="utf-8"
        )
        result = _run_region_raw_with_cwd(
            _DUMP_MODE_MARKER,
            {"INPUT_GCC_PATH": "/opt/gcc-14/bin/g++"},
            tmp_path,
            _DUMP_COMPILE_CONTEXT_START,
        )
        assert result.returncode != 0
        assert "::error::" in result.stdout
        assert "::error::" in result.stderr
        assert "build.query" in result.stderr

    def test_explicit_build_config_invalid_document_also_fails_loud(
        self, tmp_path: Path
    ) -> None:
        """Schema validity is independent of the trust question -- an
        explicit build-config is trusted to run build.query/
        compile.compiler unstripped, but it must still be a schema-valid
        document, the same as a direct `compare --config <file>` would
        require."""
        build_config = tmp_path / "my-build-config.yml"
        build_config.write_text("build:\n  query: 7\n", encoding="utf-8")
        result = _run_region_raw_with_cwd(
            _DUMP_MODE_MARKER,
            {
                "INPUT_GCC_PATH": "/opt/gcc-14/bin/g++",
                "INPUT_BUILD_CONFIG": str(build_config),
            },
            tmp_path,
            _DUMP_COMPILE_CONTEXT_START,
        )
        assert result.returncode != 0
        assert "::error::" in result.stdout

    def test_valid_discovered_document_is_unaffected(self, tmp_path: Path) -> None:
        """Negative control: a schema-valid discovered document must still
        merge normally -- this fix must not reject anything it didn't
        reject before."""
        (tmp_path / ".abicheck.yml").write_text(
            "severity:\n  abi_breaking: error\n", encoding="utf-8"
        )
        cmd, _ = _run_region_with_cwd(
            _DUMP_MODE_MARKER,
            {"INPUT_GCC_PATH": "/opt/gcc-14/bin/g++"},
            tmp_path,
            _DUMP_COMPILE_CONTEXT_START,
        )
        doc = json.loads(
            Path(cmd[cmd.index("--config") + 1]).read_text(encoding="utf-8")
        )
        assert doc["severity"] == {"abi_breaking": "error"}


class TestMergeConfigOverlayClearsSourcesRootBlocksWhenNoSourcesConfigExists:
    """P1 finding (Codex review, fresh evidence, PR #1222 ninth round): a
    ``--sources`` tree with NO ``.abicheck.yml`` anywhere in it at all
    (``discover_build_config`` returns ``None``) previously left
    ``_merge_config_overlay_with_discovered_project_config``'s ``base``
    untouched -- so the checkout-root document's own ``build:``/
    ``sources:``/``compile:``/``source:``/``debug:`` (whichever this run's
    own ``_sources_root_blocks`` names as sources-root-exclusive) survived
    into the synthesized ``--config`` overlay unchanged. The real,
    non-overlay pipeline never falls back to the checkout document for
    these blocks when a ``--sources`` tree is given and has no config of
    its own -- ``embed_build_source()``'s own ``cfg_path = build_config or
    discover_build_config(raw_sources)`` resolves to ``None`` either way,
    so every one of those blocks falls back to a bare ``BuildConfig()``'s
    pure defaults. See the identical fix (and test) at
    ``actions/check-target/action.yml``'s own "Generate assurance-overlay
    config" step in
    ``tests/test_reusable_workflows_assurance_overlay_compile_context.py::
    TestAssuranceOverlayClearsSourcesRootBlocksWhenNoSourcesConfigExists``."""

    def test_dump_mode_clears_all_five_sources_root_blocks(
        self, tmp_path: Path
    ) -> None:
        """`dump` (``ABICHECK_SOURCES_PAIRWISE``/``ABICHECK_SOURCES_MERGE_
        COMPILE`` both unset) is the single-document-exclusive shape --
        ``build:``/``sources:``/``compile:``/``source:``/``debug:`` are ALL
        sources-root-exclusive here, so all five must clear to defaults
        when the ``--sources`` tree has no config of its own. The
        overlay's own synthesized ``compile.compiler`` (from
        ``INPUT_GCC_PATH``) still applies afterwards -- clearing the
        checkout's own ``compile:`` block does not remove the overlay's own
        contribution to that same key."""
        (tmp_path / ".abicheck.yml").write_text(
            "build:\n  system: bazel\n"
            "sources:\n  graph: summary\n"
            "compile:\n  std: c++20\n"
            "source:\n  method: headers\n"
            "debug:\n  format: btf\n"
            "severity:\n  abi_breaking: error\n",
            encoding="utf-8",
        )
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        # Deliberately no `.abicheck.yml` at all under src_dir.
        cmd, _ = _run_region_with_cwd(
            _DUMP_MODE_MARKER,
            {
                "INPUT_GCC_PATH": "/opt/gcc-14/bin/g++",
                "INPUT_SOURCES": str(src_dir),
            },
            tmp_path,
            _DUMP_COMPILE_CONTEXT_START,
        )
        doc = json.loads(
            Path(cmd[cmd.index("--config") + 1]).read_text(encoding="utf-8")
        )
        assert "build" not in doc
        assert "sources" not in doc
        assert "source" not in doc
        assert "debug" not in doc
        # The checkout's own `compile.std` is gone, but the overlay's own
        # synthesized `compile.compiler` still applies.
        assert doc["compile"] == {"compiler": "/opt/gcc-14/bin/g++"}
        # Every other checkout-root setting survives untouched.
        assert doc["severity"] == {"abi_breaking": "error"}
        assert doc["compile"]["compiler"] == "/opt/gcc-14/bin/g++"
