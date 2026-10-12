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

"""Keep a multi-header L2 parse alive when individual headers cannot parse.

``-H include/`` expands to every header in the directory and parses them as
one aggregate translation unit, so a single header the frontend cannot
compile -- oneDNN's ``dnnl_sycl.hpp`` raises ``#error "Unsupported
compiler"`` under castxml -- used to sink the whole directory: no L2 surface
at all, for any header. The clang backend already drops *direct-inclusion
guard* headers (``dumper_clang_errors.retry_excluding_error_headers``); the
default castxml backend had nothing.

This module attributes each compile error to the top-level header whose
``#include`` chain produced it, drops exactly those headers, and retries.
It is deliberately **not** silent: every dropped header is logged, and the
caller records the list on the snapshot (``AbiSnapshot.ast_toolchain
["unparseable_headers_excluded"]``) so the report states the reduced
evidence. Deterministic: attribution depends only on the diagnostics text
and the ordered header list, and the retained order is the input order.

It never guesses. A failure that is a *conflict between* listed headers
(an error whose attached ``note:`` -- e.g. ``previous definition is here``
-- attributes to a different listed header) is not self-contained: which
header to drop would be arbitrary, so it re-raises. The criterion is
structural (clang's diagnostic notes), so it is deterministic and costs no
extra parse. An error it cannot attribute to a specific header (in
the aggregate itself, in a toolchain header reached from every header, a
toolchain-version failure), or one attributed to *every* remaining header,
re-raises the original failure unchanged.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

from ..errors import SnapshotError
from ..model.header_exclusion_record import EXCLUDED_HEADERS_TOOLCHAIN_KEY
from ..process_resources import BudgetedExecutor

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

__all__ = [
    "EXCLUDED_HEADERS_TOOLCHAIN_KEY",
    "attribute_failing_headers",
    "parse_excluding_unparseable_headers",
]

log = logging.getLogger(__name__)

T = TypeVar("T")


# Include-chain frames. clang prints one ``In file included from X:N:`` line
# per level, outermost first. GCC prints one group, innermost first:
# ``In file included from X:N,`` then continuation lines
# ``                 from Y:N,`` ... with the last (outermost) ending in ``:``.
_FRAME_RE = re.compile(
    r"^In file included from (?P<file>.+?):(?P<line>\d+)(?::\d+)?(?P<end>[,:])\s*$"
)
_CONT_RE = re.compile(r"^\s+from (?P<file>.+?):(?P<line>\d+)(?::\d+)?(?P<end>[,:])\s*$")
_ERROR_RE = re.compile(r"^(?P<file>.+?):(?P<line>\d+)(?::\d+)?:\s*(?:fatal )?error:")
_NOTE_RE = re.compile(r"^(?P<file>.+?):(?P<line>\d+)(?::\d+)?:\s*note:")


def _norm(path: str | Path) -> str:
    try:
        return str(Path(path).resolve())
    except (OSError, RuntimeError):
        return str(path)


def _scan_diagnostics(
    stderr: str, headers: Sequence[Path]
) -> tuple[set[int], set[int]]:
    """``(failing, conflicting)``: 0-based indices into *headers*.

    *failing* holds the headers whose ``#include`` chain raised an error. Each
    error is attributed through its include chain: when the outermost frame is
    the aggregate (a file that is none of *headers*), the input is the file
    that frame includes -- the chain's next entry -- looked up in *headers* by
    path; only without such a frame does the innermost listed file in the
    chain name it. The aggregate's line numbers are never read: its layout
    belongs to whoever writes it (a preamble include precedes the headers), so
    a line-to-index rule would silently blame a neighbour the moment that
    layout changed. An error nothing in the chain attributes is skipped, never
    guessed at.

    *conflicting* holds the error-side indices whose error has a ``note:``
    (``previous definition is here``, ``candidate``, ...) attributing to a
    *different* listed header: such a failure exists only because the two
    headers share one translation unit, so dropping either would be an
    arbitrary choice and the caller must fail instead.
    """
    index = {_norm(h): i for i, h in enumerate(headers)}
    failing: set[int] = set()
    conflicting: set[int] = set()
    chain: list[tuple[str, int]] = []
    current: int | None = None  # attribution of the error notes belong to
    gcc_group: list[tuple[str, int]] | None = None  # innermost-first
    for raw in stderr.splitlines():
        line = raw.rstrip()
        frame = _FRAME_RE.match(line)
        cont = _CONT_RE.match(line) if gcc_group is not None else None
        if frame or cont:
            m = frame or cont
            assert m is not None
            loc = (m.group("file"), int(m.group("line")))
            if frame and m.group("end") == ":" and gcc_group is None:
                chain.append(loc)  # clang style (or a one-level GCC chain)
                continue
            gcc_group = [*(gcc_group or []), loc]
            if m.group("end") == ":":  # GCC group complete: store outermost-first
                chain.extend(reversed(gcc_group))
                gcc_group = None
            continue
        gcc_group = None
        err = _ERROR_RE.match(line)
        if err:
            located = [*chain, (err.group("file"), int(err.group("line")))]
            current = _attribute(located, index)
            if current is not None:
                failing.add(current)
        else:
            note = _NOTE_RE.match(line)
            if note and current is not None:
                located = [*chain, (note.group("file"), int(note.group("line")))]
                other = _attribute(located, index)
                if other is not None and other != current:
                    conflicting.add(current)
        chain = []
    return failing, conflicting


def _attribute(located: list[tuple[str, int]], index: dict[str, int]) -> int | None:
    # The aggregate TU's own frame names the top-level input: the file it
    # includes, i.e. the chain's next entry. Prefer it over any inner listed
    # header -- when listed A includes listed B and B fails only under a
    # macro A set, the input to drop is A, not B. Resolved by path, never by
    # the frame's line number: the aggregate's layout is its writer's
    # (``write_castxml_aggregate`` puts a preamble first), and an unlisted
    # next entry (that preamble, a toolchain file) attributes to nothing.
    outer_file, _outer_line = located[0]
    if len(located) > 1 and _norm(outer_file) not in index:
        return index.get(_norm(located[1][0]))
    # No aggregate frame: the innermost listed header in the chain.
    for file, _line in reversed(located):
        idx = index.get(_norm(file))
        if idx is not None:
            return idx
    return None


def attribute_failing_headers(
    exc: SnapshotError, headers: Sequence[Path]
) -> list[Path]:
    """The headers *exc*'s diagnostics blame, by the same rules the exclusion
    loop below uses; ``[]`` when that loop would re-raise instead (nothing
    attributable, a conflict between listed headers, or every header blamed).
    """
    stderr = exc.attribution_stderr or exc.stderr or str(exc)
    bad, conflicts = _scan_diagnostics(stderr, headers)
    if not bad or conflicts or len(bad) >= len(headers):
        return []
    return [headers[i] for i in sorted(bad)]


def parse_excluding_unparseable_headers(
    headers: Sequence[Path],
    attempt: Callable[[list[Path]], T],
    *,
    is_header_specific: Callable[[SnapshotError], bool] = lambda _exc: True,
) -> tuple[T, list[Path]]:
    """Run ``attempt(headers)``, dropping headers that fail to compile.

    Returns ``(result, excluded)``. Only a multi-header parse is reduced; a
    single ``-H`` file is the user's explicit choice and its failure is
    theirs to see. Each round removes at least one header, so the loop is
    bounded by ``len(headers)``. The failure is re-raised unchanged when
    no header is attributable, when every remaining header is, or when
    *is_header_specific* says the failure is not about any one header (a
    toolchain-version mismatch).
    """
    active = list(headers)
    excluded: list[Path] = []
    # Headers blamed only by an isolated probe, not yet confirmed in the
    # context (preceding headers) a full parse gives them.
    unconfirmed: list[Path] = []
    probing = True
    while True:
        try:
            result = attempt(active)
        except SnapshotError as exc:
            if len(active) < 2 or not is_header_specific(exc):
                raise
            # A failed language-mode retry carries the retry's diagnostics
            # for attribution (the original mode's errors on C++ syntax
            # would implicate every C++ header, not the failing one).
            # A clash *between* listed headers is not attributable to either:
            # which one to drop would be an arbitrary choice, and the user's
            # explicit ``--exclude-header`` is the way to make it.
            dropped = attribute_failing_headers(exc, active)
            if not dropped:
                raise
            log.warning(
                "L2 header parse failed in %d of %d header(s); excluding them and "
                "retrying (reduced evidence: their declarations are absent from "
                "the L2 surface): %s",
                len(dropped),
                len(active),
                ", ".join(str(p) for p in dropped),
            )
            excluded.extend(dropped)
            active = [h for h in active if h not in dropped]
            if probing and _stopped_at_fatal(exc) and len(active) >= 2:
                # A fatal error ends the frontend run, so the diagnostics
                # above can only ever name one header per fatal; dropping
                # them one round at a time costs one full parse per bad
                # header (21 rounds on oneDAL). Probe the rest in halving
                # waves instead: rounds scale with log(n), not with f.
                found = _probe_remaining(active, attempt, is_header_specific)
                if found:
                    log.warning(
                        "L2 header parse: probing found %d more unparseable "
                        "header(s); excluding them (reduced evidence): %s",
                        len(found),
                        ", ".join(str(p) for p in found),
                    )
                    excluded.extend(found)
                    unconfirmed.extend(found)
                    blamed = set(found)
                    active = [h for h in active if h not in blamed]
            continue
        if unconfirmed:
            # A probe parsed its group without the headers before it, so a
            # header that needs an earlier one (its context in the full TU)
            # fails there but not here. Re-check each in its real context;
            # the restored set then goes through one full parse, and any
            # failure there is attributed by the ordinary full-context rules.
            restored = _confirm_in_context(headers, active, unconfirmed, attempt)
            unconfirmed = []
            probing = False
            if restored:
                keep = set(active) | set(restored)
                active = [h for h in headers if h in keep]
                excluded = [h for h in excluded if h not in set(restored)]
                continue
        return result, excluded


def _confirm_in_context(
    headers: Sequence[Path],
    active: Sequence[Path],
    suspects: Sequence[Path],
    attempt: Callable[[list[Path]], object],
) -> list[Path]:
    """Suspects that parse after the active headers preceding them.

    Concurrent waves to a fixed point: suspect ``s`` is parsed as the
    context headers (active plus suspects already restored) that precede it
    in input order, followed by ``s`` -- the context it has in the full
    parse. A restored suspect joins the context of the next wave, so a chain
    ``h0 -> h1 -> h2`` whose middle link was also a suspect restores every
    link. At most ``len(suspects)`` waves. Returned in input order.
    """
    position = {h: i for i, h in enumerate(headers)}

    def ok(group: list[Path]) -> bool:
        try:
            attempt(group)
        except SnapshotError:
            return False
        return True

    context = set(active)
    pending = list(suspects)
    restored: list[Path] = []
    with BudgetedExecutor(_probe_jobs()) as pool:
        while pending:
            groups = [
                sorted(
                    (h for h in context if position[h] < position[s]),
                    key=position.__getitem__,
                )
                + [s]
                for s in pending
            ]
            passed = list(pool.map(ok, groups))
            wave = [s for s, good in zip(pending, passed, strict=True) if good]
            if not wave:
                break
            restored.extend(wave)
            context.update(wave)
            pending = [s for s in pending if s not in context]
    return sorted(restored, key=position.__getitem__)


def _stopped_at_fatal(exc: SnapshotError) -> bool:
    """Whether *exc*'s frontend run ended at a fatal error (or the error
    limit), so headers after it were never checked."""
    text = exc.attribution_stderr or exc.stderr or str(exc)
    return "fatal error:" in text or "too many errors emitted" in text


def _probe_jobs() -> int:
    try:
        jobs = int(os.environ.get("ABICHECK_UNPARSEABLE_PROBE_JOBS", "0"))
    except ValueError:
        jobs = 0
    return jobs if jobs > 0 else max(1, min(4, os.cpu_count() or 1))


def _probe_remaining(
    active: Sequence[Path],
    attempt: Callable[[list[Path]], object],
    is_header_specific: Callable[[SnapshotError], bool],
) -> list[Path]:
    """Headers in *active* that fail, found in halving waves of probes.

    Each wave parses disjoint, order-preserving groups concurrently; a group
    that fails is attributed by the same rules as the main loop, its blamed
    headers recorded, and its remainder split again while its run stopped at
    a fatal error. A group whose failure attributes to nothing is split too;
    a single header failing in isolation is recorded only when its own
    diagnostics blame it. Sequential waves are ``O(log n)`` for any number
    of independent failures. Returned in input order; a probe result is
    discarded (the caller re-runs the reduced full set, so the result it
    returns comes from the same full-context parse as before).
    """
    order = {h: i for i, h in enumerate(active)}
    found: set[Path] = set()
    mid = len(active) // 2
    groups: list[list[Path]] = [list(active[:mid]), list(active[mid:])]

    def run(group: list[Path]) -> SnapshotError | None:
        try:
            attempt(group)
        except SnapshotError as exc:
            return exc
        return None

    with BudgetedExecutor(_probe_jobs()) as pool:
        while groups:
            outcomes = list(pool.map(run, groups))
            nxt: list[list[Path]] = []
            for group, exc in zip(groups, outcomes, strict=True):
                if exc is None or not is_header_specific(exc):
                    continue
                stderr = exc.attribution_stderr or exc.stderr or str(exc)
                bad, conflicts = _scan_diagnostics(stderr, group)
                if conflicts:
                    continue
                blamed = [group[i] for i in sorted(bad)]
                if len(group) == 1:
                    found.update(blamed)
                    continue
                if len(blamed) >= len(group):
                    blamed = []
                found.update(blamed)
                rest = [h for h in group if h not in set(blamed)]
                if blamed and not _stopped_at_fatal(exc):
                    # Every error was reported; the rest is clean here.
                    continue
                if len(rest) >= 2:
                    half = len(rest) // 2
                    nxt += [rest[:half], rest[half:]]
                elif rest:
                    nxt.append(rest)
            groups = nxt
    return sorted(found, key=order.__getitem__)
