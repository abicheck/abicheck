"""Compute half of the Markdown "Not Evaluated (Contract)" section.

``compute_not_evaluated`` reads the ``Change`` sequence and returns the
``render_markdown`` section struct that ``render_not_evaluated_section``
formats. It lived in the flat ``reporter_markdown`` module, which ``report/``
may only reach through a back-reference; it now sits in ``report/`` beside its
renderer. ``reporter_markdown`` re-exports the name, so existing importers are
unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .render_markdown import NotEvaluatedEntry, NotEvaluatedSection

if TYPE_CHECKING:
    from ..model.change import Change


def compute_not_evaluated(
    not_evaluated: list[Change],
) -> NotEvaluatedSection | None:
    """The structured intermediate for :func:`~abicheck.report.render_markdown.render_not_evaluated_section`.

    Disclose the findings compatibility policy did not score (ADR-049 D1).

    These are real detector facts that carry no verdict: contract evaluation
    either proved the entity outside the declared contract, or could not
    resolve it from the evidence the run had. They are deliberately absent
    from the four verdict sections above -- filing an unscored finding under
    "Breaking Changes" would contradict the verdict printed at the top of the
    same report -- so this section is what keeps them visible, with the
    relevance and reason code that explain *why* they did not gate.

    ``None`` (and so entirely absent) unless the run opted into
    ``--contract``.
    """
    if not not_evaluated:
        return None
    entries = []
    for c in not_evaluated:
        relevance = getattr(c, "contract_relevance", None)
        reason = getattr(c, "contract_reason_code", None)
        label = getattr(relevance, "value", None) or "UNKNOWN"
        suffix = f" ({reason})" if reason else ""
        entries.append(NotEvaluatedEntry(change=c, label=label, suffix=suffix))
    return NotEvaluatedSection(entries=tuple(entries))
