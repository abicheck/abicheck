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

"""Which of an :class:`AbiSnapshot`'s raw-unreliable ``*_facts_reliable``
flags (``model.snapshot_reliability.raw_unreliable_facts``) are actually
consulted by their one real detector, given *this* snapshot's own
``ast_producer``/``from_headers``/``from_headers_inferred`` shape.

**Moved here from ``model/`` (Codex review, PR #1209 round 7).** Deciding
whether a flag is "actually consulted by [its] detector" requires tracking
outer, detector-layer behavior (``diff_symbols.py`` and siblings) -- exactly
the "does it matter" question ``model/AGENTS.md``'s Purpose section says the
innermost model layer must never answer, and it forced that package to track
this package's own gating logic, which is backwards (D1's imports point
inward: ``policy -> model``, never the reverse). ``model.snapshot_reliability``
now keeps only the raw flag-shape accessor
(:func:`~abicheck.model.snapshot_reliability.raw_unreliable_facts`); this
module is the single-snapshot half of the consultation table, and
``policy.analysis_assurance_schema_staleness``'s pair-aware narrowing builds
on top of *this* function for the cross-side gates its own docstring covers,
rather than re-deriving which flags are single-snapshot-consulted a third
time.

Both call sites -- ``serialization.decode_snapshot``'s load-time
``UserWarning`` and ``analysis_assurance_schema_staleness.schema_staleness_
status`` -- import THIS function rather than keeping their own copy of the
table, so the two can never independently drift on what counts as
"degraded" (see ``analysis_assurance.py``'s module docstring and
``AGENTS.md``'s "Record before disposing" / weaker-evidence-narrows-
conclusions principles).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..model.snapshot_reliability import raw_unreliable_facts

if TYPE_CHECKING:
    from ..model import AbiSnapshot

__all__ = ["degraded_reliability_facts"]


def degraded_reliability_facts(snap: AbiSnapshot) -> list[str]:
    """The sorted names of *snap*'s ``*_facts_reliable`` flags that are both
    False and actually consulted by their one real detector, given *snap*'s
    own ``ast_producer``/``from_headers``/``from_headers_inferred``.

    Per-flag rationale (mirrors the detectors' own gates -- see each named
    module for the full account):

    - ``header_cv_facts_reliable``/``clang_vtable_facts_reliable``/
      ``param_kind_facts_reliable`` -- consulted unconditionally: their real
      consumers (``diff_symbols.py``/``diff_types.py``/``diff_layout.py``/
      ``diff_vtable_layout.py``) apply regardless of header confirmation
      (DWARF- and ELF-only snapshots read these facts too).
    - ``clang_deprecation_facts_reliable``/``clang_field_initializer_facts_
      reliable``/``clang_restrict_facts_reliable`` -- consulted only when
      *this* snapshot is confirmed (non-inferred) header-aware: their real
      consumers (``diff_symbols._diff_func_deprecated`` via ``fact_
      provenance.fact_producer``, ``diff_types_field_facts._diff_field_
      default_initializer``, ``diff_symbols._diff_param_restrict``) all
      gate on header confirmation before ever reading the flag.
    - ``clang_va_list_facts_reliable``/``castxml_var_access_facts_reliable``
      -- consulted only when this snapshot is confirmed header-aware AND its
      own ``ast_producer`` is the exact matching backend: their one real
      consumer (``diff_symbols._diff_param_va_list``/``_diff_var_access``)
      is gated per-declaration on that snapshot's own recorded producer.

    This is deliberately the SINGLE-snapshot answer -- it cannot see the
    other side of a comparison, so a flag whose real consumer ALSO requires
    the *other* side to independently satisfy a header/producer gate (the
    five flags above, other than the two unconditional ones) can still be
    reported here even when the pair as a whole would never actually reach
    the affected detector. ``policy.analysis_assurance_schema_staleness``'s
    pair-aware narrowing is what corrects for that at the comparison level;
    this function's job is only "does *snap* alone look degraded", not "does
    this PAIR'S comparison actually read the degraded fact".
    """
    header_confirmed = snap.from_headers and not snap.from_headers_inferred
    consulted_when: dict[str, bool] = {
        "header_cv_facts_reliable": True,
        "clang_deprecation_facts_reliable": header_confirmed,
        "clang_field_initializer_facts_reliable": header_confirmed,
        "clang_vtable_facts_reliable": True,
        "clang_restrict_facts_reliable": header_confirmed,
        "clang_va_list_facts_reliable": (
            header_confirmed and snap.ast_producer == "clang"
        ),
        "castxml_var_access_facts_reliable": (
            header_confirmed and snap.ast_producer == "castxml"
        ),
        "param_kind_facts_reliable": True,
    }
    return sorted(name for name in raw_unreliable_facts(snap) if consulted_when[name])
