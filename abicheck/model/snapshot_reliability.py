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

"""The fixed list of :class:`AbiSnapshot`'s ``*_facts_reliable`` flag names,
plus the raw accessor for which of them are currently ``False`` on a given
snapshot.

**Scope, deliberately narrow (Codex review, PR #1209 round 7):** this module
answers only "what is this fact" (ADR-061 D1, ``model/AGENTS.md``'s Purpose
section) -- which flags exist on ``AbiSnapshot`` and their current boolean
value. It does NOT decide whether a flag is "actually consulted" by any
detector for a snapshot's own ``ast_producer``/header-awareness shape --
that is an algorithm over outer, detector-layer behavior (``diff_symbols.py``
and siblings), which is exactly the "does it matter" question this
package's own Purpose section says the model layer must never answer. An
earlier revision of this module *did* encode that consultation table
directly here; moved to ``policy.analysis_assurance_degraded_facts`` instead,
which builds on :func:`raw_unreliable_facts` for the single-snapshot half of
the same computation ``policy.analysis_assurance_schema_staleness`` narrows
further, pair-aware, for its own cross-side gates.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .snapshot import AbiSnapshot

__all__ = ["RELIABILITY_FLAG_NAMES", "raw_unreliable_facts"]

#: Every ``AbiSnapshot`` field of the ``*_facts_reliable`` shape. Declarative
#: data (a fixed field-name list), not an algorithm -- the same D1-sanctioned
#: kind of "what is this fact" fact ``change_catalog/`` and ``fact_registry.py``
#: hold for their own closed vocabularies (``model/AGENTS.md``).
RELIABILITY_FLAG_NAMES: tuple[str, ...] = (
    "header_cv_facts_reliable",
    "clang_deprecation_facts_reliable",
    "clang_field_initializer_facts_reliable",
    "clang_vtable_facts_reliable",
    "clang_restrict_facts_reliable",
    "clang_va_list_facts_reliable",
    "castxml_var_access_facts_reliable",
    "param_kind_facts_reliable",
)


def raw_unreliable_facts(snap: AbiSnapshot) -> list[str]:
    """The sorted names of *snap*'s :data:`RELIABILITY_FLAG_NAMES` fields that
    are currently ``False`` -- with no judgment about whether any detector
    actually reads that flag for *this* snapshot's producer/header-awareness
    shape. That narrowing is ``policy.analysis_assurance_degraded_facts.
    degraded_reliability_facts``'s job, not this module's.
    """
    return sorted(name for name in RELIABILITY_FLAG_NAMES if not getattr(snap, name))
