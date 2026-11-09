# SPDX-License-Identifier: Apache-2.0
"""Hold execution tuning through an operation's final report consumer."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from ..model.performance import (
    PerformanceProfile,
    current_performance_profile,
    performance_profile_scope,
    tuning_for,
)
from ..storage.acyclic_json import gc_relaxed


@contextmanager
def execution_profile_scope(profile: PerformanceProfile | None) -> Iterator[None]:
    """Tune the whole operation, including projection and serialization.

    Restoring the collector thresholds between classification and rendering
    makes the renderer immediately rescan the retained comparison graph.
    The caller must hold this scope until its final result consumer finishes.
    """
    with performance_profile_scope(profile):
        with gc_relaxed(tuning_for(current_performance_profile()).relaxed_gc):
            yield
