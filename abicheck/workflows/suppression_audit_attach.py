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

"""Attach one comparison's suppression audit trail to its ``DiffResult``.

``DiffResult.suppression_audit`` (report schema 2.24) used to be computed
only by the native single-pair ``compare`` CLI, after its own post-
classification scoping. A typed-API (``run_compare_request``) report and a
release member's report rendered the *same* suppression file with no audit
block at all, so stale and high-risk rules were invisible on those routes
(F2 route-parity harness) -- the ADR-067 record-before-disposing rule the
field exists for held on one front end only. The computation now lives here
and every pairwise Tier-2 path calls it.
"""

from __future__ import annotations

from typing import Any

from ..policy.evaluate import effective_kind_sets

__all__ = ["attach_suppression_audit"]


def attach_suppression_audit(result: Any, suppression: Any) -> None:
    """Compute and attach *result*'s suppression audit, or do nothing.

    A no-op when *suppression* is ``None`` (no suppression file in play).
    Otherwise audits the full pre-suppression change set -- kept plus
    suppressed -- plus any ``--used-by``/``--required-symbol``
    ``scoped_only_changes``, so a caller that scopes *after* classification
    (the native CLI) calls this once scoping has run and a rule matching
    only a scoping-synthesized finding is not misreported as stale. Known
    limit: ``scope_diff_to_app``/``scope_diff_to_required_symbols`` apply
    suppression internally to their own candidates, so a rule matching only
    a candidate they already dropped is still invisible here.

    Audited against the *effective*, policy-override-applied breaking set
    (not the static ``BREAKING_KINDS``), with the policy file passed through
    so a selector-scoped ``reclassify:`` rule is classified by its own
    rule's resolution.
    """
    if suppression is None:
        return
    effective_breaking_kinds, _, _, _ = effective_kind_sets(result)
    result.suppression_audit = suppression.audit(
        list(result.changes)
        + list(result.suppressed_changes)
        + list(getattr(result, "scoped_only_changes", ()) or ()),
        breaking_kinds=effective_breaking_kinds,
        policy_file=getattr(result, "policy_file", None),
    )
