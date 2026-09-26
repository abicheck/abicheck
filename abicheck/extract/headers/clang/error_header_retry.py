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

"""Dropping the top-level headers that refuse direct inclusion from a clang
header-AST parse, and re-parsing the rest.

Moved out of ``dumper_clang_errors`` whole when the evidence-entity-model gap
A3 fix made the retry *record* what it dropped (``abicheck_excluded_headers`` on
the result, carried onward by ``storage.ast_parse_exclusions``) instead of only
logging it. Pure string/driver logic, testable without a compiler.
"""

from __future__ import annotations

import logging
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

__all__ = ["retry_excluding_error_headers"]

log = logging.getLogger(__name__)

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
        # Recorded, not only logged (evidence-entity-model gap A3): the
        # snapshot's header coverage is partial, and `_parse_clang_ast_result`
        # carries this onto the tree and the cache entry.
        setattr(result, "abicheck_excluded_headers", [str(p) for p in excluded])
        log.warning(
            "L2 header parse: excluded %d header(s) not meant for direct "
            "inclusion (they raise #error): %s. Their declarations are absent "
            "from the L2 surface — point -H at the library's umbrella header "
            "(e.g. oneapi/tbb.h) to include the intended public API.",
            len(excluded),
            ", ".join(p.name for p in excluded),
        )
    return result
