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

"""ADR-067 D3: the rule a disposition rests on, as the audit reports it.

Split out of the sibling :mod:`abicheck.policy.disposition_ledger` when that
module passed the architecture gate's 800-line production ceiling. The seam is
a real one rather than a line count: this is the *projection of a suppression
rule* the report reads, and it knows nothing about the ledger, the records, or
the gate -- which is also why the ledger imports it and never the reverse.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .rule_identity import rule_identity as rule_identity_of

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..suppression import Suppression


@dataclass(frozen=True, slots=True)
class RuleProvenance:
    """ADR-067 D3's "rule id, source file, reason, expiry" for one match.

    Built from a :class:`~abicheck.suppression.Suppression`'s already-existing
    fields — this adds no field to the suppression grammar. ``intent`` is
    always ``"unspecified"`` today: ADR-067 D5's explicit ``intent:`` key is
    S3 work, and its own migration default for every rule that predates it is
    exactly this value, so recording it now costs nothing and keeps the
    consumer (``semver.recommend_release``'s "suppressed (intent:
    unspecified), not compatible" wording) honest rather than silent.
    """

    rule_id: str | None = None
    source_file: str | None = None
    reason: str | None = None
    label: str | None = None
    expires: str | None = None
    intent: str = "unspecified"
    allow_public_break: bool = False

    def to_dict(self) -> dict[str, object]:
        """JSON-safe mapping; ``None`` fields are emitted so the ledger's rows
        keep one stable shape for machine consumers."""
        return {
            "rule_id": self.rule_id,
            "source_file": self.source_file,
            "reason": self.reason,
            "label": self.label,
            "expires": self.expires,
            "intent": self.intent,
            "allow_public_break": self.allow_public_break,
        }


def rule_provenance(
    rule: Suppression | None, *, source_file: str | None = None
) -> RuleProvenance | None:
    """Project one suppression *rule* onto :class:`RuleProvenance`.

    Duck-typed (``getattr``) rather than importing ``Suppression``, keeping
    this module a leaf; ``None`` in, ``None`` out, which is the honest answer
    for a change whose matching rule was not recorded (a ``DiffResult``
    reconstructed from JSON, for instance).
    """
    if rule is None:
        return None
    expires = getattr(rule, "expires", None)
    label = getattr(rule, "label", None)
    reason = getattr(rule, "reason", None)
    rule_id = _rule_identity(rule) or label or reason
    return RuleProvenance(
        rule_id=rule_id,
        source_file=source_file,
        reason=reason,
        label=label,
        expires=expires.isoformat() if expires is not None else None,
        allow_public_break=bool(getattr(rule, "allow_public_break", False)),
    )


def _rule_identity(rule: object) -> str | None:
    """The rule's canonical selector-and-gate identity, when derivable.

    Deliberately not the free-form ``label``/``reason`` prose: two rules
    sharing a label must still be distinguishable in the audit. Delegates to
    :func:`abicheck.policy.rule_identity.rule_identity`, the *same* derivation
    ``SuppressionList.rule_identities`` uses -- this used to be a second,
    hand-picked field list here, which omitted the gate fields and so merged a
    public-only waiver and a proven-unreachable-only waiver sharing one
    ``symbol_pattern`` into a single audit row (Codex review).
    """
    return rule_identity_of(rule)
