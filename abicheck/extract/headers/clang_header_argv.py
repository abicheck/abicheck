# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Clang header-AST argv for the L2 aggregate parse (ADR-061 extract layer).

The L2 header backend has no :class:`~abicheck.buildsource.build_evidence.CompileUnit`,
so its include directories and pass-through options are typed here as
:class:`HeaderCompileContext` and rendered by :func:`render_clang_header_argv`.
``extract`` may not import ``buildsource`` (architecture/modules.yaml), so the
renderer lives beside its only caller rather than in the compile-invocation model.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..._compiler_options import (
    clang_ast_dump_tail,
    has_explicit_std,
    split_gcc_options,
)
from ...header_utils import drop_include_tokens_duplicating_paths
from ..compiler_driver import needs_sycl_host_only


@dataclass(frozen=True)
class HeaderCompileContext:
    """The clang header-AST argv inputs for one L2 aggregate parse.

    The L2 header backend has no :class:`~..build_evidence.CompileUnit`: its
    include directories (already resolved by the header compile context) and
    pass-through options arrive as these typed fields. The renderer is
    :func:`render_clang_header_argv`.
    """

    cc_bin: str
    extra_includes: tuple[Path, ...] = ()
    sysroot: Path | None = None
    nostdinc: bool = False
    gcc_options: str | None = None
    gcc_option_tokens: tuple[str, ...] = ()
    force_cpp: bool = False
    force_cpp20: bool = False
    system_includes: tuple[str, ...] = ()
    dpcpp_multi_context: bool = False
    dpcpp_host_context: bool = False


def render_clang_header_argv(
    context: HeaderCompileContext, agg_path: Path
) -> list[str]:
    """Build the ``clang -ast-dump=json`` command for the aggregate header.

    Mirrors :func:`_build_castxml_command`'s flag handling (includes, sysroot,
    ``-nostdinc``, pass-through options, C-vs-C++ language mode and the C++20
    bump) so the clang backend parses the same TU under the same context.
    ``-fsyntax-only``/``-ferror-limit=0`` keeps parsing past recoverable
    errors so a single bad decl does not blank the whole dump.

    ``system_includes`` are host-compiler-probed system dirs (see
    :func:`_probe_gnu_system_includes`) injected as ``-isystem`` so clang
    finds the same libstdc++/libc headers castxml gets via
    ``--castxml-cc-gnu``. Emitted **last** (after the user's ``-I`` and
    pass-through ``--compiler-option``) so a user-supplied
    ``-isystem`` for a cross/hermetic SDK wins. Skipped under ``-nostdinc``.

    On an Intel oneAPI driver, ``-fsycl-host-only`` is appended per
    :func:`needs_sycl_host_only` (PR #643) -- skipped when
    ``dpcpp_multi_context`` is set, since that request wants both passes.

    ``dpcpp_host_context`` requests the same SYCL-enabled host compilation
    directly, rather than producing a complete device AST only to frame and
    discard it. ``dpcpp_multi_context`` (ADR-050 D5, G32 Phase D) adds ``-fsycl -v``
    when *cc_bin* is DPC++-capable (:func:`is_dpcpp_family_binary`) --
    ``-fsycl`` splits the driver into a host + one-or-more-device
    compilation passes, and ``-v`` emits the ``-cc1 ... -triple <T> ...
    -fsycl-is-(host|device)`` stderr lines :mod:`abicheck.sycl_context`
    needs to correlate each stdout document back to a host/device ``kind``.
    """
    cmd = [context.cc_bin]
    for inc in context.extra_includes:
        cmd += ["-I", str(inc)]
    if context.sysroot:
        cmd += [f"--sysroot={context.sysroot.as_posix()}"]
    if context.nostdinc:
        cmd += ["-nostdinc"]
    if context.gcc_options:
        cmd += split_gcc_options(context.gcc_options)
    # Repeatable --compiler-option: one literal argument each (no shlex split).
    # Dropped when an entry duplicates a directory already emitted above via
    # `extra_includes` — see `drop_include_tokens_duplicating_paths`'s own
    # docstring for why this can otherwise happen and what it broke.
    cmd += drop_include_tokens_duplicating_paths(context.gcc_option_tokens, cmd)
    if context.dpcpp_host_context:
        if "-fsycl" not in cmd:
            cmd.append("-fsycl")
        if "-fsycl-host-only" not in cmd:
            cmd.append("-fsycl-host-only")
    elif not context.dpcpp_multi_context and needs_sycl_host_only(context.cc_bin, cmd):
        cmd.append("-fsycl-host-only")
    # Auto-probed host system dirs go *after* the user's pass-through flags, so a
    # user-supplied -isystem (cross/hermetic SDK) keeps higher priority (Codex review).
    for sysinc in context.system_includes:
        cmd += ["-isystem", sysinc]
    explicit_std = has_explicit_std(context.gcc_options, context.gcc_option_tokens)
    if not context.force_cpp:
        if not explicit_std:
            cmd += ["-x", "c", "-std=gnu11"]
    elif not explicit_std:
        # Select the C++ language explicitly (``-x c++``) rather than relying on
        # the aggregate file's extension: the C→C++ retry reuses a ``.h`` aggregate
        # that clang would otherwise parse as C. Only bump the standard to gnu++20
        # when C++20 syntax was detected; otherwise leave clang's default dialect.
        cmd += ["-x", "c++"]
        if context.force_cpp20:
            cmd += ["-std=gnu++20"]
    if context.dpcpp_multi_context:
        cmd += ["-fsycl", "-v"]
    cmd += clang_ast_dump_tail(str(agg_path))
    return cmd
