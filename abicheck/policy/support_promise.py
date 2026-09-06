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

"""ADR-065 D1/D6 (S3): **support-promise changes**, under a contract policy.

D1 keeps five things apart, and this module owns the fifth. Four of them --
the analysis boundary, the selection, the expected inventory, and the
per-member acquisition state -- say what a run *was asked to do* and *what
happened*; a support-promise change says what the **project promises to
ship** changed. It is a contract change with evidence, and D1 is explicit
that it is "emitted as a finding under a configurable policy, never inferred
from acquisition state alone".

So this module is deliberately two things and not one:

* the **policy field** (:data:`SUPPORT_PROMISE_POLICIES`,
  :func:`validate_support_promise_policy`) -- ``off`` by default, exactly
  like ``--on-incomplete-scope``'s own ``warn`` default: every pre-existing
  invocation emits nothing new. ``declared`` opts in;
* the **derivation** (:func:`support_promise_changes`), which reads *only*
  :attr:`~abicheck.model.scope_acquisition.ScopeAcquisitionRecord.
  proven_removed_members` / ``proven_added_members``. Those are already
  empty unless the side lacking the member has a ``PROVEN`` inventory (D2),
  so an unmatched member under an unproven inventory can never reach a
  finding here however the policy is set -- the policy decides whether a
  *proven* contract change is reported, never whether an unproven one is
  invented.

The finding is not a duplicate of ``BUNDLE_LIBRARY_REMOVED``:
``bundle_detectors`` emits that one only when a surviving sibling in the
same release imports the missing library. A retired promise is about the
release's published component set, so it holds for a component with no
intra-bundle consumer at all -- which is the case ``--fail-on-removed-
library`` handled with an exit code and no finding.

Each change carries its own completeness receipt in ``old_value``/
``new_value`` (ADR-065 D2's 2026-09 clarification: the receipt names the
side the claim is about and the provenance that established it), so a
reviewer can check *what* proved the absence without re-deriving it.
"""

from __future__ import annotations

from ..checker_types import Change
from ..model.change_catalog.kinds import ChangeKind
from ..model.scope_acquisition import ScopeAcquisitionRecord

__all__ = [
    "DEFAULT_SUPPORT_PROMISE_POLICY",
    "SUPPORT_PROMISE_POLICIES",
    "support_promise_changes",
    "validate_support_promise_policy",
]

#: The contract-policy field's two values. ``off`` (the default) emits
#: nothing; ``declared`` reports a proven inventory change as a finding.
SUPPORT_PROMISE_POLICIES: tuple[str, ...] = ("off", "declared")
DEFAULT_SUPPORT_PROMISE_POLICY = "off"


def validate_support_promise_policy(value: str | None) -> str:
    """The effective policy for *value* (``None`` means the default), or
    ``ValueError`` for anything outside :data:`SUPPORT_PROMISE_POLICIES`."""
    if value is None:
        return DEFAULT_SUPPORT_PROMISE_POLICY
    if value not in SUPPORT_PROMISE_POLICIES:
        raise ValueError(
            "support-promise policy must be one of "
            f"{', '.join(SUPPORT_PROMISE_POLICIES)}, got {value!r}"
        )
    return value


def support_promise_changes(
    record: ScopeAcquisitionRecord | None, policy: str | None
) -> list[Change]:
    """The support-promise findings for *record* under *policy*.

    Empty under ``off`` (the default), empty for a run with no acquisition
    record (a scalar comparison has no component set to promise), and empty
    whenever no member's absence is proven -- which is the ordinary case,
    since a live directory operand and a direct file pair never prove one.
    Order follows the record's own sorted member order, so the finding list
    cannot depend on directory listing order.
    """
    effective = validate_support_promise_policy(policy)
    if record is None or effective == "off":
        return []
    changes: list[Change] = []
    for member in record.proven_removed_members:
        changes.append(
            Change(
                kind=ChangeKind.SUPPORT_PROMISE_COMPONENT_RETIRED,
                symbol=member.name,
                description=(
                    f"{member.name} was shipped by the old release and is absent "
                    "from the new release's proven-complete component inventory: "
                    "the project no longer promises this component"
                ),
                old_value="shipped",
                new_value=(
                    "absent; NEW inventory proven complete "
                    f"({record.new_inventory.provenance})"
                ),
            )
        )
    for member in record.proven_added_members:
        changes.append(
            Change(
                kind=ChangeKind.SUPPORT_PROMISE_COMPONENT_INTRODUCED,
                symbol=member.name,
                description=(
                    f"{member.name} is shipped by the new release and is absent "
                    "from the old release's proven-complete component inventory: "
                    "a newly promised component"
                ),
                old_value=(
                    "absent; OLD inventory proven complete "
                    f"({record.old_inventory.provenance})"
                ),
                new_value="shipped",
            )
        )
    return changes
