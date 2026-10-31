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

"""``action/run.sh``'s ``_compile_context_sources_pairwise()`` /
``_old_library_is_stored_snapshot()`` -- whether a single-pair ``compare``
promotes a ``--sources`` tree's own ``compile:``/``source:``/``debug:``
blocks pair-wide or single-sided, keyed on whether ``$INPUT_OLD_LIBRARY``
is a genuinely live binary or a stored snapshot/baseline (PR #1171, sixth
through eighth review rounds).

Split out of ``test_action_compile_context_parity.py`` (a ``debt.yaml``
``no_growth``-tracked module already at the AI-readiness ``file-size``
gate's 2000-line hard cap) purely to keep that file under the cap -- this
module re-extracts the small set of shared bash-harness primitives
(``RUN_SH``, the compile-context region/merge-helper/isolation extractors,
``_run_bash_script``, ``_run_region_with_cwd``) verbatim rather than
importing them from its sibling, mirroring
``test_action_release_topology_windows_paths.py``'s own identical split
(and that module's docstring, which explains why: every bash-harness test
module in this directory duplicates this plumbing rather than
cross-importing test code).

See ``test_action_compile_context_parity.py``'s
``TestCompileContextDiscoversSourcesRootOwnConfig`` for the sibling
tests covering the mode-only (``dump`` vs. pairwise/audit-only ``compare``)
half of this same decision, and that class's own docstring for the full
history.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import pytest
from _workflow_exec import bash_executable, require_bash
from test_action_compile_context_parity import (
    _read_compile_config_overlay,
    _resolved_context,
    _shared_context,
    _sources_root_cfg,
)

from abicheck.buildsource.build_config import BuildConfig, load_build_config

RUN_SH = Path(__file__).resolve().parents[1] / "action" / "run.sh"

_COMPARE_MODE_MARKER = 'elif [[ "$MODE" == "compare" ]]; then'

_COMPILE_CONTEXT_START = 'add_single_flag "--ast-frontend" "${INPUT_FRONTEND:-}"'

# compare's region (Phase 7) starts at the gating comment (these inputs are
# no longer gated by operand shape: the release fan-out threads the
# both-sides compile context to each pair's header dump too) and ends at
# its call to the shared helper. Covers both the two-sided shape and
# the audit-only shape (old-library/abi-baseline both omitted) -- both live
# in this same branch since ADR-068's Action-input-lifecycle amendment
# retired `mode: scan` outright.
_COMPARE_COMPILE_CONTEXT_START = (
    "# The L2 compile-context inputs (lang/ast-frontend/gcc-*/sysroot/nostdinc)"
)
_COMPARE_COMPILE_CONTEXT_END = "\n  add_compile_context_flags true\n"

_END_MARKER_FOR_START: dict[str, str] = {
    _COMPARE_COMPILE_CONTEXT_START: _COMPARE_COMPILE_CONTEXT_END,
}

_MODE_VALUE_FOR_MARKER: dict[str, str] = {
    _COMPARE_MODE_MARKER: "compare",
}

# add_compile_context_flags() itself (Phase 7): extracted verbatim, since
# compare's region calls it instead of forwarding flags directly -- same
# "parse the real file, don't hand-copy it" discipline as the rest of this
# module (and its sibling). Its Python heredoc body contains no line
# matching either boundary marker, so a plain substring search is safe.
_COMPILE_CONTEXT_FN_START = "_compile_set_string() {"
_COMPILE_CONTEXT_FN_END = "    CMD+=(--set compile.nostdinc=true)\n  fi\n}\n"


def _add_compile_context_flags_source() -> str:
    text = RUN_SH.read_text(encoding="utf-8")
    start = text.index(_COMPILE_CONTEXT_FN_START)
    end = text.index(_COMPILE_CONTEXT_FN_END, start) + len(_COMPILE_CONTEXT_FN_END)
    return text[start:end]


# _is_release_style_operand is defined once, well before any mode branch;
# compare's extracted region calls it, so the harness needs its real
# definition rather than a hand-copied stub.
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
# prerequisites -- extracted verbatim (a harness silently leaving either
# unset/empty would exercise none of the real CWD-shadowing/shlex-aware
# behavior these tests depend on downstream).
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
# through -- extracted verbatim, same markers
# test_action_release_topology_config.py's own
# `_merge_config_overlay_fn_source` uses.
_MERGE_FN_START = "_merge_config_overlay_with_discovered_project_config() {"
_MERGE_FN_END = "\n}\n"


def _merge_config_overlay_fn_source() -> str:
    text = RUN_SH.read_text(encoding="utf-8")
    start = text.index(_MERGE_FN_START)
    end = text.index(_MERGE_FN_END, start) + len(_MERGE_FN_END)
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
# `_mktemp_canonical` -- extracted verbatim for the same reason as every
# other prerequisite above.
_MKTEMP_CANONICAL_START = "_mktemp_canonical() {"
_MKTEMP_CANONICAL_END = "\n}\n"


def _mktemp_canonical_source() -> str:
    text = RUN_SH.read_text(encoding="utf-8")
    start = text.index(_MKTEMP_CANONICAL_START)
    end = text.index(_MKTEMP_CANONICAL_END, start) + len(_MKTEMP_CANONICAL_END)
    return text[start:end]


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
    require_bash()
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


def _run_region_with_cwd(
    mode_marker: str,
    env_extra: dict[str, str],
    cwd: Path,
    start_marker: str = _COMPILE_CONTEXT_START,
) -> tuple[list[str], str]:
    """Run one mode's extracted compile-context region from the given
    working directory -- needed so ``add_compile_context_flags``'s own
    ``.abicheck.yml``/``--sources`` discovery sees the real fixtures each
    test below writes to ``tmp_path``."""
    harness = (
        f'MODE="{_MODE_VALUE_FOR_MARKER[mode_marker]}"\n'
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


class TestCompileContextPairwiseOldLibraryClassification:
    """``_compile_context_sources_pairwise()`` / ``_old_library_is_stored_snapshot()``
    (PR #1171): single-pair ``compare`` is pairwise, and a ``--sources`` tree's own
    ``compile:``/``source:``/``debug:`` must not reach the context both sides
    share, when OLD is a genuinely live binary. When OLD is a stored snapshot or
    an ABI baseline it does no extraction of its own, so the sources root is not
    the shared context either.

    ADR-091 D4: the Action no longer writes the shared context. Its argv carries
    only its own compile inputs as ``--set compile.*``, so the assertions check
    (a) that argv is exactly the Action's input and (b) the shared context the
    CLI resolves for compare (the checkout document, no ``--sources``), which is
    what these tests were pinning."""

    @staticmethod
    def _pairwise_env(old_library: str, sources: str = "src") -> dict[str, str]:
        return {
            "INPUT_COMPILER": "/opt/gcc-14/bin/g++",
            "INPUT_SOURCES": sources,
            "INPUT_OLD_LIBRARY": old_library,
        }

    def test_sources_root_compile_block_is_never_sourced_from_it_under_pairwise_compare(
        self, tmp_path: Path
    ) -> None:
        """A live OLD operand keeps this pairwise: the shared context stays the
        checkout document's, and the sources root's ``source:``/``debug:`` never
        reach it (they would apply pair-wide)."""
        (tmp_path / ".abicheck.yml").write_text(
            "compile:\n  sysroot: /opt/checkout-sysroot\n", encoding="utf-8"
        )
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / ".abicheck.yml").write_text(
            "compile:\n  sysroot: /opt/new-side-only-sysroot\n"
            "sources:\n  method: s6\n"
            "debug:\n  format: dwarf\n",
            encoding="utf-8",
        )
        cmd, _ = _run_region_with_cwd(
            _COMPARE_MODE_MARKER,
            self._pairwise_env("old.so"),
            tmp_path,
            _COMPARE_COMPILE_CONTEXT_START,
        )
        assert _read_compile_config_overlay(cmd) == {"compiler": "/opt/gcc-14/bin/g++"}
        checkout = tmp_path / ".abicheck.yml"
        shared, _ = _shared_context(cmd, checkout)
        assert shared.sysroot == Path("/opt/checkout-sysroot")
        assert load_build_config(checkout).source_method is None
        assert load_build_config(checkout).debug_format is None

    def test_sources_root_compile_block_survives_an_empty_sources_root_config_under_pairwise_compare(
        self, tmp_path: Path
    ) -> None:
        """An empty sources-root file clears ``build:`` (exclusive selection), but
        pairwise ``compile:`` stays the checkout document's own value, and this
        Action's ``compile.compiler`` still applies on top."""
        (tmp_path / ".abicheck.yml").write_text(
            "severity:\n  abi_breaking: error\n"
            "build:\n  system: make\n"
            "compile:\n  sysroot: /opt/checkout-sysroot\n",
            encoding="utf-8",
        )
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / ".abicheck.yml").write_text("", encoding="utf-8")
        cmd, _ = _run_region_with_cwd(
            _COMPARE_MODE_MARKER,
            self._pairwise_env("old.so"),
            tmp_path,
            _COMPARE_COMPILE_CONTEXT_START,
        )
        assert _read_compile_config_overlay(cmd) == {"compiler": "/opt/gcc-14/bin/g++"}
        shared, _ = _shared_context(cmd, tmp_path / ".abicheck.yml")
        assert shared.sysroot == Path("/opt/checkout-sysroot")
        assert _sources_root_cfg(src_dir).system == BuildConfig().system
        assert (
            load_build_config(tmp_path / ".abicheck.yml").severity_abi_breaking
            == "error"
        )

    @pytest.mark.parametrize(
        ("old_library", "content"),
        [
            ("old.json", b'{"schema_version": 1}'),
            ("baseline.json.gz", b"\x1f\x8b\x08\x00stub"),
            ("baseline.json.zst", b"(\xb5/\xfdstub"),
            ("OLD.JSON", b'{"schema_version": 1}'),
            ("old.abicheck.json", b'{"schema_version": 1}'),
            ("old_leading_whitespace.json", b'  \n\t{"schema_version": 1}'),
            ("old.dump", b"$VAR1 = {\n  'foo' => 'bar'\n};\n"),
            ("old_unrecognized.bin", b"not a recognized format at all"),
        ],
    )
    def test_sources_root_compile_block_is_never_sourced_from_it_under_compare_with_stored_old_snapshot(
        self, tmp_path: Path, old_library: str, content: bytes
    ) -> None:
        """A stored OLD operand does no extraction, so the sources root's own
        ``compile:`` never becomes the shared context: the checkout document's
        sysroot stands, and the sources-root sysroot is absent."""
        (tmp_path / ".abicheck.yml").write_text(
            "compile:\n  sysroot: /opt/checkout-sysroot\n", encoding="utf-8"
        )
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / ".abicheck.yml").write_text(
            "compile:\n  sysroot: /opt/sources-root-sysroot\n"
            "sources:\n  method: s6\n"
            "debug:\n  format: dwarf\n",
            encoding="utf-8",
        )
        (tmp_path / old_library).write_bytes(content)
        cmd, _ = _run_region_with_cwd(
            _COMPARE_MODE_MARKER,
            self._pairwise_env(old_library),
            tmp_path,
            _COMPARE_COMPILE_CONTEXT_START,
        )
        assert _read_compile_config_overlay(cmd) == {"compiler": "/opt/gcc-14/bin/g++"}
        checkout = tmp_path / ".abicheck.yml"
        shared, _ = _shared_context(cmd, checkout)
        assert shared.sysroot == Path("/opt/checkout-sysroot")
        assert load_build_config(checkout).source_method is None
        assert load_build_config(checkout).debug_format is None

    def test_sources_root_source_debug_never_leak_into_compare_with_stored_old_snapshot(
        self, tmp_path: Path
    ) -> None:
        """The checkout's own ``source:``/``debug:`` are the shared ones; the
        sources root's ``s6``/``dwarf`` never replace them."""
        (tmp_path / ".abicheck.yml").write_text(
            "sources:\n  method: headers\ndebug:\n  format: btf\n", encoding="utf-8"
        )
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / ".abicheck.yml").write_text(
            "sources:\n  method: s6\ndebug:\n  format: dwarf\n", encoding="utf-8"
        )
        (tmp_path / "old.abicheck.json").write_bytes(b'{"schema_version": 1}')
        cmd, _ = _run_region_with_cwd(
            _COMPARE_MODE_MARKER,
            self._pairwise_env("old.abicheck.json"),
            tmp_path,
            _COMPARE_COMPILE_CONTEXT_START,
        )
        assert _read_compile_config_overlay(cmd) == {"compiler": "/opt/gcc-14/bin/g++"}
        checkout_cfg = load_build_config(tmp_path / ".abicheck.yml")
        assert checkout_cfg.source_method == "headers"
        assert checkout_cfg.debug_format == "btf"

    def test_compile_disjoint_keys_do_not_merge_under_compare_with_stored_old_snapshot(
        self, tmp_path: Path
    ) -> None:
        """Disjoint keys: the checkout's ``compile.std`` is shared; the sources
        root's ``include_dirs`` is not merged into it."""
        (tmp_path / ".abicheck.yml").write_text(
            "compile:\n  std: c++20\n", encoding="utf-8"
        )
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / ".abicheck.yml").write_text(
            "compile:\n  include_dirs: [foo]\n", encoding="utf-8"
        )
        (tmp_path / "old.abicheck.json").write_bytes(b'{"schema_version": 1}')
        cmd, _ = _run_region_with_cwd(
            _COMPARE_MODE_MARKER,
            self._pairwise_env("old.abicheck.json"),
            tmp_path,
            _COMPARE_COMPILE_CONTEXT_START,
        )
        assert _read_compile_config_overlay(cmd) == {"compiler": "/opt/gcc-14/bin/g++"}
        checkout = tmp_path / ".abicheck.yml"
        assert load_build_config(checkout).compile_std == "c++20"
        _shared_ctx, includes = _shared_context(cmd, checkout)
        assert not any("foo" in str(path) for path in includes)

    @pytest.mark.parametrize(
        "old_library",
        ["old.so", "old.json", "does-not-exist.json"],
    )
    def test_sources_root_compile_block_stays_pairwise_when_old_library_is_a_live_binary(
        self, tmp_path: Path, old_library: str
    ) -> None:
        """A live binary, a binary named like a snapshot, and an OLD that does not
        exist yet all classify as pairwise (live-or-unknown is not a stored
        snapshot), so the shared context stays the checkout's."""
        (tmp_path / ".abicheck.yml").write_text(
            "compile:\n  sysroot: /opt/checkout-sysroot\n", encoding="utf-8"
        )
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / ".abicheck.yml").write_text(
            "compile:\n  sysroot: /opt/new-side-only-sysroot\n"
            "sources:\n  method: s6\n"
            "debug:\n  format: dwarf\n",
            encoding="utf-8",
        )
        if old_library != "does-not-exist.json":
            (tmp_path / old_library).write_bytes(
                b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 8
            )
        cmd, _ = _run_region_with_cwd(
            _COMPARE_MODE_MARKER,
            self._pairwise_env(old_library),
            tmp_path,
            _COMPARE_COMPILE_CONTEXT_START,
        )
        assert _read_compile_config_overlay(cmd) == {"compiler": "/opt/gcc-14/bin/g++"}
        checkout = tmp_path / ".abicheck.yml"
        shared, _ = _shared_context(cmd, checkout)
        assert shared.sysroot == Path("/opt/checkout-sysroot")
        assert load_build_config(checkout).source_method is None
        assert load_build_config(checkout).debug_format is None


