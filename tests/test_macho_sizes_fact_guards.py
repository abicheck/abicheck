"""The guard branches of the MACHO_OBJECT_SIZES extension fact (ADR-062 D19).

Platform-independent: snapshots are built in memory with explicit Mach-O blocks.
The two states a real reader cannot be steered into on Linux (a fact that is
present but unreadable, and a stored export index past the block) are reached by
replacing only ``read_extension_fact``; every other step runs unmodified.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from abicheck.errors import SnapshotError
from abicheck.model.availability import FactStatus
from abicheck.model.macho_facts import MachoExport, MachoMetadata
from abicheck.model.snapshot import AbiSnapshot
from abicheck.storage import extension_facts as ef
from abicheck.storage.extension_fact_catalog import MACHO_OBJECT_SIZES
from abicheck.storage.extension_facts import (
    apply_macho_sizes_fact,
    macho_sizes_would_be_lost,
    read_extension_fact,
    sync_macho_sizes_fact,
)


def _bare() -> AbiSnapshot:
    return AbiSnapshot(library="libx.dylib", version="1")


def _macho(*exports: MachoExport, derived: bool | None = None) -> AbiSnapshot:
    meta = MachoMetadata(exports=list(exports))
    meta.sizes_derived = derived
    return AbiSnapshot(library="libx.dylib", version="1", platform="macho", macho=meta)


def test_would_be_lost_is_false_without_a_macho_block() -> None:
    assert macho_sizes_would_be_lost(_bare()) is False


def test_would_be_lost_only_when_a_derivation_or_size_is_held() -> None:
    # Negative control: an underived block with no sizes loses nothing.
    assert macho_sizes_would_be_lost(_macho(MachoExport(name="_ZTV3Foo"))) is False
    # A derivation flag, or any export size, is state a flat encoder would drop.
    assert macho_sizes_would_be_lost(_macho(derived=True)) is True
    sized = MachoExport(name="_ZTV3Foo", size=16)
    assert macho_sizes_would_be_lost(_macho(sized)) is True


def test_sync_without_a_macho_block_records_nothing() -> None:
    snap = _bare()
    sync_macho_sizes_fact(snap)
    assert read_extension_fact(snap, MACHO_OBJECT_SIZES).status is (
        FactStatus.NOT_COLLECTED
    )


def test_sync_while_the_section_is_unreadable_writes_nothing() -> None:
    blocked = _macho(derived=True)
    blocked.unreadable_sections = ((MACHO_OBJECT_SIZES.section, "reader failed"),)
    sync_macho_sizes_fact(blocked)
    # The section's failure is what the reader reports; no value was written.
    stored = read_extension_fact(blocked, MACHO_OBJECT_SIZES)
    assert stored.status is FactStatus.FAILED
    assert stored.value is None

    # Negative control: the same derived block, readable, writes the fact.
    readable = _macho(derived=True)
    sync_macho_sizes_fact(readable)
    assert read_extension_fact(readable, MACHO_OBJECT_SIZES).status is (
        FactStatus.PRESENT
    )


def test_apply_without_a_macho_block_is_a_no_op() -> None:
    snap = _bare()
    apply_macho_sizes_fact(snap)
    assert snap.macho is None


def test_apply_while_blocked_leaves_the_sizes_unset(monkeypatch) -> None:
    # A fact that exists but cannot be read, on an unreadable section: quietly
    # skipped, not refused, and the export keeps no size.
    unreadable = SimpleNamespace(status=FactStatus.FAILED, value=None, diagnostics=())
    monkeypatch.setattr(ef, "read_extension_fact", lambda snap, fact: unreadable)

    snap = _macho(MachoExport(name="_ZTV3Foo"))
    snap.unreadable_sections = ((MACHO_OBJECT_SIZES.section, "reader failed"),)
    apply_macho_sizes_fact(snap)
    assert snap.macho.exports[0].size is None

    # Negative control: the same unreadable fact on a readable section is refused.
    readable = _macho(MachoExport(name="_ZTV3Foo"))
    with pytest.raises(SnapshotError, match="extension fact is unreadable"):
        apply_macho_sizes_fact(readable)


def test_apply_refuses_an_export_index_past_the_block(monkeypatch) -> None:
    stored = SimpleNamespace(
        status=FactStatus.PRESENT,
        value={"derived": True, "sizes": [[5, 8, "_ZTI3Foo"]]},
        diagnostics=(),
    )
    monkeypatch.setattr(ef, "read_extension_fact", lambda snap, fact: stored)

    with pytest.raises(SnapshotError, match="names export 5 of 1"):
        apply_macho_sizes_fact(_macho(MachoExport(name="_ZTV3Foo")))

    # Negative control: the same stored row addressing export 0 is applied.
    snap = _macho(MachoExport(name="_ZTV3Foo"))
    stored.value["sizes"] = [[0, 8, "_ZTI3Foo"]]
    apply_macho_sizes_fact(snap)
    assert snap.macho.exports[0].size == 8
    assert snap.macho.exports[0].size_bound_by == "_ZTI3Foo"
    assert snap.macho.sizes_derived is True
