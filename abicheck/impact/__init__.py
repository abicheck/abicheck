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

"""Unified impact-assessment model (G29 Phase 3 slice 1, ADR-052).

See :mod:`abicheck.impact.model` for the dataclasses and
:mod:`abicheck.impact.engine` for :func:`assess_change`, the builder.
"""

from __future__ import annotations

from .engine import assess_change
from .model import FindingDecision, GraphProofPath, ImpactAssessment, ProofStep

__all__ = [
    "FindingDecision",
    "GraphProofPath",
    "ImpactAssessment",
    "ProofStep",
    "assess_change",
]

# `correlate_root_causes`/`RootCauseGroup` (impact.correlation) are
# deliberately NOT imported here at module scope: `checker_types.py` imports
# `abicheck.impact.model.ImpactAssessment` while it is still being defined,
# and `impact.correlation` imports `checker_types.Change` at its own module
# scope -- eagerly importing `.correlation` from this package's `__init__`
# would close that cycle (`checker_types -> impact -> impact.correlation ->
# checker_types`, the middle module not yet finished initializing). Callers
# needing the composer import `abicheck.impact.correlation` directly (e.g.
# `reporter.py`, `reporter_markdown.py`), which is safe once `checker_types`
# has finished loading.
