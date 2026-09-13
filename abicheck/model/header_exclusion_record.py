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

"""Stamping a snapshot with the header exclusions it was built under.

Lives in ``model`` rather than beside the matching rules in
``extract.header_exclusions``, and that is a dependency-direction fact
rather than a taste one: three different layers produce a narrowed snapshot
and each must record it. ``workflows.input_resolution`` handles the native
``--exclude-header`` path, and ``compat.cli`` -- a ``frontends``-layer
module, which may import ``model`` but not ``extract`` -- handles the
descriptor's own ``<skip_headers>``/``<skip_including>``. Putting the
recorder in ``extract`` made the second one a forbidden import (caught by
``check_architecture.py``, not by review).

The split is clean on its own terms too: *matching* a pattern against a
header list is extraction's job; *setting a field on a snapshot* is a model
operation with no extraction dependency at all.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

#: The two rules a run may have matched its exclusion patterns by. ``glob``
#: is native ``--exclude-header`` (fnmatch, plus a ``*/<pattern>`` try);
#: ``exact`` is a descriptor's ``<skip_headers>`` basename-or-path
#: membership. Recorded rather than inferred -- see
#: :func:`exclusions_are_symmetric`.
GLOB_MATCHING = "glob"
EXACT_MATCHING = "exact"

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .snapshot import AbiSnapshot


def record_header_exclusions(
    snapshot: AbiSnapshot,
    exclude_headers: Sequence[str],
    *,
    extracted_now: bool = True,
    matching: str = GLOB_MATCHING,
) -> AbiSnapshot:
    """*snapshot* carrying the ``--exclude-header`` patterns it was built under.

    Returns it unchanged when there were none, so every run that does not use
    the flag produces a byte-identical snapshot to before this existed.

    *extracted_now* is ``False`` for an operand this run **loaded** rather
    than extracted. Such a snapshot parsed no headers in this invocation, so
    this run's patterns say nothing about it -- and it already carries the
    patterns it was really built under. Stamping anyway overwrote that
    provenance with an unrelated request: a snapshot dumped under
    ``original.h``, loaded under ``--exclude-header current.h``, came back
    claiming ``current.h`` (Codex review, reproduced). Worse than a wrong
    label, it can make an asymmetric pair look symmetric -- which is exactly
    the comparison the recorded patterns exist to expose.
    """
    if not exclude_headers or not extracted_now:
        return snapshot
    snapshot.excluded_header_patterns = tuple(exclude_headers)
    snapshot.excluded_header_matching = matching
    return snapshot


def exclusions_are_symmetric(
    old_patterns: Sequence[str],
    new_patterns: Sequence[str],
    old_matching: str = GLOB_MATCHING,
    new_matching: str = GLOB_MATCHING,
) -> bool:
    """Whether two sides were narrowed by the same set of patterns.

    The one comparison of this field, shared rather than reimplemented. The
    comparability gate (``extract.header_exclusions.
    exclusion_asymmetry_reason``) and the coverage warning
    (``confidence.header_exclusion_warnings``) each had their own, and they
    disagreed: the gate compared sets and the warning compared *tuples*, so a
    native run's CLI order against a compat path's sorted record accepted the
    pair and then told the reader its exclusions differed and its findings
    might be scope artefacts (Codex review). A false reduced-confidence
    diagnostic on a pair the tool had just declared comparable.

    Sets, because the patterns are a filter: stating one twice, or in the
    other order, narrows the surface identically. Callers rendering the
    patterns for a human should sort them for the same reason -- two runs
    that did the same thing should not read differently.

    **The matching mode is part of the comparison, not an afterthought.**
    The same text is not the same scope under both rules: native
    ``--exclude-header`` is ``fnmatch`` and additionally tries
    ``*/<pattern>``, so ``include/foo.h`` excludes ``/pkg/include/foo.h``;
    a descriptor's exact membership does not, so it keeps it.

    Two rounds of review falsified two successive attempts to decide this
    from the *pattern text* -- first recording the raw text for both, then
    dropping only metacharacter-bearing patterns on the theory that a plain
    spelling means the same thing under both rules. It does not, for any
    pattern containing a path separator (Codex review, with that exact
    counterexample). Per this repository's own "attempted twice, reverted
    twice" discipline, the third attempt is not another text heuristic: the
    mode is recorded as the fact it is, and two sides are comparable only
    if they narrowed by the same patterns *under the same rule*.

    The cost is accepted deliberately: a descriptor skip and a native
    exclusion naming a bare ``b.h`` do achieve the same thing, and are now
    refused anyway. Refusing a comparable pair costs a run; accepting an
    incomparable one manufactures findings, and nothing in the patterns
    alone proves which case a given pair is.
    """
    if frozenset(old_patterns) != frozenset(new_patterns):
        return False
    # Mode only matters when there is something for it to have matched: a
    # side that excluded nothing is not narrower under any rule, so a
    # snapshot with no patterns stays comparable with anything.
    if not old_patterns and not new_patterns:
        return True
    return old_matching == new_matching
