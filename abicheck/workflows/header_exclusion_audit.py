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

"""What a run should say about the ``--exclude-header`` rules it resolved.

A `workflows`-owned module rather than a branch inside the CLI, for the
usual dependency-direction reason: answering it means reading the header
inputs from disk, which is ``extract``'s job, and a ``frontends`` module may
not import ``extract`` at all (``check_architecture.py`` catches it, not
review). The front end asks the question and prints the answer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence
    from pathlib import Path


def unmatched_exclusion_warning(
    headers: Sequence[Path],
    exclude_headers: Sequence[str],
) -> str | None:
    """One warning naming every rule that matched no header, or ``None``.

    **One** warning, for the whole run: the rules are run-wide, so a
    directory/package fan-out over 28 libraries must not repeat the
    identical global line 28 times and bury it. That is why this is answered
    once, above the scalar-versus-release branch, from the composed
    both-sides header inputs every side is narrowed from -- rather than
    inside the per-library comparison, which is where a per-library fact
    (what *that* library's surface omitted) correctly belongs.

    A rule matching nothing is a request recorded as honoured and never
    performed -- the pattern is stamped on the snapshot, folded into the
    configuration digest, and reported as an omission, while the header it
    named is still being parsed. It warns rather than erroring because a
    typo in one pattern is not a reason to refuse the run, unlike the
    manifest combination ``extract.header_exclusions.
    reject_exclusions_against_a_manifest`` does refuse.
    """
    if not exclude_headers:
        return None
    from ..extract.header_exclusions import unmatched_exclusion_patterns

    unmatched = unmatched_exclusion_patterns(headers, exclude_headers)
    if not unmatched:
        return None
    return (
        "Warning: --exclude-header matched no header for: "
        + ", ".join(unmatched)
        + ". Those rules narrowed nothing, but are still recorded as this "
        "run's exclusion configuration -- check the pattern against the "
        "header names actually under -H."
    )


def observed_member_exclusion_identities(
    library_results: Iterable[Mapping[str, Any]],
) -> list[str]:
    """Each completed member comparison's *observed* exclusion identity.

    Read off the stashed ``_diff_result`` of every entry that produced one,
    which is only possible before ``_strip_diff_results_and_adjust_verdict``
    removes them -- hence a named function called at that point rather than
    an expression buried in the receipt assembly further down, where the
    evidence no longer exists.

    An entry with no ``_diff_result`` contributed no observation and is
    skipped rather than counted as "excluded nothing": a member that failed,
    was unmatched, or was never compared did not observe an empty rule set,
    it observed nothing at all. Collapsing those two is the same
    absence-of-evidence-as-evidence error the surrounding change exists to
    correct, and it would silently turn a mixed release into an agreeing
    one.
    """
    return [
        getattr(entry["_diff_result"], "excluded_header_patterns", "") or ""
        for entry in library_results
        if entry.get("_diff_result") is not None
    ]
