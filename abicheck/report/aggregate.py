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
"""Pure projections of an aggregate workflow result.

These functions do not load reports or alter compatibility, coverage, or gate
facts. They preserve the established aggregate JSON and text contracts while
rendering ownership migrates toward the canonical report document.
"""

from __future__ import annotations

from typing import Any

from abicheck.workflows.aggregate.fold import AggregateResult


def render_aggregate_json(result: AggregateResult) -> dict[str, Any]:
    """Project *result* to the stable JSON-compatible aggregate document."""
    return result.to_dict()


def render_aggregate_text(result: AggregateResult) -> str:
    """Project *result* to the stable human-readable aggregate report."""
    return result.render_text()


__all__ = ["render_aggregate_json", "render_aggregate_text"]
