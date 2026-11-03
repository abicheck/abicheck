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
(ADR-079, schema v70).

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
  ``unrecorded`` fall back to the pre-v70 rule (read when the table holds an
  entry or its header was parsed, else failed);
* D2: a v70 document writes ``export_read`` exactly when the state is not
  ``complete``; a pre-v70 document reads as ``unrecorded``;
* D4: the export fact, the ``exports`` coverage record, the release export
  names and the comparison all follow that effective state.
"""

from __future__ import annotations

import copy
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
from abicheck.serialization import snapshot_from_dict, snapshot_to_dict
from abicheck.workflows.consumer_scope import require_read_library

PLATFORMS = ("elf", "pe", "macho")
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


def _operand(snap: AbiSnapshot, operand: str) -> AbiSnapshot:
    return snap if operand == "live" else snapshot_from_dict(snapshot_to_dict(snap))


def _platform_block(document: dict[str, Any], platform: str) -> dict[str, Any] | None:
    """The stored platform block, in the flat or the sectioned shape."""
    if platform in document:
        return document[platform]
    return document["sections"]["binary"]["payload"][platform]


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

    # D2: the stored copy carries exactly the recorded state.
    assert meta.export_read is state
    stored_block = _platform_block(snapshot_to_dict(built), platform)
    assert stored_block is not None
    assert ("export_read" in stored_block) == (state.value != "complete")

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
def test_a_pre_v70_document_reads_as_unrecorded(platform: str, recorded: str) -> None:
    """D3: a document older than v70 has no record of the read, whatever key
    a later writer would have put there; its block reads ``unrecorded`` and
    keeps the pre-v70 rule (a parsed block holding entries reads complete)."""
    document = snapshot_to_dict(
        _snapshot(platform, NAMES, state=ExportReadState(recorded))
    )
    _platform_block(document, platform).pop("export_read", None)
    document["schema_version"] = 69
    snap = snapshot_from_dict(copy.deepcopy(document))
    assert getattr(snap, platform).export_read is ExportReadState.UNRECORDED
    assert snapshot_export_table_state(snap) is ExportTableState.READ


@pytest.mark.parametrize("platform", PLATFORMS)
def test_an_unrecorded_block_is_never_upgraded_to_complete_on_resave(
    platform: str,
) -> None:
    """A re-saved pre-v70 block writes ``unrecorded``: at v70 an absent key
    means ``complete``, which the block never established."""
    snap = _snapshot(platform, (), parsed=False, state=ExportReadState.UNRECORDED)
    block = _platform_block(snapshot_to_dict(snap), platform)
    assert block["export_read"] == "unrecorded"
    again = snapshot_from_dict(snapshot_to_dict(snap))
    assert getattr(again, platform).export_read is ExportReadState.UNRECORDED
    assert snapshot_export_table_state(again) is ExportTableState.FAILED


@pytest.mark.parametrize("platform", PLATFORMS)
def test_a_complete_read_encodes_as_the_prior_version_apart_from_the_number(
    platform: str,
) -> None:
    """D2: ``complete`` adds no key, so the document differs from the one an
    unstamped block would have written only by ``export_read`` itself."""
    complete = snapshot_to_dict(_snapshot(platform, NAMES))
    partial = snapshot_to_dict(
        _snapshot(platform, NAMES, state=ExportReadState.PARTIAL)
    )
    partial_block = _platform_block(partial, platform)
    assert partial_block.pop("export_read") == "partial"
    assert partial == complete
    assert "export_read" not in _platform_block(complete, platform)


@pytest.mark.parametrize("platform", PLATFORMS)
def test_a_garbled_state_is_refused(platform: str) -> None:
    document = snapshot_to_dict(_snapshot(platform, NAMES))
    _platform_block(document, platform)["export_read"] = "mostly"
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
