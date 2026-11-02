# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""A section a newer writer stored in a ProjectSnapshot package blocks only
that section, exactly as in a single-file document (ADR-062 D14).

Exhaustive over section kind x cause (a newer ``section_schema_version``, an
unknown must-understand name), the package written the way a newer writer
would write it: the section object itself carries the cause inside
``ArtifactRef.sections`` and the manifest states the newer version. The
oracle is the single-file document with the same mutation, compared through
the real ``abicheck compare`` CLI: same blocked detectors, same cause text,
same findings, same exit code. A re-save keeps the section byte-for-byte;
the identity section still refuses in both forms.

Bug class ``storage.wire_extension_dropped_or_refused``.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.compare.section_readers import DETECTOR_SECTIONS
from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.errors import SnapshotError
from abicheck.model import Function, RecordType, TypeField
from abicheck.model.snapshot import AbiSnapshot
from abicheck.project_snapshot_legacy import (
    read_stored_snapshot_document,
    write_legacy_snapshot_package,
)
from abicheck.project_snapshot_store import DirectoryObjectStore, write_project_manifest
from abicheck.serialization import SCHEMA_VERSION, snapshot_from_dict, snapshot_to_json
from abicheck.storage.package import ObjectRef
from tests._project_manifest_reader import read_project_manifest

_SECTIONS = ("binary", "build", "debug", "declarations", "layout", "types")
_CAUSES = ("newer_version", "must_understand")


def _snap(version: str, symbols: list[str]) -> AbiSnapshot:
    return AbiSnapshot(
        library="libm.so.1",
        version=version,
        functions=[Function(name=n, mangled=n, return_type="int") for n in symbols],
        types=[
            RecordType(
                name="T",
                kind="struct",
                size_bits=32,
                fields=[TypeField(name="a", type="int", offset_bits=0)],
            )
        ],
        elf=ElfMetadata(
            soname="libm.so.1", symbols=[ElfSymbol(name=n) for n in symbols]
        ),
    )


def _mutate_section(raw: dict, cause: str) -> dict:
    out = dict(raw)
    if cause == "newer_version":
        out["section_schema_version"] += 1
    else:
        out["must_understand"] = ["vendor.never"]
    return out


def _file(
    tmp_path: Path, name: str, snap: AbiSnapshot, section: str | None, cause: str
) -> Path:
    document = json.loads(snapshot_to_json(snap))
    if section is not None:
        document["sections"][section] = _mutate_section(
            document["sections"][section], cause
        )
        document["section_schema_versions"][section] = document["sections"][section][
            "section_schema_version"
        ]
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _package(
    tmp_path: Path, name: str, snap: AbiSnapshot, section: str | None, cause: str
) -> Path:
    """Written as a newer writer would: the cause is on the stored object."""
    root = tmp_path / name
    write_legacy_snapshot_package(
        snap, root, artifact_id="libm", max_known_schema_version=SCHEMA_VERSION
    )
    if section is None:
        return root
    manifest = read_project_manifest(root)
    store = DirectoryObjectStore(root)
    (artifact,) = manifest.artifact_refs
    raw = _mutate_section(dict(store.get(artifact.sections[section].digest)), cause)
    sections = {
        **artifact.sections,
        section: ObjectRef(kind=section, digest=store.put(raw)),
    }
    versions = dataclasses.replace(
        manifest.versions,
        section_schema_versions={
            **manifest.versions.section_schema_versions,
            section: raw["section_schema_version"],
        },
    )
    write_project_manifest(
        root,
        dataclasses.replace(
            manifest,
            versions=versions,
            artifact_refs=(dataclasses.replace(artifact, sections=sections),),
        ),
    )
    return root


def _compare(tmp_path: Path, old: Path, new: Path) -> tuple[int, dict]:
    report_path = tmp_path / f"{old.stem}-{new.stem}.r.json"
    result = CliRunner().invoke(
        main, ["compare", str(old), str(new), "-o", f"json={report_path}"]
    )
    return result.exit_code, json.loads(report_path.read_text())


def _observed(exit_code: int, report: dict) -> tuple:
    gaps = sorted(
        (d["name"], d["coverage_gap"])
        for d in report["detectors"]
        if (d.get("coverage_gap") or "").startswith("not comparable")
    )
    changes = sorted((c["kind"], c.get("symbol")) for c in report["changes"])
    return exit_code, gaps, changes, report["analysis_assurance"]["status"]


@pytest.mark.parametrize("side", ["old", "new"])
@pytest.mark.parametrize("cause", _CAUSES)
@pytest.mark.parametrize("section", _SECTIONS)
def test_package_blocks_like_the_single_file_document(
    tmp_path: Path, section: str, cause: str, side: str
) -> None:
    snaps = {"old": _snap("1", ["f", "g"]), "new": _snap("2", ["f"])}
    results = {}
    for form, build in (("file", _file), ("package", _package)):
        d = tmp_path / form
        d.mkdir()
        paths = {
            s: build(d, s, snaps[s], section if s == side else None, cause)
            for s in ("old", "new")
        }
        results[form] = _observed(*_compare(d, paths["old"], paths["new"]))
    assert results["package"] == results["file"]
    exit_code, gaps, _changes, status = results["file"]
    # The file form is itself checked against the declared reads.
    assert {name for name, _gap in gaps} == {
        n for n, reads in DETECTOR_SECTIONS.items() if section in reads
    }
    assert all(f"'{section}'" in gap for _name, gap in gaps)
    assert status == "partial"
    assert exit_code in (0, 1, 4)


@pytest.mark.parametrize("cause", _CAUSES)
@pytest.mark.parametrize("section", _SECTIONS)
def test_resave_keeps_the_blocked_section_exactly(
    tmp_path: Path, section: str, cause: str
) -> None:
    root = _package(tmp_path, "pkg", _snap("1", ["f"]), section, cause)
    manifest = read_project_manifest(root)
    stored_object = DirectoryObjectStore(root).get(
        manifest.artifact_refs[0].sections[section].digest
    )
    loaded = snapshot_from_dict(read_stored_snapshot_document(root)[0])
    assert [k for k, _ in loaded.unreadable_sections] == [section]
    # Single-file re-save: the section is the stored object, byte-for-byte.
    document = json.loads(snapshot_to_json(loaded))
    assert json.dumps(document["sections"][section], sort_keys=True) == json.dumps(
        stored_object, sort_keys=True
    )
    assert (
        document["section_schema_versions"][section]
        == stored_object["section_schema_version"]
    )
    # Package re-save, read back: same blocked set, same section, stable.
    again = tmp_path / "again"
    write_legacy_snapshot_package(
        loaded, again, artifact_id="libm", max_known_schema_version=SCHEMA_VERSION
    )
    back = snapshot_from_dict(read_stored_snapshot_document(again)[0])
    assert back.unreadable_sections == loaded.unreadable_sections
    assert json.loads(snapshot_to_json(back)) == document


@pytest.mark.parametrize("cause", _CAUSES)
@pytest.mark.parametrize("form", ["file", "package"])
def test_a_blocked_identity_section_refuses(
    tmp_path: Path, form: str, cause: str
) -> None:
    build = _file if form == "file" else _package
    bad = build(tmp_path, "bad", _snap("1", ["f"]), "provenance", cause)
    good = _file(tmp_path, "good", _snap("1", ["f"]), None, cause)
    result = CliRunner().invoke(main, ["compare", str(good), str(bad)])
    assert result.exit_code != 0
    assert "Upgrade abicheck" in result.output
    if form == "package":
        with pytest.raises(SnapshotError, match="provenance"):
            read_stored_snapshot_document(bad)
