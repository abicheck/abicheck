# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""The S2 preprocessor tier's two **probe families**, and why they are tallied
apart.

``preprocessor_facts.py`` runs two different clang invocations for two
different questions:

- ``clang -E -dM``, once per compile unit, whose captured ABI-macro values feed
  :func:`~abicheck.buildsource.preprocessor_facts.find_macro_divergence`;
- ``clang -M``, once per public header, whose resolved include set feeds
  :func:`~abicheck.buildsource.preprocessor_facts.find_private_header_leaks`.

The tier originally counted only run-wide totals — attempted, succeeded, and
probes truncated by ``ABICHECK_PREPROCESSOR_SCAN_MAX_PROBES``. Those totals are
fine for a coverage *row*, but they cannot answer a per-check question, and two
defects followed from asking them one anyway (Codex review, P2):

1. **Cross-contamination.** A build with more compile units than the probe cap
   marked the *header-leak* check insufficient even when every public header had
   been probed successfully — and truncating headers invalidated a fully
   completed macro check the same way. Each check's sufficiency depended on the
   other one's failures, which silently undid the point of answering
   sufficiency per check at all.

2. **"Probed and found nothing" read as "never probed."** A successful
   ``-E -dM`` probe of a unit that defines none of the curated ABI macros
   contributes no entry to ``abi_macros``, so the derived ``tus_scanned`` is
   ``0`` for an ordinary build with no ABI-toggle macros. That is *full*
   coverage, but a count of non-empty macro maps cannot say so, and the check
   could therefore never establish an absence — its evolution stayed
   ``not_evaluated`` forever.

Both are the same root cause: coverage was inferred from *findings* and from
aggregates, rather than tallied against the probes actually run. So each family
counts its own attempts, successes, and truncations, and a caller asks about the
family whose evidence its question rests on.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from typing import Any

#: ``clang -E -dM`` over each compile unit — the macro-divergence evidence.
MACRO_PROBES = "macro"

#: ``clang -M`` over each public header — the private-header-leak evidence.
HEADER_PROBES = "header"

#: Every family, so a third cannot be added without choosing where it counts.
PROBE_FAMILIES: tuple[str, ...] = (MACRO_PROBES, HEADER_PROBES)


@dataclass
class ProbeTallies:
    """Per-family attempted/succeeded/truncated counts, safe to update from the
    probe thread pool (:func:`abicheck.parallel_probe.run_parallel_probes`).

    A plain ``+= 1`` on a dict entry is not atomic, so every mutation takes the
    lock — the same reasoning as ``ClangPreprocessorExtractor``'s own aggregate
    counters, which these always sum to.
    """

    attempted: dict[str, int] = field(default_factory=dict)
    succeeded: dict[str, int] = field(default_factory=dict)
    truncated: dict[str, int] = field(default_factory=dict)
    _lock: threading.Lock = field(
        default_factory=threading.Lock, compare=False, repr=False
    )

    def record(self, family: str, *, ok: bool) -> None:
        """Record one completed probe of *family*."""
        with self._lock:
            self.attempted[family] = self.attempted.get(family, 0) + 1
            if ok:
                self.succeeded[family] = self.succeeded.get(family, 0) + 1

    def record_truncated(self, family: str, count: int) -> None:
        """Record *count* probes of *family* that the cap skipped entirely."""
        if count <= 0:
            return
        with self._lock:
            self.truncated[family] = self.truncated.get(family, 0) + count

    def snapshot(self) -> ProbeTallies:
        """A detached copy, for handing to a result object."""
        with self._lock:
            return ProbeTallies(
                attempted=dict(self.attempted),
                succeeded=dict(self.succeeded),
                truncated=dict(self.truncated),
            )

    def to_dict(self) -> dict[str, dict[str, int]]:
        """The three report keys, spelled as the report has always spelled them."""
        return {
            "family_attempted": dict(self.attempted),
            "family_succeeded": dict(self.succeeded),
            "family_truncated": dict(self.truncated),
        }


#: Probes attempted per family before the cap applies, absent an override.
DEFAULT_MAX_PROBES = 512

#: How each family names its own unit, for the cap diagnostic.
_FAMILY_NOUNS: dict[str, tuple[str, str]] = {
    MACRO_PROBES: ("macro", "compile unit(s)"),
    HEADER_PROBES: ("header-leak", "public header(s)"),
}


def max_probes() -> int:
    """The per-family probe cap (``ABICHECK_PREPROCESSOR_SCAN_MAX_PROBES``)."""
    env = os.environ.get("ABICHECK_PREPROCESSOR_SCAN_MAX_PROBES", "").strip()
    if env:
        try:
            value = int(env)
        except ValueError:
            value = 0
        if value > 0:
            return value
    return DEFAULT_MAX_PROBES


def apply_probe_cap(
    items: list[Any], family: str, tallies: ProbeTallies
) -> tuple[list[Any], str | None]:
    """Trim *items* to the cap, tallying and describing what was dropped.

    Returns the kept items and a diagnostic, or ``None`` when nothing was
    dropped. Lives here rather than in each ``capture_*`` method because the
    cap is a per-family fact and the tally it feeds is this module's: two hand
    copies of "trim, add to the count, append a message" is how a truncated
    compile-unit set came to be counted against the header-leak check.
    """
    cap = max_probes()
    if len(items) <= cap:
        return items, None
    dropped = len(items) - cap
    tallies.record_truncated(family, dropped)
    noun, unit = _FAMILY_NOUNS[family]
    return items[:cap], (
        f"preprocessor {noun} scan capped at {cap} {unit}; {dropped} skipped "
        "(set ABICHECK_PREPROCESSOR_SCAN_MAX_PROBES to raise)"
    )
