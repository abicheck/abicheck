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

"""clang L2 header-parse error analysis (split out of ``dumper.py``).

Pure string-only diagnostics over clang stderr — the C→C++ language-probe signal
(:func:`_is_missing_cpp_stdlib_header_error`) and the ``#error`` header
attribution (:func:`_headers_failing_in_aggregate`) — plus the graceful
exclusion driver (:func:`retry_excluding_error_headers`) that drops headers not
meant for direct inclusion and re-parses. Kept in its own module so ``dumper.py``
stays under the file-size cap; the parsers are unit-tested without a compiler.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import subprocess

log = logging.getLogger(__name__)

#: C++ ``<cXXX>`` C-compatibility headers. A missing one of these under a C-mode
#: parse unambiguously means the TU is C++ — the signal that drives the clang
#: C→C++ retry. Deliberately restricted to the ``<cXXX>`` spellings rather than
#: the *full* C++ library set: a C project never bare-includes ``<cstddef>``,
#: whereas plain names like ``<string>``/``<version>`` collide with plausible C
#: project headers (oneTBB itself ships a ``version.h``), so matching those could
#: re-parse a broken C build as C++ — silently caching a wrong AST instead of
#: reporting the genuine missing dependency (Codex review). In practice the first
#: miss for a C++ TU parsed in C mode is always a ``<cXXX>`` header (libstdc++
#: pulls them in early); the rare "pure-C++ header missing first" case is handled
#: by passing ``--lang c++`` explicitly.
_CPP_STDLIB_HEADERS = frozenset(
    {
        "cassert",
        "cctype",
        "cerrno",
        "cfenv",
        "cfloat",
        "cinttypes",
        "ciso646",
        "climits",
        "clocale",
        "cmath",
        "csetjmp",
        "csignal",
        "cstdalign",
        "cstdarg",
        "cstdbool",
        "cstddef",
        "cstdint",
        "cstdio",
        "cstdlib",
        "cstring",
        "ctgmath",
        "ctime",
        "cuchar",
        "cwchar",
        "cwctype",
    }
)

#: ``'<name>' file not found`` — captures the quoted include that clang could not
#: resolve, checked against :data:`_CPP_STDLIB_HEADERS`.
_MISSING_HEADER_RE = re.compile(r"'([^'/]+)' file not found")

#: clang's rendered source line under a diagnostic, e.g. ``  21 |     #error …``.
#: Used to confirm a header failure is a preprocessor ``#error`` (a header not
#: meant for direct inclusion) rather than a real compile error before excluding.
_RENDERED_ERROR_DIRECTIVE = re.compile(r"^\s*\d+\s*\|.*#\s*error\b")

#: Phrasing that marks a ``#error`` as a *direct-inclusion guard* — an internal
#: header that refuses to be ``#include``d on its own. Only these are safe to
#: exclude, and the bar is deliberately high: the message must carry an
#: unambiguous direct-inclusion signal — ``directly`` tied to an ``include`` verb
#: ("do not #include this header directly", "directly include"), or the literal
#: phrase ``internal header``. Nothing else qualifies. Every config / feature /
#: include-order ``#error`` on an otherwise-public header — "Set FOO to include
#: optional support", "feature X not included in this build", "define
#: MYLIB_CONFIG first", "Do not include public.h before config.h" — lacks both
#: signals, so it surfaces as a hard parse failure telling the user to fix the
#: build rather than the header being silently dropped from the L2 surface
#: (Codex P2, iterated: only ``directly``/``internal header`` count as guards).
_DIRECT_INCLUDE_GUARD_RE = re.compile(
    r"\binclude[ds]?\b.{0,40}\bdirectly\b"  # "include(d) this header ... directly"
    r"|\bdirectly\b.{0,40}\binclude"  # "directly include ..."
    r"|\binternal header\b",
    re.IGNORECASE,
)


def _is_missing_cpp_stdlib_header_error(stderr: str) -> bool:
    """True if a clang parse failed because a C++ ``<cXXX>`` header was not found.

    Pure/string-only so it is unit-testable without a compiler. Matches clang's
    ``fatal error: '<name>' file not found`` and confirms ``<name>`` is one of the
    C++ ``<cXXX>`` C-compatibility headers (:data:`_CPP_STDLIB_HEADERS`). A C TU
    never includes one, so such a miss means the TU is C++ and a C-mode parse
    picked the wrong language — driving the C→C++ retry. Plain-name C++ headers
    (``<string>``/``<version>``/…) are deliberately *not* matched because they
    collide with plausible C project header names; matching them could silently
    re-parse a broken C build as C++ instead of reporting the missing dependency.
    """
    return any(
        m.group(1) in _CPP_STDLIB_HEADERS for m in _MISSING_HEADER_RE.finditer(stderr)
    )


def _is_direct_include_guard_failure(stderr: str) -> bool:
    """True if a parse failure looks like a header refusing direct inclusion.

    A coarse, frontend-agnostic signal used to route a *castxml* ``auto`` failure
    to the clang backend — which can granularly exclude the offending headers via
    :func:`retry_excluding_error_headers`, a thing the castxml path cannot do — so
    the ``-H <include-dir>`` case works on the default frontend too (review). The
    failure text must both look like an error and carry a direct-inclusion guard
    phrase (:data:`_DIRECT_INCLUDE_GUARD_RE`: "…include…directly" / "internal
    header"). Deliberately conservative: a castxml toolchain/syntax failure that
    merely happens to contain the word "directly" without "include" near it, or
    without any error context, does not match. Pure/string-only.
    """
    return "error" in stderr.lower() and bool(_DIRECT_INCLUDE_GUARD_RE.search(stderr))


def _headers_failing_in_aggregate(
    stderr: str, agg_path: Path, n_headers: int
) -> set[int]:
    """0-based indices of aggregate ``#include`` lines whose chain raised an error.

    The L2 aggregate TU emits one ``#include`` per header — header ``i`` on line
    ``i + 1``. A header not meant to be included directly raises a preprocessor
    ``#error`` (e.g. oneTBB's ``detail`` headers: "Do not #include this internal
    header directly"). A preview/feature-macro gate ("Set TBB_PREVIEW_… to
    include …") is *not* treated as a guard — it surfaces so the user defines the
    macro (Codex P2). When the error
    fires inside an *included* file, clang prints the include chain whose outermost
    frame is the aggregate TU — ``In file included from <agg>:<N>:`` — immediately
    before the ``error:`` line. ``<N>`` therefore identifies the offending
    top-level header, even through a deeper transitive chain (the aggregate frame
    is always printed first and persists until the next aggregate-rooted chain).

    Pure / string-only so it is unit-testable without a compiler. A header is
    excluded **only** when both hold: (1) the failure is a confirmed preprocessor
    ``#error`` (clang renders ``  21 | #error …`` below the diagnostic), and (2)
    the message reads like a *direct-inclusion guard*
    (:data:`_DIRECT_INCLUDE_GUARD_RE` — "do not include directly" / "internal
    header" / "Set … to include"). A real syntax error, a missing-build-flag
    ``#error`` (e.g. ``#error "define MYLIB_CONFIG first"``), or an error in the
    aggregate file itself is therefore *not* dropped — it surfaces as the hard
    parse failure ``dumper.py`` raises, keeping the L2 surface authoritative and
    telling the user to pass the required flag (Codex P2).
    """
    agg = str(agg_path)
    prefix = "In file included from "
    offending: set[int] = set()
    root: int | None = None
    lines = stderr.splitlines()
    for i, line in enumerate(lines):
        if line.startswith(prefix):
            rest = line[len(prefix) :]
            if rest.startswith(agg + ":"):
                tail = rest[len(agg) + 1 :].split(":", 1)[0]
                if tail.isdigit():
                    root = int(tail)
            # a deeper frame of the same chain keeps the aggregate root
            continue
        if ": error:" in line:
            if line.startswith(agg + ":"):
                root = None  # error in the umbrella itself — not header-excludable
                continue
            # Exclude only a *direct-inclusion guard*: (1) confirmed to be a
            # preprocessor #error via clang's rendered source line, AND (2) whose
            # message reads like a "don't include this directly" guard. A #error
            # reporting a missing config macro / unsupported target on a public
            # header matches (1) but not (2), so it is left in and surfaces as a
            # hard failure telling the user to pass the build flag (Codex P2).
            if root is not None:
                window = lines[i + 1 : i + 4]
                is_error_directive = any(
                    _RENDERED_ERROR_DIRECTIVE.match(w) for w in window
                )
                guard_text = " ".join([line, *window])
                if is_error_directive and _DIRECT_INCLUDE_GUARD_RE.search(guard_text):
                    idx = root - 1
                    if 0 <= idx < n_headers:
                        offending.add(idx)
    return offending


def retry_excluding_error_headers(
    *,
    result: subprocess.CompletedProcess[str],
    run_clang: Callable[[], subprocess.CompletedProcess[str]],
    write_agg: Callable[[list[Path]], None],
    agg_path: Path,
    active_headers: list[Path],
    max_attempts: int = 5,
) -> subprocess.CompletedProcess[str]:
    """Drop headers whose aggregate compile ``#error``s and re-parse; return result.

    When ``-H`` expands to a whole public include dir, some headers are not meant
    to be included directly (preview / internal ``detail`` headers) and a single
    ``#error`` would otherwise abort the entire L2 parse. Exclude the offending
    top-level headers (identified by :func:`_headers_failing_in_aggregate`),
    rewrite the aggregate via *write_agg*, and retry *run_clang* — so the rest of
    the public surface is still parsed. Bounded by *max_attempts* so a
    pathological cascade can't loop forever; a single-header ``-H`` (an umbrella
    file the user chose) is never reduced. Logs exactly which headers were dropped
    on success so the omission is never silent.
    """
    excluded: list[Path] = []
    attempts = 0
    while (
        result.returncode != 0 and len(active_headers) > 1 and attempts < max_attempts
    ):
        bad = _headers_failing_in_aggregate(
            result.stderr or "", agg_path, len(active_headers)
        )
        if not bad or len(bad) >= len(active_headers):
            break
        excluded.extend(active_headers[i] for i in sorted(bad))
        active_headers = [h for i, h in enumerate(active_headers) if i not in bad]
        write_agg(active_headers)
        result = run_clang()
        attempts += 1
    if excluded and result.returncode == 0:
        log.warning(
            "L2 header parse: excluded %d header(s) not meant for direct "
            "inclusion (they raise #error): %s. Their declarations are absent "
            "from the L2 surface — point -H at the library's umbrella header "
            "(e.g. oneapi/tbb.h) to include the intended public API.",
            len(excluded),
            ", ".join(p.name for p in excluded),
        )
    return result
