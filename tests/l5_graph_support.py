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

"""Shared helper for tests that hand-build L5 source graphs.

An unflagged graph answers every edge absence ``unknown`` (evidence-entity-
model gap A5, ``compare.edge_query.source_graph_covers``). A hand-built pair
standing for a real build whose producers all ran must therefore say so; a
test studying a specific pass state sets its own flags and is left alone.
"""

from __future__ import annotations

from abicheck.model.source_graph import SourceGraphSummary
from abicheck.model.source_graph_coverage import PASS_EDGE_KINDS, graph_records_passes


def producers_ran_unless_flagged(
    old: SourceGraphSummary, new: SourceGraphSummary
) -> tuple[SourceGraphSummary, SourceGraphSummary]:
    """Stamp every producer's pass on both graphs, unless either side
    already records a pass state (then that test is about pass states)."""
    if not (graph_records_passes(old) or graph_records_passes(new)):
        for graph in (old, new):
            graph.extractor_passes.update(dict.fromkeys(PASS_EDGE_KINDS, True))
    return old, new
