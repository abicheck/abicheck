# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Why each hidden redundant change was hidden, stated per reason.

Three producers move a change into ``DiffResult.redundant_changes``, and each
records why on ``Change.caused_by_type``: the root-type filter (the root
type's name), the rename pass (``rename:old→new``) and the export-existence
fold (``export_existence:<kind>:<symbol>``). The notes used to say "derived
from root type changes" for all of them, which was false for oneCCL's ten
folded ``exported_not_public`` findings. Every renderer reads the reasons
from here, so a new producer needs one entry below, not one per format.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from ..policy.export_existence_fold import CAUSED_BY_PREFIX as _EXISTENCE_PREFIX

__all__ = ["redundancy_reason_counts", "redundancy_reason_text"]

#: ``caused_by_type`` prefix -> reason phrase, in display order. The last
#: entry (no prefix) is the root-type filter, which stores a bare type name.
_REASONS: tuple[tuple[str, str], ...] = (
    (_EXISTENCE_PREFIX, "folded into their symbol's existence finding"),
    ("rename:", "folded into a rename"),
    ("", "derived from root type changes"),
)
_UNRECORDED = "reason not recorded"


def _reason(change: Any) -> str:
    cause = getattr(change, "caused_by_type", None)
    if not cause:
        return _UNRECORDED
    return next(phrase for prefix, phrase in _REASONS if cause.startswith(prefix))


def redundancy_reason_counts(result: Any) -> list[tuple[str, int]]:
    """``(reason, count)`` for the *redundant_count* hidden changes, in display
    order; their counts always sum to ``result.redundant_count``."""
    total = int(getattr(result, "redundant_count", 0) or 0)
    hidden = list(getattr(result, "redundant_changes", None) or [])[:total]
    counts = Counter(_reason(c) for c in hidden)
    if len(hidden) < total:  # list dropped (e.g. merged back for display)
        counts[_UNRECORDED] += total - len(hidden)
    order = [phrase for _prefix, phrase in _REASONS] + [_UNRECORDED]
    return [(phrase, counts[phrase]) for phrase in order if counts[phrase]]


def redundancy_reason_text(result: Any) -> str:
    """``"10 folded into their symbol's existence finding; 2 derived from root
    type changes"`` -- a single reason without its count."""
    counts = redundancy_reason_counts(result)
    if len(counts) == 1:
        return counts[0][0]
    return "; ".join(f"{n} {phrase}" for phrase, n in counts)
