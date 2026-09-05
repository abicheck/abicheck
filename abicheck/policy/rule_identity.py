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

"""The one canonical identity of a single suppression rule.

Two consumers need to say "*which* rule was this" and must not disagree:

* :meth:`abicheck.suppression.SuppressionList.rule_identities` — ADR-049 D7's
  effective-configuration receipt, one identity per loaded rule;
* :func:`abicheck.policy.disposition_ledger.rule_provenance` — ADR-067 D3's
  audit, which groups the ledger's records by the rule that disposed of them.

They used to derive it separately, and the audit's copy was a hand-picked
list of selector fields. That list omitted the *gate* fields
(``reachability``, ``allow_unknown_reachability``, ``allow_public_break``,
…), so two rules with the same ``symbol_pattern`` and prose but different
gates — a public-only waiver and a proven-unreachable-only waiver — rendered
one identity and were merged into a single audit row, even though they
suppress different findings. That is the repository's "no second selector
grammar" rule failing in the small: a second hand-maintained field list is a
second grammar whether or not it is spelled as one.

So the derivation lives here, once, and is generic over the rule's own
dataclass fields — a field added to :class:`~abicheck.suppression.Suppression`
later is covered by both consumers without touching either.

A leaf inside ``policy`` rather than in ``suppression.py``: ``suppression``
already imports ``policy.selectors``, so the ledger cannot import
``suppression`` back without a cycle, and the derivation itself needs nothing
from either module.
"""

from __future__ import annotations

import dataclasses

#: Excluded from every identity: prose that changes what a *reviewer* reads,
#: never what the rule *matches*. The receipt's source digest already records
#: that it changed.
_PROSE_FIELDS = frozenset({"reason"})


def rule_identity(rule: object) -> str | None:
    """*rule*'s canonical, machine-facing identity, or ``None``.

    Every populated ``init=True`` field except :data:`_PROSE_FIELDS` is
    included, so two rules that differ in any matching-relevant way — a
    selector *or* a gate — get different identities. Fields are emitted in
    declaration order with no positional index, so the identity depends only
    on the rule's own content, and each value is rendered with ``repr()`` so
    that a selector containing the ``|`` separator or an ``=`` (routine in a
    regex selector, ``symbol_pattern: "a|change_kind=x"``) cannot collide
    with a different rule carrying those as separate fields.

    ``None`` for anything that is not a dataclass instance — the audit accepts
    duck-typed rule stand-ins, and falls back to the rule's label or reason
    there rather than raising inside a report projection.
    """
    if not dataclasses.is_dataclass(rule) or isinstance(rule, type):
        return None
    parts = [
        f"{f.name}={getattr(rule, f.name)!r}"
        for f in dataclasses.fields(rule)
        if f.init
        and f.name not in _PROSE_FIELDS
        and getattr(rule, f.name) not in (None, False)
    ]
    return "|".join(parts) if parts else None
