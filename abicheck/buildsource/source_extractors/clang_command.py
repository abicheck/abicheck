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
"""Compile-command construction for the clang source extractor (ADR-030 D2):
the per-TU argv builders that turn a ``CompileUnit`` into a ``clang``
``-ast-dump=json`` or ``-E -dD`` invocation. Pure: no subprocess."""

from __future__ import annotations

import shutil
from pathlib import Path

from ..._compiler_options import clang_ast_dump_tail
from ...extract.compiler_driver import is_intel_sycl_driver, needs_sycl_host_only
from ..abi_flags import drop_code_loading_options
from ..build_evidence import CompileUnit
from ._argv import effective_directory, unredact_home
from .base import SourceExtractionError
from .compile_invocation import (
    CompilerInvocation,
    parse_compile_invocation,
    replayed_tokens,
)

#: clang extractor schema/behaviour version, recorded in the dump provenance and
#: folded into the per-TU cache key (ADR-030 D8). Bump on ANY recipe change so a
#: stale ``--cache-dir`` never reuses an old dump. 0.8: also folds in the
#: resolved compiler override (Codex review). 0.9: the reconstructed command now
#: appends ``-fsycl-host-only`` for a SYCL-enabled Intel oneAPI invocation
#: (Codex review) -- a pre-0.9 cache entry for the same TU was built without
#: that flag and must not be reused as-is. 0.10/0.11: a real build's own
#: recorded ``-fsycl-host-only``/``-fsycl-device-only`` (Intel-only SYCL
#: pass-selector flags carried through via ``abi_relevant_flags``) on a
#: non-Intel invoked binary now degrades that one TU (a clear
#: SourceExtractionError) instead of failing on the raw "unknown argument".
#: 0.10 first shipped this only for ``-fsycl-device-only``, silently
#: STRIPPING a recorded ``-fsycl-host-only`` instead -- confirmed against a
#: real clang install (Codex review) that this was itself wrong: stock
#: clang's bare ``-fsycl`` defines ``__SYCL_DEVICE_ONLY__`` (device context
#: by default), so dropping just the host-only selector silently replayed
#: an explicitly host-pinned TU as device code. 0.11 raises for both
#: selectors symmetrically; a bare, unselected ``-fsycl`` (ambiguous, no
#: explicit pin either way) is left carried through as-is, matching the L2
#: header-AST backend's own established "accepted approximation" for that
#: shape (see ``needs_sycl_host_only``'s own docstring). 0.12:
#: ``compile_invocation._carry_abi_relevant_flags`` (shared by castxml/clang) no longer
#: dedups a repeated flag WITHIN ``abi_relevant_flags`` itself -- a real,
#: layered build config can legitimately record the same toggle-style flag
#: more than once (e.g. ``-fno-sycl -fsycl -fno-sycl``), and the old dedup
#: silently dropped every repeat, corrupting last-flag-wins state for any
#: such pair, not just SYCL (Codex review). 0.13: ``_stamp_fact_set_and_
#: coverage`` now stamps a real ``compiler_family`` (``"intel-llvm"`` for
#: Intel's oneAPI fork, ``"clang"`` unchanged otherwise) instead of always
#: the ``default_fact_set`` default -- a cache entry produced under 0.12 or
#: earlier carries the stale ``"clang"`` label baked into its persisted
#: ``fact_set`` and, on a cache HIT, is returned as-is by
#: ``source_replay._replay_cache_lookup`` without ever re-running
#: ``_stamp_fact_set_and_coverage`` (Codex review, PR #756: a persistent
#: ``--source-abi-cache-dir``/``ABICHECK_L4_CACHE_DIR`` reused across an
#: abicheck upgrade would otherwise silently keep serving the old label
#: forever, since ``compute_tu_cache_key`` doesn't fold ``compiler_family``
#: in on its own -- only the extractor version invalidates it).


def _std_flag(standard: str, msvc: bool) -> list[str]:
    if not standard:
        return []
    return [f"/std:{standard}"] if msvc else [f"-std={standard}"]


