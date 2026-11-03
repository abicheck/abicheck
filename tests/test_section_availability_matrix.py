# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Every section-availability state x section x side, through write -> read
-> compare, for stored and live operands (ADR-062 D15).

The states a reader must tell apart (`model/section_availability.py`):

* ``present`` -- the section as written, identical on both sides;
* ``empty`` -- collected, with an empty payload: its emptiness is an
  observation, so the comparison reports what it lost;
* ``not_collected``, ``failed``, ``blocked`` -- its fields would hold
  defaults, so exactly the analyses reading it are not comparable, nothing
  is reported from it, and the run reads ``partial``;
* ``unrecorded`` -- absent from a stored document that has no entry for it
  (a pre-table document): read exactly as a document with no availability
  table at all is read, and listed, without lowering the run.

Oracles, none of them the reader's own helpers: the not-comparable set is
``DETECTOR_SECTIONS`` (checked against an AST scan of every detector in
``test_section_readers_contract.py``); the findings of a narrowing state
must be those of the ``present`` comparison (none: the two sides hold the
same content), never those of the ``empty`` one; ``unrecorded`` must report
exactly what the same document without its availability table reports.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from abicheck.checker import compare
from abicheck.cli import main
from abicheck.compare.section_readers import DETECTOR_SECTIONS
from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.model import (
    EnumMember,
    EnumType,
    Function,
    Param,
    RecordType,
    TypeField,
)
from abicheck.model.section_availability import SectionStatus
from abicheck.model.snapshot import AbiSnapshot
from abicheck.project_snapshot_legacy import write_legacy_snapshot_package
from abicheck.serialization import SCHEMA_VERSION, snapshot_from_dict, snapshot_to_json
from abicheck.storage.section_states import record_section_availability

_SECTIONS = ("binary", "build", "debug", "declarations", "layout", "types")
_STATES = ("present", "empty", "not_collected", "failed", "blocked", "unrecorded")
#: The reported status of each narrowing or listed state.
_LISTED = {
    "not_collected": "not_collected",
    "failed": "failed",
    "blocked": "blocked",
    "unrecorded": "unrecorded",
}
_NARROWING = {"not_collected", "failed", "blocked"}
#: Sections whose emptying the comparison observes on either side in this
#: corpus. (An ELF emptied of exports while its declarations stay is not
#: reported as a removal; that is the export-table reading, not availability.)
_OBSERVABLE = {"declarations", "types"}


def _snap(version: str) -> AbiSnapshot:
    return AbiSnapshot(
        library="libm.so.1",
        version=version,
        functions=[
            Function(
                name=n,
                mangled=n,
                return_type="int",
                params=[Param(name="t", type="T *")],
            )
            for n in ("f", "g")
        ],
        types=[
            RecordType(
                name="T",
                kind="struct",
                size_bits=32,
                fields=[TypeField(name="a", type="int", offset_bits=0)],
            )
        ],
        enums=[EnumType(name="E", members=[EnumMember(name="E0", value=0)])],
        elf=ElfMetadata(
            soname="libm.so.1", symbols=[ElfSymbol(name=n) for n in ("f", "g")]
        ),
    )


def _document(version: str) -> dict[str, Any]:
    return json.loads(snapshot_to_json(_snap(version)))


#: Every section collected and empty (the binary still an ELF, with no
#: exports).
_EMPTY = json.loads(
    snapshot_to_json(
        AbiSnapshot(
            library="libm.so.1", version="0", elf=ElfMetadata(soname="libm.so.1")
        )
    )
)


def _apply(document: dict[str, Any], section: str, state: str) -> None:
    sections = document["sections"]
    if state == "empty":
        sections[section]["payload"] = copy.deepcopy(
            _EMPTY["sections"][section]["payload"]
        )
    elif state == "blocked":
        sections[section]["must_understand"] = ["vendor.never"]
    elif state in ("not_collected", "failed", "unrecorded"):
        sections.pop(section)
        if state != "failed":
            document["section_schema_versions"].pop(section)
        if state == "unrecorded":
            document["section_availability"].pop(section)
        else:
            document["section_availability"][section] = {
                "diagnostics": ["no extractor ran"] if state == "failed" else [],
                "status": state,
            }


def _readers(section: str) -> set[str]:
    return {name for name, reads in DETECTOR_SECTIONS.items() if section in reads}


def _cli(tmp_path: Path, documents: dict[str, dict[str, Any]], form: str) -> dict:
    tmp_path.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, document in documents.items():
        if form == "file":
            path = tmp_path / f"{name}.json"
            path.write_text(json.dumps(document), encoding="utf-8")
        else:
            path = tmp_path / name
            write_legacy_snapshot_package(
                snapshot_from_dict(copy.deepcopy(document)),
                path,
                artifact_id="libm",
                max_known_schema_version=SCHEMA_VERSION,
            )
        paths.append(str(path))
    report_path = tmp_path / "r.json"
    result = CliRunner().invoke(main, ["compare", *paths, "-o", f"json={report_path}"])
    assert report_path.exists(), result.output
    return json.loads(report_path.read_text())


def _changes(report: dict) -> list[tuple[str, str]]:
    return sorted((c["kind"], c.get("symbol") or "") for c in report["changes"])


def _stored_report(
    tmp_path: Path, section: str, state: str, side: str, form: str, table: bool = True
) -> dict:
    documents = {"old": _document("1"), "new": _document("2")}
    _apply(documents[side], section, state)
    if not table:
        documents[side].pop("section_availability")
    return _cli(tmp_path, documents, form)


