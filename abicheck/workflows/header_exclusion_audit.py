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

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence
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
