"""TEMPORARY Windows diagnostic: how the fields of _TP_CALLBACK_ENVIRON_V3 come out.

Runs only on Windows, on the case the depth projection flags as unchanged
(case226). It dumps each side with headers and without, and prints every
_TP_CALLBACK_ENVIRON* field from each source (castxml record types and the PDB
debug layout). It fails on purpose so the Windows CI log carries the output.
Removed once the false type_field_type_changed is root-caused.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from abicheck.dumper import dump
from tests.test_depth_projection_matches_shallow_dump import (
    _built_case,
    _header_list,
)

CASE = "case226_header_only_template_default_unchanged"


def _read(fn):
    try:
        return fn()
    except Exception as exc:  # diagnostic: record the failure, never crash
        return f"<{type(exc).__name__}: {exc}>"


@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "win32", reason="Windows diagnostic only")
def test_windows_callback_environ_fields(
    tmp_path: Path, shared_cmake_build_dir: Path | None
) -> None:
    v1_lib, v2_lib, v1_hdr, v2_hdr = _built_case(CASE, tmp_path, shared_cmake_build_dir)
    lines: list[str] = []
    for side, lib, hdr in (("old", v1_lib, v1_hdr), ("new", v2_lib, v2_hdr)):
        for route, headers in (("headers", _header_list(hdr)), ("binary", [])):
            snap = dump(lib, headers=headers, version=side)
            decl = _read(lambda: snap.declarations)
            types = _read(lambda: list(decl.types))
            if isinstance(types, list):
                for rec in types:
                    if "CALLBACK_ENVIRON" in str(getattr(rec, "name", "")):
                        for f in _read(lambda rec=rec: list(rec.fields)) or []:
                            lines.append(
                                f"[{side}/{route}] castxml.record {rec.name}: {f!r}"[
                                    :300
                                ]
                            )
            layout = _read(lambda: decl.debug_layout)
            structs = _read(lambda: layout.structs) if layout is not None else None
            if isinstance(structs, dict):
                for name, sl in structs.items():
                    if "CALLBACK_ENVIRON" in name:
                        for f in _read(lambda sl=sl: list(sl.fields)) or []:
                            lines.append(
                                f"[{side}/{route}] pdb.layout {name}: {f!r}"[:300]
                            )
            else:
                lines.append(f"[{side}/{route}] debug_layout={str(layout)[:120]}")
    raise AssertionError("WINDOWS-DIAG\n" + "\n".join(lines))