@pytest.mark.parametrize("form", ["file", "package"])
@pytest.mark.parametrize("side", ["old", "new"])
@pytest.mark.parametrize("state", _STATES)
@pytest.mark.parametrize("section", _SECTIONS)
def test_stored_state_matrix(
    tmp_path: Path, section: str, state: str, side: str, form: str
) -> None:
    if form == "package" and state == "unrecorded":
        pytest.skip("a package names its sections in its ref; nothing is unrecorded")
    report = _stored_report(tmp_path / "s", section, state, side, form)
    assurance = report["analysis_assurance"]
    not_comparable = {
        d["name"]
        for d in report["detectors"]
        if (d.get("coverage_gap") or "").startswith("not comparable")
    }
    listed = assurance.get("section_availability", [])
    if state in _NARROWING:
        assert not_comparable == _readers(section)
        # Never read as empty: the comparison reports exactly what the
        # identical `present` sides report -- nothing.
        assert _changes(report) == []
        assert assurance["status"] == "partial"
        assert [(e["side"], e["section"], e["status"]) for e in listed] == [
            (side, section, _LISTED[state])
        ]
        if state == "not_collected":
            assert any("was not collected" in n for n in assurance["notes"])
        return
    assert not_comparable == set()
    if state == "present":
        assert _changes(report) == [] and listed == []
        return
    if state == "empty":
        assert listed == []
        if section in _OBSERVABLE:
            assert _changes(report) != []
        return
    # unrecorded: read as a document with no availability table at all is
    # read (its defaults), and only listed.
    pre_table = _stored_report(tmp_path / "p", section, state, side, form, table=False)
    assert _changes(report) == _changes(pre_table)
    assert assurance["status"] == pre_table["analysis_assurance"]["status"]
    assert [(e["side"], e["section"], e["status"]) for e in listed] == [
        (side, section, "unrecorded")
    ]


_LIVE_STATES = ("present", "empty", "not_collected", "failed")


def _live(section: str, state: str, version: str) -> AbiSnapshot:
    if state == "empty":
        document = _document(version)
        _apply(document, section, "empty")
        return snapshot_from_dict(document)
    snap = _snap(version)
    if state in ("not_collected", "failed"):
        record_section_availability(
            snap, section, SectionStatus(state), ["no extractor ran"]
        )
    return snap


def _outcome(old: AbiSnapshot, new: AbiSnapshot) -> tuple[Any, ...]:
    result = compare(old, new)
    assurance = result.analysis_assurance.to_dict()  # type: ignore[union-attr]
    return (
        sorted((str(c.kind), c.symbol or "") for c in result.changes),
        sorted(
            (d.name, d.coverage_gap or "")
            for d in result.detector_results
            if d.not_evaluated
        ),
        assurance["status"],
        assurance.get("section_availability", []),
    )


def _reloaded(snap: AbiSnapshot) -> AbiSnapshot:
    return snapshot_from_dict(json.loads(snapshot_to_json(snap)))


@pytest.mark.parametrize("side", ["old", "new"])
@pytest.mark.parametrize("state", _LIVE_STATES)
@pytest.mark.parametrize("section", _SECTIONS)
def test_live_and_stored_operands_agree(section: str, state: str, side: str) -> None:
    """A live snapshot carrying a state compares exactly as its written and
    re-read dump does; the dump stores the state, and re-saves unchanged."""
    live = {"old": _snap("1"), "new": _snap("2")}
    live[side] = _live(section, state, "1" if side == "old" else "2")
    stored = {k: _reloaded(v) for k, v in live.items()}
    outcome = _outcome(live["old"], live["new"])
    assert outcome == _outcome(stored["old"], stored["new"])
    written = snapshot_to_json(stored[side])
    assert snapshot_to_json(snapshot_from_dict(json.loads(written))) == written
    entry = json.loads(written)["section_availability"][section]
    assert entry["status"] == ("present" if state in ("present", "empty") else state)
    changes, not_evaluated, status, listed = outcome
    if state in _NARROWING:
        assert {name for name, _ in not_evaluated} >= _readers(section)
        assert changes == [] and status == "partial"
        assert [(e["side"], e["section"], e["status"]) for e in listed] == [
            (side, section, state)
        ]
    else:
        assert listed == []


def test_a_not_collected_section_is_never_the_empty_one() -> None:
    """The bug class: swapping unavailable evidence for empty evidence must
    change the result. Per section, the not-collected and the
    collected-empty comparisons differ wherever emptiness is observable."""
    for section in _OBSERVABLE:
        old = _snap("1")
        assert _outcome(old, _live(section, "not_collected", "2"))[0] == []
        assert _outcome(old, _live(section, "empty", "2"))[0] != []


@pytest.mark.parametrize("status", ["not_collected", "failed"])
def test_identity_and_absence_kinds_cannot_be_recorded(status: str) -> None:
    snap = _snap("1")
    with pytest.raises(ValueError):
        record_section_availability(snap, "provenance", SectionStatus(status), ["x"])
    with pytest.raises(ValueError):
        # `graph` says "not collected" by being None.
        record_section_availability(snap, "graph", SectionStatus.NOT_COLLECTED, [])
    with pytest.raises(ValueError):
        record_section_availability(snap, "types", SectionStatus.BLOCKED, ["x"])
    assert snap.section_availability == () and snap.unreadable_sections == ()


def test_a_flat_document_keeps_a_not_collected_section() -> None:
    """A flat legacy document cannot say "not collected"; a snapshot holding
    one is stored sectioned wherever flattening would lose it."""
    from abicheck.storage.wire_extensions import flattening_loses

    snap = _live("types", "not_collected", "2")
    assert flattening_loses(snap)
    assert not flattening_loses(_live("graph", "present", "2"))
