# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""ADR-062 D14 residual: no writer stores a flat mapping that silently lost
its carriers.

A flat ``snapshot_to_dict`` document has no place for a failed or blocked
section, an unknown section, header or record extensions, or a document-level
must-understand list. Every writer that accepts a flat mapping -- the
single-file sectioned document, a directory package, a bundle package, a
baseline set -- would store such a mapping as complete. The fix closes the
class where flat mappings are made: `snapshot_to_dict` refuses a snapshot
whose flat document would lose stored state, so every flat mapping a writer
can be handed is a complete statement of its snapshot. Writers of a snapshot
take the `AbiSnapshot` (`write_legacy_snapshot_package`, the ``dump`` write,
the probe-matrix file).

Oracle (independent of the code under test): the generator's own choice of
per-section states, and the single-file document it built. For every writer
entry point and every generated combination, a write followed by a read
either re-saves byte-for-byte as that document (every carrier kept) or fails
loudly -- never a successful round trip that re-saves as something else.
"""

from __future__ import annotations

import copy
import json
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings

from abicheck.cli_dump_helpers import write_snapshot_and_report
from abicheck.errors import SnapshotError
from abicheck.model import AbiSnapshot
from abicheck.model.bundle_facts import BUNDLE_FACTS_ARTIFACT_TYPE
from abicheck.model.probe_matrix import MatrixSnapshot, ProbeResult
from abicheck.project_snapshot_legacy import (
    read_stored_snapshot_document,
    write_legacy_snapshot_package,
)
from abicheck.serialization import (
    SCHEMA_VERSION,
    load_snapshot,
    snapshot_from_dict,
    snapshot_to_dict,
    to_sectioned_document,
)
from abicheck.storage.import_baseline_set import (
    export_baseline_set,
    import_baseline_set,
)
from abicheck.storage.import_bundle_facts import (
    export_bundle_facts,
    import_bundle_facts,
)
from abicheck.storage.import_v1 import export_legacy_snapshot, import_legacy_snapshot
from abicheck.storage.package import InMemoryObjectStore
from abicheck.storage.wire_extensions import record_section_failure
from abicheck.workflows.findings import (
    matrix_snapshot_from_dict,
    matrix_snapshot_to_json,
)
from tests.unit.storage.test_package_carriers_roundtrip import (
    _BASE,
    _carriers,
    _resaved,
)

_LIBRARY = _BASE["sections"]["provenance"]["payload"]["library"]


# --- writers that accept a flat mapping: handed `snapshot_to_dict(loaded)` --


def _via_sectioned_document(flat: dict[str, Any]) -> AbiSnapshot:
    return snapshot_from_dict(
        to_sectioned_document(flat, max_known_schema_version=SCHEMA_VERSION)
    )


def _via_import_legacy_snapshot(flat: dict[str, Any]) -> AbiSnapshot:
    store = InMemoryObjectStore()
    manifest = import_legacy_snapshot(
        flat, store=store, artifact_id="a", max_known_schema_version=SCHEMA_VERSION
    )
    (artifact,) = manifest.artifact_refs
    return snapshot_from_dict(
        export_legacy_snapshot(
            artifact,
            store=store,
            source_schema_version=manifest.versions.source_schema_version,
        )
    )


def _via_import_bundle_facts(flat: dict[str, Any]) -> AbiSnapshot:
    store = InMemoryObjectStore()
    manifest = import_bundle_facts(
        {
            "artifact_type": BUNDLE_FACTS_ARTIFACT_TYPE,
            "schema_version": 2,
            "variant_fingerprint": "fp",
            "per_library_snapshots": {_LIBRARY: flat},
        },
        store=store,
        max_known_schema_version=SCHEMA_VERSION,
    )
    document = export_bundle_facts(manifest, store=store)
    return snapshot_from_dict(document["per_library_snapshots"][_LIBRARY])


def _via_import_baseline_set(flat: dict[str, Any]) -> AbiSnapshot:
    store = InMemoryObjectStore()
    manifest = import_baseline_set(
        {
            "manifest_version": 1,
            "project_ref": "refs/heads/main",
            "profile": "default",
            "snapshot_schema": SCHEMA_VERSION,
            "artifacts": [{"library": _LIBRARY, "snapshot": "a.abi.json"}],
        },
        {_LIBRARY: flat},
        store=store,
        max_known_schema_version=SCHEMA_VERSION,
    )
    _metadata, documents = export_baseline_set(manifest, store=store)
    return snapshot_from_dict(documents[_LIBRARY])


def _via_legacy_package_with_a_mapping(flat: dict[str, Any]) -> AbiSnapshot:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "pkg"
        write_legacy_snapshot_package(
            flat,  # type: ignore[arg-type]  # the refused input under test
            root,
            artifact_id="a",
            max_known_schema_version=SCHEMA_VERSION,
        )
        return snapshot_from_dict(read_stored_snapshot_document(root)[0])


_FLAT_WRITERS: dict[str, Callable[[dict[str, Any]], AbiSnapshot]] = {
    "sectioned_document": _via_sectioned_document,
    "import_legacy_snapshot": _via_import_legacy_snapshot,
    "import_bundle_facts": _via_import_bundle_facts,
    "import_baseline_set": _via_import_baseline_set,
    "write_legacy_snapshot_package": _via_legacy_package_with_a_mapping,
}


# --- writers that take the snapshot itself ---------------------------------


def _via_legacy_package(snap: AbiSnapshot) -> AbiSnapshot:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "pkg"
        write_legacy_snapshot_package(
            snap, root, artifact_id="a", max_known_schema_version=SCHEMA_VERSION
        )
        return snapshot_from_dict(read_stored_snapshot_document(root)[0])


def _via_dump_write(snap: AbiSnapshot) -> AbiSnapshot:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "out.abi.json"
        write_snapshot_and_report(snap, path, "none", "binary")
        return load_snapshot(path)


def _via_probe_matrix(snap: AbiSnapshot) -> AbiSnapshot:
    matrix = MatrixSnapshot(
        library=_LIBRARY,
        version="1",
        spec_name="spec",
        results=[ProbeResult(configuration_id="c", probe_id="p", snapshot=snap)],
    )
    back = matrix_snapshot_from_dict(json.loads(matrix_snapshot_to_json(matrix)))
    restored = back.results[0].snapshot
    assert restored is not None
    return restored


_SNAPSHOT_WRITERS: dict[str, Callable[[AbiSnapshot], AbiSnapshot]] = {
    "write_legacy_snapshot_package": _via_legacy_package,
    "dump": _via_dump_write,
    "probe_matrix": _via_probe_matrix,
}


def _loaded(document: dict[str, Any], blocked: frozenset[str]) -> AbiSnapshot:
    loaded = snapshot_from_dict(copy.deepcopy(document))
    # The generator's own choice is the oracle; the load must agree first.
    assert {kind for kind, _ in loaded.unreadable_sections} == blocked
    assert _resaved(loaded) == document
    return loaded


@pytest.mark.parametrize("writer", sorted(_FLAT_WRITERS))
@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(case=_carriers())
def test_a_flat_mapping_write_keeps_every_carrier_or_fails_loudly(
    writer: str, case: tuple[dict[str, Any], frozenset[str]]
) -> None:
    document, blocked = case
    loaded = _loaded(document, blocked)
    try:
        back = _FLAT_WRITERS[writer](snapshot_to_dict(loaded))
    except (SnapshotError, TypeError):
        return  # loud: the caller is told, nothing is stored as complete
    assert {kind for kind, _ in back.unreadable_sections} == blocked
    assert _resaved(back) == document


@pytest.mark.parametrize("writer", sorted(_SNAPSHOT_WRITERS))
@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(case=_carriers())
def test_a_snapshot_writer_keeps_every_carrier(
    writer: str, case: tuple[dict[str, Any], frozenset[str]]
) -> None:
    document, blocked = case
    back = _SNAPSHOT_WRITERS[writer](_loaded(document, blocked))
    assert {kind for kind, _ in back.unreadable_sections} == blocked
    assert _resaved(back) == document


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(case=_carriers())
def test_snapshot_to_dict_refuses_exactly_when_the_flat_document_loses_state(
    case: tuple[dict[str, Any], frozenset[str]],
) -> None:
    # Oracle: the generated single-file document. Lossy means the flat
    # document, read back and re-saved, is not that document; it is computed
    # through the sectioned writer, which keeps carriers, from the flat
    # encoding the refusal guards.
    document, blocked = case
    loaded = _loaded(document, blocked)
    flat_alone = copy.deepcopy(loaded)
    flat_alone.wire_extensions = None  # what a flat document can say
    flat_alone.unreadable_sections = []
    lossy = _resaved(flat_alone) != document
    if lossy:
        with pytest.raises(SnapshotError, match="flat snapshot_to_dict"):
            snapshot_to_dict(loaded)
    else:
        assert _resaved(snapshot_from_dict(snapshot_to_dict(loaded))) == document


def test_a_mapping_is_refused_by_the_package_writer() -> None:
    flat = snapshot_to_dict(AbiSnapshot(library="libx.so", version="1"))
    with (
        tempfile.TemporaryDirectory() as tmp,
        pytest.raises(TypeError, match="takes an AbiSnapshot"),
    ):
        write_legacy_snapshot_package(
            flat,  # type: ignore[arg-type]
            Path(tmp) / "pkg",
            artifact_id="a",
            max_known_schema_version=SCHEMA_VERSION,
        )


@pytest.mark.parametrize("section", ["binary", "build", "debug", "layout", "types"])
def test_a_producer_recorded_failure_survives_the_dump_write(
    section: str, tmp_path: Path
) -> None:
    # The `dump` write used to flatten the snapshot and section the flat
    # payload, so a section the extractor recorded as failed
    # (`workflows/dump/pe.py`) was written as present and empty.
    snap = AbiSnapshot(library="libx.dll", version="1")
    record_section_failure(snap, section, ["pdb parse failed"])
    path = tmp_path / "out.abi.json"
    write_snapshot_and_report(snap, path, "none", "binary")
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["section_availability"][section] == {
        "diagnostics": ["pdb parse failed"],
        "status": "failed",
    }
    assert section not in stored["sections"]
    assert [kind for kind, _ in load_snapshot(path).unreadable_sections] == [section]
