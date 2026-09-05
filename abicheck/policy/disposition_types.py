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

"""ADR-067 D2's terminal disposition vocabulary.

One enum, on its own, because three modules need it and two of them need each
other: the ledger stores it, the gate module answers it, and the closer
assigns it. A leaf inward of all three is what lets them share it without a
cycle. Re-exported from :mod:`abicheck.policy.disposition_ledger`, which
remains the name every consumer imports.
"""

from __future__ import annotations

from enum import Enum


class Disposition(str, Enum):
    """ADR-067 D2's terminal effective-gate disposition of one change.

    Exactly one of these is recorded per atomically detected change. The
    ``str`` mixin is deliberate: every value is emitted verbatim into the
    JSON report, so the enum member and its wire spelling cannot drift.
    """

    #: Evaluated by policy and contributing to the compatibility gate.
    GATING = "gating"
    #: Evaluated by policy, contributing nothing to the gate.
    NON_GATING = "non_gating"
    #: Removed from the visible set by a ``--suppress`` rule.
    SUPPRESSED = "suppressed"
    #: Proven outside the selected contract/public surface (ADR-024/049).
    OUT_OF_CONTRACT = "out_of_contract"
    #: Relevance could not be resolved from the available evidence.
    UNRESOLVED_RELEVANCE = "unresolved_relevance"
    #: Collapsed into another finding by redundancy/root-cause grouping.
    DEDUPLICATED = "deduplicated"


#: The one disposition that counts towards the *effective* (gating) total.
