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

"""A stored snapshot's per-section schema versions are checked against this
build's registry before any section is decoded (regression T5).

Before the gate, `{"section_schema_versions": {"binary": 99}}` on an otherwise
current v1 `binary` section loaded silently and compared: the migration chain
only ever sees the section DTO's own version, and that one was current, so the
inventory the writer advertised was never read back. The top-level
`schema_version` was refused with an "upgrade abicheck" error; the per-section
inventory now gets the same treatment, through the public load path.

The section kinds are not hand-listed: they come from
`dto.SECTION_SCHEMA_VERSIONS` (the registry), and the encoder's own output is
asserted to cover exactly that set, so a new section kind cannot escape the
parametrization.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from abicheck.errors import IncompatibleSnapshotSchemaError
from abicheck.serialization import SCHEMA_VERSION, snapshot_from_dict, snapshot_to_dict
from abicheck.storage.canonical import canonical_form
from abicheck.storage.dto import (
    BASELINE_SET_SECTION_KIND,
    BUNDLE_COMPOSITION_SECTION_KIND,
    SECTION_SCHEMA_VERSIONS,
    SEMANTIC_IR_SECTION_KIND,
)
from abicheck.storage.sectioned_document import (
    SECTION_SCHEMA_VERSIONS_KEY,
    SECTIONS_KEY,
    to_sectioned_document,
)

# The committed fixture is a real sectioned dump (schema 48); loading it warns
# that it predates this build -- the documented, expected older-schema path.
pytestmark = [
    pytest.mark.filterwarnings("ignore:Snapshot schema_version"),
    pytest.mark.filterwarnings("ignore:This snapshot carries facts preserved"),
]

_FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "header_graph"
    / "case09_cpp_vtable__v2.json"
)

# Variant-level kinds live in a bundle/baseline-set package, never in one
# snapshot's own sections, so the snapshot encoder never writes them.
_VARIANT_LEVEL_KINDS = frozenset(
    {BUNDLE_COMPOSITION_SECTION_KIND, BASELINE_SET_SECTION_KIND}
)
_SNAPSHOT_KINDS = tuple(sorted(set(SECTION_SCHEMA_VERSIONS) - _VARIANT_LEVEL_KINDS))


def _snapshot_flat_document() -> dict:
    """A real snapshot as the flat legacy document the encoder consumes."""
    return snapshot_to_dict(
        snapshot_from_dict(json.loads(_FIXTURE.read_text(encoding="utf-8")))
    )


def _current_document() -> dict:
    """The encoder's own current-version sectioned output, for a real snapshot."""
    return to_sectioned_document(
        _snapshot_flat_document(), max_known_schema_version=SCHEMA_VERSION
    )


def _load(document: dict):
    """The public load path; a copy, because a load must not own the caller's dict."""
    return snapshot_from_dict(copy.deepcopy(document))


def test_encoder_writes_exactly_the_registered_snapshot_kinds() -> None:
    """The parametrization below is only as complete as this: every
    snapshot-level registry kind must be a section the encoder really writes."""
    written = set(_current_document()[SECTIONS_KEY])
    assert written == set(_SNAPSHOT_KINDS)
    assert SEMANTIC_IR_SECTION_KIND in written


@pytest.mark.parametrize("kind", _SNAPSHOT_KINDS)
def test_current_version_of_every_section_loads(kind: str) -> None:
    document = _current_document()
    assert document[SECTION_SCHEMA_VERSIONS_KEY][kind] == SECTION_SCHEMA_VERSIONS[kind]
    expected = canonical_form(_snapshot_flat_document())
    assert canonical_form(snapshot_to_dict(_load(document))) == expected


@pytest.mark.parametrize("kind", _SNAPSHOT_KINDS)
def test_inventory_claiming_a_too_new_section_version_is_refused(kind: str) -> None:
    """The reported case (D5): the inventory says 99 for a section whose own
    DTO is current. Refused through the public load path, with the same
    upgrade wording as the top-level `schema_version` check."""
    document = _current_document()
    newer = SECTION_SCHEMA_VERSIONS[kind] + 1
    document[SECTION_SCHEMA_VERSIONS_KEY][kind] = newer
    with pytest.raises(
        IncompatibleSnapshotSchemaError, match="Upgrade abicheck"
    ) as info:
        _load(document)
    assert kind in str(info.value)
    assert str(newer) in str(info.value)


