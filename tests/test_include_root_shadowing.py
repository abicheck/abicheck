# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""An inferred ``-H`` include root never lets a package header shadow libc.

``_implicit_header_includes`` used to add every ``-H`` file's own directory
as ``-I``. For an umbrella below a conventional root (protobuf's
``include/google/protobuf/message.h``), that put the interior directory ahead
of the system dirs, so its flat ``endian.h`` captured glibc's
``#include <endian.h>`` from ``sys/types.h`` and the parse failed. The rule:
under an ``include``/``inc`` root only the root is inferred; a flat layout
(no such root) still infers the file's own directory.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from abicheck.header_utils import _implicit_header_includes

# libc header names a library is likely to reuse for its own flat files.
_LIBC_NAMES = ("endian.h", "string.h", "time.h", "stdint.h", "limits.h", "assert.h")


@pytest.mark.parametrize("root_name", ["include", "inc", "Include"])
@pytest.mark.parametrize("depth", [1, 2, 3])
def test_interior_directory_under_a_root_is_not_inferred(
    tmp_path: Path, root_name: str, depth: int
) -> None:
    root = tmp_path / root_name
    interior = root.joinpath(*[f"d{i}" for i in range(depth)])
    interior.mkdir(parents=True)
    umbrella = interior / "api.h"
    umbrella.write_text("")
    for name in _LIBC_NAMES:
        (interior / name).write_text("")
    dirs = _implicit_header_includes([umbrella])
    assert root in dirs
    # No inferred dir may hold a libc-named file the umbrella's tree ships.
    for d in dirs:
        assert not any((d / name).exists() for name in _LIBC_NAMES), d


def test_umbrella_directly_in_root_still_infers_root(tmp_path: Path) -> None:
    root = tmp_path / "include"
    root.mkdir()
    (root / "dnnl.hpp").write_text("")
    assert _implicit_header_includes([root / "dnnl.hpp"]) == [root]


def test_flat_layout_still_infers_own_directory(tmp_path: Path) -> None:
    flat = tmp_path / "mylib"
    flat.mkdir()
    (flat / "api.h").write_text("")
    assert _implicit_header_includes([flat / "api.h"]) == [flat]


@pytest.mark.integration
@pytest.mark.skipif(
    shutil.which("castxml") is None or shutil.which("gcc") is None,
    reason="needs castxml and gcc",
)
def test_dump_parses_umbrella_whose_package_ships_endian_h(tmp_path: Path) -> None:
    """The protobuf shape end to end: ``dump -H include/pkg/api.h`` parses."""
    pkg = tmp_path / "include" / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "endian.h").write_text('#error "package endian.h shadowed <endian.h>"\n')
    (pkg / "api.h").write_text("#include <sys/types.h>\nint pkg_api(int);\n")
    src = tmp_path / "api.c"
    src.write_text("int pkg_api(int a) { return a; }\n")
    lib = tmp_path / "libpkg.so"
    subprocess.run(["gcc", "-shared", "-fPIC", "-o", str(lib), str(src)], check=True)
    out = tmp_path / "snap.json"
    proc = subprocess.run(
        ["abicheck", "dump", str(lib), "-H", str(pkg / "api.h"), "-o", str(out)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "pkg_api" in out.read_text()
