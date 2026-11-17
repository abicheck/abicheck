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
"""The audited spelling-table record and placeholder rule (ADR-062 D2).

:class:`GenerationSpellingChange` is one row of the per-generation table in
:mod:`.generation_spelling_rows`; :func:`is_placeholder_reason` is the rule
its reasons are held to. ``scripts/generation_corpus_gate.py`` reads
:data:`PLACEHOLDER_REASONS` from this file by AST.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "PLACEHOLDER_REASONS",
    "GenerationSpellingChange",
    "is_placeholder_reason",
]


@dataclass(frozen=True)
class GenerationSpellingChange:
    """What one extractor generation changed about stored type spellings.

    *rewrites* maps an old whole-word spelling to the spelling this
    generation writes instead; empty means the generation changed no
    spelling, and *reason* then says why (it must be non-empty either way).

    *extent_shift* is a respelling no load-time rewrite can apply soundly:
    this generation spells every array extent the older extractor wrote as
    ``N`` as ``N + extent_shift`` for some, but not all, arrays (a stored
    snapshot does not say which backend wrote an extent). Comparison demotes
    a finding whose spellings differ only by that shift instead.

    *restores_erased_extents* says this generation spells an array bound the
    older DWARF extractor erased (``char[8]`` where it wrote ``char[]``). A
    stored ``T[]`` does not say what the bound was, so no rewrite is sound;
    comparison demotes a finding whose spellings differ only by that erasure.
    """

    reason: str
    rewrites: tuple[tuple[str, str], ...] = ()
    extent_shift: int = 0
    #: This generation also declares a load-time rewrite of stored *facts*
    #: (not spellings), such as ``castxml_noexcept_migration.py``: a document
    #: from before it re-encodes to different bytes by design.
    migrates_facts: bool = False
    restores_erased_extents: bool = False
    #: Name prefixes of functions an older generation recorded and this one
    #: no longer does (an extractor artifact, never a library entity), such
    #: as castxml's compiler-declared ``__builtin_*``. No load-time rewrite
    #: can recover the dropped entity soundly, so comparison demotes a
    #: finding on such a function between operands that straddle this
    #: generation instead (``policy/retired_function_drift.py``).
    retires_function_prefixes: tuple[str, ...] = ()


#: Reasons that say nothing about what was audited; the bump gate refuses them.
PLACEHOLDER_REASONS = frozenset(
    {
        "",
        "tbd",
        "todo",
        "none",
        "n/a",
        "no change",
        "no spelling change",
        "no stored type spelling changed",
        "no stored type spelling changed in this generation",
        "bumped for a semantic change",
    }
)


def is_placeholder_reason(reason: str) -> bool:
    """Whether *reason* is empty or a bare "no change" placeholder."""
    return reason.strip().lower().rstrip(".") in PLACEHOLDER_REASONS