@pytest.mark.parametrize("kind", _SNAPSHOT_KINDS)
def test_section_written_at_a_too_new_version_is_refused(kind: str) -> None:
    """A writer newer than this build stamps both the inventory and the
    section's own DTO with the new version: still refused, not decoded."""
    document = _current_document()
    newer = SECTION_SCHEMA_VERSIONS[kind] + 1
    document[SECTION_SCHEMA_VERSIONS_KEY][kind] = newer
    document[SECTIONS_KEY][kind]["section_schema_version"] = newer
    with pytest.raises(IncompatibleSnapshotSchemaError, match="Upgrade abicheck"):
        _load(document)


@pytest.mark.parametrize("kind", _SNAPSHOT_KINDS)
def test_inventory_disagreeing_with_the_section_is_refused(kind: str) -> None:
    """A valid inventory entry against a section DTO stamped with a different
    version: a document that contradicts itself is corrupt, whichever side is
    the one that was hand-edited."""
    document = _current_document()
    document[SECTIONS_KEY][kind]["section_schema_version"] = 0
    with pytest.raises(ValueError, match="section_schema_version"):
        _load(document)


@pytest.mark.parametrize("kind", _SNAPSHOT_KINDS)
def test_older_version_with_no_registered_migration_is_refused(kind: str) -> None:
    """An older section version is only readable through a registered
    migration step. Today every kind but semantic_ir is at version 1, so the
    only older value is 0, which `SectionDTO` refuses as not a positive int
    before any migration is consulted; a positive older version with no step
    is refused by `migrate_section_dto` (covered in test_dto.py). Either way
    the load fails loudly rather than decoding the section as current."""
    document = _current_document()
    document[SECTION_SCHEMA_VERSIONS_KEY][kind] = 0
    document[SECTIONS_KEY][kind]["section_schema_version"] = 0
    with pytest.raises(ValueError, match="positive int|registered migration"):
        _load(document)


def test_committed_v1_semantic_ir_section_migrates_to_the_current_version() -> None:
    """The one older section version with a registered migration: the
    committed sectioned fixture carries `semantic_ir` at v1. It must load
    through the chain and come back re-encoded at the current version."""
    raw = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    assert raw[SECTION_SCHEMA_VERSIONS_KEY][SEMANTIC_IR_SECTION_KIND] == 1
    snapshot = _load(raw)
    reencoded = to_sectioned_document(
        snapshot_to_dict(snapshot), max_known_schema_version=SCHEMA_VERSION
    )
    assert (
        reencoded[SECTION_SCHEMA_VERSIONS_KEY][SEMANTIC_IR_SECTION_KIND]
        == SECTION_SCHEMA_VERSIONS[SEMANTIC_IR_SECTION_KIND]
        == 2
    )


def _write_directory_package(root: Path) -> None:
    from abicheck.project_snapshot_legacy import write_legacy_snapshot_package

    flat = _snapshot_flat_document()
    write_legacy_snapshot_package(
        flat, root, artifact_id=flat["library"], max_known_schema_version=SCHEMA_VERSION
    )


def test_directory_package_at_current_versions_reads(tmp_path: Path) -> None:
    from abicheck.project_snapshot_legacy import read_legacy_snapshot_document

    root = tmp_path / "pkg"
    _write_directory_package(root)
    rebuilt = read_legacy_snapshot_document(root)
    assert canonical_form(rebuilt) == canonical_form(_snapshot_flat_document())


@pytest.mark.parametrize("kind", _SNAPSHOT_KINDS)
def test_directory_manifest_claiming_a_too_new_section_is_refused(
    tmp_path: Path, kind: str
) -> None:
    """The directory-backed reader reads the same inventory from `manifest.json`
    (`versions.section_schema_versions`), so the same claim is refused there
    too, before any section is exported."""
    from abicheck.project_snapshot_legacy import read_legacy_snapshot_document

    root = tmp_path / "pkg"
    _write_directory_package(root)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    inventory = manifest["versions"]["section_schema_versions"]
    assert inventory[kind] == SECTION_SCHEMA_VERSIONS[kind]
    inventory[kind] = SECTION_SCHEMA_VERSIONS[kind] + 1
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(IncompatibleSnapshotSchemaError, match="Upgrade abicheck"):
        read_legacy_snapshot_document(root)
