# Copyright 2026 Nikolay Petrov
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

"""Delegating facade over ``abicheck.workflows.artifact`` (ADR-061 Phase 3).

The per-input resolve/execute pipeline this module used to *be* now lives in
:mod:`abicheck.workflows.artifact.resolve` (decide the plan) and
:mod:`abicheck.workflows.artifact.execute` (run it and report what it
achieved). This module survives only as the import path several callers and
tests already use.

**Import the owner, not this facade, in new code**, and patch the owner in
tests -- a ``monkeypatch.setattr`` against a name re-exported here binds the
facade's copy of the reference, which the real caller never reads.
"""

from __future__ import annotations

from .workflows.artifact.execute import (
    SideResolution,
    _resolve_side_snapshot_impl,
    embed_side_build_source,
    enforce_requested_depth,
    resolve_side_snapshot,
)
from .workflows.artifact.resolve import (
    BaselineReuseContext,
    _gated_build_query_inputs,
    _seeded_includes_and_compile_context,
    is_raw_source_tree,
    reject_hybrid_source_frontend,
    resolve_baseline_compile_context,
)

__all__ = [
    "BaselineReuseContext",
    "SideResolution",
    "embed_side_build_source",
    "enforce_requested_depth",
    "is_raw_source_tree",
    "reject_hybrid_source_frontend",
    "resolve_baseline_compile_context",
    "resolve_side_snapshot",
]

# Private names re-exported for the tests that reach for them directly. Listed
# explicitly rather than left to `import *` so a reader can see the whole
# compatibility surface, and marked so ruff does not strip them as unused.
_ = (
    _gated_build_query_inputs,
    _resolve_side_snapshot_impl,
    _seeded_includes_and_compile_context,
)
