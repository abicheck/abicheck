"""ADR-062 D14/D15 -- optional data is preserved, must-understand data
blocks only its readers, and section availability is stated.

Formerly `test_extension_preservation_gap.py`: strict `xfail` probes of the
gap (bug class `storage.wire_extension_dropped_or_refused`). Each probe is
now a regression test, run over every sibling it applies to (each
identity-matched record list, each known section kind) rather than the one
reported input, against oracles that are not the implementation's helpers:
the input document itself (byte-equivalence on re-save) and the registry's
own detector results.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.errors import IncompatibleSnapshotSchemaError
from abicheck.model import EnumMember, EnumType, Function, RecordType, Variable
from abicheck.model.snapshot import AbiSnapshot
from abicheck.serialization import snapshot_from_dict, snapshot_to_json
from abicheck.storage.dto import SECTION_SCHEMA_VERSIONS, SectionDTO
from abicheck.storage.package import PackageManifest
from abicheck.storage.snapshot_encode import snapshot_content_digest
from abicheck.storage.snapshot_evidence import snapshot_evidence_digest

_RECORD_LISTS = [
    ("declarations", "functions"),
    ("declarations", "variables"),
    ("types", "types"),
    ("declarations", "enums"),
]


def _snapshot(version: str = "1.0") -> AbiSnapshot:
    return AbiSnapshot(
        library="libext.so.1",
        version=version,
        functions=[
            Function(name="f", mangled="f", return_type="int"),
            Function(name="g", mangled="g", return_type="int"),
        ],
        variables=[Variable(name="v", mangled="v", type="int")],
        types=[RecordType(name="T", kind="struct")],
        enums=[EnumType(name="E", members=[EnumMember(name="E0", value=0)])],
        elf=ElfMetadata(soname="libext.so.1", symbols=[ElfSymbol(name="f")]),
    )


def _document(snap: AbiSnapshot | None = None) -> dict[str, Any]:
    return json.loads(snapshot_to_json(snap or _snapshot()))


def _resave(document: dict[str, Any]) -> dict[str, Any]:
    return json.loads(snapshot_to_json(snapshot_from_dict(copy.deepcopy(document))))


def _records(document: dict[str, Any], section: str, field: str) -> list[dict]:
    return document["sections"][section]["payload"][field]


# -- optional data is preserved ---------------------------------------------


@pytest.mark.parametrize(("section", "field"), _RECORD_LISTS)
@pytest.mark.parametrize(
    "extra",
    [
        {"x_future_note": "kept"},
        {"extensions": {"vendor.note": {"n": [1, 2]}}},
        {"x_a": None, "x_b": {"nested": True}},
    ],
)
def test_unknown_record_fields_survive_a_re_save(
    section: str, field: str, extra: dict[str, Any]
) -> None:
    """Oracle: re-saving the plain document, with the extras written into the
    same record -- the load's own backfills apply to both sides alike."""
    plain = _resave(_document())
    document = copy.deepcopy(plain)
    _records(document, section, field)[-1].update(extra)
    assert _resave(document)["sections"][section] == document["sections"][section]
    assert _resave(plain)["sections"] == plain["sections"]


def test_record_extras_follow_their_identity_not_their_position() -> None:
    """Re-save after the producer reordered records: the extra stays on the
    record it was read from."""
    document = _document()
    _records(document, "declarations", "functions")[1]["x_on_g"] = 1
    snap = snapshot_from_dict(copy.deepcopy(document))
    snap.declarations.functions.reverse()
    resaved = json.loads(snapshot_to_json(snap))
    by_name = {r["mangled"]: r for r in _records(resaved, "declarations", "functions")}
    assert by_name["g"].get("x_on_g") == 1
    assert "x_on_g" not in by_name["f"]


@pytest.mark.parametrize("payload", [{"note": "kept"}, {}, {"deep": [{"a": 1}]}])
def test_an_unknown_optional_section_is_kept_verbatim(payload: dict) -> None:
    document = _document()
    document["sections"]["x_future"] = {
        "section_kind": "x_future",
        "section_schema_version": 3,
        "payload": payload,
    }
    document["section_schema_versions"]["x_future"] = 3
    snap = snapshot_from_dict(copy.deepcopy(document))
    assert snap.unreadable_sections == ()
    resaved = json.loads(snapshot_to_json(snap))
    assert resaved["sections"]["x_future"] == document["sections"]["x_future"]
    assert resaved["section_schema_versions"]["x_future"] == 3


