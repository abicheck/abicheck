# Copyright 2026 Nikolay Petrov
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

"""Folding several comparisons' disposition ledgers into one.

ADR-067's rule is that a disposition keeps its rule and reason, and that
nothing silently vanishes. A multi-library ``compat check`` ran policy once
per DSO and then *dropped* every ledger, so the merged report rebuilt a
ledger from the merged buckets -- recovering totals but not the rules,
reasons, reclassifications or acknowledgment matches. A release whose policy
demonstrably acted reported an empty policy trail (Codex review).

The ledger's own :meth:`~DispositionLedger.extend_records` exists for this
and nothing else. :meth:`~DispositionLedger.record` cannot serve: it is keyed
on the live ``Change`` object a disposition was applied to, and a merge has
the records rather than the objects behind them. The ledger this builds is a
finished artifact for reporting -- it carries none of the identity-keying
state, so a further comparison must never record into it.

Concatenation is the right fold here, and that is not true of most fields in
that merge: a ledger is a *list of observations*, not a per-library scalar
like ``old_metadata`` whose release-level meaning would have to be invented.
Each record simply gains the library it came from.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

from .disposition_ledger import DispositionLedger

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .disposition_types import DispositionRecord


def merge_disposition_ledgers(
    ledgers: Sequence[tuple[str, object | None]],
) -> DispositionLedger | None:
    """One ledger over every ``(library, ledger)`` pair, or ``None``.

    ``None`` when no member carried a ledger at all -- an empty ledger and
    "policy never ran" are different claims, and inventing the first would
    report a release as having a clean, complete policy trail it never had.

    A member's own ``None`` is skipped rather than treated as an empty
    ledger, for the same reason.

    Records are appended in the caller's library order and each is stamped
    with its library. Deliberately *not* deduplicated across libraries: two
    DSOs suppressing the same symbol under the same rule are two real
    dispositions, and collapsing them would under-report exactly the "100
    removals detected, 100 suppressed by rule X" total ADR-067 exists to
    keep visible.
    """
    records: list[DispositionRecord] = []
    saw_one = False
    for library, ledger in ledgers:
        if ledger is None:
            continue
        member = getattr(ledger, "records", None)
        if member is None:
            continue
        saw_one = True
        for record in member() if callable(member) else member:
            records.append(dataclasses.replace(record, library=library))
    if not saw_one:
        return None
    merged = DispositionLedger()
    merged.extend_records(records)
    return merged
