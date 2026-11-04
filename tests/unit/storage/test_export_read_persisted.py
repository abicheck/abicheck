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

"""A partial export-table read survives storage and every reader honors it
(ADR-079, stored as the ``binary`` section's ``abicheck.export_read``
extension fact, ADR-062 D19).

Bug class ``evidence.silent_degradation_to_clean_verdict``: a stored snapshot
could not say its export table was cut short, so it read as a complete table
forever -- a symbol in the unread part read as unexported, and a comparison
against it reported a removal nobody observed.

The property crosses every recorded read outcome with a live operand (the
in-memory snapshot the dump builds) and a stored one (encoded and decoded),
on ELF, PE and Mach-O, over generated export tables and header-parse states.
The oracle is the ADR's own decision table, written out below as literal
mappings -- never the reader under test:

* D1/D3: ``partial``/``failed`` are taken as recorded; ``complete`` and
  ``unrecorded`` fall back to the pre-ADR-079 rule (read when the table holds an
  entry or its header was parsed, else failed);
* D2: the stored fact lists exactly the platforms that are not ``complete``,
  and is must-understand exactly for a ``partial``/``failed`` one; a
  document without the fact reads as ``unrecorded``;
* D4: the export fact, the ``exports`` coverage record, the release export
  names and the comparison all follow that effective state.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from abicheck.checker import compare
from abicheck.compare.edge_query import export_coverage_records
from abicheck.errors import SnapshotError
from abicheck.extract.library_export_facts import read_library_export_facts
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.model.availability import FactStatus
from abicheck.model.consumer_requirements import LibraryExportsUnreadableError
from abicheck.model.edge_coverage import ProducerRun
from abicheck.model.elf_facts import ElfMetadata, ElfSymbol, SymbolType
from abicheck.model.export_index import (
    ExportTableState,
    read_default_export_names,
    snapshot_export_table_state,
)
from abicheck.model.export_read import ExportReadState, export_read_state
from abicheck.model.macho_facts import MachoExport, MachoMetadata
from abicheck.model.pe_facts import PeExport, PeMetadata
from abicheck.serialization import (
    snapshot_from_dict,
    snapshot_to_dict,
    snapshot_to_json,
)
from abicheck.storage import wire_extensions
from abicheck.storage.extension_fact_catalog import EXPORT_READ
from abicheck.workflows.consumer_scope import require_read_library

PLATFORMS = ("elf", "pe", "macho")
FACT = EXPORT_READ.name
STATES = tuple(ExportReadState)
OPERANDS = ("live", "stored")
NAMES = ("a_fn", "b_fn", "c_fn", "d_fn")

# --- the oracle: ADR-079's decision table, spelled out -----------------------

#: D3: a recorded partial/failed read is taken as recorded.
_RECORDED_GAP = {"partial": "partial", "failed": "failed"}
#: D4: effective read -> export-table state / coverage / library fact status.
_TABLE_STATE = {
    "complete": ExportTableState.READ,
    "partial": ExportTableState.PARTIAL,
    "failed": ExportTableState.FAILED,
}
_COVERAGE = {
    "complete": (ProducerRun.RAN, ""),
    "partial": (ProducerRun.FAILED, "export_table_partially_read"),
    "failed": (ProducerRun.FAILED, "export_table_not_read"),
}
_FACT_STATUS = {
    "complete": FactStatus.PRESENT,
    "partial": FactStatus.PARTIAL,
    "failed": FactStatus.FAILED,
}


def _effective(recorded: str, n_exports: int, parsed: bool) -> str:
    if recorded in _RECORDED_GAP:
        return _RECORDED_GAP[recorded]
    return "complete" if (n_exports or parsed) else "failed"


# --- snapshot builders ---------------------------------------------------------


def _block(platform: str, names: tuple[str, ...], parsed: bool, state: ExportReadState):
    if platform == "elf":
        return ElfMetadata(
            soname="libx.so.1",
            machine="EM_X86_64" if parsed else "",
            symbols=[ElfSymbol(name=n, sym_type=SymbolType.FUNC) for n in names],
            export_read=state,
        )
    if platform == "pe":
        return PeMetadata(
            machine="IMAGE_FILE_MACHINE_AMD64" if parsed else "",
            exports=[PeExport(name=n, ordinal=i + 1) for i, n in enumerate(names)],
            export_read=state,
        )
    return MachoMetadata(
        filetype="MH_DYLIB" if parsed else "",
        cpu_type="X86_64" if parsed else "",
        exports=[MachoExport(name=n) for n in names],
        export_read=state,
    )


def _snapshot(
    platform: str,
    names: tuple[str, ...],
    *,
    parsed: bool = True,
    state: ExportReadState = ExportReadState.COMPLETE,
    version: str = "1",
) -> AbiSnapshot:
    return AbiSnapshot(
        library="libx",
        version=version,
        platform=platform,
        functions=[
            Function(name=n, mangled=n, return_type="int", visibility=Visibility.PUBLIC)
            for n in names
        ],
        **{platform: _block(platform, names, parsed, state)},
    )


def _stored(snap: AbiSnapshot) -> dict[str, Any]:
    """*snap* as written to disk: the single-file sectioned document."""
    document: dict[str, Any] = json.loads(snapshot_to_json(snap))
    return document


def _operand(snap: AbiSnapshot, operand: str) -> AbiSnapshot:
    return snap if operand == "live" else snapshot_from_dict(_stored(snap))


def _binary_header(document: dict[str, Any]) -> dict[str, Any]:
    section: dict[str, Any] = document["sections"]["binary"]
    return section


#: Oracle for D2 under ADR-062 D19: the stored fact lists exactly the
#: platforms that are not complete; it is must-understand exactly when one
#: of them is partial or failed.
def _expected_fact(platform: str, state: ExportReadState) -> dict[str, str] | None:
    if state is ExportReadState.UNRECORDED:
        return None
    return {} if state is ExportReadState.COMPLETE else {platform: state.value}


# --- the property --------------------------------------------------------------


@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    platform=st.sampled_from(PLATFORMS),
    state=st.sampled_from(STATES),
    operand=st.sampled_from(OPERANDS),
    names=st.lists(st.sampled_from(NAMES), unique=True, max_size=4).map(tuple),
    parsed=st.booleans(),
)
def test_every_reader_follows_the_recorded_read(
    platform: str,
    state: ExportReadState,
    operand: str,
    names: tuple[str, ...],
    parsed: bool,
) -> None:
    built = _snapshot(platform, names, parsed=parsed, state=state)
    snap = _operand(built, operand)
    meta = getattr(snap, platform)

    # D2: the stored copy carries exactly the recorded state, as the fact.
    assert meta.export_read is state
    header = _binary_header(_stored(built))
    assert "export_read" not in header["payload"][platform]
    assert header.get("extensions", {}).get(FACT) == _expected_fact(platform, state)
    assert (FACT in header.get("must_understand", ())) == (
        state.value in ("partial", "failed")
    )

    effective = _effective(state.value, len(names), parsed)
    assert snapshot_export_table_state(snap) is _TABLE_STATE[effective]
    (record,) = export_coverage_records(snap)
    assert (record.run, record.reason) == _COVERAGE[effective]
    facts = read_library_export_facts(snap)
    assert facts.status is _FACT_STATUS[effective]
    assert facts.export_names == frozenset(names)
    expected_names = frozenset(names) if effective == "complete" else None
    assert read_default_export_names(snap) == expected_names
    if effective == "complete":
        assert require_read_library(facts) is facts
    else:
        with pytest.raises(LibraryExportsUnreadableError):
            require_read_library(facts)


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    platform=st.sampled_from(PLATFORMS),
    state=st.sampled_from(STATES),
    operand=st.sampled_from(OPERANDS),
    kept=st.lists(st.sampled_from(NAMES), unique=True, min_size=1, max_size=3).map(
        tuple
    ),
)
def test_a_removal_resting_on_an_unread_table_is_never_a_break(
    platform: str, state: ExportReadState, operand: str, kept: tuple[str, ...]
) -> None:
    """D4 for the comparison: OLD reads complete; NEW keeps *kept* and records
    *state*. Every removed name is reported (never dropped); it scores as a
    break exactly when NEW's table records no gap, and the assurance, the
    scope and the reported states agree with the recorded read."""
    old = _operand(_snapshot(platform, NAMES), operand)
    new = _operand(_snapshot(platform, kept, state=state, version="2"), operand)
    result = compare(old, new)
    removed = set(NAMES) - set(kept)
    gap = state.value in ("partial", "failed")

    by_symbol = {c.symbol: c for c in result.changes if c.symbol in removed}
    assert set(by_symbol) == removed  # record before disposing: nothing dropped
    for change in by_symbol.values():
        assert change.kind.value == "func_removed"
        if gap:
            assert change.modulation_rule == "partial_export_table_read"
            assert change.effective_verdict is not None
            assert change.effective_verdict.value == "COMPATIBLE_WITH_RISK"
        else:
            assert change.modulation_rule is None
    assert (result.verdict.value == "BREAKING") is (not gap)

    assurance = result.analysis_assurance
    assert assurance.export_read == {
        "old": {platform: "complete"},
        "new": {platform: state.value},
    }
    if gap:
        assert assurance.status == "partial"
        assert any(
            f"new snapshot, {platform}: {state.value}" in n for n in assurance.notes
        )


# --- encoding rules ------------------------------------------------------------


@pytest.mark.parametrize("platform", PLATFORMS)
@pytest.mark.parametrize("recorded", ["complete", "partial", "failed"])
def test_a_document_without_the_fact_reads_as_unrecorded(
    platform: str, recorded: str
) -> None:
    """D3: a document carrying no fact (written before it existed) has no
    record of the read, whatever a later writer would have recorded; its
    block reads ``unrecorded`` and keeps the pre-ADR-079 rule (a parsed
    block holding entries reads complete)."""
    document = _stored(_snapshot(platform, NAMES, state=ExportReadState(recorded)))
    header = _binary_header(document)
    header.pop("extensions", None)
    header.pop("must_understand", None)
    snap = snapshot_from_dict(copy.deepcopy(document))
    assert getattr(snap, platform).export_read is ExportReadState.UNRECORDED
    assert snapshot_export_table_state(snap) is ExportTableState.READ


@pytest.mark.parametrize("platform", PLATFORMS)
def test_an_unrecorded_block_is_never_written_as_complete(platform: str) -> None:
    """A block with no record writes no fact, so it re-reads ``unrecorded``,
    and a flat document (which carries no section header) never claims a
    read either."""
    snap = _snapshot(platform, (), parsed=False, state=ExportReadState.UNRECORDED)
    assert FACT not in _binary_header(_stored(snap)).get("extensions", {})
    for again in (
        snapshot_from_dict(_stored(snap)),
        snapshot_from_dict(snapshot_to_dict(snap)),
        snapshot_from_dict(
            snapshot_to_dict(_snapshot(platform, NAMES, state=ExportReadState.PARTIAL))
        ),
    ):
        assert getattr(again, platform).export_read is ExportReadState.UNRECORDED
    assert snapshot_export_table_state(again) is ExportTableState.READ


@pytest.mark.parametrize("platform", PLATFORMS)
def test_a_fact_already_stored_survives_a_resave(platform: str) -> None:
    """Load -> save keeps the fact byte-for-byte (ADR-062 D14)."""
    first = _stored(_snapshot(platform, NAMES, state=ExportReadState.PARTIAL))
    again = _stored(snapshot_from_dict(copy.deepcopy(first)))
    assert _binary_header(again) == _binary_header(first)


@pytest.mark.parametrize("platform", PLATFORMS)
def test_a_complete_read_changes_no_block_and_blocks_no_reader(platform: str) -> None:
    """D2: ``complete`` adds no block key and no must-understand name, so a
    reader that predates the fact reads the section; a partial read differs
    from it only by the fact's value and its must-understand entry."""
    complete = _stored(_snapshot(platform, NAMES))
    partial = _stored(_snapshot(platform, NAMES, state=ExportReadState.PARTIAL))
    assert _binary_header(partial)["payload"] == _binary_header(complete)["payload"]
    assert "must_understand" not in _binary_header(complete)
    assert _binary_header(partial)["must_understand"] == [FACT]


