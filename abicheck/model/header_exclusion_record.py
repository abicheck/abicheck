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

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .snapshot import AbiSnapshot


def record_header_exclusions(
    snapshot: AbiSnapshot,
    exclude_headers: Sequence[str],
    *,
    extracted_now: bool = True,
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
    return snapshot
