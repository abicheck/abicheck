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

"""Pure path helpers for header (``-H``) inputs.

A leaf module (stdlib-only) so both the service layer (``service._dump_elf``)
and the ``dump`` CLI helper (``cli_dump_helpers.perform_elf_dump``) can share the
include-root derivation without an import cycle (``cli`` → ``cli_dump_helpers`` →
``service`` → … → ``cli``).
"""

from __future__ import annotations

import os
import shlex
from collections.abc import Sequence
from pathlib import Path

#: Conventional include-root directory names. A ``-H`` umbrella that lives
#: *under* such a directory (e.g. ``include/oneapi/tbb.h``) writes its own
#: includes relative to that root (``#include "oneapi/tbb/..."``), so the root —
#: not the file's immediate parent — is what must be on the search path.
_INCLUDE_ROOT_NAMES = frozenset({"include", "inc"})

#: Recognised C/C++ header file suffixes for directory ``-H`` *expansion*
#: (``service_scan``). Deliberately conservative — these become standalone
#: translation units, so it must not sweep in files meant only to be ``#include``d
#: (``.inl``/``.tcc`` template bodies). Lives in this leaf module so the consumers
#: can share it without an import cycle.
HEADER_SUFFIXES = frozenset(
    {".h", ".hh", ".hpp", ".hxx", ".h++", ".ipp", ".tpp", ".inc"}
)

#: Header-like suffixes whose edits must invalidate the AST cache
#: (``dumper._cache_key``'s include-dir mtime walk). A **superset** of
#: :data:`HEADER_SUFFIXES`: cache invalidation has the opposite bias from
#: expansion — it must be *generous* (any file that can change the parsed AST),
#: so it also covers the ``.inl``/``.tcc`` template-implementation includes that
#: headers commonly pull in but that are never standalone TUs. Decoupling the two
#: lets the walk catch a ``.inl``/``.tcc`` edit without making ``-H <dir>`` try to
#: parse those files (#454).
CACHE_HEADER_SUFFIXES = HEADER_SUFFIXES | frozenset({".inl", ".tcc"})

#: Compiler flags that contribute an include *search directory*. Their presence
#: in the pass-through compile context means a real build supplied its own
#: include tree, which an inferred ``-H`` root must defer to. Both GNU/clang
#: (``-I``/``-isystem``/…) and MSVC/clang-cl (``/I``/``/external:I``/``/imsvc``)
#: spellings are recognised so an MSVC build context (``--gcc-path cl.exe`` with
#: ``/I`` options) is not mistaken for "no build context" (Codex review).
#: Distinct, case-sensitive prefixes (``-I`` ≠ ``-isystem``/``-iquote``);
#: ``startswith`` covers both spaced (``-I dir``) and attached (``-Idir``) forms.
_INCLUDE_FLAG_PREFIXES = (
    "-I",
    "-isystem",
    "-iquote",
    "-idirafter",
    "-cxx-isystem",  # GNU / clang
    "/I",
    "/external:I",
    "/imsvc",  # MSVC / clang-cl
)


def _implicit_header_includes(headers: list[Path]) -> list[Path]:
    """Include directories implied by the ``-H`` inputs themselves.

    A ``-H`` *directory* is its own include root; a ``-H`` *file* contributes
    its parent directory **plus** any ancestor conventionally named ``include``/
    ``inc``. Adding these to the compiler search path lets quote/angle includes
    written relative to the public-header root resolve without a separate ``-I``
    (the abicheck P3 finding) — both the umbrella-at-root case (oneDNN's
    ``include/dnnl.hpp``) and the nested-umbrella case (oneTBB's
    ``include/oneapi/tbb.h`` doing ``#include "oneapi/tbb/blocked_range.h"``).
    Returns existing directories, de-duplicated in discovery order; the user's
    ``-I``/``--include`` entries still take precedence (they are listed first).
    """
    dirs: list[Path] = []
    seen: set[str] = set()

    def _add(d: Path) -> None:
        if not d.is_dir():
            return
        key = str(d.resolve())
        if key not in seen:
            seen.add(key)
            dirs.append(d)

    for h in headers:
        # A directory is its own root; a file contributes its parent. Either way
        # also walk up to any conventional include root — a `-H include/oneapi`
        # (dir) or `-H include/oneapi/tbb.h` (file) still writes includes
        # relative to `include/`, so that root must be on the path too.
        _add(h if h.is_dir() else h.parent)
        for ancestor in h.parents:
            if ancestor.name.lower() in _INCLUDE_ROOT_NAMES:
                _add(ancestor)
    return dirs


def _context_tokens(
    gcc_options: str | None, gcc_option_tokens: Sequence[str]
) -> list[str]:
    """The pass-through compile flags as a flat token list (string + tokens)."""
    toks: list[str] = list(gcc_option_tokens)
    if gcc_options:
        try:
            toks += shlex.split(gcc_options, posix=os.name != "nt")
        except ValueError:
            toks += gcc_options.split()
    return toks


