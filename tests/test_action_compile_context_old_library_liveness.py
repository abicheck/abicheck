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

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import pytest

RUN_SH = Path(__file__).resolve().parents[1] / "action" / "run.sh"

_COMPARE_MODE_MARKER = 'elif [[ "$MODE" == "compare" ]]; then'

_COMPILE_CONTEXT_START = 'add_single_flag "--ast-frontend" "${INPUT_AST_FRONTEND:-}"'

# compare's region (Phase 7) starts at the gating comment (these inputs are
# gated to the single-pair path, since the release fan-out can't thread a
# CompileContext to each pair's header dump) and ends at the single-pair
# branch's call to the shared helper. Covers both the two-sided shape and
# the audit-only shape (old-library/abi-baseline both omitted) -- both live
# in this same branch since ADR-068's Action-input-lifecycle amendment
# retired `mode: scan` outright.
_COMPARE_COMPILE_CONTEXT_START = (
    "# The L2 compile-context inputs (ast-frontend/gcc-*/sysroot/nostdinc/lang)"
)
_COMPARE_COMPILE_CONTEXT_END = "else\n    add_compile_context_flags true\n  fi"

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
_COMPILE_CONTEXT_FN_START = '_COMPILE_CONTEXT_CONFIG_OVERLAY=""'
_COMPILE_CONTEXT_FN_END = '\n  CMD+=(--config "$_COMPILE_CONTEXT_CONFIG_OVERLAY")\n}\n'


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
    """``_compile_context_sources_pairwise()``/
    ``_old_library_is_stored_snapshot()`` (PR #1171, sixth through eighth
    review rounds): single-pair ``compare`` is pairwise -- excluding a
    ``--sources`` tree's own ``compile:``/``source:``/``debug:`` blocks
    from the sources-root block-replacement, since promoting a NEW-only
    setting would silently apply it to OLD's own header parsing too --
    only when OLD is a genuinely *live* binary. When OLD is a stored
    snapshot or resolved ``--abi-baseline``, it does no header/debug
    extraction of its own at all, so it has no "other side" for those
    settings to leak into (the same shape as ``dump``/``scan --against``,
    covered by the sibling module's
    ``TestCompileContextDiscoversSourcesRootOwnConfig``).

    ``_old_library_is_stored_snapshot`` went through three rounds of
    Codex review, each closing a real gap the previous round left open --
    extension-only (sixth round) missed a live binary named e.g.
    ``old.json``; content-sniffing by individual format (seventh round,
    JSON/gzip/zstd magic only) missed a JSON snapshot with leading
    whitespace and the Action's own documented ABICC-Perl-dump
    old-library input. The eighth round collapsed the rule to its actual
    invariant instead of adding a fourth format-specific case: NOT a
    recognized live-binary format (ELF/PE/Mach-O magic) IS a stored
    operand, full stop -- covering every non-binary shape
    ``resolve_input()`` accepts without needing a dedicated branch per
    format.
    """

    def test_sources_root_compile_block_is_never_sourced_from_it_under_pairwise_compare(
        self, tmp_path: Path
    ) -> None:
        """A genuinely live OLD operand keeps this pairwise: a --sources
        tree's own compile:/source:(singular)/debug: blocks must NOT be
        promoted into the single shared --config, since they are pair-wide
        (resolve_compile_context "applies to both sides",
        resolved_cfg.source_method, resolved_cfg.debug_format). Promoting
        compile: here would silently apply a NEW-only compile context
        (defines, include dirs, language) to OLD's own header parsing too.
        An explicit old-library is required here (unlike an earlier
        revision of this test): omitting it entirely is now the audit-only
        shape (old-library/abi-baseline both omitted, ADR-068's
        Action-input-lifecycle amendment), which is single-sided, not
        pairwise -- see the sibling class below."""
        (tmp_path / ".abicheck.yml").write_text(
            "compile:\n  sysroot: /opt/checkout-sysroot\n", encoding="utf-8"
        )
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / ".abicheck.yml").write_text(
            "compile:\n  sysroot: /opt/new-side-only-sysroot\n"
            "source:\n  method: s6\n"
            "debug:\n  format: dwarf\n",
            encoding="utf-8",
        )
        cmd, _ = _run_region_with_cwd(
            _COMPARE_MODE_MARKER,
            {
                "INPUT_GCC_PATH": "/opt/gcc-14/bin/g++",
                "INPUT_SOURCES": "src",
                "INPUT_OLD_LIBRARY": "old.so",
            },
            tmp_path,
            _COMPARE_COMPILE_CONTEXT_START,
        )
        doc = json.loads(
            Path(cmd[cmd.index("--config") + 1]).read_text(encoding="utf-8")
        )
        # The checkout-root's own compile.sysroot survives -- the
        # sources-root's own compile: block is never consulted.
        assert doc["compile"]["sysroot"] == "/opt/checkout-sysroot"
        # The sources-root's source:/debug: blocks never reach the shared
        # config at all -- they would apply pair-wide if they did.
        assert "source" not in doc
        assert "debug" not in doc

    def test_sources_root_compile_block_survives_an_empty_sources_root_config_under_pairwise_compare(
        self, tmp_path: Path
    ) -> None:
        """Companion to the dump-mode empty-config test in the sibling
        module, for the pairwise (single-pair ``compare``) side: an empty
        --sources-root config must NOT clear the checkout-root's own
        pair-wide ``compile:`` block, since that block is never sourced
        from (or cleared by) the sources root under pairwise mode in the
        first place. An explicit old-library is required here for the same
        reason as the sibling test above: omitting it is the audit-only
        shape, not the pairwise one."""
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
            {
                "INPUT_GCC_PATH": "/opt/gcc-14/bin/g++",
                "INPUT_SOURCES": "src",
                "INPUT_OLD_LIBRARY": "old.so",
            },
            tmp_path,
            _COMPARE_COMPILE_CONTEXT_START,
        )
        doc = json.loads(
            Path(cmd[cmd.index("--config") + 1]).read_text(encoding="utf-8")
        )
        # build: is single-sided even under pairwise compare (embed_build_
        # source()'s own selection is per-operand for L3-L5) -- cleared.
        assert "build" not in doc
        # compile.sysroot is pair-wide -- never touched by the (empty)
        # sources-root config under pairwise mode.
        assert doc["compile"]["sysroot"] == "/opt/checkout-sysroot"
        assert doc["compile"]["compiler"] == "/opt/gcc-14/bin/g++"
        assert doc["severity"] == {"abi_breaking": "error"}

    @pytest.mark.parametrize(
        "old_library,content",
        [
            ("old.abicheck.json", b'{"schema_version": 1}'),
            ("old.json", b'{"schema_version": 1}'),
            ("OLD.JSON", b'{"schema_version": 1}'),
            # gzip magic (1f 8b) -- real gzip bytes aren't needed, only the
            # magic prefix this classifier itself checks.
            ("baseline.json.gz", b"\x1f\x8b\x08\x00stub"),
            # zstd magic (28 b5 2f fd).
            ("baseline.json.zst", b"\x28\xb5\x2f\xfdstub"),
            # A JSON snapshot with leading whitespace before the `{` --
            # sniff_text_format() (abicheck/workflows/input_resolution.py)
            # `.lstrip()`s before checking, so this is still a real JSON
            # snapshot to the actual resolver (eighth round).
            ("old_leading_whitespace.json", b'  \n\t{"schema_version": 1}'),
            # An ABICC Perl dump ($VAR1 prefix, `compat/abicc_dump_import.py`
            # `looks_like_perl_dump`) -- a documented old-library input
            # (action.yml) this classifier explicitly punted on in the
            # seventh round; the eighth round's "not a live binary" rule
            # covers it without needing its own case (eighth round).
            ("old.dump", b"$VAR1 = {\n  'foo' => 'bar'\n};\n"),
            # Content this classifier cannot identify at all -- still not a
            # live binary by magic bytes, so still "stored" under the
            # eighth round's collapsed rule (whatever `resolve_input()`
            # itself eventually does with it -- likely a hard error -- it
            # is not live header/debug extraction against a compile
            # context, which is all this decision is about).
            ("old_unrecognized.bin", b"not a recognized format at all"),
        ],
    )
    def test_sources_root_compile_block_is_sourced_from_it_under_compare_with_stored_old_snapshot(
        self, tmp_path: Path, old_library: str, content: bytes
    ) -> None:
        """A ``compare`` whose OLD operand is a stored snapshot (whatever a
        prior ``abicheck dump -o ...`` named it -- a direct old-library
        input, or what an ``abi-baseline`` auto-fetch always resolves
        ``old-library`` to) has no "other side" for a --sources tree's
        compile: to leak into: OLD does no header/debug extraction at all.
        The earlier ``$MODE == "compare"`` check alone could not see this
        and unconditionally treated every compare as pairwise, silently
        discarding NEW's own sources-root compile: settings. Companion to
        the dump-mode single-sided test in the sibling module, and the
        negative (genuinely pairwise, live OLD) tests just above.

        ``_old_library_is_stored_snapshot`` is content-sniffed and
        simplified to its actual invariant (class docstring) -- so each
        case here writes real bytes exercising that rule, not just a
        suggestively-named, empty/nonexistent path.

        ``compile:`` itself is NOT "the same single-sided promotion as
        dump" (Codex review, fresh evidence, PR #1222 fourth round, second
        finding -- corrected here): unlike `dump`/`scan --against`'s
        genuine single-document-exclusive ``compile:`` selection,
        ``compare``'s own ``resolve_compile_context(..., build_config=
        cfg_path, ...)`` ALWAYS independently resolves the checkout-root
        document's ``compile:`` block FIRST, unconditionally (confirmed by
        calling the real function directly against fixtures shaped exactly
        like this test's own), and NEW's own ``--sources`` tree only folds
        ON TOP of that already-resolved context via a second
        ``merge_compile_config`` call (``compare.py``'s
        ``_maybe_dump_side``) -- so a genuine per-field conflict (like this
        test's own ``sysroot``, set by BOTH documents to different values)
        resolves to the CHECKOUT's value, not the sources-root's; only a
        key the checkout document does NOT set falls through to the
        sources-root one.

        ``source:``/``debug:`` are NOT the same shape as ``compile:``
        here, despite both being "single-sided" in the pairwise sense (P1
        finding, Codex review, fresh evidence, PR #1222 eighth round,
        correcting this test's own previous assertion): unlike `dump`/
        `scan --against`'s genuine single-document-exclusive resolution of
        these two, `compare`'s own pipeline NEVER re-resolves `source:`/
        `debug:` from NEW's ``--sources`` tree at all --
        ``frontends/cli/commands/compare.py``'s
        ``_embed_inline_source_side`` receives both as already-frozen
        arguments (``_resolved_collect_mode``/``_resolved_debug``) computed
        once from the CHECKOUT-side ``resolved_cfg``, before the
        ``--sources`` tree is even considered. So the sources-root
        document's own ``source:``/``debug:`` values below must be ignored
        entirely for `compare` -- neither document here defines them at
        the checkout level, so the real pipeline's own view has nothing
        for either key, and the synthesized overlay must match that
        (absent), not manufacture a value from NEW's tree the real
        pipeline never reads."""
        (tmp_path / ".abicheck.yml").write_text(
            "compile:\n  sysroot: /opt/checkout-sysroot\n", encoding="utf-8"
        )
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / ".abicheck.yml").write_text(
            "compile:\n  sysroot: /opt/sources-root-sysroot\n"
            "source:\n  method: s6\n"
            "debug:\n  format: dwarf\n",
            encoding="utf-8",
        )
        (tmp_path / old_library).write_bytes(content)
        cmd, _ = _run_region_with_cwd(
            _COMPARE_MODE_MARKER,
            {
                "INPUT_GCC_PATH": "/opt/gcc-14/bin/g++",
                "INPUT_SOURCES": "src",
                "INPUT_OLD_LIBRARY": old_library,
            },
            tmp_path,
            _COMPARE_COMPILE_CONTEXT_START,
        )
        doc = json.loads(
            Path(cmd[cmd.index("--config") + 1]).read_text(encoding="utf-8")
        )
        # source:/debug: are NEVER sourced from NEW's --sources tree for
        # `compare` -- neither document here sets them at the checkout
        # level, so the real pipeline's own view (and this overlay) has
        # nothing for either key.
        assert "source" not in doc
        assert "debug" not in doc
        # compile: is a genuine two-stage MERGE for `compare` (see the
        # docstring above) -- the checkout document's own `sysroot` was
        # independently resolved FIRST and wins this real conflict; the
        # sources-root's differing value never applies at all.
        assert doc["compile"]["sysroot"] == "/opt/checkout-sysroot"

    def test_sources_root_source_debug_never_leak_into_compare_with_stored_old_snapshot(
        self, tmp_path: Path
    ) -> None:
        """Companion positive control for the fix above: when the
        CHECKOUT document sets `source:`/`debug:`, those checkout values
        must survive into the overlay UNCHANGED for `compare` against a
        stored OLD snapshot -- even though NEW's own ``--sources`` tree
        sets conflicting values of its own, which the real pipeline never
        reads and this overlay must therefore ignore too."""
        (tmp_path / ".abicheck.yml").write_text(
            "source:\n  method: headers\ndebug:\n  format: btf\n",
            encoding="utf-8",
        )
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / ".abicheck.yml").write_text(
            "source:\n  method: s6\ndebug:\n  format: dwarf\n",
            encoding="utf-8",
        )
        (tmp_path / "old.abicheck.json").write_bytes(b'{"schema_version": 1}')
        cmd, _ = _run_region_with_cwd(
            _COMPARE_MODE_MARKER,
            {
                "INPUT_GCC_PATH": "/opt/gcc-14/bin/g++",
                "INPUT_SOURCES": "src",
                "INPUT_OLD_LIBRARY": "old.abicheck.json",
            },
            tmp_path,
            _COMPARE_COMPILE_CONTEXT_START,
        )
        doc = json.loads(
            Path(cmd[cmd.index("--config") + 1]).read_text(encoding="utf-8")
        )
        assert doc["source"] == {"method": "headers"}
        assert doc["debug"] == {"format": "btf"}

    def test_compile_disjoint_keys_merge_under_compare_with_stored_old_snapshot(
        self, tmp_path: Path
    ) -> None:
        """The disjoint-key (no conflict) sibling of the test above,
        proving the MERGE direction rather than only which side wins a
        conflict: the checkout document sets ONLY ``compile.std``, the
        sources-root document sets ONLY ``compile.include_dirs`` -- the
        merged overlay must carry BOTH, exactly what the real
        ``resolve_compile_context``/``merge_compile_config`` two-stage fold
        (confirmed by calling those functions directly against fixtures
        shaped like this one) would produce for the identical documents."""
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
            {
                "INPUT_GCC_PATH": "/opt/gcc-14/bin/g++",
                "INPUT_SOURCES": "src",
                "INPUT_OLD_LIBRARY": "old.abicheck.json",
            },
            tmp_path,
            _COMPARE_COMPILE_CONTEXT_START,
        )
        doc = json.loads(
            Path(cmd[cmd.index("--config") + 1]).read_text(encoding="utf-8")
        )
        assert doc["compile"]["std"] == "c++20"
        assert doc["compile"]["include_dirs"] == [str((src_dir / "foo").resolve())]

    def test_sources_root_compile_block_stays_pairwise_when_old_library_is_a_live_binary(
        self, tmp_path: Path
    ) -> None:
        """Negative control for the parametrized test above: a real live
        binary (ELF magic bytes) stays on the pre-existing, safe pairwise
        side even though this is the identical --sources/checkout-config
        setup -- confirming the new exclusion is keyed on the operand's
        actual content, not on merely setting old-library or on --sources
        being present."""
        (tmp_path / ".abicheck.yml").write_text(
            "compile:\n  sysroot: /opt/checkout-sysroot\n", encoding="utf-8"
        )
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / ".abicheck.yml").write_text(
            "compile:\n  sysroot: /opt/new-side-only-sysroot\n"
            "source:\n  method: s6\n"
            "debug:\n  format: dwarf\n",
            encoding="utf-8",
        )
        (tmp_path / "old.so").write_bytes(b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 8)
        cmd, _ = _run_region_with_cwd(
            _COMPARE_MODE_MARKER,
            {
                "INPUT_GCC_PATH": "/opt/gcc-14/bin/g++",
                "INPUT_SOURCES": "src",
                "INPUT_OLD_LIBRARY": "old.so",
            },
            tmp_path,
            _COMPARE_COMPILE_CONTEXT_START,
        )
        doc = json.loads(
            Path(cmd[cmd.index("--config") + 1]).read_text(encoding="utf-8")
        )
        assert doc["compile"]["sysroot"] == "/opt/checkout-sysroot"
        assert "source" not in doc
        assert "debug" not in doc

    def test_sources_root_compile_block_stays_pairwise_when_old_library_is_a_live_binary_named_like_a_snapshot(
        self, tmp_path: Path
    ) -> None:
        """A live ELF binary literally named ``old.json`` (the extension-
        only version of this check, sixth round, would have misclassified
        it as a stored snapshot). ``resolve_input()`` checks binary magic
        bytes BEFORE any JSON/text sniffing, so this operand is genuinely
        live and pairwise, regardless of its filename;
        ``_old_library_is_stored_snapshot`` must agree."""
        (tmp_path / ".abicheck.yml").write_text(
            "compile:\n  sysroot: /opt/checkout-sysroot\n", encoding="utf-8"
        )
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / ".abicheck.yml").write_text(
            "compile:\n  sysroot: /opt/new-side-only-sysroot\n"
            "source:\n  method: s6\n"
            "debug:\n  format: dwarf\n",
            encoding="utf-8",
        )
        (tmp_path / "old.json").write_bytes(b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 8)
        cmd, _ = _run_region_with_cwd(
            _COMPARE_MODE_MARKER,
            {
                "INPUT_GCC_PATH": "/opt/gcc-14/bin/g++",
                "INPUT_SOURCES": "src",
                "INPUT_OLD_LIBRARY": "old.json",
            },
            tmp_path,
            _COMPARE_COMPILE_CONTEXT_START,
        )
        doc = json.loads(
            Path(cmd[cmd.index("--config") + 1]).read_text(encoding="utf-8")
        )
        assert doc["compile"]["sysroot"] == "/opt/checkout-sysroot"
        assert "source" not in doc
        assert "debug" not in doc

    def test_sources_root_compile_block_stays_pairwise_when_old_library_does_not_exist_yet(
        self, tmp_path: Path
    ) -> None:
        """An old-library path this classifier cannot open at all (does not
        exist on disk at this point in the script) falls back to the
        pre-existing, safe "pairwise" side rather than guessing -- matching
        ``_is_release_style_operand``'s own identical `-f` fallback."""
        (tmp_path / ".abicheck.yml").write_text(
            "compile:\n  sysroot: /opt/checkout-sysroot\n", encoding="utf-8"
        )
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / ".abicheck.yml").write_text(
            "compile:\n  sysroot: /opt/new-side-only-sysroot\n"
            "source:\n  method: s6\n"
            "debug:\n  format: dwarf\n",
            encoding="utf-8",
        )
        cmd, _ = _run_region_with_cwd(
            _COMPARE_MODE_MARKER,
            {
                "INPUT_GCC_PATH": "/opt/gcc-14/bin/g++",
                "INPUT_SOURCES": "src",
                "INPUT_OLD_LIBRARY": "does-not-exist.json",
            },
            tmp_path,
            _COMPARE_COMPILE_CONTEXT_START,
        )
        doc = json.loads(
            Path(cmd[cmd.index("--config") + 1]).read_text(encoding="utf-8")
        )
        assert doc["compile"]["sysroot"] == "/opt/checkout-sysroot"
        assert "source" not in doc
        assert "debug" not in doc


