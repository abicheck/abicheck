# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""ADR-062 D14/D15 carriers through a ProjectSnapshot package, generalized.

For a random combination of per-section states (present, header extensions,
an understood or unknown must-understand name, a newer section version,
failed, not collected), unknown sections, a document-level must-understand
list, and unknown keys at every record nesting level, a single-file snapshot
must survive single-file -> load -> package -> load -> single-file save
exactly, block exactly the sections the generated states say block (the
oracle is the generator's own choice, not the implementation's decision),
and store an `evidence` digest that covers the carriers.
"""

from __future__ import annotations

import copy
import json
import tempfile
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from abicheck.elf_metadata import ElfImport, ElfMetadata, ElfSymbol
from abicheck.errors import SnapshotError
from abicheck.model import (
    AbiSnapshot,
    EnumMember,
    EnumType,
    Function,
    Param,
    RecordType,
    TypeField,
    Variable,
)
from abicheck.project_snapshot_legacy import (
    read_stored_snapshot_document,
    write_legacy_snapshot_package,
)
from abicheck.serialization import SCHEMA_VERSION, snapshot_from_dict, snapshot_to_json
from abicheck.storage.snapshot_evidence import snapshot_evidence_digest

#: Every section the base document writes except the identity section.
_SECTIONS = ("binary", "build", "debug", "declarations", "layout", "types")
#: State -> whether it blocks that section's readers.
_STATES = {
    "present": False,
    "header_extensions": False,
    "must_understand_known": False,
    "must_understand_unknown": True,
    "newer_version": True,
    "failed": True,
    "not_collected": False,
}
#: Where an unknown key can sit, as a path from a section payload.
_NESTED = {
    "function": ("declarations", ("functions", 0)),
    "param": ("declarations", ("functions", 0, "params", 0)),
    "variable": ("declarations", ("variables", 0)),
    "enum": ("declarations", ("enums", 0)),
    "enum_member": ("declarations", ("enums", 0, "members", 0)),
    "record": ("types", ("types", 0)),
    "record_field": ("types", ("types", 0, "fields", 0)),
    "elf": ("binary", ("elf",)),
    "elf_symbol": ("binary", ("elf", "symbols", 0)),
    "elf_import": ("binary", ("elf", "imports", 0)),
}

_JSON = st.recursive(
    st.none() | st.booleans() | st.integers(-5, 5) | st.text("abc", max_size=3),
    lambda inner: (
        st.lists(inner, max_size=2)
        | st.dictionaries(st.text("xyz", min_size=1, max_size=2), inner, max_size=2)
    ),
    max_leaves=4,
)


def _base_document() -> dict[str, Any]:
    snap = AbiSnapshot(
        library="libcarrier.so.1",
        version="1.0",
        functions=[
            Function(
                name="f",
                mangled="f",
                return_type="int",
                params=[Param(name="a", type="int")],
            )
        ],
        variables=[Variable(name="v", mangled="v", type="int")],
        types=[
            RecordType(
                name="T", kind="struct", fields=[TypeField(name="x", type="int")]
            )
        ],
        enums=[EnumType(name="E", members=[EnumMember(name="E0", value=0)])],
        elf=ElfMetadata(
            soname="libcarrier.so.1",
            symbols=[ElfSymbol(name="f")],
            imports=[ElfImport(name="malloc")],
        ),
    )
    once = json.loads(snapshot_to_json(snap))
    # One load/save first: load-time backfill settles, so the base is a fixpoint.
    return json.loads(snapshot_to_json(snapshot_from_dict(once)))


_BASE = _base_document()


def _at(payload: dict[str, Any], path: tuple[Any, ...]) -> dict[str, Any]:
    node: Any = payload
    for step in path:
        node = node[step]
    assert isinstance(node, dict)
    return node


@st.composite
def _carriers(draw: st.DrawFn) -> tuple[dict[str, Any], frozenset[str]]:
    doc = copy.deepcopy(_BASE)
    blocked: set[str] = set()
    for location in draw(st.sets(st.sampled_from(sorted(_NESTED)))):
        section, path = _NESTED[location]
        _at(doc["sections"][section]["payload"], path)[f"x_{location}"] = draw(_JSON)
    for kind in _SECTIONS:
        state = draw(st.sampled_from(sorted(_STATES)))
        if _STATES[state]:
            blocked.add(kind)
        section = doc["sections"][kind]
        if state == "header_extensions":
            section["extensions"] = {"vendor.note": draw(_JSON)}
        elif state == "must_understand_known":
            section["must_understand"] = ["declarations"]
        elif state == "must_understand_unknown":
            section["must_understand"] = ["vendor.never"]
        elif state == "newer_version":
            section["section_schema_version"] += 1
            doc["section_schema_versions"][kind] += 1
        elif state in ("failed", "not_collected"):
            del doc["sections"][kind]
            if state == "not_collected":
                del doc["section_schema_versions"][kind]
            doc["section_availability"][kind] = {
                "diagnostics": ["extractor crashed"] if state == "failed" else [],
                "status": state,
            }
    for index in range(draw(st.integers(0, 2))):
        kind = f"x_future_{index}"
        doc["sections"][kind] = {"payload": draw(_JSON), "section_schema_version": 1}
        doc["section_schema_versions"][kind] = 1
        doc["section_availability"][kind] = {"diagnostics": [], "status": "present"}
    if draw(st.booleans()):
        doc["must_understand"] = ["types"]
    return doc, frozenset(blocked)


def _resaved(snap: AbiSnapshot) -> dict[str, Any]:
    return json.loads(snapshot_to_json(snap))


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(_carriers())
def test_carriers_round_trip_through_a_package(
    case: tuple[dict[str, Any], frozenset[str]],
) -> None:
    document, blocked = case
    loaded = snapshot_from_dict(copy.deepcopy(document))
    assert {kind for kind, _ in loaded.unreadable_sections} == blocked
    assert _resaved(loaded) == document
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "pkg"
        write_legacy_snapshot_package(
            loaded, root, artifact_id="a", max_known_schema_version=SCHEMA_VERSION
        )
        stored, ref = read_stored_snapshot_document(root)
        back = snapshot_from_dict(stored)
        ref_doc = json.loads(
            next((root / "refs" / "artifacts").rglob("*.json")).read_text()
        )
    assert {kind for kind, _ in back.unreadable_sections} == blocked
    assert _resaved(back) == document
    assert ref.evidence_digest == snapshot_evidence_digest(loaded)
    assert snapshot_evidence_digest(back) == snapshot_evidence_digest(loaded)
    # A ref carrying the carriers is written at the version an older reader
    # refuses; a clean one keeps version 1 (and its bytes).
    clean = document == _BASE
    assert (ref.carriers is None) == clean
    assert ref_doc["ref_schema_version"] == (1 if clean else 2)
    # No section the package left out is read back as present and empty.
    assert not blocked & set(ref.sections)


@pytest.mark.parametrize("kind", _SECTIONS)
@pytest.mark.parametrize("name", ["vendor.never", "x.other"])
def test_an_unknown_name_on_a_package_section_object_blocks_only_it(
    kind: str, name: str, tmp_path: Path
) -> None:
    """A section object in a package (as a newer writer would store it)
    whose header names an unknown must-understand extension blocks that
    section's readers, as in a single-file document -- not the package."""
    import dataclasses

    from abicheck.project_snapshot_store import (
        DirectoryObjectStore,
        write_project_manifest,
    )
    from abicheck.storage.package import ObjectRef
    from tests._project_manifest_reader import read_project_manifest

    root = tmp_path / "pkg"
    write_legacy_snapshot_package(
        snapshot_from_dict(copy.deepcopy(_BASE)),
        root,
        artifact_id="a",
        max_known_schema_version=SCHEMA_VERSION,
    )
    manifest = read_project_manifest(root)
    store = DirectoryObjectStore(root)
    (artifact,) = manifest.artifact_refs
    raw = dict(store.get(artifact.sections[kind].digest))
    raw["must_understand"] = [name]
    sections = dict(artifact.sections)
    sections[kind] = ObjectRef(kind=kind, digest=store.put(raw))
    write_project_manifest(
        root,
        dataclasses.replace(
            manifest,
            artifact_refs=(dataclasses.replace(artifact, sections=sections),),
        ),
    )
    stored, _ref = read_stored_snapshot_document(root)
    back = snapshot_from_dict(stored)
    assert [k for k, _ in back.unreadable_sections] == [kind]
    assert name in back.unreadable_sections[0][1]
    # Kept verbatim: a re-save writes the section with its name.
    assert _resaved(back)["sections"][kind]["must_understand"] == [name]