def _has_include_build_context(toks: list[str]) -> bool:
    """True when the compile-flag *tokens* supply their own include search dirs.

    Detects any include-search flag — GNU/clang
    ``-I``/``-isystem``/``-iquote``/``-idirafter``/``-cxx-isystem`` or MSVC/clang-cl
    ``/I``/``/external:I``/``/imsvc`` (attached or spaced). When present, a real
    build context is in play and an inferred ``-H`` root must defer to it; when
    absent, the inferred root can take ``-I`` priority. Compile-DB include dirs
    are folded into the user ``-I`` list upstream, so they need no detection here
    — an inferred ``-I`` appended after them is already lower priority. (*toks* is
    the pre-split flag list from :func:`_context_tokens`.)
    """
    return any(t.startswith(p) for t in toks for p in _INCLUDE_FLAG_PREFIXES)


def _build_context_include_dirs(toks: list[str]) -> set[str]:
    """Resolved include directories the compile-flag *tokens* already search.

    Parses every include-search flag (spaced ``-I dir`` / ``-isystem dir`` and
    attached ``-Idir`` forms, GNU and MSVC) out of *toks* and returns their
    resolved absolute paths. Used to skip an inferred ``-H`` root the build
    context already covers: re-adding such a root as ``-isystem`` would trip GCC's
    rule that a directory given with *both* ``-I`` and ``-isystem`` has its ``-I``
    ignored — demoting the build's own ``-I`` to the system position and changing
    search order (Codex review). Best-effort: relative dirs resolve against the
    cwd, the same basis the inferred roots use.
    """
    dirs: set[str] = set()
    i = 0
    while i < len(toks):
        t = toks[i]
        prefix = next((p for p in _INCLUDE_FLAG_PREFIXES if t.startswith(p)), None)
        if prefix is None:
            i += 1
            continue
        if t == prefix:  # spaced form: the directory is the next token
            if i + 1 < len(toks):
                dirs.add(str(Path(toks[i + 1]).resolve()))
            i += 2
            continue
        # Attached form ("-Idir" / "/Idir"): t is strictly longer than the prefix
        # here (the exact-match spaced form was handled above), so the operand is
        # always non-empty.
        dirs.add(str(Path(t[len(prefix) :]).resolve()))
        i += 1
    return dirs


def resolve_inferred_header_roots(
    headers: list[Path],
    user_includes: list[Path],
    *,
    gcc_options: str | None = None,
    gcc_option_tokens: Sequence[str] = (),
) -> tuple[list[Path], list[str]]:
    """Split the inferred ``-H`` include roots by how they should be searched.

    Returns ``(extra_includes, deferred_tokens)`` — exactly one is non-empty.
    The inferred roots (de-duplicated against the user's ``-I``) are emitted as:

    * plain ``-I`` (returned as extra-include :class:`Path`\\ s) when there is
      **no** build context to defer to — so they outrank the standard system
      dirs and an umbrella that includes a system-colliding name (``<endian.h>``)
      still resolves the package header rather than the system one;
    * a *deferred* token otherwise — emitted **after** the build context's flags
      and in the bucket that keeps it below every build-context include dir (see
      :func:`_deferred_include_flag`): ``-isystem`` below an above-system build
      context (``-I``/``-isystem``/…, still above the standard system dirs so the
      ``<endian.h>`` case resolves the package header), ``-idirafter`` below an
      ``-idirafter``-only build context, or — for an MSVC/clang-cl context — the
      build's own lowest include bucket (``/external:I`` / ``/imsvc`` / ``/I``)
      so the root never shadows the build's system dirs (#454).

    Shared by the ``dump`` CLI path (``cli_dump_helpers.perform_elf_dump``) and
    the service/``scan`` path (``service._dump_elf``) so they cannot drift.
    """
    # Tokenize the pass-through flags once, then reuse for every check below.
    ctx = _context_tokens(gcc_options, gcc_option_tokens)
    # Skip roots the user's -I *or* the build context already searches: re-adding
    # one the build supplies as -I would, when emitted as -isystem, void that -I
    # (GCC ignores -I for a dir also given via -isystem) and reorder the search.
    skip = {str(i.resolve()) for i in user_includes}
    skip |= _build_context_include_dirs(ctx)
    inferred = [
        d for d in _implicit_header_includes(headers) if str(d.resolve()) not in skip
    ]
    if not inferred:
        return [], []
    if _has_include_build_context(ctx):
        out: list[str] = []
        flag = _deferred_include_flag(ctx)
        for d in inferred:
            out += [flag, str(d)]
        return [], out
    return inferred, []


def _msvc_style_context(toks: list[str]) -> bool:
    """True when the compile-flag *tokens* use MSVC/clang-cl include spellings.

    Distinguishes ``/I``/``/external:I``/``/imsvc`` from the GNU forms so the
    deferred inferred root is emitted in the same dialect — a GNU ``-isystem``
    is silently ignored by ``cl.exe``/``clang-cl`` (Codex review).
    """
    msvc = ("/I", "/external:I", "/imsvc")
    return any(t.startswith(p) for t in toks for p in msvc)


