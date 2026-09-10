# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Cross-source evolution summary section (ADR-068 D3, plan P2, schema 3.3).

Follows this package's compute/render split (see ``abicheck/report/
AGENTS.md``): :func:`compute_cross_source_evolution_summary` reads
``DiffResult.changes`` for any :class:`~abicheck.checker_types.Change`
carrying a non-``None`` ``cross_source_evolution`` and returns a small frozen
fact; :func:`render_cross_source_evolution_json` turns it into the JSON
block. Neither computes anything the changes list does not already state,
and neither depends on ``--format``/``--write``/demangling/any filter --
ADR-068 D4 (presentation never changes analysis).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..checker_types import Change
from ..policy.evidence_status import CrossSourceEvolution


@dataclass(frozen=True, slots=True)
class CrossSourceEvolutionSummary:
    """Per-state counts of ``Change.cross_source_evolution``-stamped findings."""

    introduced: int
    resolved: int
    persistent: int
    not_evaluated: int


def compute_cross_source_evolution_summary(
    changes: Sequence[Change],
) -> CrossSourceEvolutionSummary | None:
    """Summarize *changes* by ``cross_source_evolution``, or ``None`` if none carry it.

    ``None`` (never an all-zero summary) means *changes* carries no
    evolution-stamped finding -- for every real front end (CLI, typed API,
    Action) that's a genuinely clean result, since none of them can disable
    the migrated stage (``compare()`` runs it automatically,
    ``cross_source_checks`` on by default with no public opt-out --
    ADR-068 D4/D5). The one exception is the Tier-1 core's own internal
    ``cross_source_checks`` keyword (kept only so a test can isolate the
    stage): a direct ``checker.compare(..., cross_source_checks=False)``
    call also produces ``None`` here, meaning "the stage didn't run" rather
    than "ran and found nothing" -- not reachable from any documented
    front end. Omitted from the JSON report entirely, matching every other
    optional block's own convention.
    """
    counts = dict.fromkeys(CrossSourceEvolution, 0)
    total = 0
    for c in changes:
        evolution = getattr(c, "cross_source_evolution", None)
        if evolution is None:
            continue
        counts[evolution] += 1
        total += 1
    if total == 0:
        return None
    return CrossSourceEvolutionSummary(
        introduced=counts[CrossSourceEvolution.INTRODUCED],
        resolved=counts[CrossSourceEvolution.RESOLVED],
        persistent=counts[CrossSourceEvolution.PERSISTENT],
        not_evaluated=counts[CrossSourceEvolution.NOT_EVALUATED],
    )


def render_cross_source_evolution_json(
    summary: CrossSourceEvolutionSummary | None,
) -> dict[str, int] | None:
    """Project *summary* into the JSON report's ``cross_source_evolution`` block."""
    if summary is None:
        return None
    return {
        "introduced": summary.introduced,
        "resolved": summary.resolved,
        "persistent": summary.persistent,
        "not_evaluated": summary.not_evaluated,
    }


def change_cross_source_evolution_field(c: Change) -> str | None:
    """The per-``Change`` JSON field value for ``cross_source_evolution``, or ``None``."""
    cse = getattr(c, "cross_source_evolution", None)
    return cse.value if cse is not None else None


#: Human-readable tag for each ``Change.cross_source_evolution`` state,
#: rendered inline next to a hygiene finding in Markdown output so it isn't
#: mistaken for newly-introduced drift on a re-diff of an otherwise-
#: unchanged pair of releases. Also consulted from the JSON-safe row shape
#: ``report/render_markdown_document.py`` builds for the default full-mode
#: report (keyed by the same ``.value`` string this module's own
#: :func:`change_cross_source_evolution_field` already produces).
CROSS_SOURCE_EVOLUTION_MD_TAGS: dict[str, str] = {
    "introduced": "🆕 introduced",
    "resolved": "✅ resolved",
    "persistent": "♻️ persistent (pre-existing, not new)",
    "not_evaluated": "❔ not evaluated on one side",
}


def cross_source_evolution_md_suffix(c: object) -> str:
    """``"\\n  > Cross-source hygiene: ..."`` tag for a stamped *c*, or ``""``.

    ADR-068 finding A: :func:`compute_cross_source_evolution` (``workflows.
    cross_source_evolution``) stamps every cross-source hygiene finding with
    its OLD->NEW evolution state, but before this fix no Markdown renderer
    ever read it -- a byte-identical rebuild's *entirely pre-existing*
    hygiene debt rendered indistinguishably from newly introduced drift. The
    JSON report has always carried this via
    :func:`change_cross_source_evolution_field`; this mirrors it into every
    Markdown call site instead of only JSON's.
    """
    cse = getattr(c, "cross_source_evolution", None)
    if cse is None:
        return ""
    label = cse.value if hasattr(cse, "value") else str(cse)
    tag = CROSS_SOURCE_EVOLUTION_MD_TAGS.get(label, label)
    return f"\n  > Cross-source hygiene: {tag}"