def test_an_unknown_name_on_the_identity_section_object_refuses(
    tmp_path: Path,
) -> None:
    import dataclasses

    from abicheck.project_snapshot_store import (
        DirectoryObjectStore,
        write_project_manifest,
    )
    from abicheck.storage.package import ObjectRef
    from tests._project_manifest_reader import read_project_manifest

    root = tmp_path / "pkg"
    write_legacy_snapshot_package(
        snapshot_from_dict(copy.deepcopy(_BASE)),
        root,
        artifact_id="a",
        max_known_schema_version=SCHEMA_VERSION,
    )
    manifest = read_project_manifest(root)
    store = DirectoryObjectStore(root)
    (artifact,) = manifest.artifact_refs
    raw = dict(store.get(artifact.sections["provenance"].digest))
    raw["must_understand"] = ["vendor.never"]
    sections = {
        **artifact.sections,
        "provenance": ObjectRef(kind="provenance", digest=store.put(raw)),
    }
    write_project_manifest(
        root,
        dataclasses.replace(
            manifest,
            artifact_refs=(dataclasses.replace(artifact, sections=sections),),
        ),
    )
    with pytest.raises(SnapshotError, match="vendor.never"):
        read_stored_snapshot_document(root)


def test_a_ref_v1_reader_refuses_a_ref_with_carriers(tmp_path: Path) -> None:
    """The fail-closed half of the version decision: a ref stating version 1
    while carrying `carriers` is corrupt, and a version this build does not
    read is refused (`package_publish.ref_record`)."""
    from abicheck.storage.package_publish import ref_record

    with pytest.raises(ValueError, match="need 2"):
        ref_record({"ref_schema_version": 1, "carriers": {}}, "ref")
    with pytest.raises(ValueError, match="reads 1 to 2"):
        ref_record({"ref_schema_version": 3}, "ref")
    assert ref_record({"ref_schema_version": 2, "carriers": {}}, "ref") == {
        "carriers": {}
    }
