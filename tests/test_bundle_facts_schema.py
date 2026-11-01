"""The published ``BundleFacts`` schemas describe what abicheck writes and
refuse what abicheck's reader refuses.

Known gap "``BundleFacts`` (and its G40 archive container) has no published
JSON Schema" (closed 2026-11-01). Two documents, two schemas:
``bundle_facts.schema.json`` (the single-file JSON document ``dump RELEASE
--bundle-facts-out`` writes) and ``bundle_facts_archive_manifest.schema.json``
(``manifest.json`` inside the G40 zip form).

Bug class: a persisted container with no machine contract, so its shape can
drift from its reader unnoticed. The invariant, checked from both ends:

* **Real output validates.** Every document the writers produce -- the CLI
  capture over several release shapes, a degraded member (schema 3), a
  recorded public surface and instantiation manifest (schema 4), in both the
  JSON and the archive form -- validates against its schema.
* **The schema refuses what the reader refuses.** Each top-level mutation
  below is one the reader (``bundle_facts_from_dict`` /
  ``read_bundle_facts_archive``, the oracle) rejects; the schema must reject
  it too, and the unmutated document must pass both.
* **Versions cannot drift.** The schemas' ``maximum`` versions are the
  reader constants.
"""

from __future__ import annotations

import copy
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from abicheck.bundle_manifest import InstantiationManifest, ManifestEntry
from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function, Variable, Visibility
from abicheck.model.bundle_facts import BUNDLE_FACTS_SCHEMA_VERSION, BundleFacts
from abicheck.model.release_surface import PublicObligation, ReleasePublicSurface
from abicheck.schemas.documents import (
    load_bundle_facts_archive_manifest_schema,
    load_bundle_facts_schema,
)
from abicheck.serialization import snapshot_to_json
from abicheck.storage.bundle_facts_archive import BUNDLE_ARCHIVE_SCHEMA_VERSION
from abicheck.storage.bundle_facts_codec import (
    bundle_facts_from_dict,
    bundle_facts_to_dict,
    load_bundle_facts,
    save_bundle_facts,
)
from tests.schema_validation import requires_jsonschema, validator_for

pytestmark = requires_jsonschema


def _fn(name: str) -> Function:
    return Function(
        name=name, mangled=name, return_type="int", visibility=Visibility.PUBLIC
    )


def _snap(lib: str, *, functions=(), variables=()) -> AbiSnapshot:
    return AbiSnapshot(
        library=f"{lib}.so",
        version="1",
        functions=list(functions),
        variables=list(variables),
        from_headers=True,
    )


#: Release shapes the CLI capture is run over.
_RELEASES: dict[str, dict[str, AbiSnapshot]] = {
    "one-member": {"liba": _snap("liba", functions=[_fn("a")])},
    "three-members": {
        "liba": _snap("liba", functions=[_fn("a")]),
        "libb": _snap(
            "libb",
            variables=[
                Variable(
                    name="v", mangled="v", type="int", visibility=Visibility.PUBLIC
                )
            ],
        ),
        "libc": _snap("libc"),
    },
}


def _errors(schema: dict[str, Any], document: Any) -> list[str]:
    return [e.message for e in validator_for(schema).iter_errors(document)]


def _cli_capture(tmp_path: Path, members: dict[str, AbiSnapshot]) -> Path:
    root = tmp_path / "release"
    root.mkdir()
    for name, snap in members.items():
        (root / f"{name}.json").write_text(snapshot_to_json(snap), encoding="utf-8")
    out = tmp_path / "facts.json"
    result = CliRunner().invoke(
        main, ["dump", str(root), "--bundle-facts-out", str(out)]
    )
    assert result.exit_code == 0, result.output
    return out


