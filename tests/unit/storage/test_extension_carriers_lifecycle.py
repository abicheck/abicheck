# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""ADR-062 D14/D15 carriers across the snapshot's lifecycle (PR #1864 review).

Two invariants, checked against the written document rather than the
carrier object itself:

* a snapshot whose stored document carried a failed/blocked section,
  an unknown section or record/header extensions keeps that state through
  every in-process copy (``dataclasses.replace``, ``copy``, ``deepcopy``,
  pickle) -- a re-save still writes it, never ``present`` and empty;
* a ProjectSnapshot directory package stores it (``ArtifactRef.carriers``);
  a writer that cannot represent it (a bundle document/archive/package,
  built from the flat ``snapshot_to_dict`` document) refuses instead of
  storing a false clean.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import pickle
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.errors import SnapshotError
from abicheck.model import Function, RecordType
from abicheck.model.bundle_facts import BundleFacts
from abicheck.model.snapshot import AbiSnapshot
from abicheck.project_snapshot_legacy import (
    read_stored_snapshot_document,
    write_legacy_snapshot_package,
)
from abicheck.serialization import (
    SCHEMA_VERSION,
    save_bundle_facts,
    snapshot_from_dict,
    snapshot_to_json,
)
from abicheck.storage.bundle_facts_codec import bundle_facts_to_dict
from abicheck.storage.snapshot_evidence import snapshot_evidence_digest


def _document() -> dict[str, Any]:
    snap = AbiSnapshot(
        library="libext.so.1",
        version="1.0",
        functions=[Function(name="f", mangled="f", return_type="int")],
        types=[RecordType(name="T", kind="struct")],
        elf=ElfMetadata(soname="libext.so.1", symbols=[ElfSymbol(name="f")]),
    )
    return json.loads(snapshot_to_json(snap))


def _failed(doc: dict[str, Any], section: str) -> dict[str, Any]:
    doc["sections"].pop(section)
    doc["section_availability"][section] = {
        "diagnostics": ["dwarf: truncated .debug_info"],
        "status": "failed",
    }
    return doc


def _blocked_must_understand(doc: dict[str, Any]) -> dict[str, Any]:
    doc["sections"]["types"]["must_understand"] = ["vendor.never"]
    return doc


def _unknown_section(doc: dict[str, Any]) -> dict[str, Any]:
    doc["sections"]["x_future"] = {"section_schema_version": 1, "payload": {"k": 1}}
    doc["section_schema_versions"]["x_future"] = 1
    return doc


def _record_field(doc: dict[str, Any]) -> dict[str, Any]:
    doc["sections"]["declarations"]["payload"]["functions"][0]["x_note"] = "kept"
    return doc


def _header_extensions(doc: dict[str, Any]) -> dict[str, Any]:
    doc["sections"]["declarations"]["extensions"] = {"vendor.note": {"n": 1}}
    return doc


# Each mutation, and an independent oracle on the re-saved document.
_CARRIERS: dict[str, tuple[Callable[[dict], dict], Callable[[dict], bool]]] = {
    "failed": (
        lambda d: _failed(d, "types"),
        lambda w: (
            w["section_availability"]["types"]["status"] == "failed"
            and "types" not in w["sections"]
        ),
    ),
    "blocked": (
        _blocked_must_understand,
        lambda w: w["sections"]["types"]["must_understand"] == ["vendor.never"],
    ),
    "unknown_section": (
        _unknown_section,
        lambda w: w["sections"]["x_future"]["payload"] == {"k": 1},
    ),
    "record_field": (
        _record_field,
        lambda w: any(
            f.get("x_note") == "kept"
            for f in w["sections"]["declarations"]["payload"]["functions"]
        ),
    ),
    "header_extensions": (
        _header_extensions,
        lambda w: (
            w["sections"]["declarations"]["extensions"] == {"vendor.note": {"n": 1}}
        ),
    ),
}

_COPIES: dict[str, Callable[[Any], Any]] = {
    "replace": lambda s: dataclasses.replace(s),
    "replace_version": lambda s: dataclasses.replace(s, version="9.9"),
    "copy": copy.copy,
    "deepcopy": copy.deepcopy,
    "pickle": lambda s: pickle.loads(pickle.dumps(s)),
}


def _loaded(carrier: str):
    mutate, _ = _CARRIERS[carrier]
    return snapshot_from_dict(mutate(_document()))


@pytest.mark.parametrize("how", sorted(_COPIES))
@pytest.mark.parametrize("carrier", sorted(_CARRIERS))
def test_copies_keep_the_carriers(carrier: str, how: str) -> None:
    original = _loaded(carrier)
    copied = _COPIES[how](original)
    assert copied.unreadable_sections == original.unreadable_sections
    written = json.loads(snapshot_to_json(copied))
    assert _CARRIERS[carrier][1](written)
    # The copy's digest still covers the extensions (D14).
    assert (
        snapshot_evidence_digest(copied)
        == snapshot_evidence_digest(
            snapshot_from_dict(json.loads(snapshot_to_json(original)))
        )
        or how == "replace_version"
    )


@pytest.mark.parametrize("section", ["types", "declarations"])
def test_unreadable_without_carriers_refuses_to_write(section: str) -> None:
    snap = snapshot_from_dict(_failed(_document(), section))
    stripped = dataclasses.replace(snap, wire_extensions=None)
    with pytest.raises(SnapshotError, match="unreadable section"):
        snapshot_to_json(stripped)


@pytest.mark.parametrize("carrier", sorted(_CARRIERS))
def test_a_package_carries_the_carriers(carrier: str, tmp_path: Path) -> None:
    """A directory package stores the carriers (`ArtifactRef.carriers`), so
    the snapshot reads back with the same blocked sections and re-saves to
    the same single-file document -- never a false clean."""
    loaded = _loaded(carrier)
    root = tmp_path / "pkg"
    write_legacy_snapshot_package(
        loaded, root, artifact_id="a", max_known_schema_version=SCHEMA_VERSION
    )
    document, ref = read_stored_snapshot_document(root)
    back = snapshot_from_dict(document)
    assert back.unreadable_sections == loaded.unreadable_sections
    written = json.loads(snapshot_to_json(back))
    assert _CARRIERS[carrier][1](written)
    assert written == json.loads(snapshot_to_json(loaded))
    assert ref.carriers is not None
    assert ref.evidence_digest == snapshot_evidence_digest(loaded)


@pytest.mark.parametrize("carrier", sorted(_CARRIERS))
@pytest.mark.parametrize("fmt", ["json", "archive", "dict"])
def test_bundle_writers_refuse(carrier: str, fmt: str, tmp_path: Path) -> None:
    facts = BundleFacts(per_library_snapshots={"libext.so.1": _loaded(carrier)})
    with pytest.raises(SnapshotError, match="single-file snapshot"):
        if fmt == "dict":
            bundle_facts_to_dict(facts)
        else:
            save_bundle_facts(facts, tmp_path / f"b.{fmt}", format=fmt)


def test_a_clean_loaded_snapshot_still_packages_with_its_evidence_digest(
    tmp_path: Path,
) -> None:
    snap = snapshot_from_dict(_document())
    root = tmp_path / "pkg"
    write_legacy_snapshot_package(
        snap, root, artifact_id="a", max_known_schema_version=SCHEMA_VERSION
    )
    document, ref = read_stored_snapshot_document(root)
    back = snapshot_from_dict(document)
    assert back.unreadable_sections == ()
    assert [t.name for t in back.declarations.types] == ["T"]
    # The stored digest is the snapshot's own (no carriers here).
    assert ref.evidence_digest == snapshot_evidence_digest(snap)
