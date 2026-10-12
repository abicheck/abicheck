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

"""Advanced DWARF extraction records its own status (known gap).

``advanced_facts_collected`` used to infer "extraction ran" from the payload,
so a run that completed and found nothing read the same as a run that never
happened. These tests pin the explicit status: set by the extraction, read by
the predicate, carried into the compare contract as ``not_evaluated`` with a
reason that names the state, and never written into the stored document.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.analysis_assurance import _dwarf_context_status
from abicheck.checker import compare
from abicheck.dwarf_presence import cheap_dwarf_presence_metadata
from abicheck.dwarf_unified import parse_dwarf
from abicheck.model import AbiSnapshot
from abicheck.model.dwarf_facts import (
    AdvancedDwarfMetadata,
    AdvancedExtractionStatus,
    advanced_facts_collected,
)
from abicheck.serialization import snapshot_from_dict
from abicheck.storage.snapshot_encode import snapshot_content_digest, snapshot_to_json

S = AdvancedExtractionStatus

_PAYLOAD = {"calling_conventions": {"_Z1fv": "fastcall"}}


def _snap(**kwargs: object) -> AbiSnapshot:
    defaults: dict[str, object] = dict(
        library="lib.so", version="1.0", from_headers=True
    )
    defaults.update(kwargs)
    return AbiSnapshot(**defaults)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("status", "has_dwarf", "payload", "arch", "expected"),
    [
        # A run that completed and established nothing is still a run. With no
        # target arch the old inference read this as "not collected".
        (S.COMPLETED, True, {}, "", True),
        (S.COMPLETED, True, {}, "x86_64", True),
        (S.COMPLETED, True, _PAYLOAD, "", True),
        # Completed, but the binary had no DWARF: nothing was collected.
        (S.COMPLETED, False, {}, "x86_64", False),
        # The explicit status wins over whatever the payload happens to hold.
        (S.NOT_RUN, True, {}, "", False),
        (S.NOT_RUN, True, _PAYLOAD, "x86_64", False),
        (S.FAILED, True, {}, "x86_64", False),
        (S.FAILED, True, _PAYLOAD, "x86_64", False),
        # Unrecorded (a stored snapshot, or a hand-built object) keeps the old
        # inference, so no existing reading changes.
        (S.UNRECORDED, True, {}, "", False),
        (S.UNRECORDED, True, {}, "x86_64", True),
        (S.UNRECORDED, True, _PAYLOAD, "", True),
        (S.UNRECORDED, False, _PAYLOAD, "x86_64", False),
    ],
)
def test_predicate_follows_the_recorded_status(
    status, has_dwarf, payload, arch, expected
):
    meta = AdvancedDwarfMetadata(
        has_dwarf=has_dwarf, target_arch=arch, extraction=status, **payload
    )
    assert advanced_facts_collected(meta) is expected


# parse_dwarf reads ELF only; gcc on macOS emits Mach-O, which reads as FAILED.
@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "linux", reason="needs an ELF-emitting gcc")
def test_real_parse_records_completed_and_presence_records_not_run(tmp_path: Path):
    if shutil.which("gcc") is None:
        pytest.skip("needs gcc to build a real shared library")
    src = tmp_path / "a.c"
    src.write_text("int x = 1;\n")
    so = tmp_path / "liba.so"
    subprocess.run(
        ["gcc", "-g", "-shared", "-fPIC", "-o", str(so), str(src)], check=True
    )

    _meta, adv = parse_dwarf(so)
    assert adv.has_dwarf is True
    assert adv.extraction is S.COMPLETED
    assert advanced_facts_collected(adv) is True

    _meta, presence = cheap_dwarf_presence_metadata(so)
    assert presence.has_dwarf is True
    assert presence.extraction is S.NOT_RUN
    assert advanced_facts_collected(presence) is False


@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "linux", reason="needs an ELF-emitting gcc")
def test_binary_without_dwarf_records_completed_not_failed(tmp_path: Path):
    if shutil.which("gcc") is None:
        pytest.skip("needs gcc to build a real shared library")
    src = tmp_path / "a.c"
    src.write_text("int x = 1;\n")
    so = tmp_path / "liba.so"
    subprocess.run(["gcc", "-shared", "-fPIC", "-o", str(so), str(src)], check=True)

    _meta, adv = parse_dwarf(so)
    assert adv.has_dwarf is False
    assert adv.extraction is S.COMPLETED
    assert advanced_facts_collected(adv) is False


@pytest.mark.parametrize("make_input", ["missing", "not_elf"])
def test_unreadable_input_records_failed_not_no_dwarf(make_input, tmp_path: Path):
    path = tmp_path / "input.so"
    if make_input == "not_elf":
        path.write_bytes(b"this is not an ELF file" * 8)
    _meta, adv = parse_dwarf(path)
    assert adv.has_dwarf is False
    assert adv.extraction is S.FAILED
    assert advanced_facts_collected(adv) is False


@pytest.mark.parametrize(
    ("state", "reason_fragment"),
    [(S.FAILED, "failed"), (S.NOT_RUN, "did not run")],
)
def test_unusable_side_is_not_evaluated_with_a_reason_naming_the_state(
    state, reason_fragment
):
    collected = AdvancedDwarfMetadata(
        has_dwarf=True,
        target_arch="x86_64",
        extraction=S.COMPLETED,
        **_PAYLOAD,
    )
    unusable = AdvancedDwarfMetadata(has_dwarf=True, extraction=state)
    result = compare(_snap(dwarf_advanced=collected), _snap(dwarf_advanced=unusable))
    record = next(d for d in result.detector_results if d.name == "advanced_dwarf")
    assert record.not_evaluated is True
    assert record.coverage_gap is not None
    assert reason_fragment in record.coverage_gap


def test_completed_empty_parse_runs_the_detector_instead_of_being_skipped():
    empty = AdvancedDwarfMetadata(has_dwarf=True, extraction=S.COMPLETED)
    result = compare(_snap(dwarf_advanced=empty), _snap(dwarf_advanced=empty))
    record = next(d for d in result.detector_results if d.name == "advanced_dwarf")
    assert record.not_evaluated is False


def test_failed_side_reads_as_asymmetric_not_as_no_debug_info():
    collected = AdvancedDwarfMetadata(
        has_dwarf=True, target_arch="x86_64", extraction=S.COMPLETED, **_PAYLOAD
    )
    failed = AdvancedDwarfMetadata(has_dwarf=True, extraction=S.FAILED)
    status, notes = _dwarf_context_status(
        _snap(dwarf_advanced=collected), _snap(dwarf_advanced=failed)
    )
    assert status == "asymmetric"
    assert any("failed" in note for note in notes)


def test_status_is_not_persisted_so_the_stored_document_is_unchanged():
    recorded = _snap(
        dwarf_advanced=AdvancedDwarfMetadata(
            has_dwarf=True, target_arch="x86_64", extraction=S.COMPLETED
        )
    )
    unrecorded = _snap(
        dwarf_advanced=AdvancedDwarfMetadata(has_dwarf=True, target_arch="x86_64")
    )
    assert snapshot_to_json(recorded) == snapshot_to_json(unrecorded)
    assert snapshot_content_digest(recorded) == snapshot_content_digest(unrecorded)

    loaded = snapshot_from_dict(json.loads(snapshot_to_json(recorded)))
    assert loaded.declarations.debug_advanced is not None
    assert loaded.declarations.debug_advanced.extraction is S.UNRECORDED
    # Provenance, not content: the decoded copy still compares equal.
    assert loaded.declarations.debug_advanced == recorded.declarations.debug_advanced
