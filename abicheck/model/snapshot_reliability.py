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

"""The one place that decides which of an :class:`AbiSnapshot`'s
``*_facts_reliable`` flags are both (a) False and (b) actually consulted by
some detector for this snapshot's own AST producer / header-confirmation
shape.

Split out of ``serialization.decode_snapshot`` (which used to compute this
list purely to build its own load-time ``UserWarning`` text) so that a
*second* consumer -- ``analysis_assurance.compute_analysis_assurance`` --
can answer "is this snapshot carrying stale, tool-upgrade-degraded facts"
without re-deriving the same seven-flag consultation table a third time. By
the time ``serialization.decode_snapshot`` has fully constructed an
``AbiSnapshot``, every one of these flags, plus ``from_headers``/
``from_headers_inferred``/``ast_producer``, already carries its final,
resolved value (explicit dict key if present, else the schema-version +
producer derivation) -- so this function needs nothing but the snapshot
itself, not the raw decode-time locals ``decode_snapshot`` computed them
from.

Both call sites read *this* function's result rather than keeping their own
copy of the table, so the load-time warning and the reported
``analysis_assurance`` status can never independently drift on what counts
as "degraded" (the exact failure mode this module exists to close -- see
``analysis_assurance.py``'s module docstring and ``AGENTS.md``'s "Record
before disposing" / weaker-evidence-narrows-conclusions principles).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .snapshot import AbiSnapshot

__all__ = ["degraded_reliability_facts"]


def degraded_reliability_facts(snap: AbiSnapshot) -> list[str]:
    """The sorted names of *snap*'s ``*_facts_reliable`` flags that are both
    False and actually consulted by their one real detector, given *snap*'s
    own ``ast_producer``/``from_headers``/``from_headers_inferred``.

    Mirrors ``serialization.decode_snapshot``'s own ``_degraded_facts``
    construction exactly (see that call site's long comment for the full
    per-flag rationale, including why ``clang_va_list_facts_reliable``/
    ``castxml_var_access_facts_reliable`` are gated on this side's exact
    producer rather than "hybrid too", and why five of the seven flags also
    require CONFIRMED, non-inferred header awareness before their one real
    consumer ever reads them at all) -- deliberately kept as one function
    both call sites import, rather than two hand-synced copies.
    """
    header_confirmed = snap.from_headers and not snap.from_headers_inferred
    return sorted(
        name
        for name, reliable, consulted in (
            ("header_cv_facts_reliable", snap.header_cv_facts_reliable, True),
            (
                "clang_deprecation_facts_reliable",
                snap.clang_deprecation_facts_reliable,
                header_confirmed,
            ),
            (
                "clang_field_initializer_facts_reliable",
                snap.clang_field_initializer_facts_reliable,
                header_confirmed,
            ),
            (
                "clang_vtable_facts_reliable",
                snap.clang_vtable_facts_reliable,
                True,
            ),
            (
                "clang_restrict_facts_reliable",
                snap.clang_restrict_facts_reliable,
                header_confirmed,
            ),
            (
                "clang_va_list_facts_reliable",
                snap.clang_va_list_facts_reliable,
                header_confirmed and snap.ast_producer == "clang",
            ),
            (
                "castxml_var_access_facts_reliable",
                snap.castxml_var_access_facts_reliable,
                header_confirmed and snap.ast_producer == "castxml",
            ),
            (
                "param_kind_facts_reliable",
                snap.param_kind_facts_reliable,
                True,
            ),
        )
        if not reliable and consulted
    )
