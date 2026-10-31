"""ADR-062 D15: the wire contract is independent of runtime classes.

Two invariants:

1. **Pinned bytes.** A fixed corpus is serialized to the sectioned document
   and every section's field inventory and canonical sha256 is checked in
   (`tests/fixtures/wire_contract/sections.json`). While a section's
   `section_schema_version` and the document `schema_version` are unchanged,
   its bytes may not move: a runtime-only refactor that leaks into the wire
   fails here, and a deliberate wire change has to bump the version (and
   ship its migration) before the pin is re-recorded with
   `python -m tests.unit.storage.test_wire_contract --record`.
2. **Runtime-only refactors.** Re-declaring a model record with its fields
   in another order, under another class name, produces the same bytes.

Plus the generated field inventory (D15 (1), `tests/_wire_inventory.py`):
`tests/fixtures/wire_contract/field_inventory.json` is regenerated from the
encoders and diffed; a moved section needs a version bump and a D13
migration.

Plus D3 at section level: *absent*, *present and empty*, and *failed* are
stored distinctly and read back distinctly -- an absent section never
reads as an empty one, and a failed one never reads as either (the
section-header `section_availability` table: see
`test_extension_preservation.py` for `failed` entries).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from abicheck.elf_metadata import ElfImport, ElfMetadata, ElfSymbol
from abicheck.model import (
    EnumMember,
    EnumType,
    Function,
    Param,
    RecordType,
    TypeField,
    Variable,
)
from abicheck.model.availability import FactStatus
from abicheck.model.fact import Fact
from abicheck.model.snapshot import AbiSnapshot
from abicheck.serialization import snapshot_from_dict, snapshot_to_json
from abicheck.storage.canonical import canonical_json
from abicheck.storage.dto import SECTION_SCHEMA_VERSIONS, migration_source_versions
from abicheck.storage.sectioned_document import SECTIONS_KEY
from tests._wire_inventory import field_inventory

_PIN = (
    Path(__file__).resolve().parents[2] / "fixtures" / "wire_contract" / "sections.json"
)
#: The generated per-section field inventory (`tests/_wire_inventory.py`, D15).
_INVENTORY = _PIN.with_name("field_inventory.json")


def _corpus(function_cls: type = Function) -> AbiSnapshot:
    return AbiSnapshot(
        library="libwire.so.1",
        version="1.0",
        functions=[
            function_cls(
                name="wire_open",
                mangled="wire_open",
                return_type="int",
                params=[
                    Param(name="path", type="const char *"),
                    Param(name="flags", type="int"),
                ],
                is_extern_c=True,
            ),
            function_cls(
                name="Wire::close",
                mangled="_ZN4Wire5closeEv",
                return_type="void",
                is_virtual=True,
                vtable_index=2,
            ),
        ],
        variables=[Variable(name="wire_version", mangled="wire_version", type="int")],
        types=[
            RecordType(
                name="Wire",
                kind="struct",
                size_bits=64,
                fields=[TypeField(name="fd", type="int", offset_bits=0)],
            )
        ],
        enums=[
            EnumType(
                name="WireMode",
                members=[
                    EnumMember(name="WIRE_R", value=0),
                    EnumMember(name="WIRE_W", value=1),
                ],
            )
        ],
        elf=ElfMetadata(
            soname="libwire.so.1",
            needed=["libc.so.6"],
            symbols=[ElfSymbol(name="wire_open"), ElfSymbol(name="wire_version")],
            imports=[ElfImport(name="open")],
        ),
    )


def _document(snap: AbiSnapshot) -> dict[str, Any]:
    return json.loads(snapshot_to_json(snap))


def _section_pins(document: dict[str, Any]) -> dict[str, Any]:
    pins: dict[str, Any] = {}
    for kind, section in sorted(document[SECTIONS_KEY].items()):
        payload = section.get("payload")
        pins[kind] = {
            "section_schema_version": section["section_schema_version"],
            "fields": sorted(payload) if isinstance(payload, dict) else [],
            "sha256": hashlib.sha256(canonical_json(section).encode()).hexdigest(),
        }
    return {"schema_version": document["schema_version"], "sections": pins}


def _record() -> None:
    _PIN.parent.mkdir(parents=True, exist_ok=True)
    _PIN.write_text(
        json.dumps(_section_pins(_document(_corpus())), indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    _INVENTORY.write_text(_inventory_text(), encoding="utf-8")


def _inventory_text() -> str:
    return (
        json.dumps(field_inventory(_document(_corpus())), indent=2, sort_keys=True)
        + "\n"
    )


def _migrates(kind: str, pinned: int, current: int) -> bool:
    """A registered D13 migration chain reaches *current* from *pinned*."""
    steps = migration_source_versions(kind)
    return pinned < current and all(v in steps for v in range(pinned, current))


def test_generated_field_inventory_matches_the_checked_in_one() -> None:
    """D15 (1): the inventory is generated from the encoders over the fixed
    corpus and diffed against the checked-in copy. A section whose
    inventory moved must have bumped its `section_schema_version` with a
    D13 migration from the pinned version; only then may the copy be
    re-recorded (`python -m tests.unit.storage.test_wire_contract --record`)."""
    pinned = json.loads(_INVENTORY.read_text(encoding="utf-8"))
    current = json.loads(_inventory_text())
    for key in ("document", "availability_entry"):
        assert current[key] == pinned[key], (
            f"the document-level {key} moved without a schema change"
        )
    assert set(current["sections"]) == set(pinned["sections"])
    for kind, pin in pinned["sections"].items():
        now = current["sections"][kind]
        if now["header"] == pin["header"] and now["fields"] == pin["fields"]:
            continue
        assert _migrates(
            kind, pin["section_schema_version"], now["section_schema_version"]
        ), (
            f"section {kind!r} changed its wire fields without a "
            "section_schema_version bump and a D13 migration from "
            f"{pin['section_schema_version']}"
        )
        pytest.fail(f"section {kind!r} was bumped; re-record the inventory")


def test_inventory_covers_the_header_carriers() -> None:
    inventory = json.loads(_INVENTORY.read_text(encoding="utf-8"))
    assert inventory["availability_entry"] == ["diagnostics", "status"]
    assert "section_availability" in inventory["document"]
    for section in inventory["sections"].values():
        assert {"extensions", "must_understand", "payload"} <= set(section["header"])


def test_the_inventory_detects_a_renamed_record_field() -> None:
    """The generator is not vacuous: a key renamed inside a record moves
    the inventory of exactly that section."""
    document = _document(_corpus())
    record = document[SECTIONS_KEY]["declarations"]["payload"]["functions"][0]
    record["return_type_renamed"] = record.pop("return_type")
    before = field_inventory(_document(_corpus()))["sections"]
    after = field_inventory(document)["sections"]
    assert {k for k in before if before[k] != after[k]} == {"declarations"}
    assert not _migrates("declarations", 1, SECTION_SCHEMA_VERSIONS["declarations"])


def test_section_bytes_are_pinned_while_their_versions_hold() -> None:
    pinned = json.loads(_PIN.read_text(encoding="utf-8"))
    current = _section_pins(_document(_corpus()))
    rerecord = (
        "re-record with `python -m tests.unit.storage.test_wire_contract --record`"
    )
    assert current["schema_version"] == pinned["schema_version"], (
        f"snapshot schema moved {pinned['schema_version']} -> "
        f"{current['schema_version']}; review the section diffs and {rerecord}"
    )
    assert set(current["sections"]) == set(pinned["sections"]), rerecord
    for kind, pin in pinned["sections"].items():
        now = current["sections"][kind]
        if now["section_schema_version"] != pin["section_schema_version"]:
            pytest.fail(f"section {kind!r} version moved; {rerecord}")
        assert now["fields"] == pin["fields"], (
            f"section {kind!r} changed its field inventory without a "
            f"section_schema_version bump: {sorted(set(now['fields']) ^ set(pin['fields']))}"
        )
        assert now["sha256"] == pin["sha256"], (
            f"section {kind!r} changed its bytes without a section_schema_version "
            "bump -- a wire change needs a version and a migration (D13/D15)"
        )


def test_pinned_versions_match_the_registry() -> None:
    pinned = json.loads(_PIN.read_text(encoding="utf-8"))
    for kind, pin in pinned["sections"].items():
        assert pin["section_schema_version"] == SECTION_SCHEMA_VERSIONS[kind]


@pytest.mark.parametrize("seed", range(5))
def test_a_runtime_only_dataclass_refactor_keeps_the_bytes(seed: int) -> None:
    """Re-declare `Function` with its fields in a shuffled order under a
    different class name -- a runtime-only refactor -- and serialize the
    same corpus through it."""
    import random

    fields = [(f.name, f.type, f) for f in dataclasses.fields(Function)]
    required = [
        entry
        for entry in fields
        if entry[2].default is dataclasses.MISSING
        and entry[2].default_factory is dataclasses.MISSING
    ]
    optional = [entry for entry in fields if entry not in required]
    random.Random(seed).shuffle(required)
    random.Random(seed + 100).shuffle(optional)
    refactored = dataclasses.make_dataclass(
        f"FunctionRefactored{seed}",
        [
            (
                name,
                typ,
                dataclasses.field(
                    default=f.default,
                    default_factory=f.default_factory,
                    kw_only=f.kw_only,
                ),
            )
            if f in [e[2] for e in optional]
            else (name, typ)
            for name, typ, f in required + optional
        ],
        bases=(Function,),
    )
    assert snapshot_to_json(_corpus(refactored)) == snapshot_to_json(_corpus())


def _round_trip(snap: AbiSnapshot) -> AbiSnapshot:
    return snapshot_from_dict(json.loads(snapshot_to_json(snap)))


_PHI_STATES = {
    "absent": None,
    "present-empty": Fact.present(frozenset()),
    "present": Fact.present(frozenset({"WIRE_API"})),
    "failed": Fact.failed("header scan crashed"),
}


def _observed(fact: Fact[Any] | None) -> tuple[object, object]:
    if fact is None:
        return (None, None)
    return (fact.status, fact.value if fact.is_present else None)


def test_absent_empty_and_failed_fact_families_stay_distinct() -> None:
    """Each state reads back as itself, and no two states read back alike."""
    observed = {
        state: _observed(
            _round_trip(
                dataclasses.replace(_corpus(), public_header_identifiers_fact=fact)
            ).public_header_identifiers_fact
        )
        for state, fact in _PHI_STATES.items()
    }
    assert observed == {
        # Absent reads back as "never collected", not as an empty value.
        "absent": (FactStatus.NOT_COLLECTED, None),
        "present-empty": (FactStatus.PRESENT, frozenset()),
        "present": (FactStatus.PRESENT, frozenset({"WIRE_API"})),
        "failed": (FactStatus.FAILED, None),
    }
    assert len(set(observed.values())) == len(observed)


def test_an_absent_platform_section_never_reads_as_an_empty_one() -> None:
    absent_snap = dataclasses.replace(_corpus(), elf=None)
    empty_snap = dataclasses.replace(_corpus(), elf=ElfMetadata())
    assert _round_trip(absent_snap).elf is None
    assert _round_trip(empty_snap).elf == ElfMetadata()
    assert _document(absent_snap) != _document(empty_snap)


if __name__ == "__main__" and "--record" in sys.argv:
    _record()
