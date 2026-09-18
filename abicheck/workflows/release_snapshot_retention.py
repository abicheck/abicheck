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

"""Which side's *full* ``AbiSnapshot`` a release fan-out must keep resident.

A directory/package ``compare`` holds every matched member's comparison
result until the release-level folds have run, so whatever a member entry
retains is multiplied by the member count and held for the whole run. The
compact stand-in already exists (``bundle_models.BundleSignatureEvidence``,
G38 Phase 9) and the bundle layer reads it duck-typed, so the only question
is *which consumer actually needs the full graph* -- and it is a per-side
question, not one boolean.

It was one boolean. ``need_full_snapshots`` turned on together for OLD and
NEW as soon as JUnit or ``--bundle-facts-out`` was requested, and both
consumers read **only the OLD side**:

* JUnit builds ``(DiffResult, old_snapshot)`` pairs
  (``cli_compare_release_pairwise._compare_release_libraries``) and
  ``junit_report`` resolves declaration locations out of the OLD snapshot.
* ``--bundle-facts-out`` captures the OLD side's per-library snapshots as
  the baseline document (``cli_compare_release_helpers._write_bundle_facts``,
  which iterates the same ``diff_pairs``).

Nothing anywhere reads a stashed ``_new_snapshot``: the one reader that
looks for it (``_collect_bundle_result``) falls back to the compact
evidence and is satisfied by it. So requesting JUnit or a baseline pinned a
second full snapshot graph per member that no code path ever opened.

:class:`SnapshotRetention` replaces the boolean with the per-side answer and
states each side's consumers, so adding a consumer means adding it here
rather than re-flipping a shared switch. ``new_full`` is therefore ``False``
for every combination this CLI can produce today -- deliberately expressed
as "no consumer requires it" rather than hard-coded, and pinned by
``tests/test_release_snapshot_retention.py``, which enumerates the real
consumer call sites so a future one cannot quietly start reading a NEW
snapshot that is no longer there.

Nothing here decides *evidence*: the compact stand-in preserves exactly what
the bundle layer reads, so a member's findings, coverage, provider
attribution and verdict are unchanged either way. This module only decides
what is kept in memory after the member's comparison has finished.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["SnapshotRetention", "resolve_snapshot_retention"]


@dataclass(frozen=True)
class SnapshotRetention:
    """Per-side retention for one release fan-out.

    *old_full*/*new_full* mean "keep the member's full :class:`AbiSnapshot`
    on this side after its comparison completes". The other side keeps the
    compact :class:`~abicheck.bundle_models.BundleSignatureEvidence`, which
    is what the bundle cross-library analysis consumes on either side.
    """

    old_full: bool = False
    new_full: bool = False
    #: Why the full OLD side is retained, for the memory trace and the
    #: ``counts`` record. Empty when it is not.
    old_consumers: tuple[str, ...] = ()
    new_consumers: tuple[str, ...] = ()

    @property
    def any_full(self) -> bool:
        """Whether either side keeps a full snapshot at all."""
        return self.old_full or self.new_full

    def as_counts(self) -> dict[str, object]:
        """A memory-trace-friendly record of this decision."""
        return {
            "old_full": self.old_full,
            "new_full": self.new_full,
            "old_consumers": list(self.old_consumers),
            "new_consumers": list(self.new_consumers),
        }


def resolve_snapshot_retention(
    *,
    junit: bool = False,
    bundle_facts_out: bool = False,
) -> SnapshotRetention:
    """Resolve retention from the run's *actual* output requirements.

    *junit* covers both the primary ``--format junit`` and a secondary
    ``-o junit=...`` render -- the two share one pair list. *bundle_facts_out*
    is ``--bundle-facts-out``.

    Neither consumer reads the NEW side, so ``new_full`` stays ``False``;
    see this module's docstring for the call sites that establish that.
    """
    old_consumers: list[str] = []
    if junit:
        old_consumers.append("junit")
    if bundle_facts_out:
        old_consumers.append("bundle_facts_out")
    return SnapshotRetention(
        old_full=bool(old_consumers),
        new_full=False,
        old_consumers=tuple(old_consumers),
        new_consumers=(),
    )
