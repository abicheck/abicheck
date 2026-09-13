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

"""Persistent hygiene debt does not decide this release's verdict.

A finding stamped ``PERSISTENT`` is present on *both* sides: whatever it
describes, this release did not do it. Letting such a finding set the
headline verdict answers a question nobody asked -- "is this codebase
tidy?" -- with the answer to the one they did ask: "did this release break
anything?" Intel MKL's 2026.0.0 -> 2026.1.0 pair carries 39,955 persistent
``exported_not_public`` findings, every one of them downgrading a release
whose ground truth is +1 symbol / -0.

Two deliberate limits, and neither is negotiable for the exclusion to be
safe:

* Only findings whose *resolved* category is ``COMPATIBLE_WITH_RISK``. A
  persistent ``odr_type_variant`` or ``header_build_context_mismatch``
  names a real defect present in the candidate artifact and still gates.
* Never for a kind the project's own policy document speaks about
  (:func:`policy_governs`). Excluding a finding a maintainer explicitly
  classified would silently overrule them, which is the opposite of the
  hygiene problem being solved.

The findings themselves are untouched: still detected, still reported,
still counted, still tagged ``PERSISTENT`` (``AGENTS.md``'s "record before
disposing"). Only the verdict stops reading them.

Split out of ``checker.py``, which carries a ``no_growth`` baseline in
``architecture/debt.yaml``; the classification rule is ``policy``'s job in
any case (ADR-061's routing table).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .classification import (
    excluded_from_verdict_as_persistent_hygiene,
    policy_kind_sets,
)

if TYPE_CHECKING:
    from ..checker_types import Change
    from ..policy_file import PolicyFile


def drop_persistent_hygiene(
    changes: list[Change],
    policy: str,
    policy_file: PolicyFile | None,
) -> list[Change]:
    """*changes* minus the pre-existing cross-source hygiene debt that must
    not drive this release's verdict
    (:func:`~abicheck.policy.classification.
    excluded_from_verdict_as_persistent_hygiene`).

    Applied here, at the one verdict chokepoint, rather than in
    :func:`_verdict_scored_population` alongside the ``RESOLVED`` exclusion:
    deciding this needs the finding's *resolved* category, which is a
    function of the active policy, and only this function has it. The
    category is resolved against the same kind sets the verdict computation
    itself is about to use -- ``policy_file.base_policy`` when a policy
    document is in effect, so a document that changed the base profile does
    not get judged against ``strict_abi``'s partitions.

    **A finding the policy document states a rule about is never excluded**,
    whether through ``overrides:`` or ``reclassify:``. Those rules are applied
    by ``PolicyFile.compute_verdict``, not by the base kind sets, so the
    category this function resolves does not see them: a project that
    deliberately set ``exported_not_public: breaking`` would have had every
    persistent instance silently dropped from its own verdict -- the exact
    opposite of what it asked for. Verified by executing that case, not by
    reasoning about it; an earlier revision of this docstring asserted it
    "fails in the safe direction" and was simply wrong
    (``TestPersistentHygieneRespectsAnExplicitPolicy``).

    The exclusion is deliberately withheld for *any* mention of the kind,
    including one that lowers its severity, rather than only for a
    promotion. A project that has written the kind into its policy at all is
    managing that kind itself, and quietly removing its findings from the
    verdict is not this function's call to make.
    """
    base = policy_file.base_policy if policy_file is not None else policy
    sets = policy_kind_sets(base)
    return [
        c
        for c in changes
        if policy_governs(c, policy_file)
        or not excluded_from_verdict_as_persistent_hygiene(c, *sets)
    ]


def policy_governs(change: Change, policy_file: PolicyFile | None) -> bool:
    """Whether *policy_file* states a rule that applies to *change*.

    Both rule namespaces, because both change a finding's resolved verdict
    and neither is visible in the base profile's kind sets:
    ``overrides:`` is kind-global, so a kind lookup answers it; ``reclassify:``
    is selector-scoped, so the rule is *asked whether it matches this
    finding* rather than mined for the kinds it mentions.

    Asking the rule is not a refinement, it is the only correct form. An
    earlier version collected ``rule.kinds``/``rule.kind`` -- neither of
    which exists; the field is ``change_kind`` -- so every reclassify rule
    was silently ignored, and a rule like ``{kind: exported_not_public,
    symbol: x, to: break}`` had its finding dropped before
    ``PolicyFile.compute_verdict`` ever saw it, turning a deliberate
    ``break`` into ``NO_CHANGE`` (Codex review). Mining kind names would
    also have missed a rule with no kind selector at all -- a symbol- or
    namespace-scoped rule that legitimately applies to a hygiene finding.
    """
    if policy_file is None:
        return False
    if change.kind in (getattr(policy_file, "overrides", {}) or {}):
        return True
    return any(
        rule.matches(change) for rule in (getattr(policy_file, "reclassify", ()) or ())
    )
