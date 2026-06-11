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

"""Offline tests for the shared conda-forge engine (``conda_harness``).

Only the pure resolution/naming/extraction helpers are exercised here (no
network, conda, or abicheck). The orchestration in ``validate`` is
integration-only and validated by hand against real binaries.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path("validation/scripts/conda_harness.py")


def _load_module():
    spec = importlib.util.spec_from_file_location("conda_harness", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# A trimmed anaconda.org-style payload: two versions, two subdirs, multiple builds.
_API = {
    "files": [
        {
            "version": "2.9.4",
            "basename": "linux-64/libxml2-2.9.4-0.tar.bz2",
            "attrs": {"subdir": "linux-64"},
        },
        {
            "version": "2.9.4",
            "basename": "linux-64/libxml2-2.9.4-4.tar.bz2",
            "attrs": {"subdir": "linux-64"},
        },
        {
            "version": "2.9.4",
            "basename": "linux-64/libxml2-2.9.4-2.tar.bz2",
            "attrs": {"subdir": "linux-64"},
        },
        {
            "version": "2.9.4",
            "basename": "osx-64/libxml2-2.9.4-4.tar.bz2",
            "attrs": {"subdir": "osx-64"},
        },
        {
            "version": "2.9.3",
            "basename": "linux-64/libxml2-2.9.3-9.tar.bz2",
            "attrs": {"subdir": "linux-64"},
        },
    ]
}


def test_conda_download_url_joins_channel_and_basename() -> None:
    mod = _load_module()
    assert (
        mod.conda_download_url("linux-64/libxml2-2.9.4-4.tar.bz2")
        == "https://conda.anaconda.org/conda-forge/linux-64/libxml2-2.9.4-4.tar.bz2"
    )
    # tolerate a stray leading slash on the basename
    assert mod.conda_download_url("/linux-64/x.conda").count("conda-forge//") == 0


def test_select_conda_basename_picks_newest_build_in_subdir() -> None:
    mod = _load_module()
    # highest build number (-4) wins among the three linux-64 builds of 2.9.4
    assert (
        mod.select_conda_basename(_API, "2.9.4", "linux-64")
        == "linux-64/libxml2-2.9.4-4.tar.bz2"
    )
    # subdir is respected
    assert (
        mod.select_conda_basename(_API, "2.9.4", "osx-64")
        == "osx-64/libxml2-2.9.4-4.tar.bz2"
    )
    # missing version -> None (pair stays UNCOMPARABLE upstream)
    assert mod.select_conda_basename(_API, "9.9.9", "linux-64") is None
    assert mod.select_conda_basename(_API, "2.9.4", "win-64") is None


def test_build_number_parses_hash_and_plain_builds() -> None:
    mod = _load_module()
    assert mod.build_number("linux-64/zstd-1.5.5-hfc55251_0.conda") == 0
    assert mod.build_number("linux-64/libxml2-2.9.4-4.tar.bz2") == 4
    assert mod.build_number("linux-64/tbb-2021.9.0-hf52228f_3.conda") == 3
    assert mod.build_number("no-build-number") == -1


def test_logical_name_strips_so_suffix_and_embedded_version() -> None:
    mod = _load_module()
    assert mod.logical_name("lib/libxml2.so.2.9.4") == "libxml2"
    assert mod.logical_name("lib/libcapnp-1.4.0.so") == "libcapnp"
    assert mod.logical_name("lib/libssl.so.3") == "libssl"


def test_extract_tar_zst_python_backend(tmp_path: Path) -> None:
    # Guards the .conda path: when zstandard is importable the loop must extract
    # a .tar.zst without any system zstd binary. Skips where it isn't installed
    # (the runtime then relies on the documented tar --zstd fallback).
    import io
    import tarfile

    zstandard = pytest.importorskip("zstandard")
    mod = _load_module()

    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tf:
        payload = b"\x7fELF-not-a-real-elf"
        info = tarfile.TarInfo("lib/libfoo.so.1")
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))

    zst = tmp_path / "pkg-foo.tar.zst"
    zst.write_bytes(zstandard.ZstdCompressor().compress(raw.getvalue()))

    into = tmp_path / "out"
    into.mkdir()
    mod.extract_tar_zst(zst, into)

    extracted = into / "lib" / "libfoo.so.1"
    assert extracted.is_file()
    assert extracted.read_bytes() == payload


def test_extract_sos_skips_non_elf_linker_scripts(tmp_path: Path) -> None:
    # A conda package can ship a GNU ld linker-script `.so` (plain text). It must
    # not be recorded as a shared object, or abicheck gets fed a text file and an
    # otherwise comparable pair is wrongly skipped.
    import tarfile

    mod = _load_module()

    pkgdir = tmp_path / "stage"
    lib = pkgdir / "lib"
    lib.mkdir(parents=True)
    (lib / "libfoo.so.1").write_bytes(b"\x7fELF\x02\x01\x01\x00rest")  # real ELF
    (lib / "libfoo.so").write_text("INPUT(libfoo.so.1)\n")  # ld linker script

    pkg = tmp_path / "pkg-foo-1.0-0.tar.bz2"
    with tarfile.open(pkg, "w:bz2") as tf:
        tf.add(lib / "libfoo.so.1", arcname="lib/libfoo.so.1")
        tf.add(lib / "libfoo.so", arcname="lib/libfoo.so")

    sos = mod.extract_sos(pkg, tmp_path / "out")
    assert list(sos) == ["libfoo"]  # one logical lib, from the ELF only
    assert sos["libfoo"].endswith("libfoo.so.1")