@pytest.mark.parametrize("platform", PLATFORMS)
@pytest.mark.parametrize(
    ("state", "blocked"),
    [
        (ExportReadState.COMPLETE, False),
        (ExportReadState.PARTIAL, True),
        (ExportReadState.FAILED, True),
    ],
)
def test_a_reader_that_predates_the_fact(
    platform: str,
    state: ExportReadState,
    blocked: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-079 D5 as amended (ADR-062 D19): an older reader (one that does not
    understand the fact) reads a complete document normally and blocks only
    the ``binary`` section of a partial or failed one -- it never reads the
    cut-short table as complete."""
    document = _stored(_snapshot(platform, NAMES, state=state))
    monkeypatch.setattr(wire_extensions, "UNDERSTOOD_EXTENSIONS", frozenset())
    snap = snapshot_from_dict(copy.deepcopy(document))
    assert ({k for k, _ in snap.unreadable_sections} == {"binary"}) is blocked
    # Blocked: no platform block is decoded at all, so nothing reads the
    # cut-short table as complete; otherwise the block is read as stored.
    assert (getattr(snap, platform) is None) is blocked


@pytest.mark.parametrize("platform", PLATFORMS)
@pytest.mark.parametrize("garbled", ["mostly", {"elf": "complete"}, {"dos": "partial"}])
def test_a_garbled_fact_is_refused(platform: str, garbled: object) -> None:
    document = _stored(_snapshot(platform, NAMES, state=ExportReadState.PARTIAL))
    _binary_header(document)["extensions"][FACT] = garbled
    with pytest.raises(SnapshotError, match="export_read"):
        snapshot_from_dict(document)


@pytest.mark.parametrize(
    ("failure_noted", "exports_read", "expected"),
    [
        (False, False, ExportReadState.COMPLETE),
        (False, True, ExportReadState.COMPLETE),
        (True, True, ExportReadState.PARTIAL),
        (True, False, ExportReadState.FAILED),
    ],
)
def test_the_writer_rule(
    failure_noted: bool, exports_read: bool, expected: ExportReadState
) -> None:
    """D1, exhaustively: what a recording read reports."""
    assert (
        export_read_state(failure_noted=failure_noted, exports_read=exports_read)
        is expected
    )


@pytest.mark.parametrize("platform", PLATFORMS)
@pytest.mark.parametrize("operand", OPERANDS)
def test_a_removal_the_new_headers_establish_still_stands(
    platform: str, operand: str
) -> None:
    """A real break is preserved: when NEW parsed headers and no longer
    declares ``d_fn``, its removal is a header fact, not an export-table
    absence, and stays a break even against a partial table. ``c_fn`` is still
    declared by NEW's headers but missing from the partial table: only that
    one is annotated."""
    old = _operand(_snapshot(platform, NAMES), operand)
    kept = ("a_fn", "b_fn")
    built = AbiSnapshot(
        library="libx",
        version="2",
        platform=platform,
        ast_producer="castxml",
        functions=[
            Function(name=n, mangled=n, return_type="int", visibility=Visibility.PUBLIC)
            for n in (*kept, "c_fn")
        ],
        **{platform: _block(platform, kept, True, ExportReadState.PARTIAL)},
    )
    new = _operand(built, operand)
    result = compare(old, new)
    removed = {c.symbol: c for c in result.changes if c.kind.value == "func_removed"}
    assert removed["d_fn"].modulation_rule is None
    assert result.verdict.value == "BREAKING"
    # ``c_fn`` is still declared: never scored as a removal (ELF reports it,
    # annotated; PE/Mach-O do not report it at all).
    c_fn = removed.get("c_fn")
    assert c_fn is None or c_fn.modulation_rule == "partial_export_table_read"