def _clang_context_args(
    invocation: CompilerInvocation, *, clang_bin: str
) -> tuple[list[str], bool]:
    """The shared compile-context argv prefix (no mode tail / source) and msvc flag.

    Renders the parsed *invocation* (see :mod:`.compile_invocation`): the
    language standard, defines/undefines, include and system-include paths,
    sysroot, target triple, and the replayed ABI-relevant flags, so both the AST
    pass and the macro pass parse the same TU the real build compiled. A
    clang-cl/MSVC invocation is driven through clang's ``cl`` driver mode.

    A real build's own recorded ``-fsycl`` (carried through as a replayed
    token) makes Intel's oneAPI DPC++/C++ driver (icx/icpx/dpcpp[-cl]) run *two*
    separate ``-cc1`` passes for one compile -- a SYCL device-side pass and a
    host-side pass, each writing a complete document to the same stdout stream,
    which breaks both the AST pass's single-document JSON reader and the macro
    pass's single-stream text reader. Append ``-fsycl-host-only`` to collapse
    the compile back to the single host-side pass that links into the scanned
    binary. A no-op for any non-Intel driver, or when the caller's own flags
    already pin a single pass or explicitly disable SYCL; see
    ``needs_sycl_host_only``'s own docstring for the full precedence.

    The host-only decision is gated on *clang_bin* -- the binary this command
    will actually be run with -- not on the invocation's emulated compiler.
    Gating on the emulated one would append ``-fsycl-host-only`` to a stock
    clang invocation whenever a SYCL TU's real build used an Intel driver, and
    stock clang hard-rejects that flag.
    """
    msvc = invocation.msvc
    cmd: list[str] = []
    if msvc:
        cmd.append("--driver-mode=cl")
    # Force the language so a header replayed directly still parses as C/C++.
    if not msvc:
        cmd += ["-x", invocation.language]
    cmd += _std_flag(invocation.standard, msvc)
    define_opt = "/D" if msvc else "-D"
    undef_opt = "/U" if msvc else "-U"
    for key, value in invocation.defines:
        cmd.append(f"{define_opt}{key}={value}" if value else f"{define_opt}{key}")
    for undef in invocation.undefines:
        cmd.append(f"{undef_opt}{undef}")
    inc_opt = "/I" if msvc else "-I"
    for inc in invocation.include_dirs:
        cmd += [inc_opt, inc]
    for inc in invocation.system_include_dirs:
        cmd += ["/I", inc] if msvc else ["-isystem", inc]
    if invocation.sysroot and not msvc:
        cmd.append(f"--sysroot={invocation.sysroot}")
    if invocation.target_triple and not msvc:
        cmd.append(f"--target={invocation.target_triple}")
    extra = replayed_tokens(invocation, cmd)
    if invocation.standard:
        # ``standard`` is normalized from the *effective* (last) dialect flag.
        # Do not replay an earlier conflicting -std=/std: token after it, or
        # clang would silently parse the TU at the wrong language level.
        extra = [
            flag
            for flag in extra
            if not flag.startswith("-std=")
            and not flag.lower().startswith(("/std:", "-std:"))
        ]
    if not is_intel_sycl_driver(clang_bin):
        # Stock clang hard-rejects a recorded -fsycl-host-only/-fsycl-device-only
        # ("unknown argument"), and its bare -fsycl compiles as DEVICE context by
        # default. Dropping -fsycl-host-only would silently flip a host-pinned TU
        # into device code, so both selectors degrade this one TU explicitly.
        pinned = {"-fsycl-host-only", "-fsycl-device-only"} & set(extra)
        if pinned:
            raise SourceExtractionError(
                f"compile unit {invocation.source!r} was recorded with "
                f"{sorted(pinned)!r} (an Intel-only SYCL pass-selector "
                "flag), but the binary this command will actually run "
                f"with ({clang_bin!r}) is not an Intel oneAPI DPC++/C++ "
                "driver and cannot honor it. L4 source-ABI replay has no "
                "way to correctly represent that pass under a different "
                "compiler, so this translation unit degrades to partial "
                "coverage rather than being silently replayed under the "
                "wrong compilation context -- set compile.compiler in "
                ".abicheck.yml to the real icx/icpx/dpcpp driver to extract it."
            )
    if needs_sycl_host_only(clang_bin, [*cmd, *extra]):
        extra = [*extra, "-fsycl-host-only"]
    cmd += extra
    # Both the AST-dump and the macro (``-E -dD``) argv carry this list, and a
    # code-loading option runs a shared object in either, so the deny rule
    # applies here, once, rather than in each builder.
    return drop_code_loading_options(cmd), msvc


