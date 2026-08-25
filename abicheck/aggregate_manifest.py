"""Compatibility facade for the aggregate expected-target contract.

New internal code imports :mod:`abicheck.workflows.aggregate.resolve`.
"""

from __future__ import annotations

from .workflows.aggregate.resolve import (
    AGGREGATE_MANIFEST_VERSION,
    AggregateError,
    ExpectedTargets,
    OnMissingRequired,
    OnUnexpectedTarget,
    resolve_gate_policy,
)

__all__ = [
    "AGGREGATE_MANIFEST_VERSION",
    "AggregateError",
    "ExpectedTargets",
    "OnMissingRequired",
    "OnUnexpectedTarget",
    "resolve_gate_policy",
]
