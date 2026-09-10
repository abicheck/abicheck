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

"""Compatibility facade over :mod:`abicheck.policy.coverage_ledger`
(ADR-061 gap B).

The real implementation — ADR-049 Phase 5's unsuppressible contract-
coverage ledger — moved to that `policy`-owned module. This flat module
now only re-exports its public surface, unchanged, for existing external
and (legacy, unmigrated) internal callers. Canonical internal callers
(``policy/*``, ``report/*``) import :mod:`abicheck.policy.coverage_ledger`
directly.
"""

from __future__ import annotations

from .policy.coverage_ledger import (
    REQUIRED_PROVIDERS as REQUIRED_PROVIDERS,
    CoverageFailure as CoverageFailure,
    coverage_exit_contribution as coverage_exit_contribution,
    coverage_failures as coverage_failures,
    coverage_failures_for_context as coverage_failures_for_context,
    suppression_reaches_coverage_failures as suppression_reaches_coverage_failures,
)

__all__ = [
    "REQUIRED_PROVIDERS",
    "CoverageFailure",
    "coverage_exit_contribution",
    "coverage_failures",
    "coverage_failures_for_context",
    "suppression_reaches_coverage_failures",
]
