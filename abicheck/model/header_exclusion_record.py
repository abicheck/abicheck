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

#: The rules a run may have matched its exclusion patterns by. ``glob`` is
#: native ``--exclude-header`` (fnmatch, plus a ``*/<pattern>`` try).
#: Recorded rather than inferred -- see :func:`exclusions_are_symmetric`.
GLOB_MATCHING = "glob"
#: A descriptor's ``<skip_headers>`` before it gained ABICC's real rule
#: classes, when it was a plain basename-or-path membership test. **No run
#: produces this any more**; it stays recognised only so a baseline dumped
#: by such a build loads as the narrowing it really performed, rather than
#: as :data:`UNKNOWN_MATCHING`, and is correctly refused against a snapshot
#: narrowed under :data:`DESCRIPTOR_MATCHING` -- the two really do name
#: different sets of headers for the same text.
EXACT_MATCHING = "exact"
#: A descriptor's ``<skip_headers>`` under real ABICC rule classes --
#: basename, component-boundary path/directory, or compiled pattern (see
#: :mod:`abicheck.model.header_skip_rules`). Its own rule rather than a
#: variant of :data:`EXACT_MATCHING`, for the same reason ``exact`` is not
#: a variant of ``glob``: the same text names a different set of headers
#: under each, so two sides narrowed under different rules are not
#: comparable. A snapshot written by a build that only had
#: ``exact`` therefore no longer compares equal to one written now -- which
#: is the honest answer, because the two runs really did exclude different
#: sets (``fftw/fftw.h`` excluded nothing under ``exact``).
DESCRIPTOR_MATCHING = "abicc"
#: A snapshot that recorded exclusion patterns *before* the rule was
#: persisted (schema v47). Not a third rule -- the absence of the answer.
#: Defaulting such a snapshot to ``glob`` was wrong: descriptor exclusions
#: were already being recorded under v47, so a baseline holding
#: ``include/foo.h`` matched *exactly* would have loaded as a glob and
#: compared clean against a native glob snapshot that excluded a different
#: set of headers (Codex review). Unprovable is not the same as native.
UNKNOWN_MATCHING = "unknown"

#: Every rule this build can reason about. A snapshot naming anything else --
#: a hand-edited file, or one written by a build that knows a rule this one
#: does not -- is read as :data:`UNKNOWN_MATCHING` rather than taken at its
#: word: two sides both claiming ``"regex"`` would otherwise compare equal and
#: be approved, though this reader cannot establish what either excluded
#: (Codex review). Recognising a name is not the same as implementing it.
KNOWN_MATCHING_RULES: frozenset[str] = frozenset(
    {GLOB_MATCHING, EXACT_MATCHING, DESCRIPTOR_MATCHING, UNKNOWN_MATCHING}
)


def normalize_matching(value: object) -> str:
    """*value* if this build can reason about it, else ``"unknown"``."""
    text = str(value or "").strip()
    return text if text in KNOWN_MATCHING_RULES else UNKNOWN_MATCHING


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
    # An unrecorded rule is not a rule. Two such snapshots are refused even
    # when their patterns match, because "both were probably native" is a
    # guess about how each was produced, and guessing is what the recorded
    # mode exists to stop. Re-dumping either side under this build answers it.
    if UNKNOWN_MATCHING in (old_matching, new_matching):
        return False
    return old_matching == new_matching


def canonical_exclusion_identity(
    patterns: Sequence[str],
    matching: str = GLOB_MATCHING,
) -> str:
    """*patterns* as one stable identity string, or ``""`` for none.

    The one spelling every consumer that needs an *identity* for a run's
    header exclusions uses -- the configuration digest
    (``DiffResult.excluded_header_patterns`` ->
    ``effective_config_digest``'s ``surface.exclude_headers``) and the
    dry-run/diagnostic emit alike -- so the same rules never read as two
    different configurations depending on who rendered them.

    Canonical in exactly the way :func:`exclusions_are_symmetric` compares:
    sorted and de-duplicated, because the patterns are a filter and stating
    one twice or in the other order narrows the surface identically; and
    prefixed with the matching rule, because the same text does not name
    the same set of headers under ``glob`` as under a descriptor's rules.
    Two runs that excluded the same headers share this string; two runs
    that excluded different ones cannot.
    """
    unique = sorted(frozenset(p for p in patterns if p))
    if not unique:
        return ""
    return f"{normalize_matching(matching)}:" + ",".join(unique)


def comparison_exclusion_identity(old: AbiSnapshot | None, new: AbiSnapshot) -> str:
    """The canonical exclusion identity for a *comparison* of *old* and *new*.

    Read off the snapshots rather than from a caller's parameter: they
    record what was actually excluded when each side was extracted, which is
    the thing that narrowed the compared surface -- a stored baseline
    carries its own rules, and no parameter at the comparison layer would
    describe it.

    One value, not two. NEW is consulted, with OLD as the fallback for a
    declared-absent baseline, because an asymmetric pair never reaches a
    comparison at all: ``comparability`` refuses it
    (``extract.header_exclusions.exclusion_asymmetry_reason``), so the two
    sides agree by construction whenever both exist.
    """
    side = new if getattr(new, "excluded_header_patterns", ()) else old
    return canonical_exclusion_identity(
        getattr(side, "excluded_header_patterns", ()) or (),
        getattr(side, "excluded_header_matching", GLOB_MATCHING) or GLOB_MATCHING,
    )
