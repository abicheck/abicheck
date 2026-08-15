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
from .dumper_clang import _is_clang_family_binary

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

    Matches the full ``.../lib{,32,64,x32}/gcc[-cross]/<triple>/<ver>/include
    [-fixed]`` shape at the *end* of the path — the multilib segment
    (:data:`_GNU_MULTILIB_DIRS`) immediately followed by ``gcc``/``gcc-cross``,
    then exactly a ``<triple>`` and a ``<ver>`` segment, then a final
    ``include``/``include-fixed`` segment — rather than merely scanning for a
    multilib+``gcc`` pair anywhere in the path. A scan-anywhere check
    over-matches: a real libstdc++/libc dir can legitimately live *underneath*
    a ``lib/gcc/...`` tree without being GCC's own resource dir itself (e.g.
    ``.../lib/gcc/<triple>/<ver>/include/c++/<ver>`` — real ``std::`` headers
    a distro nests inside its GCC install, not GCC's own intrinsics/builtins
    dir), and would be wrongly dropped, starving clang of real stdlib headers
    (Codex review, PR #643, round 3). Requiring the exact trailing shape
    means only the literal resource dir itself (or ``include-fixed``) matches,
    not anything merely nested inside the same ``lib/gcc`` subtree.

    The path is lexically normalized (``os.path.normpath``) before splitting
    into parts: GCC and Intel's icpx/icx report their ``-print-search-dirs``/
    ``-v`` search paths with the compiler's own install dir plus a literal
    ``../../../../`` walk back up to the real location (e.g.
    ``/usr/lib/gcc/x86_64-linux-gnu/13/../../../../include/c++/13``, which is
    really ``/usr/include/c++/13`` — genuine libstdc++, not a GCC-internal
    resource dir). Splitting the *unresolved* string into parts would still
    see the ``lib``/``gcc`` segments from the walked-back-out-of prefix and
    misclassify it. ``normpath`` only collapses ``.``/``..`` lexically (no
    filesystem access, no symlink resolution), keeping this pure/string-only
    and unit-testable without a real toolchain — a path traversing a symlink
    needs OS-level (``realpath``) resolution for ``..`` to denote the same
    directory the kernel would open, which is the caller's job when it wants
    that guarantee (:func:`_probe_gnu_system_includes` does, since it already
    knows *path* exists there).

    Also matches Homebrew's packaged GCC layout, which nests an *extra*
    alias segment and a second ``gcc``/``gcc-cross`` segment:
    ``.../lib{,32,64,x32}/gcc[-cross]/current/gcc[-cross]/<triple>/<ver>/
    include[-fixed]`` (confirmed against a real Homebrew GCC install: its
    build is configured with ``--libdir=<prefix>/lib/gcc/current``, and
    GCC's own build script always appends ``gcc/<target>/<version>`` beneath
    whatever libdir it is given, regardless of prefix — so the literal
    ``gcc`` segment reappears a second time, with Homebrew's version-alias
    ``current`` sitting between the two occurrences; Codex review, PR #643,
    round 6). The alias segment's value is not checked (only its structural
    position between the two ``gcc`` segments) — genuinely arbitrary,
    Homebrew just always spells it ``current`` in practice — so this stays a
    *structural* shape match, not a hardcoded special case for that one
    string, and does not loosen the leaf/gcc-segment checks that keep a
    merely-nested libstdc++ dir (round 3) excluded.
    """
    parts = Path(os.path.normpath(path)).parts
    if len(parts) >= 5:
        multilib, gcc_segment, _triple, _ver, leaf = parts[-5:]
        if (
            leaf in ("include", "include-fixed")
            and gcc_segment in _GNU_COMPILER_RESOURCE_SEGMENTS
            and multilib in _GNU_MULTILIB_DIRS
        ):
            return True
    if len(parts) >= 7:
        multilib, gcc_segment, _alias, gcc_segment2, _triple, _ver, leaf = parts[-7:]
        if (
            leaf in ("include", "include-fixed")
            and gcc_segment2 in _GNU_COMPILER_RESOURCE_SEGMENTS
            and gcc_segment in _GNU_COMPILER_RESOURCE_SEGMENTS
            and multilib in _GNU_MULTILIB_DIRS
        ):
            return True
    return False


def _is_gnu_compiler_resource_dir_for_existing(d: str) -> bool:
    """Symlink-aware wrapper around :func:`_is_gnu_compiler_resource_dir`.

    Only correct to call once the caller already knows *d* exists on disk
    (:func:`_probe_gnu_system_includes` checks ``Path(d).is_dir()`` first) —
    see that function's docstring for the full reasoning on why the raw
    string and ``os.path.realpath(d)`` are combined differently depending on
    whether *d* contains a literal ``..`` component.
    """
    if ".." in Path(d).parts:
        return _is_gnu_compiler_resource_dir(os.path.realpath(d))
    return _is_gnu_compiler_resource_dir(d) or _is_gnu_compiler_resource_dir(
        os.path.realpath(d)
    )


def _probe_gnu_system_includes(cc_bin: str, *, cpp: bool) -> list[str]:
    """Probe *cc_bin* for the system include dirs it would search (best-effort).

    Best-effort: any probe failure (no compiler, timeout, or an already-
    exhausted scan --budget) yields ``[]`` so the dump still runs on clang's
    own detection. Only existing directories are returned, in the compiler's
    own search order. GCC's own compiler resource dir is filtered out (see
    :func:`_is_gnu_compiler_resource_dir`): feeding it to clang as
    ``-isystem`` makes clang use GCC's intrinsics headers, which reference
    GCC-only builtins clang does not implement and the parse fails.

    The classifier itself only collapses ``.``/``..`` lexically
    (``os.path.normpath``), which is correct as long as no traversed
    component is a symlink — a symlink followed by ``..`` resolves relative
    to the symlink's *target* directory at the OS level (``realpath``
    semantics), not the symlink's own location, so lexical collapsing alone
    can misjudge which directory an unresolved ``../``-bearing path actually
    denotes.

    Classifying the raw string ``d`` and its ``os.path.realpath(d)`` can each
    be wrong depending on *why* the path is ambiguous, so which one to trust
    is decided by whether ``d`` contains a literal ``..`` component at all
    (:func:`Path(d).parts <pathlib.PurePath.parts>` keeps ``..`` as its own
    element — unlike ``normpath``, it is not collapsed just by asking for
    ``.parts``) — never both via ``or``:

    - **No ``..`` at all** (the compiler reported the directory verbatim,
      possibly itself a symlink): check *both* the raw string and its
      ``realpath`` — reject if *either* matches (an ``or``, safe here since
      there is no ``..`` to make the lexical form ambiguous). Two
      independent, mirror-image hazards need both directions covered:
      (a) a terminal symlink — the canonical
      ``.../lib/gcc/<triple>/<ver>/include`` path itself symlinked to
      storage outside any ``lib/gcc`` hierarchy — must still classify as
      GCC's resource dir by the name it was reported under; ``realpath``
      alone would resolve straight past that lexical evidence and wrongly
      keep it, feeding clang GCC's incompatible intrinsics headers (Codex
      review, round 2 — confirmed with a real symlink where the raw
      classifier returns ``True`` and a realpath-only classifier would have
      returned ``False``); (b) the mirror case — an arbitrary alias path
      (e.g. ``/opt/toolchain/include``, nothing resource-shaped about the
      name itself) that is a symlink *to* the real resource dir — must
      classify as a resource dir too; the raw string alone would miss that
      evidence entirely, wrongly keeping GCC's intrinsics headers under an
      innocuous-looking alias (Codex review, round 8 — confirmed with a
      real symlink where the raw classifier returns ``False`` and the
      realpath classifier returns ``True``). Neither direction is safe to
      skip, but combining them with ``or`` here introduces no new false
      positive: a real libstdc++/libc dir's own raw name or its resolved
      target coincidentally matching the *exact* GCC resource shape would
      have to be GCC's own resource dir to begin with (round 3 already
      established the shape is that precise).
    - **``..`` is present**: trust *only* ``os.path.realpath(d)`` — safe and
      cheap since ``Path(d).is_dir()`` (short-circuiting ``and``, evaluated
      first) has already confirmed ``d`` exists. Once a path traverses a
      ``..``, the lexical string can no longer be trusted either way: it can
      wrongly say "not a resource dir" when a symlinked path component makes
      the walk-back land back inside GCC's tree (round 1), or wrongly say
      "is a resource dir" when a symlinked *mid-path* component makes a
      lexically resource-shaped string actually resolve to a real,
      unrelated system include dir elsewhere — confirmed with
      ``.../lib/gcc/<triple>/<ver>/hop/../include`` where ``hop`` symlinks
      to ``<external>/deep``: lexically this collapses right back to the
      resource shape, but the compiler's ``open()`` call actually lands on
      ``<external>/include``, a real, unrelated dir that must be *kept*
      (Codex review, round 7). Checking the raw string in *addition to*
      realpath here — as an earlier version of this fix did — would
      wrongly drop that real include dir on the lexical match alone; only
      realpath is trustworthy once ``..`` is involved, so ``or``-ing it
      with the raw check is never correct, only checking realpath alone is.

    :func:`_is_gnu_compiler_resource_dir` itself stays pure/string-only and
    testable against synthetic paths that don't exist on disk either way —
    this ``..``-presence decision lives in
    :func:`_is_gnu_compiler_resource_dir_for_existing`, called only from
    here, the one place that already knows the path exists and can afford a
    ``realpath`` syscall.

    Bounded by the tighter of its own 15s cap and the active deadline, and
    process-group-safe on timeout, same as the main clang/castxml subprocess
    calls — otherwise a tight ``--budget`` could still spend up to ~15s per
    probe (two probes: C and C++) in a slow/hung compiler before the
    budget-aware parse is ever reached (Codex review). Nesting a narrower
    scope also stops a *generous* ``--budget`` from letting a hung probe run
    for the whole remaining scan budget instead of its own 15s ceiling —
    ``run_bounded()`` honors an active outer deadline verbatim, not
    ``min(timeout, left)`` (Codex review, round 2).
    """
    lang = "c++" if cpp else "c"
    probe_timeout = 15.0
    scan_remaining = deadline.remaining()
    if scan_remaining is not None:
        probe_timeout = min(probe_timeout, scan_remaining)
    try:
        with deadline.deadline_scope(probe_timeout):
            proc = deadline.run_bounded(
                [cc_bin, "-E", "-x", lang, "-v", "-"],
                input="",
                capture_output=True,
                text=True,
                timeout=probe_timeout,
            )
    except (OSError, subprocess.SubprocessError, deadline.DeadlineExceeded):
        return []
    return [
        d
        for d in _parse_gnu_include_search_dirs(proc.stderr or "")
        if Path(d).is_dir() and not _is_gnu_compiler_resource_dir_for_existing(d)
    ]


def _resolve_probe_compiler(
    compiler: str, gcc_path: str | None, gcc_prefix: str | None
) -> str | None:
    """Pick a GNU ``gcc``/``g++`` driver to probe for system includes, or None.

    Prefers an explicit GNU ``--compiler`` (a clang-family binary there is
    useless for discovering the host libstdc++, so it is skipped — this
    includes the non-"clang"-spelled aliases in
    :data:`abicheck.dumper_clang._CLANG_FAMILY_ALIAS_NAMES`, e.g.
    ``icx``/``icpx``/``dpcpp``/``dpcpp-cl``, not just names containing
    "clang"), then the cross ``--compiler-prefix`` driver, then ``g++``/``gcc`` on
    PATH. Returns the first that resolves, or ``None`` when no GNU compiler is
    available (then clang falls back to its own detection).
    """
    cpp = compiler in ("c++", "g++", "clang++")
    primary = "g++" if cpp else "gcc"
    candidates: list[str] = []
    if gcc_path and not _is_clang_family_binary(gcc_path):
        candidates.append(gcc_path)
    if gcc_prefix:
        candidates.append(f"{gcc_prefix}{primary}")
    candidates += [primary, "gcc" if cpp else "g++"]
    for cand in candidates:
        if shutil.which(cand):
            return cand
    return None


#: Pass-through flags that signal a hermetic/cross/selected-toolchain parse — if
#: the caller already supplied any of these via ``--compiler-option``,
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
        tok.startswith(f) for tok in gcc_option_tokens for f in _PROBE_SUPPRESSING_FLAGS
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
    ``--compiler-option`` (``-nostdinc``/``-nostdinc++``/``--sysroot``/``-isysroot``/
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
