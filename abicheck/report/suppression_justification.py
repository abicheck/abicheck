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

"""One statement of *why* a finding was suppressed, for every renderer.

The SARIF ``suppressions[].justification`` of both the two-sided ``compare``
(``sarif.py``) and the ``--no-baseline`` audit (``no_baseline_render.py``),
and the audit's JUnit ``<skipped message>``, all come from
:func:`suppression_justification`. Its input is the run's own ADR-067 D3
record -- ``DispositionLedger.rule_for(change)``, the same record the JSON
report publishes as ``suppressed_changes[].rule`` -- never a re-evaluation of
the rule set and never ``Change.suppression_rule`` alone, which is
``SuppressionOutcome.rule_label()``'s ``label or reason`` collapse and so
drops whichever the rule stated second, along with its file and expiry.

The text names the rule (its label, else its ``id``), the reason, and where
the rule came from: the suppression document it was read from, an inline
rule (a typed ``SuppressionList`` with no file), or a built-in abicheck
policy decision (a release-level rule such as the lockstep SONAME
suppression, which no user document stated). Only a run with no record
falls back to the collapsed display label.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .no_baseline_document import suppression_rule_label

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["sarif_suppression", "suppression_justification", "suppression_source"]

#: The statement for a suppression nothing recorded and nothing labelled.
UNATTRIBUTED_SUPPRESSION = "suppressed by an abicheck --suppress rule"


def suppression_source(provenance: Mapping[str, Any]) -> str:
    """Where the rule in *provenance* came from, as a reader would say it.

    A rule read from a suppression document carries its ``source_file``. A
    rule from a ``SuppressionList`` with no file still has its position in
    that list (``entry_index``). A record with neither was not stated by any
    user rule set: it is a policy decision abicheck made itself.
    """
    source_file = provenance.get("source_file")
    if source_file:
        return f"from {source_file}"
    if provenance.get("entry_index") is not None:
        return "inline suppression rule"
    return "abicheck policy"


def suppression_justification(change: Any, provenance: Mapping[str, Any] | None) -> str:
    """``<name>: <reason> (rule <id>; <source>; expires <date>)``.

    *name* is the rule's label, else its stated id; a rule stating only one of
    label and reason shows that one alone, so no field appears twice (Codex
    review, P2: a reason-only rule once rendered as ``"<reason>: <reason>"``).
    The parenthetical carries what the name does not: the id when a label
    was shown, the source, and the expiry.
    """
    label = suppression_rule_label(change, provenance)
    if not provenance:
        # No record: only the display label is knowable. Keep the mechanism
        # in front of it, so the text still says where the suppression came
        # from rather than reading as a bare word.
        return (
            f"{UNATTRIBUTED_SUPPRESSION}: {label}"
            if label
            else UNATTRIBUTED_SUPPRESSION
        )
    rule_id = provenance.get("rule_id")
    # An id-less rule's `rule_id` is a derived digest (`rule_identity` is
    # then set): shown only when nothing a human wrote names the rule.
    stated_id = rule_id if provenance.get("rule_identity") is None else None
    reason = provenance.get("reason")
    name = label or stated_id
    head = f"{name}: {reason}" if name and reason else str(name or reason or "")
    details: list[str] = []
    if rule_id and rule_id != name and (stated_id or not head):
        # The id joins this suppression to the JSON report's rule record; a
        # derived one is shown only when the rule stated nothing else.
        details.append(f"rule {rule_id}")
    details.append(suppression_source(provenance))
    if provenance.get("expires"):
        details.append(f"expires {provenance['expires']}")
    return f"{head or UNATTRIBUTED_SUPPRESSION} ({'; '.join(details)})"


def sarif_suppression(
    change: Any, provenance: Mapping[str, Any] | None
) -> dict[str, Any]:
    """One SARIF 2.1.0 ``suppressions[]`` entry (§3.34) for *change*.

    ``justification`` is the prose; ``properties`` the structured record
    itself, so a consumer deciding whether a waiver still applies reads the
    source file and expiry as data. No record, no ``properties``: a
    fabricated ADR-067 record would be worse than none.
    """
    suppression: dict[str, Any] = {
        "kind": "external",
        "justification": suppression_justification(change, provenance),
    }
    if provenance:
        suppression["properties"] = dict(provenance)
    return suppression
