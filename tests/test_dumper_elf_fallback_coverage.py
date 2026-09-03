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

"""``dumper_elf_fallback._try_dwarf_snapshot`` branch coverage.

``tests/test_dwarf_semantic_ir.py`` exercises this function end-to-end
against real compiled fixtures, but only ever with ``headers=[]`` and
``dwarf_only=False`` on a binary whose DWARF walk actually produced
functions -- three of the function's own branches (the ``--dwarf-only``
"ignoring provided headers" warning, the "headers were actually given"
suppression of the no-headers info log, and the "DWARF produced no
functions/variables of its own" types-only fallback) are never reached
that way. Reproducing them with a real compiled binary is impractical for
the last case (the DWARF DIE walk's own type-reachability filter seeds
its root set from admitted functions/variables -- see
``dwarf_snapshot._DwarfSnapshotBuilder._filter_types_by_reachability``, so
a fixture with real record types but zero admitted functions/variables
is not something a small, understandable C++ source file can reliably
produce). Monkeypatching ``dwarf_snapshot.build_snapshot_from_dwarf`` --
the exact, already-established pattern
``tests/test_dumper_layout_backfill.py`` uses for the same module -- lets
each branch be exercised in isolation against the real control-flow
``_try_dwarf_snapshot`` itself contains, rather than against a toy
reimplementation of it.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

import abicheck.dwarf_snapshot as dwarf_snapshot
from abicheck.dumper_elf_fallback import _try_dwarf_snapshot
from abicheck.model import AbiSnapshot, Function, RecordType
from abicheck.model.dwarf_facts import AdvancedDwarfMetadata, DwarfMetadata


def _args(headers: list[Path], dwarf_only: bool):
    return dict(
        so_path=Path("/nonexistent/lib.so"),
        elf_meta=None,
        dwarf_meta=DwarfMetadata(),
        dwarf_adv=AdvancedDwarfMetadata(),
        version="1.0",
        profile_hint=None,
        headers=headers,
        dwarf_only=dwarf_only,
        session=None,
    )


def test_dwarf_only_with_headers_warns_and_ignores_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--dwarf-only`` with headers still supplied is a real, documented
    footgun (the flag says "use DWARF as primary", not "headers are an
    error") -- it must warn, not fail, and must still build a snapshot."""
    fake_snap = AbiSnapshot(library="lib.so", version="1.0")
    monkeypatch.setattr(
        dwarf_snapshot, "build_snapshot_from_dwarf", lambda *a, **k: fake_snap
    )

    with pytest.warns(UserWarning, match="ignoring provided headers"):
        snap, dwarf_only_types = _try_dwarf_snapshot(
            **_args(headers=[Path("some_header.h")], dwarf_only=True)
        )

    assert snap is fake_snap
    assert dwarf_only_types == []
    # dwarf_only=True forces the "use DWARF" branch even with empty
    # functions/variables -- semantic_ir is always populated on that path.
    assert snap.semantic_ir is not None


def test_headers_present_suppresses_the_no_headers_info_log(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """When real headers were supplied *and* DWARF still produced its own
    functions, the "no headers provided" info log (which exists purely to
    tell a headerless-dump user why DWARF is filling in for headers) must
    not fire -- headers were not, in fact, absent."""
    fake_snap = AbiSnapshot(
        library="lib.so",
        version="1.0",
        functions=[Function(name="f", mangled="_Z1fv", return_type="void")],
    )
    monkeypatch.setattr(
        dwarf_snapshot, "build_snapshot_from_dwarf", lambda *a, **k: fake_snap
    )

    with caplog.at_level(logging.INFO, logger="abicheck.dumper"):
        snap, dwarf_only_types = _try_dwarf_snapshot(
            **_args(headers=[Path("real_header.h")], dwarf_only=False)
        )

    assert snap is fake_snap
    assert dwarf_only_types == []
    assert not any("No headers provided" in rec.message for rec in caplog.records)


def test_no_functions_or_variables_falls_back_to_types_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DWARF producing neither functions nor variables of its own (the
    binary exports only assembly-only/constructor wrappers the DWARF
    subprogram filter rejected) must not be treated as "use this
    snapshot" -- the caller gets ``(None, types)`` so
    ``_build_symbol_only_snapshot`` can take over, preserving the types
    DWARF *did* find without misrepresenting the empty functions/variables
    lists as authoritative."""
    widget = RecordType(name="Widget", kind="struct")
    fake_snap = AbiSnapshot(library="lib.so", version="1.0", types=[widget])
    monkeypatch.setattr(
        dwarf_snapshot, "build_snapshot_from_dwarf", lambda *a, **k: fake_snap
    )

    snap, dwarf_only_types = _try_dwarf_snapshot(**_args(headers=[], dwarf_only=False))

    assert snap is None
    assert dwarf_only_types == [widget]
