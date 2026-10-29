"""A real ``abicheck dump`` document survives the public load/save round trip.

Generalizes the ``dump_provenance`` defect: ``load_snapshot`` then
``save_snapshot`` silently dropped the provenance block the ``dump`` CLI
writes (``sections.provenance.payload.dump_provenance``), because the typed
``AbiSnapshot`` had no slot for it. Rather than pin that one key, this walks
every key path of a dumped document and requires each one to survive
``load -> save -> load -> save``. A future section that the typed model drops
then fails here without anyone having to name it first.

Builds three small libraries from source (C with headers, C++ with headers,
C without headers) so the documents differ in which evidence sections exist.
Requires gcc, g++ and castxml.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _snapshot_document_reader import read_snapshot_document

from abicheck.serialization import load_snapshot, save_snapshot
from tests._abicheck_subprocess import abicheck_command

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform != "linux", reason="builds an ELF .so"),
    pytest.mark.skipif(shutil.which("gcc") is None, reason="gcc not found"),
    pytest.mark.skipif(shutil.which("g++") is None, reason="g++ not found"),
    pytest.mark.skipif(shutil.which("castxml") is None, reason="castxml not found"),
]

#: Key paths a load -> save round trip is allowed to change. Empty on purpose:
#: every key a real dump writes is reproduced by the public API today, so an
#: entry added here must name why a re-save should not keep that key.
VOLATILE_KEY_PATHS: frozenset[str] = frozenset()

_C_SRC = (
    "int add(int a, int b) { return a + b; }\nint sub(int a, int b) { return a - b; }\n"
)
_C_HDR = "#pragma once\nint add(int a, int b);\nint sub(int a, int b);\n"
_CXX_SRC = (
    "namespace ns {\n"
    "struct Box { int v; int get() const { return v; } };\n"
    "int mul(int a, int b) { return a * b; }\n"
    "}\n"
    "int twice(int x) { return 2 * x; }\n"
)
_CXX_HDR = (
    "#pragma once\n"
    "namespace ns {\n"
    "struct Box { int v; int get() const; };\n"
    "int mul(int a, int b);\n"
    "}\n"
    "int twice(int x);\n"
)
_C_STRUCT_SRC = (
    "typedef struct { int x; double y; } pt;\n"
    "int norm1(pt p) { return p.x + (int)p.y; }\n"
)

#: name -> (compiler language, source, header or None)
CASES: dict[str, tuple[str, str, str | None]] = {
    "c_with_headers": ("c", _C_SRC, _C_HDR),
    "cpp_with_headers": ("cpp", _CXX_SRC, _CXX_HDR),
    "c_binary_only": ("c", _C_STRUCT_SRC, None),
}


def key_paths(node: object, prefix: str = "") -> set[str]:
    """Every dict key path in *node*, ``[]`` standing for any list element.

    List positions are not part of a path, so a reordered list does not read
    as a dropped key, but a key missing from every element does.
    """
    out: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            path = f"{prefix}/{key}"
            out.add(path)
            out |= key_paths(value, path)
    elif isinstance(node, list):
        for item in node:
            out |= key_paths(item, f"{prefix}/[]")
    return out


def _build_and_dump(tmp_path: Path, name: str) -> Path:
    lang, src, header = CASES[name]
    src_file = tmp_path / f"{name}.{'c' if lang == 'c' else 'cpp'}"
    src_file.write_text(src, encoding="utf-8")
    so = tmp_path / f"lib{name}.so"
    compiler = "gcc" if lang == "c" else "g++"
    subprocess.run(
        [compiler, "-shared", "-fPIC", "-g", "-o", str(so), str(src_file)],
        check=True,
        capture_output=True,
        text=True,
    )
    dumped = tmp_path / f"{name}.abicheck.json"
    cmd = abicheck_command("dump", str(so), "--version", "1.0")
    if header is not None:
        header_file = tmp_path / f"{name}.h"
        header_file.write_text(header, encoding="utf-8")
        cmd += ["-H", str(header_file)]
    cmd += ["-o", str(dumped)]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    return dumped


@pytest.mark.parametrize("name", sorted(CASES))
def test_public_round_trip_keeps_every_dumped_key_path(
    tmp_path: Path, name: str
) -> None:
    dumped = _build_and_dump(tmp_path, name)
    original = json.loads(dumped.read_text(encoding="utf-8"))
    expected = key_paths(original) - VOLATILE_KEY_PATHS

    first = tmp_path / "first.abicheck.json"
    second = tmp_path / "second.abicheck.json"
    save_snapshot(load_snapshot(dumped), first)
    save_snapshot(load_snapshot(first), second)

    for out in (first, second):
        missing = sorted(expected - key_paths(json.loads(out.read_text("utf-8"))))
        assert missing == [], f"{out.name} lost key paths: {missing}"


@pytest.mark.parametrize("name", sorted(CASES))
def test_public_round_trip_keeps_dump_provenance_value(
    tmp_path: Path, name: str
) -> None:
    dumped = _build_and_dump(tmp_path, name)
    original = read_snapshot_document(dumped)["dump_provenance"]
    assert set(original) >= {
        "requested_depth",
        "effective_depth",
        "degraded",
        "frontend",
    }

    out = tmp_path / "rt.abicheck.json"
    save_snapshot(load_snapshot(dumped), out)

    assert read_snapshot_document(out)["dump_provenance"] == original
