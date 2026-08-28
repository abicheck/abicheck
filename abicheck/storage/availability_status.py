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

"""Re-export shim — this vocabulary moved to :mod:`abicheck.model.availability`.

ADR-063 Phase 0: `Fact[T]` (`abicheck/model/fact.py`) needs this same
vocabulary, and ADR-061's dependency direction is `storage -> model`, never
the reverse — so the vocabulary itself had to move to `model/` for `Fact[T]`
to be constructible without an import cycle. This module survives as a
re-export shim for one release so existing `abicheck.storage.
availability_status.*` imports keep working; import from
`abicheck.model.availability` in new code. `FactAvailability` (the ledger
record, which legitimately depends on `model`) stays in
`abicheck.storage.availability` unchanged.
"""

from __future__ import annotations

from ..model.availability import (
    ASSERTS_NO_PRODUCER,
    COMPARABLE_STATUSES,
    CONFIDENCE_ORDER,
    GAP_STATUSES,
    STATUS_ORDER,
    Confidence,
    FactStatus,
    worse_confidence,
    worse_status,
)

__all__ = [
    "ASSERTS_NO_PRODUCER",
    "COMPARABLE_STATUSES",
    "CONFIDENCE_ORDER",
    "Confidence",
    "GAP_STATUSES",
    "STATUS_ORDER",
    "FactStatus",
    "worse_confidence",
    "worse_status",
]
