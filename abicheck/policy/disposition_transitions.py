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
"""The disposition rewrites the ledger applies after a record is made.

ADR-067 D2: a record's disposition is decided once and later passes may only
move it along fixed edges. Each edge was written inline at its pass (and
restated by the next pass that needed it), so the allowed moves lived only in
comments. Here each is one named, pure function of a record, and
``tests/test_disposition_transitions.py`` checks every one against a table
written in the test over every disposition and flag combination.

The ledger's own pass order (promote, then narrow by scope, once) is checked
by ``DispositionLedger`` itself; this module only says what one pass does to
one record.
"""

from __future__ import annotations

from dataclasses import replace

from .disposition_types import Disposition, DispositionRecord

#: Dispositions a gate scored: the only ones a gate decision can move.
EVALUATED = frozenset({Disposition.GATING, Disposition.NON_GATING})
#: Contract dispositions an explicit consumer scope may promote back into
#: the evaluated set (ADR-049 §4.3) -- the one widening move.
PROMOTABLE = frozenset({Disposition.OUT_OF_CONTRACT, Disposition.UNRESOLVED_RELEVANCE})


def scope_out(record: DispositionRecord) -> DispositionRecord:
    """A record the consumer-scoped gate does not score.

    An evaluated record becomes ``non_gating`` and gate-excluded, a
    ``deduplicated`` one is only marked (it keeps saying why it is not a
    finding of its own), and a policy-terminal one is untouched: scoping is
    not a second chance at a disposition (D2).
    """
    if record.disposition in EVALUATED:
        return replace(record, disposition=Disposition.NON_GATING, gate_excluded=True)
    if record.disposition is Disposition.DEDUPLICATED:
        return replace(record, gate_excluded=True)
    return record


def exclude_consumer_only(record: DispositionRecord) -> DispositionRecord:
    """A consumer-only record (D-S1) when the full-library gate decides.

    A ``gating`` record becomes ``non_gating`` and gate-excluded; a
    ``suppressed`` one stays suppressed but is marked, so release advice does
    not count a finding that gate never scored.
    """
    if record.disposition is Disposition.GATING:
        return replace(record, disposition=Disposition.NON_GATING, gate_excluded=True)
    if record.disposition is Disposition.SUPPRESSED:
        return replace(record, gate_excluded=True)
    return record
