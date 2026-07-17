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

"""castxml↔clang system-include parity: probe a host GNU compiler for its
built-in include search dirs and feed them to the clang L2 backend.

``castxml --castxml-cc-gnu g++`` runs the real compiler to discover its built-in
include paths (so the host libstdc++ ``<cstddef>`` etc. resolve), then parses
with those injected. Running ``clang -ast-dump=json`` *directly* (the clang L2
backend, :mod:`abicheck.dumper_clang`) does **not** — clang uses its own
GCC-toolchain auto-detection, which misses the host C++ stdlib in minimal
containers, non-standard prefixes, and Conda-clang setups, so scanning headers
like oneTBB's ``oneapi/tbb.h`` fails to find ``<cstddef>``. These helpers
re-create the castxml behaviour for the clang backend: ask the GNU driver where
its headers live and return them so :func:`abicheck.dumper._build_clang_header_command`
can inject them as ``-isystem``.

Split out of :mod:`abicheck.dumper` (which is at the file-size soft limit) and
re-exported there, so the public ``dumper._probe_*`` surface is unchanged.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from . import deadline

#: Env knob to disable the castxml↔clang system-include auto-detection. On by
#: default; set to a falsey value to suppress the host-compiler probe (e.g. for a
#: hermetic build that supplies its own ``-isystem``/``--sysroot``).
_AUTO_SYSINC_ENV = "ABICHECK_AUTO_SYSTEM_INCLUDES"


def _auto_system_includes_enabled() -> bool:
    """True unless the user disabled the system-include probe via the env knob."""
    return os.environ.get(_AUTO_SYSINC_ENV, "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _parse_gnu_include_search_dirs(stderr: str) -> list[str]:
    """Parse a GCC/Clang ``-E -v`` stderr into its system include search dirs.

    The driver prints the resolved search path between the
    ``#include <...> search starts here:`` and ``End of search list.`` markers,
    one directory per indented line (Clang/GCC both use this format; Darwin may
    append `` (framework directory)``). Only the angle-bracket (``<...>``) system
    block is captured — the preceding quote-include (``"..."``) block lists
    ``-iquote`` dirs, which are not system paths and must not become ``-isystem``.
    Pure/string-only so it is unit-testable without a compiler installed. Returns
    the directories in search order.
    """
    dirs: list[str] = []
    in_block = False
    for line in stderr.splitlines():
        stripped = line.strip()
        if "<...> search starts here:" in stripped:
            in_block = True
            continue
        if stripped.startswith("End of search list."):
            break
        if in_block and stripped:
            # GCC/Clang on Darwin tag framework dirs with a trailing note.
            dirs.append(stripped.split(" (", 1)[0].strip())
    return dirs


#: Path segments that mark a directory as GCC's *own* compiler resource/builtins
#: dir (``GCC_INCLUDE_DIR`` / ``include-fixed``), e.g.
#: ``/usr/lib/gcc/x86_64-linux-gnu/13/include``. These hold GCC's intrinsics
#: headers (``immintrin.h``/``ia32intrin.h`` etc.) which reference GCC-only
#: ``__builtin_ia32_*`` builtins and GCC-private ``stddef.h``/``stdarg.h``. clang
#: ships its own equivalents in its resource dir, so injecting GCC's as
#: ``-isystem`` makes clang pick up headers full of builtins it does not define
#: and the parse fails. Drop them from the probe — only the libstdc++ and libc
#: dirs should cross over to the clang backend.
_GNU_COMPILER_RESOURCE_SEGMENTS = ("gcc", "gcc-cross")

#: The multilib library dir names that precede ``gcc``/``gcc-cross`` in a GCC
#: resource path (``/usr/lib/gcc/…``, ``/usr/lib64/gcc/…``, ``/usr/libx32/…``).
#: Matched exactly rather than by ``startswith("lib")`` so an unrelated dir such
#: as ``…/libfoo/gcc/…`` is not misclassified as a GCC resource dir.
_GNU_MULTILIB_DIRS = frozenset({"lib", "lib32", "lib64", "libx32"})


def _is_gnu_compiler_resource_dir(path: str) -> bool:
    """True if *path* is a GCC compiler-internal include dir (not libstdc++/libc).

    Matches the ``.../lib{,32,64,x32}/gcc[-cross]/<triple>/<ver>/include[-fixed]``
    layout by looking for a multilib library segment (:data:`_GNU_MULTILIB_DIRS`)
    immediately followed by ``gcc``/``gcc-cross``. Pure/string-only so it is
    unit-testable without a real toolchain.
    """
    parts = Path(path).parts
    for prev, cur in zip(parts, parts[1:]):
        if cur in _GNU_COMPILER_RESOURCE_SEGMENTS and prev in _GNU_MULTILIB_DIRS:
            return True
    return False


def _probe_gnu_system_includes(cc_bin: str, *, cpp: bool) -> list[str]:
    """Probe *cc_bin* for the system include dirs it would search (best-effort).

    Best-effort: any probe failure (no compiler, timeout, or an already-
    exhausted scan --budget) yields ``[]`` so the dump still runs on clang's
    own detection. Only existing directories are returned, in the compiler's
    own search order. GCC's own compiler resource dir is filtered out (see
    :func:`_is_gnu_compiler_resource_dir`): feeding it to clang as
    ``-isystem`` makes clang use GCC's intrinsics headers, which reference
    GCC-only builtins clang does not implement and the parse fails.

    Bounded by the active deadline (not just the fixed 15s) and process-
    group-safe on timeout, same as the main clang/castxml subprocess calls —
    otherwise a tight ``--budget`` could still spend up to ~15s per probe (two
    probes: C and C++) in a slow/hung compiler before the budget-aware parse
    is ever reached (Codex review).
    """
    lang = "c++" if cpp else "c"
    try:
        proc = deadline.run_bounded(
            [cc_bin, "-E", "-x", lang, "-v", "-"],
            input="",
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError, deadline.DeadlineExceeded):
        return []
    return [
        d
        for d in _parse_gnu_include_search_dirs(proc.stderr or "")
        if Path(d).is_dir() and not _is_gnu_compiler_resource_dir(d)
    ]


def _resolve_probe_compiler(
    compiler: str, gcc_path: str | None, gcc_prefix: str | None
) -> str | None:
    """Pick a GNU ``gcc``/``g++`` driver to probe for system includes, or None.

    Prefers an explicit GNU ``--gcc-path`` (a clang there is useless for
    discovering the host libstdc++, so it is skipped), then the cross
    ``--gcc-prefix`` driver, then ``g++``/``gcc`` on PATH. Returns the first that
    resolves, or ``None`` when no GNU compiler is available (then clang falls
    back to its own detection).
    """
    cpp = compiler in ("c++", "g++", "clang++")
    primary = "g++" if cpp else "gcc"
    candidates: list[str] = []
    if gcc_path and "clang" not in Path(gcc_path).name.lower():
        candidates.append(gcc_path)
    if gcc_prefix:
        candidates.append(f"{gcc_prefix}{primary}")
    candidates += [primary, "gcc" if cpp else "g++"]
    for cand in candidates:
        if shutil.which(cand):
            return cand
    return None


#: Pass-through flags that signal a hermetic/cross/selected-toolchain parse — if
#: the caller already supplied any of these via ``--gcc-options``/``--gcc-option``,
#: the host-compiler probe must stay out of the way (matching what the structured
#: ``nostdinc`` / ``sysroot`` fields do). Substring match covers ``-nostdinc`` /
#: ``-nostdinc++``, ``--sysroot`` / ``--sysroot=…`` / ``-isysroot``, the GCC
#: toolchain selectors (``--gcc-toolchain=…`` / ``--gcc-install-dir=…``), and a
#: cross ``--target=…`` / ``-target …`` — in all of those, probing the host
#: ``g++`` would inject the wrong libstdc++/libc dirs.
_PROBE_SUPPRESSING_FLAGS = (
    "-nostdinc",
    "--sysroot",
    "-isysroot",
    "--gcc-toolchain",
    "--gcc-install-dir",
    "--target",
    "-target",
)


def _pass_through_suppresses_probe(
    gcc_options: str | None, gcc_option_tokens: tuple[str, ...]
) -> bool:
    """True if pass-through flags already isolate the parse (skip the probe)."""
    if gcc_options and any(f in gcc_options for f in _PROBE_SUPPRESSING_FLAGS):
        return True
    return any(
        tok.startswith(f)
        for tok in gcc_option_tokens
        for f in _PROBE_SUPPRESSING_FLAGS
    )


def _resolve_clang_system_includes(
    compiler: str,
    *,
    gcc_path: str | None,
    gcc_prefix: str | None,
    sysroot: Path | None,
    nostdinc: bool,
    force_cpp: bool,
    gcc_options: str | None = None,
    gcc_option_tokens: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """Resolve the ``-isystem`` dirs to inject for a clang header dump.

    Empty when auto-detection is disabled, ``-nostdinc`` was requested, an
    explicit ``--sysroot`` already redirects the search, the caller passed an
    equivalent hermetic/cross/selected-toolchain flag through ``--gcc-options``/
    ``--gcc-option`` (``-nostdinc``/``-nostdinc++``/``--sysroot``/``-isysroot``/
    ``--gcc-toolchain``/``--gcc-install-dir``/``--target``), or no GNU compiler is
    available to probe. Otherwise the host GNU driver's system include dirs
    (castxml↔clang parity, see :func:`_probe_gnu_system_includes`).
    """
    if (
        nostdinc
        or sysroot is not None
        or not _auto_system_includes_enabled()
        or _pass_through_suppresses_probe(gcc_options, gcc_option_tokens)
    ):
        return ()
    probe_cc = _resolve_probe_compiler(compiler, gcc_path, gcc_prefix)
    if probe_cc is None:
        return ()
    return tuple(_probe_gnu_system_includes(probe_cc, cpp=force_cpp))