class TestAuditOnlyCompareStaysSingleSidedUnconditionally:
    """Codex review, PR #1171 (P1, fresh evidence, ninth AND tenth rounds) --
    originally about legacy ``scan --against`` internally routed through
    ``compare`` (ADR-068 D2). ``mode: scan`` is retired outright now
    (ADR-068's Action-input-lifecycle amendment); the identical shape
    survives as ``compare``'s own audit-only branch (old-library and
    abi-baseline both omitted). There is no baseline operand at all in this
    shape (not merely one whose liveness might vary), so a --sources
    tree's own ``compile:``/``source:``/``debug:`` block is unconditionally
    the intended, single source for the one audited artifact -- this is
    now a structural fact of the shape, not a liveness-dependent
    classification the way a two-sided ``compare``'s OLD/NEW distinction
    is (see the sibling class above)."""

    def test_audit_only_compare_is_single_sided(self, tmp_path: Path) -> None:
        (tmp_path / ".abicheck.yml").write_text(
            "compile:\n  sysroot: /opt/checkout-sysroot\n", encoding="utf-8"
        )
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / ".abicheck.yml").write_text(
            "compile:\n  sysroot: /opt/sources-root-sysroot\n"
            "source:\n  method: s6\n"
            "debug:\n  format: dwarf\n",
            encoding="utf-8",
        )
        cmd, _ = _run_region_with_cwd(
            _COMPARE_MODE_MARKER,
            {
                "INPUT_GCC_PATH": "/opt/gcc-14/bin/g++",
                "INPUT_SOURCES": "src",
            },
            tmp_path,
            _COMPARE_COMPILE_CONTEXT_START,
        )
        doc = json.loads(
            Path(cmd[cmd.index("--config") + 1]).read_text(encoding="utf-8")
        )
        # Single-sided: no old-library at all in this shape, so the
        # sources-root's own compile:/source:/debug: are the intended,
        # only source for the one audited artifact.
        assert doc["compile"]["sysroot"] == "/opt/sources-root-sysroot"
        assert doc["source"] == {"method": "s6"}
        assert doc["debug"] == {"format": "dwarf"}
