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

"""Compatibility facade for the aggregate expected-target contract.

New internal code imports :mod:`abicheck.workflows.aggregate.resolve`.
"""

from __future__ import annotations

from .workflows.aggregate.resolve import (
    AGGREGATE_MANIFEST_VERSION,
    AggregateError,
    ExpectedTargets,
    OnMissingRequired,
    OnUnexpectedTarget,
    resolve_gate_policy,
)

__all__ = [
    "AGGREGATE_MANIFEST_VERSION",
    "AggregateError",
    "ExpectedTargets",
    "OnMissingRequired",
    "OnUnexpectedTarget",
    "resolve_gate_policy",
]