#: GNU/clang include classes searched *before* the standard system dirs. An
#: inferred root deferred below these stays above the system dirs (so a
#: system-colliding basename still resolves the package header). ``-idirafter``
#: is deliberately absent — it is searched *after* the system dirs.
_ABOVE_SYSTEM_GNU_PREFIXES = ("-I", "-iquote", "-isystem", "-cxx-isystem")

#: MSVC/clang-cl *system*-include buckets, searched after the plain ``/I``
#: directories but still above the standard ``INCLUDE`` dirs. Listed
#: **lowest-search-priority first** — ``clang-cl`` searches ``/imsvc`` dirs (added
#: "as if in ``%INCLUDE%``") *after* ``/external:I`` dirs, so ``/imsvc`` is the
#: lower bucket. :func:`_msvc_deferred_flag` returns the first present here so a
#: deferred root lands in the build's lowest bucket and can never shadow it.
_MSVC_SYSTEM_BUCKETS = ("/imsvc", "/external:I")


def _msvc_deferred_flag(toks: list[str]) -> str:
    """The MSVC/clang-cl bucket to defer an inferred root below *toks* (#454).

    Mirrors the build context's own *lowest* include bucket so the deferred
    root can never shadow it: if the context uses a system bucket
    (``/external:I``/``/imsvc``), emit in the *lowest-searched* one present — a
    plain ``/I`` root is searched *before* those system dirs and could shadow
    them, and even an ``/external:I`` root is searched before the build's
    ``/imsvc`` (``%INCLUDE%``-style) dirs (Codex review). Mirroring a bucket the
    context actually used is frontend-agnostic: the frontend that will run must
    already understand that spelling (it consumed the same flag on input) — in
    particular a context that uses ``/imsvc`` is necessarily ``clang-cl``, since
    ``cl.exe`` rejects ``/imsvc`` — which sidesteps threading the
    ``cl.exe``-vs-``clang-cl`` identity into this pure helper. Falls back to
    ``/I`` for a plain ``/I``-only context — there is no system bucket to shadow,
    so command-line order (after the build's own ``/I`` dirs) suffices.
    """
    for bucket in _MSVC_SYSTEM_BUCKETS:
        if any(t.startswith(bucket) for t in toks):
            return bucket
    return "/I"


def _deferred_include_flag(toks: list[str]) -> str:
    """The flag to defer an inferred ``-H`` root below the build-context *toks*.

    The root must search *after* every build-context include dir; the bucket
    that achieves that depends on the build context's own flags:

    * MSVC/clang-cl (``/I``/``/external:I``/``/imsvc``) → the build's lowest
      bucket via :func:`_msvc_deferred_flag` (a system-bucket context keeps the
      root from shadowing ``/external:I``/``/imsvc`` dirs; a GNU ``-isystem`` is
      silently ignored by ``cl.exe``/``clang-cl`` either way);
    * any *above-system* GNU class (``-I``/``-iquote``/``-isystem``/
      ``-cxx-isystem``) → ``-isystem`` (searched after those, still above the
      standard system dirs so a system-colliding basename resolves the package
      header);
    * otherwise the build context is ``-idirafter``-only (a *below-system*
      class) → ``-idirafter`` (after the build's own ``-idirafter`` dirs, in the
      same class, so the build's fallback keeps priority — Codex review).
    """
    if _msvc_style_context(toks):
        return _msvc_deferred_flag(toks)
    if any(t.startswith(p) for t in toks for p in _ABOVE_SYSTEM_GNU_PREFIXES):
        return "-isystem"
    return "-idirafter"


def deferred_token_dirs(deferred_tokens: Sequence[str]) -> list[Path]:
    """The directories carried by the ``<flag> <dir>`` deferred token pairs.

    The deferred inferred roots ride in ``gcc_option_tokens`` (not
    ``extra_includes``), so the header-AST cache key — which mtime-scans only
    ``extra_includes`` dirs — would miss edits to their transitively-included
    headers and reuse a stale AST (Codex review). Callers pass these dirs to the
    dumper as hash-only inputs. Pairs the flat ``[flag, dir, …]`` list (the flag
    is ``-isystem``/``-idirafter`` for GNU contexts, ``/I``/``/external:I``/
    ``/imsvc`` for MSVC ones — every pair is two tokens regardless).
    """
    return [Path(d) for _flag, d in zip(deferred_tokens[::2], deferred_tokens[1::2])]


def iter_cache_header_files(directory: Path) -> list[Path]:
    """Header-like files under *directory* whose edits should bust the AST cache.

    Recurses *directory* and returns the files whose suffix is in
    :data:`CACHE_HEADER_SUFFIXES` (the generous superset — includes ``.inl``/
    ``.tcc`` template bodies), sorted for a deterministic cache key. Used by
    ``dumper._cache_key``'s include-dir mtime walk.
    """
    return sorted(
        p for p in directory.rglob("*") if p.suffix.lower() in CACHE_HEADER_SUFFIXES
    )
