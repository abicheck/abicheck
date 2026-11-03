# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""ADR-062 D19 -- an optional new fact is a declared extension fact, not a
global ``SCHEMA_VERSION`` bump.

The "older reader" here is this build's own decoder, and the "newer writer"
is this build's writer handed a catalog with one more fact
(`ExtensionFactRegistry.extended`): exactly the pair that meets when a
release adds a fact and an older release reads its dump. The oracles are
independent of the extension machinery: the same snapshot written *without*
the fact (what the older reader must decode it to), the input document
itself (byte-equivalence on re-save), and the detector registry's own
results.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.errors import IncompatibleSnapshotSchemaError, SnapshotError
from abicheck.model import EnumMember, EnumType, Function, RecordType, Variable
from abicheck.model.availability import FactStatus
from abicheck.model.snapshot import AbiSnapshot
from abicheck.project_snapshot_legacy import (
    read_stored_snapshot_document,
    write_legacy_snapshot_package,
)
from abicheck.serialization import (
    SCHEMA_VERSION,
    snapshot_from_dict,
    snapshot_to_json,
)
from abicheck.storage import wire_extensions
from abicheck.storage.extension_fact_catalog import (
    EXTENSION_FACTS,
    ExtensionFact,
    ExtensionFactRegistry,
    ReaderSemantics,
)
from abicheck.storage.extension_facts import read_extension_fact, write_extension_fact
from abicheck.storage.snapshot_evidence import snapshot_evidence_digest

#: Sections every snapshot document writes (the optional `semantic_ir` and
#: `graph` are written only when the snapshot holds them).
_SECTIONS = ["binary", "build", "debug", "declarations", "layout", "types"]

#: Stored value shapes: scalars, nesting, empties, unicode.
_VALUES: list[Any] = [
    "partial",
    0,
    False,
    None,
    [],
    {"elf": "partial", "pe": None},
    {"nested": {"list": [1, "two", {"three": 3.5}]}},
    "été",
]


def _snapshot() -> AbiSnapshot:
    return AbiSnapshot(
        library="libext.so.1",
        version="1.0",
        functions=[
            Function(name="f", mangled="f", return_type="int"),
            Function(name="g", mangled="g", return_type="int"),
        ],
        variables=[Variable(name="v", mangled="v", type="int")],
        types=[RecordType(name="T", kind="struct")],
        enums=[EnumType(name="E", members=[EnumMember(name="E0", value=0)])],
        elf=ElfMetadata(soname="libext.so.1", symbols=[ElfSymbol(name="f")]),
    )


def _fact(
    section: str,
    semantics: ReaderSemantics = ReaderSemantics.OPTIONAL,
    *,
    when: Any = None,
    name: str = "future.note",
) -> ExtensionFact[Any]:
    return ExtensionFact(
        name=name,
        section=section,
        semantics=semantics,
        absent_reads_as="not collected: the writer predates the fact",
        owner="ADR-062",
        encode=lambda value: value,
        decode=lambda raw: raw,
        must_understand_when=when,
    )


def _newer_dump(fact: ExtensionFact[Any], value: Any) -> dict[str, Any]:
    """The document a newer build, whose catalog declares *fact*, writes."""
    snap = _snapshot()
    write_extension_fact(snap, fact, value, registry=EXTENSION_FACTS.extended(fact))
    return json.loads(snapshot_to_json(snap))


def _older_load(document: dict[str, Any]) -> AbiSnapshot:
    """This build's decoder, whose catalog does not declare the fact."""
    assert "future.note" not in wire_extensions.UNDERSTOOD_EXTENSIONS
    return snapshot_from_dict(copy.deepcopy(document))


def _plain() -> AbiSnapshot:
    return snapshot_from_dict(json.loads(snapshot_to_json(_snapshot())))


def _resaved_without(snap: AbiSnapshot, section: str, *keys: str) -> dict[str, Any]:
    """*snap* re-saved, with *keys* removed from *section*'s header: what a
    re-save of the fact-less snapshot writes when only the fact differs."""
    document = json.loads(snapshot_to_json(snap))
    for key in keys:
        document["sections"][section].pop(key, None)
    return document


# -- optional: an older reader loads the document and loses only the fact ----


@pytest.mark.parametrize("section", _SECTIONS)
@pytest.mark.parametrize("value", _VALUES, ids=repr)
def test_an_older_reader_loses_only_an_optional_fact(section: str, value: Any) -> None:
    fact = _fact(section)
    document = _newer_dump(fact, value)
    # No global bump: the document states the version this build writes.
    assert document["schema_version"] == SCHEMA_VERSION
    assert "must_understand" not in document
    assert "must_understand" not in document["sections"][section]
    old = _older_load(document)
    assert old.unreadable_sections == ()
    # The older reader keeps the fact verbatim and re-saves it unchanged;
    # the rest of its re-save is the fact-less snapshot's.
    resaved = json.loads(snapshot_to_json(old))
    assert resaved["sections"][section]["extensions"] == {"future.note": value}
    assert _resaved_without(old, section, "extensions") == json.loads(
        snapshot_to_json(_plain())
    )
    # The newer reader reads it back.
    assert read_extension_fact(old, fact).status is FactStatus.PRESENT
    assert read_extension_fact(old, fact).value == value


