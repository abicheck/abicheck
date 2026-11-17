# Copyright 2026 Nikolay Petrov
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

"""The one PE PDB decision (``pe_pdb_debug_kwargs``), with the PDB lookup stubbed.

The helper is what the dumper's PE builder and the adapter's header-free path
both call, so each branch is pinned here: no PDB, a full PDB, and a PDB read
under presence-only (presence kept, advanced pass not run).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from abicheck.model.dwarf_facts import AdvancedExtractionStatus
from abicheck.workflows.dump import pe as pe_dump

LIB = Path("/fixture/libk.dll")


def _stub(monkeypatch, meta):
    monkeypatch.setattr(
        pe_dump,
        "extract_pdb_debug_outcome",
        lambda path, pdb_path: (meta, SimpleNamespace(extraction="completed"), None),
    )


def test_no_pdb_supplies_no_debug_inputs(monkeypatch) -> None:
    _stub(monkeypatch, None)
    assert pe_dump.pe_pdb_debug_kwargs(LIB) == {}


def test_a_full_pdb_read_keeps_its_advanced_pass(monkeypatch) -> None:
    meta = SimpleNamespace(has_dwarf=True)
    _stub(monkeypatch, meta)
    kw = pe_dump.pe_pdb_debug_kwargs(LIB)
    assert kw["dwarf"] is meta
    assert kw["dwarf_advanced"].extraction == "completed"


def test_presence_only_keeps_presence_and_skips_the_advanced_pass(monkeypatch) -> None:
    meta = SimpleNamespace(has_dwarf=True)
    _stub(monkeypatch, meta)
    kw = pe_dump.pe_pdb_debug_kwargs(LIB, presence_only=True)
    assert kw["dwarf"] is meta
    assert kw["dwarf_advanced"].extraction is AdvancedExtractionStatus.NOT_RUN
    assert kw["dwarf_advanced"].has_dwarf is True
