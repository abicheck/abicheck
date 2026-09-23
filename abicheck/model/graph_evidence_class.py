# SPDX-License-Identifier: Apache-2.0
# Copyright The abicheck Authors

"""Evidence class of a graph edge kind (evidence-entity-model plan, Phase 0).

Two edges that look alike can carry very different authority: an edge read
directly off an artifact is not the same claim as one projected from a
record's own fields. Every edge kind a graph builder emits declares exactly
one class, so a consumer can tell binary evidence from a projection without
reading the builder's docstring.
"""

from __future__ import annotations

from enum import Enum


class EdgeEvidenceClass(str, Enum):
    """How an edge kind was established."""

    #: Read directly from one input's own observed facts (e.g. a header
    #: declaration's recorded source location).
    OBSERVED = "observed"
    #: A join between two independently observed facts that the builder
    #: resolved (e.g. a type spelling matched against the type index).
    RESOLVED_JOIN = "resolved_join"
    #: Projected from a single record's own fields; not matched against any
    #: other evidence and never itself proof that the relation was observed.
    DERIVED = "derived"


__all__ = ["EdgeEvidenceClass"]