@pytest.mark.parametrize("section", ["binary", "declarations", "types", "debug"])
def test_header_extensions_and_understood_names_survive(section: str) -> None:
    document = _resave(_document())
    document["sections"][section]["extensions"] = {"vendor": {"k": "v"}}
    # A known section kind is understood: it never blocks.
    document["sections"][section]["must_understand"] = ["types"]
    snap = snapshot_from_dict(copy.deepcopy(document))
    assert snap.unreadable_sections == ()
    assert (
        json.loads(snapshot_to_json(snap))["sections"][section]
        == (document["sections"][section])
    )


def test_extensions_are_inside_the_evidence_and_publication_digests() -> None:
    plain = _document()
    a, b = copy.deepcopy(plain), copy.deepcopy(plain)
    _records(a, "declarations", "functions")[0]["extensions"] = {"k": 1}
    _records(b, "declarations", "functions")[0]["extensions"] = {"k": 2}
    snaps = [snapshot_from_dict(copy.deepcopy(d)) for d in (plain, a, b)]
    evidence = {snapshot_evidence_digest(s) for s in snaps}
    publication = {snapshot_content_digest(s) for s in snaps}
    assert len(evidence) == 3
    assert len(publication) == 3


def test_a_snapshot_without_extensions_keeps_its_section_bytes() -> None:
    document = _resave(_document())
    assert _resave(document)["sections"] == document["sections"]


# -- must-understand data blocks only its readers ----------------------------


_BLOCKABLE = sorted(
    {"binary", "build", "debug", "declarations", "layout", "types"}
    & set(SECTION_SCHEMA_VERSIONS)
)


@pytest.mark.parametrize("section", _BLOCKABLE)
def test_an_unknown_must_understand_name_blocks_exactly_that_section(
    section: str,
) -> None:
    document = _document()
    document["sections"][section]["must_understand"] = ["x_layout_v2"]
    snap = snapshot_from_dict(copy.deepcopy(document))
    assert [kind for kind, _ in snap.unreadable_sections] == [section]
    assert "'x_layout_v2'" in snap.unreadable_sections[0][1]
    # Kept verbatim and written back unchanged, header included.
    resaved = json.loads(snapshot_to_json(snap))
    assert resaved["sections"][section] == document["sections"][section]


def test_an_unknown_must_understand_section_kind_blocks_its_dependent() -> None:
    document = _document()
    document["sections"]["x_future"] = {
        "section_kind": "x_future",
        "section_schema_version": 1,
        "payload": {"layout": "v2"},
    }
    document["section_schema_versions"]["x_future"] = 1
    document["sections"]["types"]["must_understand"] = ["x_future"]
    snap = snapshot_from_dict(copy.deepcopy(document))
    assert [kind for kind, _ in snap.unreadable_sections] == ["types"]
    assert snap.declarations.types == []
    assert len(snap.declarations.functions) == 2


def test_the_identity_section_cannot_be_blocked() -> None:
    document = _document()
    document["sections"]["provenance"]["must_understand"] = ["x_identity_v2"]
    with pytest.raises(IncompatibleSnapshotSchemaError, match="x_identity_v2"):
        snapshot_from_dict(document)


@pytest.mark.parametrize("names", [["x_whole"], ["types", "x_whole"]])
def test_an_unknown_document_level_name_refuses_the_document(names: list) -> None:
    document = _document()
    document["must_understand"] = names
    with pytest.raises(IncompatibleSnapshotSchemaError, match="x_whole"):
        snapshot_from_dict(document)


def test_an_understood_document_level_name_is_kept() -> None:
    document = _document()
    document["must_understand"] = ["types"]
    assert _resave(document)["must_understand"] == ["types"]


def test_a_blocked_section_disables_exactly_its_readers() -> None:
    """Through the registry: every detector reading `types` is not
    evaluated with the diagnostic; every other detector runs."""
    from abicheck.compare.section_readers import DETECTOR_SECTIONS
    from abicheck.detector_registry import registry

    document = _document()
    document["sections"]["types"]["must_understand"] = ["x_layout_v2"]
    new = snapshot_from_dict(document)
    old = snapshot_from_dict(_document())
    _, results = registry.run_all(old, new)
    for result in results:
        reads_types = "types" in DETECTOR_SECTIONS[result.name]
        blocked = "not comparable" in (result.coverage_gap or "")
        assert blocked == reads_types, result.name
        if blocked:
            assert result.not_evaluated and not result.enabled
            assert "x_layout_v2" in (result.coverage_gap or "")


