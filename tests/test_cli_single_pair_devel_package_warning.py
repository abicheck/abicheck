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

"""A development package given as ``-H/--header`` to a single-pair compare is named.

Only the release fan-out reads a development package's headers. A single-pair
``compare LIB_OLD LIB_NEW`` used to drop one without a word, leaving only a
per-library "no headers provided" note. The package is still not read (the
exit code is the comparison's own), but the stderr now names it and the side it
was given for.
"""

from __future__ import annotations

import io
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        shutil.which("g++") is None,
        reason="needs g++ to build the fixture libraries",
    ),
    pytest.mark.skipif(sys.platform != "linux", reason="builds an ELF .so"),
]

_SOURCE = "struct pt { int x; int y; };\nint keep_me(int a) { return a; }\n"
_HEADER = "struct pt { int x; int y; };\nint keep_me(int a);\n"


def _build(root: Path, name: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    src = root / f"{name}.c"
    src.write_text(_SOURCE, encoding="utf-8")
    so = root / f"lib{name}.so"
    subprocess.run(
        ["g++", "-x", "c++", "-fPIC", "-shared", "-o", str(so), str(src)],
        check=True,
        capture_output=True,
        text=True,
    )
    return so


@pytest.fixture(scope="module")
def libs(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    root = tmp_path_factory.mktemp("single_pair_devel_libs")
    return _build(root / "old", "v1"), _build(root / "new", "v2")


@pytest.fixture(autouse=True)
def _isolated_snapshot_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg_cache"))


def _devel_package(tmp_path: Path) -> Path:
    """A development package archive: one public header under ``usr/include``."""
    archive = tmp_path / "libfoo-devel.tar.gz"
    body = _HEADER.encode("utf-8")
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo("usr/include/api.h")
        info.size = len(body)
        tar.addfile(info, io.BytesIO(body))
    return archive


def _compare_output(libs: tuple[Path, Path], header_args: list[str]) -> str:
    old_so, new_so = libs
    result = CliRunner().invoke(
        main, ["compare", str(old_so), str(new_so), *header_args]
    )
    return result.output


@pytest.mark.parametrize(
    ("header_args_for", "scope"),
    [
        (lambda pkg: ["--header", str(pkg)], "both sides"),
        (lambda pkg: [f"--header=new={pkg}"], "new side"),
        (lambda pkg: [f"--header=old={pkg}"], "old side"),
    ],
    ids=["both", "new", "old"],
)
def test_a_devel_package_given_to_a_single_pair_compare_is_named(
    libs: tuple[Path, Path],
    tmp_path: Path,
    header_args_for,
    scope: str,
) -> None:
    pkg = _devel_package(tmp_path)

    output = _compare_output(libs, header_args_for(pkg))

    assert f"development package '{pkg}' ignored for the {scope}" in output, output


def test_a_plain_header_names_no_package(
    libs: tuple[Path, Path], tmp_path: Path
) -> None:
    """Negative control: a header file is read, so no package is reported."""
    header = tmp_path / "api.h"
    header.write_text(_HEADER, encoding="utf-8")

    output = _compare_output(libs, ["--header", str(header)])

    assert "development package" not in output, output


def test_no_header_names_no_package(libs: tuple[Path, Path]) -> None:
    output = _compare_output(libs, [])

    assert "development package" not in output, output
