"""Pure projections of an aggregate workflow result.

These functions do not load reports or alter compatibility, coverage, or gate
facts. They preserve the established aggregate JSON and text contracts while
rendering ownership migrates toward the canonical report document.
"""

from __future__ import annotations

from typing import Any

from abicheck.workflows.aggregate.fold import AggregateResult


def render_aggregate_json(result: AggregateResult) -> dict[str, Any]:
    """Project *result* to the stable JSON-compatible aggregate document."""
    return result.to_dict()


def render_aggregate_text(result: AggregateResult) -> str:
    """Project *result* to the stable human-readable aggregate report."""
    return result.render_text()


__all__ = ["render_aggregate_json", "render_aggregate_text"]
