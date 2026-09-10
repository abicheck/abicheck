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

"""Compatibility facade over :mod:`abicheck.policy.contract_finding_relevance`
(ADR-061 gap B).

The real implementation — which findings compatibility policy and the
change gate see (ADR-049 D1) — moved to that `policy`-owned module. This
flat module now only re-exports its public surface, unchanged.

Stays a flat, unclassified root module rather than a `policy`-package
`legacy_paths` entry for the same reason ``checker_policy.py`` does: the
`model`-owned, legacy ``checker_types.DiffResult`` imports this facade
directly, and `model` cannot statically depend on `policy`. See
``checker_policy.py``'s own module docstring for the full reasoning, which
applies identically here. Every physically-migrated internal caller
(``policy/*``, ``report/*``) imports
:mod:`abicheck.policy.contract_finding_relevance` directly instead.
"""

from __future__ import annotations

from .policy.contract_finding_relevance import (
    contract_relevance_of as contract_relevance_of,
    evaluation_status_of as evaluation_status_of,
    is_evaluated as is_evaluated,
)

__all__ = [
    "contract_relevance_of",
    "evaluation_status_of",
    "is_evaluated",
]
