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

"""The source-graph risk findings that compare two semantic graphs.

Each detector here is a pure old-vs-new comparison over two
:class:`~abicheck.model.source_graph.SourceGraphSummary` objects and emits
``COMPATIBLE_WITH_RISK`` findings only, so ``evidence_report.py`` wires them as
one call instead of one import and one line per detector.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .consumer_compiled_dependency import consumer_compiled_dependency_findings
from .virtual_dispatch_set import virtual_dispatch_set_findings

if TYPE_CHECKING:
    from ..model.change import Change
    from ..model.source_graph import SourceGraphSummary


def source_graph_risk_findings(
    old: SourceGraphSummary, new: SourceGraphSummary
) -> list[Change]:
    """Every graph-risk finding between *old* and *new*, in detector order."""
    return virtual_dispatch_set_findings(
        old, new
    ) + consumer_compiled_dependency_findings(old, new)