# -- D15: section availability -------------------------------------------------


def _failed(document: dict[str, Any], section: str) -> dict[str, Any]:
    document["sections"].pop(section)
    document["section_availability"][section] = {
        "diagnostics": ["dwarf: truncated .debug_info"],
        "status": "failed",
    }
    return document


def test_every_written_section_states_present() -> None:
    document = _document()
    assert document["section_availability"] == {
        kind: {"diagnostics": [], "status": "present"} for kind in document["sections"]
    }


@pytest.mark.parametrize("section", _BLOCKABLE)
def test_a_failed_section_reads_as_failed_not_empty(section: str) -> None:
    document = _failed(_document(), section)
    snap = snapshot_from_dict(copy.deepcopy(document))
    assert [kind for kind, _ in snap.unreadable_sections] == [section]
    assert "truncated .debug_info" in snap.unreadable_sections[0][1]
    resaved = json.loads(snapshot_to_json(snap))
    assert section not in resaved["sections"]
    assert resaved["section_availability"][section]["status"] == "failed"
    assert (
        resaved["section_schema_versions"][section]
        == (document["section_schema_versions"][section])
    )


def test_a_reader_without_the_availability_table_refuses_a_failed_section() -> None:
    """What a pre-D15 reader sees: the failed section is listed but absent,
    so its missing-section check refuses -- never an empty section."""
    document = _failed(_document(), "types")
    document.pop("section_availability")
    with pytest.raises(ValueError, match="missing section"):
        snapshot_from_dict(document)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d["section_availability"].__setitem__(
            "types", {"diagnostics": ["x"], "status": "failed"}
        ),  # failed but carries a payload
        lambda d: _failed(d, "types")["section_availability"]["types"].__setitem__(
            "diagnostics", []
        ),  # failed without diagnostics
        lambda d: _failed(d, "types")["section_schema_versions"].pop("types"),
        lambda d: d["section_availability"].__setitem__(
            "types", {"diagnostics": [], "status": "maybe"}
        ),
    ],
)
def test_contradictory_availability_is_refused(mutate) -> None:
    document = _document()
    mutate(document)
    with pytest.raises(ValueError):
        snapshot_from_dict(document)


def test_the_producer_records_a_failed_section() -> None:
    from abicheck.storage.wire_extensions import record_section_failure

    snap = _snapshot()
    record_section_failure(snap, "debug", ["dwarf: unreadable"])
    document = json.loads(snapshot_to_json(snap))
    assert "debug" not in document["sections"]
    assert document["section_availability"]["debug"] == {
        "diagnostics": ["dwarf: unreadable"],
        "status": "failed",
    }
    reloaded = snapshot_from_dict(document)
    assert [kind for kind, _ in reloaded.unreadable_sections] == ["debug"]


@pytest.mark.parametrize("section", ["provenance", "x_unknown"])
def test_the_producer_refuses_an_unfailable_section(section: str) -> None:
    from abicheck.storage.wire_extensions import record_section_failure

    with pytest.raises(ValueError):
        record_section_failure(_snapshot(), section, ["x"])


# -- carriers on the DTO and the package manifest -----------------------------


def test_section_dto_carries_must_understand_and_extensions() -> None:
    dto = SectionDTO("types", 1, {"types": []}, ("x_a",), {"k": [1]})
    assert SectionDTO.from_dict(dto.to_dict()) == dto
    plain = SectionDTO("types", 1, {"types": []})
    assert set(plain.to_dict()) == {"section_kind", "section_schema_version", "payload"}


def test_manifest_must_understand_round_trips_and_refuses_unknown(tmp_path) -> None:
    from abicheck.project_snapshot_store import (
        read_manifest_summary,
        write_project_manifest,
    )

    manifest = PackageManifest(must_understand=("types",))
    assert PackageManifest.from_dict(manifest.to_dict()) == manifest
    assert "must_understand" not in PackageManifest().to_dict()
    write_project_manifest(tmp_path, manifest)
    read_manifest_summary(tmp_path)
    write_project_manifest(tmp_path, PackageManifest(must_understand=("x_pkg",)))
    with pytest.raises(IncompatibleSnapshotSchemaError, match="x_pkg"):
        read_manifest_summary(tmp_path)
