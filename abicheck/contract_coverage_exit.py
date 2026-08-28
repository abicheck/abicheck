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

"""Back-compat shim: this module's real implementation moved to
:mod:`abicheck.policy.contract_coverage_exit` (ADR-061 physical migration
of the ``policy`` responsibility package's `legacy_paths` entries).

Every name this module used to define is re-exported here by value -- see
``abicheck/severity.py``'s own shim docstring for why a plain static
import, not a lazy ``__getattr__``, is the right shape here. New code
should import from ``abicheck.policy.contract_coverage_exit`` directly.
"""

from __future__ import annotations

from .policy.contract_coverage_exit import (
    ACCEPT_UNRESOLVED as ACCEPT_UNRESOLVED,
    CLI_MITIGATION as CLI_MITIGATION,
    announce_coverage_floor as announce_coverage_floor,
    coverage_diagnostic_from_summary as coverage_diagnostic_from_summary,
    coverage_exit_contribution as coverage_exit_contribution,
    coverage_exit_floor as coverage_exit_floor,
    coverage_exit_for_context as coverage_exit_for_context,
    coverage_failure_diagnostic as coverage_failure_diagnostic,
    coverage_failures_for_context as coverage_failures_for_context,
    fold_coverage_exit as fold_coverage_exit,
    report_carries_the_ledger as report_carries_the_ledger,
)

__all__ = [
    "ACCEPT_UNRESOLVED",
    "CLI_MITIGATION",
    "announce_coverage_floor",
    "coverage_diagnostic_from_summary",
    "coverage_exit_contribution",
    "coverage_exit_floor",
    "coverage_exit_for_context",
    "coverage_failure_diagnostic",
    "coverage_failures_for_context",
    "fold_coverage_exit",
    "report_carries_the_ledger",
]