def test_an_optional_fact_changes_no_detector_result() -> None:
    from abicheck.detector_registry import registry

    old = _plain()
    with_fact = _older_load(_newer_dump(_fact("types"), {"x": 1}))
    plain_changes, plain_results = registry.run_all(old, _plain())
    changes, results = registry.run_all(old, with_fact)
    assert changes == plain_changes == []
    assert [(r.name, r.enabled, r.coverage_gap) for r in results] == [
        (r.name, r.enabled, r.coverage_gap) for r in plain_results
    ]


def test_an_optional_fact_is_inside_the_evidence_digest() -> None:
    plain = snapshot_evidence_digest(_plain())
    a = snapshot_evidence_digest(_older_load(_newer_dump(_fact("binary"), "a")))
    b = snapshot_evidence_digest(_older_load(_newer_dump(_fact("binary"), "b")))
    assert len({plain, a, b}) == 3


# -- must-understand: blocks only its section, or the document ---------------


@pytest.mark.parametrize("section", _SECTIONS)
def test_a_section_must_understand_fact_blocks_exactly_its_section(
    section: str,
) -> None:
    from abicheck.compare.section_readers import DETECTOR_SECTIONS
    from abicheck.detector_registry import registry

    fact = _fact(section, ReaderSemantics.SECTION_MUST_UNDERSTAND)
    document = _newer_dump(fact, {"layout": "v2"})
    assert document["sections"][section]["must_understand"] == ["future.note"]
    assert document["schema_version"] == SCHEMA_VERSION
    new = _older_load(document)
    assert [kind for kind, _ in new.unreadable_sections] == [section]
    assert "'future.note'" in new.unreadable_sections[0][1]
    assert read_extension_fact(new, fact).status is FactStatus.FAILED
    _, results = registry.run_all(_plain(), new)
    for result in results:
        reads = section in DETECTOR_SECTIONS[result.name]
        blocked = "not comparable" in (result.coverage_gap or "")
        assert blocked == reads, result.name
    # Kept verbatim, header included, for a re-save.
    resaved = json.loads(snapshot_to_json(new))
    assert resaved["sections"][section] == document["sections"][section]


def test_a_document_must_understand_fact_refuses_the_document() -> None:
    fact = _fact("declarations", ReaderSemantics.DOCUMENT_MUST_UNDERSTAND)
    document = _newer_dump(fact, True)
    assert document["must_understand"] == ["future.note"]
    assert "must_understand" not in document["sections"]["declarations"]
    with pytest.raises(IncompatibleSnapshotSchemaError, match="future.note"):
        _older_load(document)


@pytest.mark.parametrize(
    "semantics",
    [ReaderSemantics.SECTION_MUST_UNDERSTAND, ReaderSemantics.DOCUMENT_MUST_UNDERSTAND],
)
@pytest.mark.parametrize(
    ("value", "blocks"),
    [("complete", False), ("partial", True), ("failed", True)],
)
def test_must_understand_when_narrows_to_the_values_that_need_it(
    semantics: ReaderSemantics, value: str, blocks: bool
) -> None:
    """ADR-079's shape: only a non-complete export read needs understanding."""
    fact = _fact("binary", semantics, when=lambda v: v != "complete")
    document = _newer_dump(fact, value)
    if not blocks:
        old = _older_load(document)
        assert old.unreadable_sections == ()
        assert _resaved_without(old, "binary", "extensions") == json.loads(
            snapshot_to_json(_plain())
        )
    elif semantics is ReaderSemantics.SECTION_MUST_UNDERSTAND:
        assert [k for k, _ in _older_load(document).unreadable_sections] == ["binary"]
    else:
        with pytest.raises(IncompatibleSnapshotSchemaError):
            _older_load(document)


def test_overwriting_a_value_withdraws_its_must_understand_entry() -> None:
    fact = _fact("binary", ReaderSemantics.SECTION_MUST_UNDERSTAND, when=bool)
    registry = EXTENSION_FACTS.extended(fact)
    snap = _snapshot()
    write_extension_fact(snap, fact, True, registry=registry)
    write_extension_fact(snap, fact, False, registry=registry)
    document = json.loads(snapshot_to_json(snap))
    assert "must_understand" not in document["sections"]["binary"]
    assert document["sections"]["binary"]["extensions"] == {"future.note": False}


def test_a_reader_that_understands_the_fact_is_not_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fact = _fact("types", ReaderSemantics.SECTION_MUST_UNDERSTAND)
    document = _newer_dump(fact, 7)
    monkeypatch.setattr(
        wire_extensions,
        "UNDERSTOOD_EXTENSIONS",
        wire_extensions.UNDERSTOOD_EXTENSIONS | {fact.name},
    )
    newer = snapshot_from_dict(copy.deepcopy(document))
    assert newer.unreadable_sections == ()
    assert read_extension_fact(newer, fact).value == 7
    resaved = json.loads(snapshot_to_json(newer))
    assert resaved["sections"]["types"] == document["sections"]["types"]


