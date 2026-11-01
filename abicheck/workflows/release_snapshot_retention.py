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

* ``--bundle-facts-out`` captures the OLD side's per-library snapshots as
  the baseline document (``workflows.release_bundle.write_bundle_facts_out``,
  which iterates the same ``diff_pairs``).

JUnit was the second OLD-side consumer, and is no longer one at all. An
audit of what it actually read off the snapshot found exactly four
attribute reads, all in ``junit_report._collect_all_symbols``, building a
symbol-name -> classname map -- so it now receives that map, projected
once per member into a
:class:`~abicheck.model.symbol_inventory.SymbolInventory` at the point
its comparison finishes, and the snapshot is released there instead of
being pinned until the release-level JUnit fold at the end of the run.
``--bundle-facts-out`` genuinely needs the whole document and is the only
remaining reason ``old_full`` is ever true -- and it keeps that document on
disk, not resident: the release passes a
:class:`~abicheck.storage.bundle_facts_spool.BlobSpool` as ``old_spool``, so
each member's OLD snapshot is encoded into it when the member's comparison
finishes and the entry keeps a :class:`~abicheck.storage.bundle_facts_spool.
SpooledBlob` handle beside the compact evidence. The writer reads the blobs
back one at a time (``workflows.release_bundle.write_bundle_facts_out``).

Nothing anywhere reads a stashed ``_new_snapshot``: the one reader that
looks for it (``collect_bundle_result``) falls back to the compact
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

from ..checker_types import DiffResult
from ..errors import SnapshotError
from ..model import AbiSnapshot
from ..model.symbol_inventory import SymbolInventory, build_symbol_inventory
from ..storage.bundle_facts_spool import BlobSpool, SpooledBlob

__all__ = [
    "SnapshotRetention",
    "release_junit_pairs",
    "release_old_member_snapshots",
    "resolve_snapshot_retention",
]


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
    #: Whether the run needs the compact OLD-side JUnit inventory. Independent
    #: of *old_full*: the inventory is what JUnit reads, and it is cheap
    #: enough (four tuples of ``str``) that it is not worth a second full
    #: snapshot to avoid.
    junit_inventory: bool = False
    #: Why the full OLD side is retained, for the memory trace and the
    #: ``counts`` record. Empty when it is not.
    old_consumers: tuple[str, ...] = ()
    new_consumers: tuple[str, ...] = ()
    #: Where a full OLD snapshot is kept when *old_full*: on disk, encoded,
    #: in this spool (the member entry keeps a ``SpooledBlob`` handle and
    #: the compact evidence), instead of resident. ``None`` keeps it
    #: resident. ``--bundle-facts-out`` always passes one.
    old_spool: BlobSpool | None = None

    def as_counts(self) -> dict[str, object]:
        """A memory-trace-friendly record of this decision."""
        return {
            "junit_inventory": self.junit_inventory,
            "old_full": self.old_full,
            "old_spooled": self.old_full and self.old_spool is not None,
            "new_full": self.new_full,
            "old_consumers": list(self.old_consumers),
            "new_consumers": list(self.new_consumers),
        }


def resolve_snapshot_retention(
    *,
    junit: bool = False,
    bundle_facts_out: bool = False,
    old_spool: BlobSpool | None = None,
) -> SnapshotRetention:
    """Resolve retention from the run's *actual* output requirements.

    *junit* covers both the primary ``--format junit`` and a secondary
    ``-o junit=...`` render -- the two share one pair list. It no longer
    requires a full snapshot: it takes the compact
    :class:`~abicheck.model.symbol_inventory.SymbolInventory`, which
    ``stash_member_evidence`` builds unconditionally, so *junit* is
    accepted (and recorded, so the trace still shows the run asked for it)
    without turning ``old_full`` on. *bundle_facts_out* is
    ``--bundle-facts-out`` and is the one consumer that still needs the
    whole document.

    Neither consumer reads the NEW side, so ``new_full`` stays ``False``;
    see this module's docstring for the call sites that establish that.

    *old_spool* moves a retained OLD snapshot out of memory: each member's
    is encoded into it when that member's comparison finishes, and read
    back only when ``--bundle-facts-out`` writes the document.
    """
    old_consumers: list[str] = []
    if bundle_facts_out:
        old_consumers.append("bundle_facts_out")
    return SnapshotRetention(
        junit_inventory=bool(junit),
        old_full=bool(old_consumers),
        new_full=False,
        old_consumers=tuple(old_consumers),
        new_consumers=(),
        old_spool=old_spool if old_consumers else None,
    )