class TestAuditOnlyCompareStaysSingleSidedUnconditionally:
    """With no OLD operand at all (the audit-only shape) the run is single-sided:
    the sources root is the one source for ``compile:``/``source:``/``debug:``."""

    def test_audit_only_compare_is_single_sided(self, tmp_path: Path) -> None:
        (tmp_path / ".abicheck.yml").write_text(
            "compile:\n  sysroot: /opt/checkout-sysroot\n", encoding="utf-8"
        )
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / ".abicheck.yml").write_text(
            "compile:\n  sysroot: /opt/sources-root-sysroot\n"
            "sources:\n  method: s6\n"
            "debug:\n  format: dwarf\n",
            encoding="utf-8",
        )
        cmd, _ = _run_region_with_cwd(
            _COMPARE_MODE_MARKER,
            {"INPUT_COMPILER": "/opt/gcc-14/bin/g++", "INPUT_SOURCES": "src"},
            tmp_path,
            _COMPARE_COMPILE_CONTEXT_START,
        )
        assert _read_compile_config_overlay(cmd) == {"compiler": "/opt/gcc-14/bin/g++"}
        assert _resolved_context(cmd, sources=src_dir).sysroot == Path(
            "/opt/sources-root-sysroot"
        )
        cfg = _sources_root_cfg(src_dir)
        assert cfg.source_method == "s6"
        assert cfg.debug_format == "dwarf"
