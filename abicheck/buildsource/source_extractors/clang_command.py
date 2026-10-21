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
from ._argv import (
    effective_directory,
    is_msvc_mode,
    pick_compiler_binary,
    replay_extra_flags,
    unredact_home,
)
from ._argv_shortopts import rebase_structured_path as _rebase_structured_path
from .base import SourceExtractionError

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
#: ``_argv._carry_abi_relevant_flags`` (shared by castxml/clang) no longer
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
    compile_unit: CompileUnit, compiler_binary: str | None, *, clang_bin: str = "clang"
) -> tuple[list[str], bool]:
    """The shared compile-context argv prefix (no mode tail / source) and msvc flag.

    Mirrors the compile unit's language standard, defines/undefines, include and
    system-include paths, sysroot, target triple, and ABI-relevant flags, so both
    the AST pass and the macro pass parse the same TU the real build compiled.

    A real build's own recorded ``-fsycl`` (carried through via
    ``replay_extra_flags``'s ``ABI_RELEVANT_FLAG_PREFIXES``) makes Intel's
    oneAPI DPC++/C++ driver (icx/icpx/dpcpp[-cl]) run *two* separate ``-cc1``
    passes for one compile — a SYCL device-side pass and a host-side pass,
    each writing a complete document to the same stdout stream back-to-back
    with no separator, which breaks both the AST pass's single-document JSON
    reader and the macro pass's single-stream text reader. Mirrors the fix
    already shipped for the L2 header-AST backend
    (``dumper_ast_config._build_clang_header_command`` /
    ``abicheck.extract.compiler_driver.needs_sycl_host_only``): append ``-fsycl-host-only`` here
    too, collapsing the compile back to the single host-side pass that
    actually links into the scanned binary — the device pass describes
    SPIR-V kernel code that never becomes part of the host ``.so``'s
    exported symbols, so L4 replay has no use for it either way. A no-op for
    any non-Intel driver, or when the caller's own flags already pin a
    single pass (``-fsycl-host-only``/``-fsycl-device-only``) or explicitly
    disable SYCL (``-fno-sycl`` last-flag-wins) — see
    ``needs_sycl_host_only``'s own docstring for the full precedence.

    The host-only decision is gated on *clang_bin* — the binary this command
    will actually be run with — not on ``pick_compiler_binary``'s *emulated*
    compiler below (``cc_bin``, used only for flag-shape decisions like
    ``msvc``/language-standard translation). Without ``--compiler``/an
    explicit ``compiler_binary`` override, ``cc_bin`` falls back to the real
    build's own recorded ``argv[0]`` (e.g. ``icpx``) purely to emulate its
    accepted flag shape, while *clang_bin* stays the generic default
    (``"clang"``) that is genuinely invoked. Gating on ``cc_bin`` instead
    would append ``-fsycl-host-only`` to a stock clang invocation whenever a
    SYCL TU's real build happened to use an Intel driver — stock clang
    hard-rejects the flag ("unknown argument"), turning every such TU into a
    failed/partial L4 extraction instead of the single ordinary pass stock
    clang already parses a bare ``-fsycl`` as (Codex review).
    """
    cc_bin = pick_compiler_binary(compile_unit, compiler_binary)
    msvc = is_msvc_mode(cc_bin)
    cc_id = "msvc" if msvc else "gnu"

    cmd: list[str] = []
    if msvc:
        cmd.append("--driver-mode=cl")
    # Force the language so a header replayed directly still parses as C/C++.
    lang = "c++" if compile_unit.language.lower() in ("cxx", "c++", "cpp") else "c"
    if not msvc:
        cmd += ["-x", lang]
    cmd += _std_flag(compile_unit.standard, msvc)
    define_opt = "/D" if msvc else "-D"
    undef_opt = "/U" if msvc else "-U"
    for key, value in compile_unit.defines.items():
        cmd.append(f"{define_opt}{key}={value}" if value else f"{define_opt}{key}")
    for undef in compile_unit.undefines:
        cmd.append(f"{undef_opt}{undef}")
    inc_opt = "/I" if msvc else "-I"
    # Round 29 follow-up (Codex review, "Apply env chdir before resolving
    # structured include paths", fresh evidence): `CompileUnit.include_paths`/
    # `system_include_paths` are already STRUCTURED, absolute paths, resolved
    # by the producing adapter against the compile unit's own recorded
    # `directory` -- before any leading `env -C DIR` prefix in `argv` is
    # considered. `extract()`'s own `run_directory` (the effective directory
    # the real compiler process ran from) already accounts for that -- but
    # only for the REPLAY subprocess's own cwd, and `replay_extra_flags()`'s
    # raw, unresolved flag values, not for these two already-absolute
    # structured fields. So `env -C build clang -Iinclude ...` recorded in
    # `directory=/work` resolves `include_paths` to `/work/include` at
    # adapter-construction time, and this function -- without the rebase
    # below -- would still emit `-I /work/include`, even though the real
    # compiler searched `/work/build/include`. Rebase each structured path
    # from its old base (`compile_unit.directory`) onto the real effective
    # one, via a literal string-prefix substitution (not a lexical
    # posixpath/ntpath join+normalize like `join_path_token` elsewhere in
    # this PR series) -- each path here was itself already constructed by
    # simply joining the SAME `compile_unit.directory` string onto a
    # relative operand, so undoing that exact prefix and re-joining the
    # effective directory in its place reproduces precisely what the
    # adapter would have produced had it known the effective directory to
    # begin with, without needing to guess the join grammar a second time.
    # A path outside `compile_unit.directory` entirely (e.g. an absolute
    # `-Isysroot/include` operand the real build recorded verbatim) has no
    # matching prefix and is therefore left unchanged, exactly as it must
    # be -- it was never relative to the pre-chdir directory in the first
    # place.
    run_directory = effective_directory(compile_unit.argv, compile_unit.directory)
    # Round 31 Finding 1 (Codex review, fresh evidence): provenance is
    # tracked per LIST POSITION, not by value -- two occurrences that
    # normalize to the identical final string (one relative-then-joined,
    # one genuinely explicit) must not collapse onto one shared answer.
    # `explicit_or_unknown()` also degrades a legacy/absent-field pack to
    # "do not rebase" rather than "derived" (round 31 Finding 3).
    include_explicit = compile_unit.explicit_or_unknown(system=False)
    system_include_explicit = compile_unit.explicit_or_unknown(system=True)
    for inc, is_explicit in zip(compile_unit.include_paths, include_explicit):
        cmd += [
            inc_opt,
            _rebase_structured_path(
                inc,
                compile_unit.directory,
                run_directory,
                explicit_absolute=is_explicit,
            ),
        ]
    for inc, is_explicit in zip(
        compile_unit.system_include_paths, system_include_explicit
    ):
        rebased = _rebase_structured_path(
            inc,
            compile_unit.directory,
            run_directory,
            explicit_absolute=is_explicit,
        )
        cmd += ["/I", rebased] if msvc else ["-isystem", rebased]
    if compile_unit.sysroot and not msvc:
        cmd.append(f"--sysroot={compile_unit.sysroot}")
    if compile_unit.target_triple and not msvc:
        cmd.append(f"--target={compile_unit.target_triple}")
    extra = replay_extra_flags(compile_unit, cmd, cc_id)
    if compile_unit.standard:
        # ``standard`` is normalized from the *effective* (last) dialect flag.
        # Do not replay an earlier conflicting -std=/std: token from
        # abi_relevant_flags after it, or clang would silently parse the TU at
        # the wrong language level (e.g. CMake's gnu++17 then c++20 pair).
        extra = [
            flag
            for flag in extra
            if not flag.startswith("-std=")
            and not flag.lower().startswith(("/std:", "-std:"))
        ]
    if not is_intel_sycl_driver(clang_bin):
        # A real build's own recorded -fsycl-host-only/-fsycl-device-only
        # (carried through above via replay_extra_flags's abi_relevant_flags/
        # argv scan) each pin a SPECIFIC SYCL compilation pass -- and stock
        # clang cannot honor either: it hard-rejects both as "unknown
        # argument", AND (confirmed empirically against a real clang 18
        # install) its own bare -fsycl with NEITHER selector compiles as
        # DEVICE context by default (defines __SYCL_DEVICE_ONLY__), not
        # host. So dropping -fsycl-device-only and falling through to a
        # bare -fsycl "coincidentally" matches its recorded pass -- but
        # dropping -fsycl-host-only does NOT: it silently flips an
        # explicitly HOST-pinned TU into a device-context compile (Codex
        # review; an earlier revision of this fix got exactly this
        # asymmetry wrong, treating the two selectors as interchangeable).
        # Both therefore degrade this one TU explicitly (a clear message,
        # not the raw "unknown argument") rather than ever stamping source
        # facts for a DIFFERENT compilation context than the one actually
        # recorded. A bare, unselected -fsycl (no explicit pin either way)
        # is deliberately left alone -- carried through as-is, matching the
        # L2 header-AST backend's own established accepted approximation
        # for that ambiguous shape (see this function's own docstring).
        pinned = {"-fsycl-host-only", "-fsycl-device-only"} & set(extra)
        if pinned:
            raise SourceExtractionError(
                f"compile unit {compile_unit.source!r} was recorded with "
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
    return cmd, msvc


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

    What it never carries is code-loading option: ``-fplugin=`` and
    ``-Xclang -load``/``-add-plugin`` run a shared object during option
    processing, so no argv scan or ABI-flag prefix admits them. The regression
    test in ``tests/test_tu_ast_argv.py`` pins that.
    """
    cmd, _msvc = _clang_context_args(compile_unit, compiler_binary, clang_bin=clang_bin)
    return [
        clang_bin,
        *drop_code_loading_options(cmd),
        *clang_ast_dump_tail(str(source)),
    ]


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
    cmd, msvc = _clang_context_args(compile_unit, compiler_binary, clang_bin=clang_bin)
    # cl-driver mode ignores -dD; clang-cl's `/d1PP` is the documented "retain
    # macro definitions in /E mode" flag, so a Windows/clang-cl build still emits
    # #define directives for macros_from_preprocessor (Codex review #339, P2). We
    # keep the line markers (no -P / no /EP) to attribute each macro to its file.
    if msvc:
        preprocess = ["/E", "/d1PP"]
    else:
        preprocess = ["-E", "-dD"]
    return [clang_bin, *cmd, *preprocess, "-ferror-limit=0", str(source)]