def _archive_manifest(path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(path) as zf:
        return json.loads(zf.read("manifest.json"))


def _schema4_facts() -> BundleFacts:
    """Every optional block present: degraded member, public surface,
    instantiation manifest, aliases."""
    return BundleFacts(
        per_library_snapshots={
            "liba.so": _snap("liba", functions=[_fn("a")]),
            "libb.so": _snap("libb"),
        },
        filesystem_aliases={"liba.so": ("liba.so.1",)},
        library_filenames={"liba.so": "liba.so.1.2", "libb.so": "libb.so"},
        degraded_members={"libb.so": "ELF-only degraded capture: test"},
        inventory_complete=True,
        public_surface=ReleasePublicSurface(
            acquisition_key="k",
            side="old",
            obligations=(
                PublicObligation("a", "a", "function", "a.h:1"),
                PublicObligation("b", "b", "variable", outside_declared_surface=True),
            ),
            declared_symbols=frozenset({"a", "b"}),
            header_count=1,
            type_names=("T",),
        ),
        manifest=InstantiationManifest(
            entries=(ManifestEntry(library="liba.so", symbol="a"),)
        ),
    )


# -- real output validates ---------------------------------------------------


@pytest.mark.parametrize("shape", sorted(_RELEASES))
def test_cli_capture_validates_in_both_forms(tmp_path: Path, shape: str) -> None:
    captured = _cli_capture(tmp_path, _RELEASES[shape])
    document = json.loads(captured.read_text(encoding="utf-8"))
    assert _errors(load_bundle_facts_schema(), document) == []

    archive = tmp_path / "facts.zip"
    save_bundle_facts(load_bundle_facts(captured), archive, format="archive")
    manifest = _archive_manifest(archive)
    assert _errors(load_bundle_facts_archive_manifest_schema(), manifest) == []


def test_cli_capture_with_a_degraded_member_validates(tmp_path: Path) -> None:
    root = tmp_path / "release"
    root.mkdir()
    (root / "liba.json").write_text(
        snapshot_to_json(_snap("liba", functions=[_fn("a")])), encoding="utf-8"
    )
    (root / "libbad.so").write_bytes(b"\x7fELF not really an ELF file")
    out = tmp_path / "facts.json"
    result = CliRunner().invoke(
        main, ["dump", str(root), "--bundle-facts-out", str(out)]
    )
    assert result.exit_code == 0, result.output
    document = json.loads(out.read_text(encoding="utf-8"))
    assert document["schema_version"] == 3 and document["degraded_members"]
    assert _errors(load_bundle_facts_schema(), document) == []


@pytest.mark.parametrize("form", ["json", "archive"])
def test_every_optional_block_validates(tmp_path: Path, form: str) -> None:
    path = tmp_path / ("facts.json" if form == "json" else "facts.zip")
    save_bundle_facts(_schema4_facts(), path, format=form)
    if form == "json":
        document = json.loads(path.read_text(encoding="utf-8"))
        assert document["schema_version"] == 4
        assert _errors(load_bundle_facts_schema(), document) == []
    else:
        manifest = _archive_manifest(path)
        assert manifest["bundle_facts_schema_version"] == 4
        assert "public_surface_blob" in manifest
        assert _errors(load_bundle_facts_archive_manifest_schema(), manifest) == []
    # The writer's own reader accepts what the schema accepted.
    assert load_bundle_facts(path).public_surface is not None


# -- the schema refuses what the reader refuses -------------------------------


def _drop(key: str):
    return lambda d: d.pop(key)


def _set(key: str, value: Any):
    return lambda d: d.__setitem__(key, value)


#: Mutations of a valid schema-4 document that ``bundle_facts_from_dict``
#: rejects. Each is applied to a fresh copy.
_DOCUMENT_MUTATIONS = {
    "missing per_library_snapshots": _drop("per_library_snapshots"),
    "per_library_snapshots not a mapping": _set("per_library_snapshots", []),
    "wrong artifact_type": _set("artifact_type", "abicheck.snapshot"),
    "schema 2+ without artifact_type": _drop("artifact_type"),
    "schema newer than the reader": _set(
        "schema_version", BUNDLE_FACTS_SCHEMA_VERSION + 1
    ),
    "artifact_type on a schema-1 document": _set("schema_version", 1),
    "degraded marker under schema 2": lambda d: (
        d.pop("public_surface"),
        d.__setitem__("schema_version", 2),
    ),
    "public surface under schema 3": _set("schema_version", 3),
    "non-string variant_fingerprint": _set("variant_fingerprint", 1),
    "null variant_fingerprint": _set("variant_fingerprint", None),
    "alias list of non-strings": _set("filesystem_aliases", {"liba.so": [1]}),
    "filename not a string": _set("library_filenames", {"liba.so": 7}),
    "degraded reason not a string": _set("degraded_members", {"libb.so": 1}),
    "non-boolean inventory_complete": _set("inventory_complete", "yes"),
    "public_surface not a mapping": _set("public_surface", ["k"]),
    "manifest without provides": _set("manifest", {}),
}


def _schema4_document() -> dict[str, Any]:
    return bundle_facts_to_dict(_schema4_facts())


def test_the_unmutated_document_passes_schema_and_reader() -> None:
    document = _schema4_document()
    assert _errors(load_bundle_facts_schema(), document) == []
    bundle_facts_from_dict(copy.deepcopy(document))


@pytest.mark.parametrize("mutation", sorted(_DOCUMENT_MUTATIONS))
def test_schema_rejects_what_the_reader_rejects(mutation: str) -> None:
    document = _schema4_document()
    _DOCUMENT_MUTATIONS[mutation](document)
    with pytest.raises(Exception):  # noqa: B017,PT011 -- the oracle: any refusal
        bundle_facts_from_dict(copy.deepcopy(document))
    assert _errors(load_bundle_facts_schema(), document), (
        f"schema accepted a document the reader refuses ({mutation})"
    )


#: Mutations of a valid archive manifest that ``read_bundle_facts_archive``
#: rejects.
_ARCHIVE_MUTATIONS = {
    "missing artifact_type": _drop("artifact_type"),
    "wrong artifact_type": _set("artifact_type", "abicheck.bundle-facts"),
    "missing schema_version": _drop("schema_version"),
    "missing bundle_facts_schema_version": _drop("bundle_facts_schema_version"),
    "archive schema newer than the reader": _set(
        "schema_version", BUNDLE_ARCHIVE_SCHEMA_VERSION + 1
    ),
    "bundle facts schema newer than the reader": _set(
        "bundle_facts_schema_version", BUNDLE_FACTS_SCHEMA_VERSION + 1
    ),
    "string schema_version": _set("schema_version", "1"),
    "missing library_blobs": _drop("library_blobs"),
    "library_blobs not a mapping": _set("library_blobs", []),
    "blob hash not a string": lambda d: d["library_blobs"].__setitem__("liba.so", 1),
}


def _rewrite_manifest(src: Path, dst: Path, manifest: dict[str, Any]) -> None:
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w") as zout:
        for item in zin.infolist():
            data = (
                json.dumps(manifest).encode()
                if item.filename == "manifest.json"
                else zin.read(item.filename)
            )
            zout.writestr(item, data)


@pytest.mark.parametrize("mutation", sorted(_ARCHIVE_MUTATIONS))
def test_archive_schema_rejects_what_the_reader_rejects(
    tmp_path: Path, mutation: str
) -> None:
    archive = tmp_path / "facts.zip"
    save_bundle_facts(_schema4_facts(), archive, format="archive")
    manifest = _archive_manifest(archive)
    _ARCHIVE_MUTATIONS[mutation](manifest)
    mutated = tmp_path / "mutated.zip"
    _rewrite_manifest(archive, mutated, manifest)
    with pytest.raises(Exception):  # noqa: B017,PT011 -- the oracle: any refusal
        load_bundle_facts(mutated, format="archive")
    assert _errors(load_bundle_facts_archive_manifest_schema(), manifest), (
        f"schema accepted a manifest the reader refuses ({mutation})"
    )


# -- versions cannot drift ----------------------------------------------------


def test_schema_maxima_are_the_reader_constants() -> None:
    document = load_bundle_facts_schema()
    archive = load_bundle_facts_archive_manifest_schema()
    assert (
        document["properties"]["schema_version"]["maximum"]
        == BUNDLE_FACTS_SCHEMA_VERSION
    )
    assert (
        archive["properties"]["schema_version"]["maximum"]
        == BUNDLE_ARCHIVE_SCHEMA_VERSION
    )
    assert (
        archive["properties"]["bundle_facts_schema_version"]["maximum"]
        == BUNDLE_FACTS_SCHEMA_VERSION
    )
