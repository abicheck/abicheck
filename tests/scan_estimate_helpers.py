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

"""Test-local operand description for :func:`abicheck.service_scan.estimate_scan`.

ADR-068 Phase 4's typed-API slice retired ``ScanRequest``: a dry-run cost
projection is a projection over an *input*, so ``estimate_scan`` now takes the
canonical :class:`~abicheck.api_types.InputSpec` plus the run-scoped scalars
(depth/mode/source method/changed-path seed/TU cap) that are not properties of
the operand at all.

The cost-model cases predate that split and are written in the old request's
vocabulary. :class:`EstimateOperand` keeps that vocabulary *in the tests* while
driving the real, current API through :func:`estimate` — deliberately a thin
adapter with no logic of its own, so it can never become a second cost model
the way a re-implemented request object would.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from abicheck.api_types import InputSpec
from abicheck.service_scan import CostEstimate, estimate_scan


@dataclass
class EstimateOperand:
    """One operand plus the run-scoped level inputs, in the retired shape."""

    binaries: list[Path] = field(default_factory=list)
    headers: list[Path] = field(default_factory=list)
    includes: list[Path] = field(default_factory=list)
    sources: Path | None = None
    compile_db: Path | None = None
    build_info: Path | None = None
    build_config: Path | None = None
    build_targets: tuple[str, ...] = ()
    mode: str = "pr"
    source_method: str | None = None
    depth: str | None = None
    changed_paths: list[str] = field(default_factory=list)
    seeded: bool = False
    max_tus: int | None = None
    #: Accepted and ignored: never an input to the cost model.
    lang: str = "c++"

    def side(self) -> InputSpec:
        """This operand as the :class:`InputSpec` ``estimate_scan`` takes."""
        return InputSpec.of(
            self.binaries[0] if self.binaries else None,
            headers=self.headers,
            includes=self.includes,
            sources=self.sources,
            build_info=self.build_info,
            build_config=self.build_config,
            build_targets=self.build_targets,
        )


def estimate(req: EstimateOperand, **kwargs: Any) -> list[CostEstimate]:
    """``estimate_scan`` driven from an :class:`EstimateOperand`."""
    return estimate_scan(
        req.side(),
        mode=req.mode,
        source_method=req.source_method,
        depth=req.depth,
        changed_paths=req.changed_paths,
        seeded=req.seeded,
        max_tus=req.max_tus,
        compile_db=req.compile_db,
        **kwargs,
    )