def test_a_package_blocks_the_same_section(tmp_path: Path) -> None:
    fact = _fact("types", ReaderSemantics.SECTION_MUST_UNDERSTAND)
    snap = _snapshot()
    write_extension_fact(snap, fact, 1, registry=EXTENSION_FACTS.extended(fact))
    root = tmp_path / "pkg"
    write_legacy_snapshot_package(
        snap, root, artifact_id="a", max_known_schema_version=SCHEMA_VERSION
    )
    document, _ = read_stored_snapshot_document(root)
    back = snapshot_from_dict(document)
    assert [kind for kind, _ in back.unreadable_sections] == ["types"]
    assert len(back.declarations.functions) == 2


# -- the write and read API ----------------------------------------------------


def test_read_states_absence_as_not_collected() -> None:
    assert read_extension_fact(_plain(), _fact("types")).status is (
        FactStatus.NOT_COLLECTED
    )


def test_read_of_a_value_that_does_not_decode_is_failed() -> None:
    def strict(raw: Any) -> int:
        if not isinstance(raw, int):
            raise TypeError("not an int")
        return raw

    fact = _fact("types")
    loaded = _older_load(_newer_dump(fact, "seven"))
    typed = ExtensionFact(
        name=fact.name,
        section="types",
        semantics=ReaderSemantics.OPTIONAL,
        absent_reads_as="not collected",
        owner="ADR-062",
        encode=int,
        decode=strict,
    )
    got = read_extension_fact(loaded, typed)
    assert got.status is FactStatus.FAILED
    assert "not an int" in got.diagnostics[0]


def test_write_refuses_an_undeclared_fact() -> None:
    with pytest.raises(ValueError, match="not declared"):
        write_extension_fact(_snapshot(), _fact("types"), 1)


def test_write_refuses_a_blocked_section() -> None:
    document = _document_with_blocked_types()
    loaded = snapshot_from_dict(document)
    fact = _fact("types")
    with pytest.raises(ValueError, match="not decoded"):
        write_extension_fact(loaded, fact, 1, registry=EXTENSION_FACTS.extended(fact))


def _document_with_blocked_types() -> dict[str, Any]:
    document = json.loads(snapshot_to_json(_snapshot()))
    document["sections"]["types"]["must_understand"] = ["x_layout_v2"]
    return document


@pytest.mark.parametrize("section", ["graph", "semantic_ir"])
def test_a_fact_on_a_section_the_document_does_not_write_is_refused(
    section: str,
) -> None:
    """D14: dropping an extension is a defect, so the write refuses."""
    fact = _fact(section)
    snap = _snapshot()
    write_extension_fact(snap, fact, 1, registry=EXTENSION_FACTS.extended(fact))
    with pytest.raises(SnapshotError, match=section):
        snapshot_to_json(snap)


# -- the catalog's declarations --------------------------------------------------


@pytest.mark.parametrize(
    ("change", "match"),
    [
        ({"name": "types"}, "dotted"),
        ({"name": "Future.Note"}, "dotted"),
        ({"name": ""}, "dotted"),
        ({"section": "nowhere"}, "unknown section"),
        ({"absent_reads_as": "  "}, "absence"),
        ({"owner": "someone"}, "must name an ADR"),
        ({"when": bool}, "must-understand semantics"),
        (
            {
                "section": "provenance",
                "semantics": ReaderSemantics.SECTION_MUST_UNDERSTAND,
            },
            "DOCUMENT_MUST_UNDERSTAND",
        ),
    ],
)
def test_a_malformed_declaration_is_refused(change: dict[str, Any], match: str) -> None:
    fields: dict[str, Any] = {
        "name": "future.note",
        "section": "types",
        "semantics": ReaderSemantics.OPTIONAL,
        "absent_reads_as": "not collected",
        "owner": "ADR-062",
        "encode": str,
        "decode": str,
        "when": None,
    }
    fields.update(change)
    when = fields.pop("when")
    with pytest.raises(ValueError, match=match):
        ExtensionFact(**fields, must_understand_when=when)


def test_a_registry_refuses_a_name_declared_twice() -> None:
    with pytest.raises(ValueError, match="declared twice"):
        ExtensionFactRegistry([_fact("types"), _fact("binary")])


def test_a_non_bool_must_understand_decision_is_refused() -> None:
    fact = _fact("types", ReaderSemantics.SECTION_MUST_UNDERSTAND, when=lambda v: v)
    with pytest.raises(TypeError, match="not bool"):
        fact.needs_understanding("yes")


def test_this_build_understands_exactly_its_catalog() -> None:
    assert wire_extensions.UNDERSTOOD_EXTENSIONS == EXTENSION_FACTS.names()
