"""Shared test helper: a snapshot's reported coverage row for one layer."""

from __future__ import annotations


def coverage_for(snap, layer):
    """The coverage row *snap*'s embedded evidence reports for *layer* -- a
    header-only dump's rows are derived from its header graph (ADR-063
    Phase 10: no synthesized pack)."""
    from abicheck.buildsource.evidence_report import optional_coverage

    value = getattr(layer, "value", layer)
    return next(
        (c for c in optional_coverage(snap.build_source, snap) if c.layer == value),
        None,
    )
