"""The per-artifact pipeline: ``ArtifactRequest -> ResolvedPlan -> Result``.

ADR-061 Phase 3's target layout, complete:

* :mod:`.contracts` -- the cleanup-thunk session type a resolved plan carries.
* :mod:`.resolve` -- decide what an extraction will do, without doing it.
* :mod:`.execute` -- run that plan and report what it actually achieved.

The two halves are deliberately separate modules rather than two functions in
one: keeping "decide" runnable without "do" is what lets ``dump --dry-run``
render the same resolved plan a real run consumes, instead of re-deriving a
preview that looks authoritative while being connected to nothing.
"""

from .contracts import ResolvedArtifactPlan
from .execute import (
    SideResolution,
    embed_side_build_source,
    enforce_requested_depth,
    resolve_side_snapshot,
)
from .resolve import (
    BaselineReuseContext,
    is_raw_source_tree,
    reject_hybrid_source_frontend,
    resolve_baseline_compile_context,
)

__all__ = [
    "BaselineReuseContext",
    "ResolvedArtifactPlan",
    "SideResolution",
    "embed_side_build_source",
    "enforce_requested_depth",
    "is_raw_source_tree",
    "reject_hybrid_source_frontend",
    "resolve_baseline_compile_context",
    "resolve_side_snapshot",
]
