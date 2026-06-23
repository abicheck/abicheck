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


def _has_include_build_context(
    gcc_options: str | None, gcc_option_tokens: Sequence[str]
) -> bool:
    """True when the compile context supplies its own include search dirs.

    Detects any include-search flag — GNU/clang
    ``-I``/``-isystem``/``-iquote``/``-idirafter``/``-cxx-isystem`` or MSVC/clang-cl
    ``/I``/``/external:I``/``/imsvc`` (attached or spaced) — in the pass-through
    ``--gcc-options`` string or the repeatable ``--gcc-option`` tokens. When
    present, a real build context is in play and an inferred ``-H`` root must
    defer to it; when absent, the inferred root can take ``-I`` priority.
    Compile-DB include dirs are folded into the user ``-I`` list upstream, so they
    need no detection here — an inferred ``-I`` appended after them is already
    lower priority.
    """
    toks: list[str] = list(gcc_option_tokens)
    if gcc_options:
        try:
            toks += shlex.split(gcc_options, posix=os.name != "nt")
        except ValueError:
            toks += gcc_options.split()
    return any(t.startswith(p) for t in toks for p in _INCLUDE_FLAG_PREFIXES)


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
    * ``-isystem`` tokens when the compile context supplies its own include dirs
      (``-I``/``-isystem``/``/I``/… in ``gcc_options``/tokens). ``-isystem`` is
      searched *after* the build context's ``-I`` **and** its earlier ``-isystem``
      entries (the build's flags are emitted first), so a real build context
      keeps priority — yet still *before* the standard system dirs, so the
      system-colliding-basename case (``<endian.h>``) keeps resolving the package
      header. (``-idirafter`` would drop below the system dirs and reintroduce
      that collision — Codex review.)

    Shared by the ``dump`` CLI path (``cli_dump_helpers.perform_elf_dump``) and
    the service/``scan`` path (``service._dump_elf``) so they cannot drift.
    """
    user = {str(i.resolve()) for i in user_includes}
    inferred = [
        d for d in _implicit_header_includes(headers) if str(d.resolve()) not in user
    ]
    if not inferred:
        return [], []
    if _has_include_build_context(gcc_options, gcc_option_tokens):
        toks: list[str] = []
        for d in inferred:
            toks += ["-isystem", str(d)]
        return [], toks
    return inferred, []
