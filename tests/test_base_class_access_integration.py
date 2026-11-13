"""Base-class access narrowing on real compiled headers (castxml and clang).

Bug class ``type.base_access_narrowing_silent``: changing a base specifier from
``public`` to ``private`` read as NO_CHANGE because neither header backend
recorded base access. Real dumps of both backends must now report the narrowing
as BASE_CLASS_ACCESS_NARROWED (API_BREAK) and never report a widening.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.dumper import dump
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.workflows.compare_policy import compare_snapshots
from abicheck.workflows.snapshot_factory import finish_provenance

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform != "linux", reason="builds ELF .so files"),
    pytest.mark.skipif(shutil.which("g++") is None, reason="g++ unavailable"),
    pytest.mark.skipif(shutil.which("castxml") is None, reason="castxml unavailable"),
    pytest.mark.skipif(shutil.which("clang++") is None, reason="clang++ unavailable"),
]

_HEADER = """#pragma GCC visibility push(default)
struct LB3 {{ int k; }};
struct LD3 : {spec} LB3 {{ int d; }};
int ld3_use(LD3* p);
#pragma GCC visibility pop
"""

_SOURCE = """#include "lib.h"
int ld3_use(LD3* p) {{ return p->d; }}
"""


def _snapshot(tmp: Path, spec: str, version: str, backend: str):  # type: ignore[no-untyped-def]
    tmp.mkdir(parents=True, exist_ok=True)
    header = tmp / "lib.h"
    header.write_text(_HEADER.format(spec=spec))
    (tmp / "lib.cpp").write_text(_SOURCE)
    so = tmp / "liblib.so"
    subprocess.run(
        [
            "g++",
            "-std=c++17",
            "-g",
            "-shared",
            "-fPIC",
            "-o",
            str(so),
            str(tmp / "lib.cpp"),
        ],
        check=True,
        capture_output=True,
    )
    snap = dump(
        so,
        [header],
        version=version,
        compiler="c++",
        lang="c++",
        header_backend=backend,
        gcc_options="-std=c++17",
    )
    return finish_provenance(snap, [header], None)


def _compare(tmp_path: Path, old_spec: str, new_spec: str, backend: str):  # type: ignore[no-untyped-def]
    old = _snapshot(tmp_path / "old", old_spec, "1", backend)
    new = _snapshot(tmp_path / "new", new_spec, "2", backend)
    return compare_snapshots(old, new).changes


@pytest.mark.parametrize("backend", ["castxml", "clang"])
@pytest.mark.parametrize(
    ("old_spec", "new_spec"),
    [("public", "private"), ("public", "protected"), ("protected", "private")],
)
def test_narrowing_is_reported_by_each_header_backend(
    tmp_path: Path, backend: str, old_spec: str, new_spec: str
) -> None:
    changes = _compare(tmp_path, old_spec, new_spec, backend)
    assert [
        (c.kind, c.old_value, c.new_value)
        for c in changes
        if c.kind is ChangeKind.BASE_CLASS_ACCESS_NARROWED
    ] == [(ChangeKind.BASE_CLASS_ACCESS_NARROWED, old_spec, new_spec)]


@pytest.mark.parametrize("backend", ["castxml", "clang"])
def test_widening_is_not_reported(tmp_path: Path, backend: str) -> None:
    changes = _compare(tmp_path, "private", "public", backend)
    assert ChangeKind.BASE_CLASS_ACCESS_NARROWED not in {c.kind for c in changes}
