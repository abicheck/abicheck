# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""ADR-062 D14/D15 carriers through every bundle container, generalized.

For the same random per-section states, unknown sections, nested unknown
record keys and document-level must-understand lists
`test_package_carriers_roundtrip.py` generates, a member snapshot must
survive a G38 bundle document, a G40 bundle archive and a bundle package:
blocked exactly where the generator says (the oracle is the generator's
choice), re-saved byte-for-byte as the single-file document it was loaded
from, beside a clean member whose stored form does not change. A document
or archive declares bundle ``schema_version`` 5 exactly when a member
carries state the flat document cannot hold, and a lower declared version
with a sectioned member is refused.
"""

from __future__ import annotations

import copy
import json
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings

from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.errors import SnapshotError
from abicheck.model import AbiSnapshot, Function
from abicheck.model.bundle_facts import BundleFacts
from abicheck.serialization import (
    bundle_facts_from_dict,
    bundle_facts_to_dict,
    load_bundle_facts,
    save_bundle_facts,
    snapshot_from_dict,
    snapshot_to_dict,
)
from abicheck.storage.bundle_facts_codec import save_spooled_bundle_facts
from abicheck.storage.bundle_facts_package import (
    read_bundle_facts_package,
    write_bundle_facts_package,
)
from abicheck.storage.bundle_facts_spool import BlobSpool
from abicheck.storage.package import InMemoryObjectStore
from tests.unit.storage.test_package_carriers_roundtrip import (
    _BASE,
    _carriers,
    _resaved,
)


def _clean() -> AbiSnapshot:
    """A clean member at its load-time fixpoint (backfill settled)."""
    built = AbiSnapshot(
        library="libclean.so.1",
        version="1",
        functions=[Function(name="c", mangled="c", return_type="int")],
        elf=ElfMetadata(soname="libclean.so.1", symbols=[ElfSymbol(name="c")]),
    )
    return snapshot_from_dict(
        snapshot_to_dict(snapshot_from_dict(snapshot_to_dict(built)))
    )


def _facts(member: AbiSnapshot) -> BundleFacts:
    return BundleFacts(
        variant_fingerprint="fp",
        per_library_snapshots={"libcarrier.so.1": member, "libclean.so.1": _clean()},
    )


def _spooled(facts: BundleFacts, path: Path, fmt: str) -> None:
    """Written the way ``--bundle-facts-out`` writes: every member spooled
    first, both forms assembled from the spool. Must be byte-identical to
    the in-memory writer (the oracle)."""
    with BlobSpool() as spool:
        members = {
            name: spool.put_snapshot(snap)
            for name, snap in facts.per_library_snapshots.items()
        }
        save_spooled_bundle_facts(
            BundleFacts(variant_fingerprint=facts.variant_fingerprint),
            members,
            spool,
            path,
            format=fmt,
        )
    direct = path.with_name("direct-" + path.name)
    save_bundle_facts(facts, direct, format=fmt)
    assert path.read_bytes() == direct.read_bytes()


def _through(container: str, facts: BundleFacts, tmp: Path) -> BundleFacts:
    if container.startswith("spool-"):
        fmt = "json" if container == "spool-document" else "archive"
        path = tmp / ("b.json" if fmt == "json" else "b.abibundle")
        _spooled(facts, path, fmt)
        return load_bundle_facts(path)
    if container == "document":
        path = tmp / "b.json"
        save_bundle_facts(facts, path)
        return load_bundle_facts(path)
    if container == "archive":
        path = tmp / "b.abibundle"
        save_bundle_facts(facts, path, format="archive")
        return load_bundle_facts(path)
    store = InMemoryObjectStore()
    manifest = write_bundle_facts_package(facts, store=store)
    return read_bundle_facts_package(manifest, store=store)


@pytest.mark.parametrize(
    "container", ["document", "archive", "package", "spool-document", "spool-archive"]
)
@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(case=_carriers())
def test_carriers_round_trip_through_a_bundle(
    container: str, case: tuple[dict[str, Any], frozenset[str]]
) -> None:
    document, blocked = case
    loaded = snapshot_from_dict(copy.deepcopy(document))
    with tempfile.TemporaryDirectory() as tmp:
        back = _through(container, _facts(loaded), Path(tmp))
    member = back.per_library_snapshots["libcarrier.so.1"]
    assert {kind for kind, _ in member.unreadable_sections} == blocked
    assert _resaved(member) == document
    clean = back.per_library_snapshots["libclean.so.1"]
    assert snapshot_to_dict(clean) == snapshot_to_dict(_clean())


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(case=_carriers())
def test_schema_version_5_exactly_when_a_member_is_sectioned(
    case: tuple[dict[str, Any], frozenset[str]],
) -> None:
    document, _blocked = case
    facts = _facts(snapshot_from_dict(copy.deepcopy(document)))
    stored = bundle_facts_to_dict(facts)
    # Oracle: the flat document alone does not re-save as what was loaded --
    # and since D14's residual closure the flat encoder refuses exactly then.
    try:
        flat = snapshot_from_dict(
            snapshot_to_dict(snapshot_from_dict(copy.deepcopy(document)))
        )
    except SnapshotError:
        carries = True
    else:
        carries = _resaved(flat) != document
        assert not carries, "snapshot_to_dict flattened away stored state"
    assert stored["schema_version"] == (5 if carries else 2)
    assert ("sections" in stored["per_library_snapshots"]["libcarrier.so.1"]) == (
        carries
    )
    # The clean member is always the flat document it always was.
    assert stored["per_library_snapshots"]["libclean.so.1"] == snapshot_to_dict(
        _clean()
    )


def _blocked_facts() -> BundleFacts:
    doc = copy.deepcopy(_BASE)
    doc["sections"]["types"]["must_understand"] = ["vendor.never"]
    return _facts(snapshot_from_dict(doc))


@pytest.mark.parametrize("declared", [1, 2, 3, 4])
def test_a_sectioned_member_under_an_older_version_is_refused(declared: int) -> None:
    stored = bundle_facts_to_dict(_blocked_facts())
    stored["schema_version"] = declared
    if declared == 1:
        del stored["artifact_type"]
    stored.pop("public_surface", None)
    with pytest.raises(ValueError, match="sectioned snapshot document"):
        bundle_facts_from_dict(stored)


def test_an_archive_declaring_an_older_version_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "b.abibundle"
    save_bundle_facts(_blocked_facts(), path, format="archive")
    rewritten = tmp_path / "old.abibundle"
    with zipfile.ZipFile(path) as src, zipfile.ZipFile(rewritten, "w") as dst:
        for info in src.infolist():
            data = src.read(info)
            if info.filename == "manifest.json":
                manifest = json.loads(data)
                assert manifest["bundle_facts_schema_version"] == 5
                manifest["bundle_facts_schema_version"] = 4
                data = json.dumps(manifest, indent=2).encode()
            dst.writestr(info, data)
    with pytest.raises(ValueError, match="sectioned snapshot document"):
        load_bundle_facts(rewritten)
