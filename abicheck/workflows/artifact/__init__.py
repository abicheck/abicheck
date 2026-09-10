# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

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
