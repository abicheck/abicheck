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

"""Compatibility facade over :mod:`abicheck.policy.reclassify` (ADR-061 gap B).

The real implementation — selector-scoped reclassification, the third
policy-file primitive, plus the per-change effective-verdict resolver —
moved to that `policy`-owned module. This flat module now only re-exports
its public surface, unchanged.

**Why this stays a flat, unclassified root module rather than a
`policy`-package `legacy_paths` entry.** ``abicheck/checker_types.py`` (the
`model`-owned, legacy ``DiffResult``) imports this facade directly —
`model` cannot statically depend on `policy`. See ``checker_policy.py``'s
own module docstring for the full reasoning, which applies identically
here. Every physically-migrated internal caller (``policy/*``) imports
:mod:`abicheck.policy.reclassify` directly instead.
"""

from __future__ import annotations

from .policy.reclassify import (
    RECLASSIFY_KNOWN_KEYS as RECLASSIFY_KNOWN_KEYS,
    KindSets as KindSets,
    ReclassifyRule as ReclassifyRule,
    active_reclassify_rules as active_reclassify_rules,
    effective_verdict_for_change as effective_verdict_for_change,
    first_matching_reclassify_verdict as first_matching_reclassify_verdict,
    reclassify_rule_for_change as reclassify_rule_for_change,
    resolve_kind_sets as resolve_kind_sets,
)

__all__ = [
    "RECLASSIFY_KNOWN_KEYS",
    "KindSets",
    "ReclassifyRule",
    "active_reclassify_rules",
    "effective_verdict_for_change",
    "first_matching_reclassify_verdict",
    "reclassify_rule_for_change",
    "resolve_kind_sets",
]
