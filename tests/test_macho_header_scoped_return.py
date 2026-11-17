# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""``extract_macho`` returns the header-scoped snapshot when headers scope it, and
only falls through to the header-free path when they do not. The export read and
the header scoping are stubbed, so this runs on any host."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import abicheck.extract.export_table_read as export_table_read
import abicheck.workflows.dump.macho as macho
from abicheck.model import AbiSnapshot

LIB = Path("/fixture/libk.dylib")


def _exports_only_read(monkeypatch) -> None:
    meta = SimpleNamespace(
        exports=[SimpleNamespace(name="twice")], install_name="", dependent_libs=[]
    )
    monkeypatch.setattr(export_table_read, "read_exports_or_fail", lambda *_a: meta)


def test_a_header_scoped_snapshot_is_returned_as_is(monkeypatch) -> None:
    _exports_only_read(monkeypatch)
    scoped = AbiSnapshot(library="libk.dylib", version="1")
    monkeypatch.setattr(
        macho, "try_header_scoped_dump", lambda *_a, **_k: (scoped, None)
    )
    assert macho.extract_macho(LIB, "1", headers=[Path("a.h")]) is scoped


class _FellThrough(Exception):
    """Raised by the header-free path's first step, so the test can see it ran."""


def test_an_unscoped_header_set_falls_through_to_the_header_free_path(
    monkeypatch,
) -> None:
    _exports_only_read(monkeypatch)
    monkeypatch.setattr(
        macho, "try_header_scoped_dump", lambda *_a, **_k: (None, "castxml failed")
    )

    def stop(*_a, **_k):
        raise _FellThrough

    monkeypatch.setattr(macho, "export_table_surface_facts", stop)
    with pytest.raises(_FellThrough):
        macho.extract_macho(LIB, "1", headers=[Path("a.h")])