def stash_member_evidence(
    entry: dict[str, object],
    key: str,
    old_snapshot: AbiSnapshot,
    new_snapshot: AbiSnapshot,
    retention: SnapshotRetention | None,
) -> None:
    """Attach one finished member's bundle evidence under *retention*.

    Each side gets the full :class:`~abicheck.model.AbiSnapshot` when a
    consumer reads it and the compact
    :class:`~abicheck.bundle_models.BundleSignatureEvidence` otherwise --
    never both, so the reader (``collect_bundle_result``) sees exactly one
    shape per side. ``None`` retention means compact on both sides.

    Lives beside the decision rather than at the release fan-out's call
    site: *what evidence a member leaves behind* is not a front end's
    choice, and the projection it applies is a workflows-owned one.
    """
    from .bundle_symbol_status import build_bundle_signature_evidence
    from .memory_trace import record_release_member

    keep = retention or SnapshotRetention()
    entry["_bundle_key"] = key
    if keep.junit_inventory:
        # Projected here, while the snapshot is still in hand and before the
        # branch below decides whether to let it go -- so a JUnit-only run
        # keeps four tuples of ``str`` per member instead of a whole
        # declaration graph, for the whole rest of the run.
        entry["_old_junit_inventory"] = build_symbol_inventory(old_snapshot)
    spooled = (
        _spool_snapshot(keep.old_spool, old_snapshot)
        if keep.old_full and keep.old_spool is not None
        else None
    )
    if spooled is not None:
        # Kept in full, on disk: the entry holds the handle, and the compact
        # evidence serves the bundle layer exactly as on a run without
        # --bundle-facts-out.
        entry["_old_snapshot_spooled"] = spooled
        entry["_old_bundle_evidence"] = build_bundle_signature_evidence(old_snapshot)
    elif keep.old_full:
        entry["_old_snapshot"] = old_snapshot
    else:
        entry["_old_bundle_evidence"] = build_bundle_signature_evidence(old_snapshot)
    if keep.new_full:
        entry["_new_snapshot"] = new_snapshot
    else:
        entry["_new_bundle_evidence"] = build_bundle_signature_evidence(new_snapshot)
    library = entry.get("library")
    record_release_member(
        library if isinstance(library, str) else key, keep.as_counts()
    )


def _spool_snapshot(spool: BlobSpool, snapshot: AbiSnapshot) -> SpooledBlob | None:
    """*snapshot*'s spooled handle, or ``None`` when it cannot be spooled.

    ``None`` keeps the snapshot resident instead, so a member's comparison
    never fails on the baseline's account: ``write_bundle_facts_out``
    spools it again and reports a persistent failure (a full disk, a
    snapshot no flat document can carry) as the ``--bundle-facts-out``
    error it always was."""
    try:
        return spool.put_snapshot(snapshot)
    except (OSError, SnapshotError, ValueError):
        return None


def release_old_member_snapshots(
    library_results: list[dict[str, object]],
) -> list[tuple[str, AbiSnapshot | SpooledBlob]]:
    """``(member file name, OLD snapshot)`` for every member entry that kept
    one -- what ``--bundle-facts-out`` persists. A snapshot kept in the
    retention's spool is its :class:`SpooledBlob` handle.

    Keyed by the entry's own ``library`` (the OLD member's file name), not by
    the ``DiffResult``'s: a JSON-snapshot member's diff carries the snapshot's
    ``library`` field, which need not name the member at all, so keying by it
    either invented a second key or, once that was refused, re-extracted a
    snapshot the run already held.

    Must be called before ``project_release_library_results`` clears those
    entries. Only a member that was compared (it has its ``DiffResult``) and
    kept its snapshot contributes; ``write_bundle_facts_out`` captures every
    other member from ``old_map`` itself.
    """
    snapshots: list[tuple[str, AbiSnapshot | SpooledBlob]] = []
    for entry in library_results:
        old_snap = entry.get("_old_snapshot") or entry.get("_old_snapshot_spooled")
        name = entry.get("library")
        if (
            isinstance(entry.get("_diff_result"), DiffResult)
            and isinstance(old_snap, (AbiSnapshot, SpooledBlob))
            and isinstance(name, str)
        ):
            snapshots.append((name, old_snap))
    return snapshots


def release_junit_pairs(
    library_results: list[dict[str, object]],
) -> list[tuple[DiffResult, SymbolInventory]]:
    """The ``(DiffResult, SymbolInventory)`` pairs a JUnit render needs.

    The OLD operand is the compact inventory :func:`stash_member_evidence`
    projected when the member finished, never the full ``AbiSnapshot``.
    A run that *also* asked for ``--bundle-facts-out`` has both on the
    entry; the inventory still wins here, so the two consumers can never
    disagree about which projection JUnit rendered from. The full-snapshot
    fallback covers a caller that supplied no retention at all (the
    ``None`` default, compact on both sides) and therefore has no
    inventory stashed -- the single-pair and test paths.
    """
    pairs: list[tuple[DiffResult, SymbolInventory]] = []
    for entry in library_results:
        diff = entry.get("_diff_result")
        inventory = entry.get("_old_junit_inventory")
        if not isinstance(inventory, SymbolInventory):
            old_snapshot = entry.get("_old_snapshot")
            inventory = (
                build_symbol_inventory(old_snapshot)
                if isinstance(old_snapshot, AbiSnapshot)
                else None
            )
        if isinstance(diff, DiffResult) and inventory is not None:
            pairs.append((diff, inventory))
    return pairs
