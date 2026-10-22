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

"""What happens to a finding after diff (ADR-067 "record before disposing").

The finding-disposition owner. Raw detector findings plus the resolved
:class:`DispositionRules` go in; a :class:`PipelineContext` comes out holding
the kept findings, every disposed-of bucket (redundant, opaque-filtered,
suppressed, out-of-surface) and a disposition ledger that accounts for each
input finding -- the ordering of the steps that decide those dispositions is
owned here (:mod:`.default_pipeline`), not by the caller.

* :func:`dispose_findings` -- run the default disposition order.
* :func:`dispose_by_suppression` -- apply suppression to findings produced
  after that run (``compare()``'s late SONAME/floor/metric/pattern findings).

The step protocol and the audited runner (:mod:`.pipeline`) and the per-run
state (:mod:`.context`) are the seam individual steps and their tests use.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .run_state import DispositionRules, PipelineContext
from .suppression import (
    SuppressionDisposition,
    dispose_by_suppression,
    overreach_diagnostic,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ...model import AbiSnapshot
    from ...model.change import Change
    from ..disposition_ledger import DispositionLedger
    from ..public_surface_query import ComparisonPublicSurfaces


def dispose_findings(
    changes: list[Change],
    old: AbiSnapshot | None,
    new: AbiSnapshot,
    rules: DispositionRules | None = None,
    *,
    ledger: DispositionLedger | None = None,
    absent_baseline: AbiSnapshot | None = None,
    public_surfaces: ComparisonPublicSurfaces | None = None,
) -> PipelineContext:
    """Dispose of *changes* (``old`` -> ``new``) under *rules*, audited.

    Runs the default step order. The returned context's buckets partition
    every input finding together with its ``disposition_ledger`` (created
    when *ledger* is ``None``); see
    :meth:`.pipeline.DispositionPipeline.run` for the parameters.
    """
    # Function-local: the default order names the step classes, whose legacy
    # module imports this package's pipeline primitives.
    from .default_pipeline import DEFAULT_PIPELINE

    return DEFAULT_PIPELINE.run(
        changes,
        old,
        new,
        rules,
        ledger=ledger,
        absent_baseline=absent_baseline,
        public_surfaces=public_surfaces,
    )


__all__ = [
    "DispositionRules",
    "PipelineContext",
    "SuppressionDisposition",
    "dispose_by_suppression",
    "dispose_findings",
    "overreach_diagnostic",
]