#: Compile-unit languages that are C, not C++. A C unit dumps under plain
#: ``clang``, the driver L4 always used for it: ``clang++`` changes the driver's
#: own defaults, and L4 output for a C unit must not depend on that.
_C_LANGUAGES = frozenset({"c", "objc"})


def ast_clang_binary(clang_bin: str, language: str = "") -> str:
    """The binary an AST dump of a *language* unit runs under.

    The L5 passes always dumped under the C++ driver (``clang++``) while L4 used
    plain ``clang``. One dump now serves both tiers, so the choice is made here.
    The default ``clang`` runs as ``clang++`` for a C++ unit when that is on
    ``PATH``; a C unit stays on ``clang`` so its L4 output is what it always was.
    An explicit ``--clang`` binary, or a ``clang`` with no ``clang++`` beside it,
    runs as given.
    """
    if clang_bin != "clang":
        return clang_bin
    if language.lower() in _C_LANGUAGES:
        return clang_bin
    if shutil.which("clang++") is not None:
        return "clang++"
    return clang_bin


def compile_unit_invocation(compile_unit: CompileUnit) -> tuple[Path, str]:
    """The source path and the directory a replay of *compile_unit* runs from.

    The directory is the EFFECTIVE one: a leading ``env -C DIR`` in the recorded
    argv means the real compiler ran from ``<directory>/DIR``, and the replayed
    flags' relative operands (``-iquote ../include``) resolve against that. Both
    the L4 and L5 readers replay from it, so they parse the same TU.
    """
    directory = unredact_home(compile_unit.directory)
    source = Path(unredact_home(compile_unit.source))
    if not source.is_absolute() and directory:
        source = Path(directory) / source
    run_directory = effective_directory(compile_unit.argv, directory) or directory
    return source, run_directory


def build_clang_command(
    compile_unit: CompileUnit,
    source: Path,
    *,
    clang_bin: str = "clang",
    compiler_binary: str | None = None,
) -> list[str]:
    """Build the ``clang -ast-dump=json`` argv for a compile unit's context (D2).

    The one AST-dump argv: L4 replay and the L5 AST passes both run it (see
    :mod:`..clang_ast_run`). It is the union of the flags either tier needs --
    the L4 context, ABI-relevant flags included. A clang-cl/MSVC compile unit is
    driven through clang's ``cl`` driver mode.

    What it never carries is a code-loading option: ``-fplugin=`` and
    ``-Xclang -load``/``-add-plugin`` run a shared object during option
    processing, so no argv scan or ABI-flag prefix admits them. The filter is
    applied in :func:`_clang_context_args`, which this builder and the macro
    builder share; ``test_shared_argv_never_admits_a_code_loading_option`` in
    ``tests/test_tu_ast_shared_run.py`` pins both.
    """
    invocation = parse_compile_invocation(compile_unit, compiler_binary)
    cmd, _msvc = _clang_context_args(invocation, clang_bin=clang_bin)
    return [clang_bin, *cmd, *clang_ast_dump_tail(str(source))]


def build_clang_macro_command(
    compile_unit: CompileUnit,
    source: Path,
    *,
    clang_bin: str = "clang",
    compiler_binary: str | None = None,
) -> list[str]:
    """Build the ``clang -E -dD`` argv that dumps macro definitions (ADR-030 D6).

    The JSON AST carries no preprocessor macros, so a separate preprocess pass
    (``-E -dD``: emit ``#define`` directives with line markers) is needed for
    ``public_macro_value_changed`` to ever fire (Codex review #339, P2). Same
    compile context as the AST pass so the macro set matches the real build.
    """
    invocation = parse_compile_invocation(compile_unit, compiler_binary)
    cmd, msvc = _clang_context_args(invocation, clang_bin=clang_bin)
    # cl-driver mode ignores -dD; clang-cl's `/d1PP` is the documented "retain
    # macro definitions in /E mode" flag, so a Windows/clang-cl build still emits
    # #define directives for macros_from_preprocessor (Codex review #339, P2). We
    # keep the line markers (no -P / no /EP) to attribute each macro to its file.
    if msvc:
        preprocess = ["/E", "/d1PP"]
    else:
        preprocess = ["-E", "-dD"]
    return [clang_bin, *cmd, *preprocess, "-ferror-limit=0", str(source)]
