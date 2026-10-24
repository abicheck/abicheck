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

* :meth:`abicheck.policy.suppression.rules.SuppressionList.rule_identities` — ADR-049 D7's
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
dataclass fields — a field added to :class:`~abicheck.policy.suppression.rules.Suppression`
later is covered by both consumers without touching either.

A leaf inside ``policy`` rather than in ``policy/suppression/rules.py``: ``suppression``
already imports ``policy.selectors``, so the ledger cannot import
``suppression`` back without a cycle, and the derivation itself needs nothing
from either module.
"""

from __future__ import annotations

import dataclasses
import datetime
import enum
import hashlib
import json
import re

#: Excluded from every identity: prose that changes what a *reviewer* reads,
#: never what the rule *matches*. The receipt's source digest already records
#: that it changed.
_PROSE_FIELDS = frozenset({"reason"})

#: Excluded because a canonical field already states it: a reclassify rule's
#: ``to_verdict`` is the enum form of its ``to`` spelling, which is kept (and
#: reads as text), so including both would only put an enum repr in the id.
_DERIVED_FIELDS = frozenset({"to_verdict"})


def _canonical(value: object) -> object:
    """*value* as a JSON-native value: an enum by its value, a date/datetime
    as ISO 8601, a compiled regex as its raw pattern string, a tuple, list,
    set or frozenset as a list (sets sorted), a mapping with string keys.
    Never ``repr()`` -- the identity must not depend on Python's repr
    spelling of a type, which differs across versions and leaks into ids."""
    if isinstance(value, enum.Enum):
        return _canonical(value.value)
    if isinstance(value, (datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, re.Pattern):
        return value.pattern
    if isinstance(value, (set, frozenset)):
        return sorted((_canonical(v) for v in value), key=_dumps)
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _canonical(v) for k, v in value.items()}
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def _dumps(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def rule_id_of(rule: object) -> str:
    """The audit's ``rule_id`` for *rule* (ADR-067 D3, P2).

    The user's own ``id:`` when the rule states one, else
    ``rule-<12 hex>``: the first 12 hex digits of the SHA-256 of the rule's
    canonical :func:`rule_identity`. That token matches ``[A-Za-z0-9._-]+``,
    so it can be pasted into an acknowledgment file, and is stable across
    Python versions and YAML key order. Never the free-form ``label``/``reason``
    prose, which two distinct rules can share; a label is only unique when
    the whole policy is known, and the two callers (the inventory and the
    per-change provenance) must derive the same id from one rule alone.
    ``label`` and ``reason`` are a last resort only for a duck-typed
    stand-in with neither a dataclass identity nor an ``id``.
    """
    user_id = getattr(rule, "id", None)
    if isinstance(user_id, str) and user_id:
        return user_id
    identity = rule_identity(rule)
    if identity is not None:
        return "rule-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    return str(getattr(rule, "label", None) or getattr(rule, "reason", None) or "rule")


def rule_identity(rule: object) -> str | None:
    """*rule*'s canonical, human-readable identity, or ``None``.

    Every populated ``init=True`` field except :data:`_PROSE_FIELDS` and
    :data:`_DERIVED_FIELDS` is included, so two rules that differ in any
    matching-relevant way — a selector *or* a gate — get different
    identities. The identity is compact canonical JSON (``sort_keys``,
    values via :func:`_canonical`): JSON string quoting means a selector
    containing ``|``, ``=``, ``"`` or ``,`` (routine in a regex,
    ``symbol_pattern: "a|change_kind=x"``) cannot collide with a different
    rule carrying those as separate fields, and no Python ``repr()``
    (``datetime.date(...)``) leaks in. The ``rule_id`` of an id-less rule
    is a digest of this string (:func:`rule_id_of`).

    ``None`` for anything that is not a dataclass instance — the audit accepts
    duck-typed rule stand-ins, and falls back to the rule's label or reason
    there rather than raising inside a report projection.
    """
    if not dataclasses.is_dataclass(rule) or isinstance(rule, type):
        return None
    fields = {
        f.name: _canonical(getattr(rule, f.name))
        for f in dataclasses.fields(rule)
        if f.init
        and f.name not in _PROSE_FIELDS
        and f.name not in _DERIVED_FIELDS
        and getattr(rule, f.name) not in (None, False)
    }
    return _dumps(fields) if fields else None
