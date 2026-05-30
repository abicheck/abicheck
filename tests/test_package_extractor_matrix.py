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

"""End-to-end extractor matrix for package.py (stdlib formats).

Existing `test_package.py` covers detection and the individual security
helpers in depth. This file adds the missing layer: *real* (non-mocked)
round-trip extraction across every stdlib-backed archive format, plus a
single parametrized malicious-payload matrix so each archive type is
checked against the same attack vectors (path traversal, absolute paths,
symlink escape, device/FIFO entries).

Formats requiring external tools (rpm2cpio/cpio, ar, zstd) are intentionally
out of scope here — they are exercised via mocks in `test_package_unit_extra.py`.
"""

from __future__ import annotations

import struct
import tarfile
import zipfile
from pathlib import Path

import pytest

from abicheck.errors import ExtractionSecurityError
from abicheck.package import (
    CondaExtractor,
    TarExtractor,
    WheelExtractor,
    detect_extractor,
    discover_shared_libraries,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _minimal_elf_so() -> bytes:
    """Return bytes of a minimal 64-bit LE ELF shared object (ET_DYN)."""
    e_ident = b"\x7fELF" + b"\x02" + b"\x01" + b"\x01" + b"\x00" * 9
    e_type = struct.pack("<H", 3)  # ET_DYN
    e_machine = struct.pack("<H", 0x3E)  # EM_X86_64
    e_version = struct.pack("<I", 1)
    rest = b"\x00" * (64 - 16 - 2 - 2 - 4)
    return e_ident + e_type + e_machine + e_version + rest


_TAR_MODES = {
    "tar": ("w", ".tar"),
    "tar.gz": ("w:gz", ".tar.gz"),
    "tar.bz2": ("w:bz2", ".tar.bz2"),
    "tar.xz": ("w:xz", ".tar.xz"),
    "tgz": ("w:gz", ".tgz"),
}


def _write_tar(path: Path, members: dict[str, bytes], mode: str) -> None:
    import io

    with tarfile.open(path, mode) as tf:
        for name, data in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))


def _write_zip(path: Path, members: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)


# ===========================================================================
# Real round-trip extraction across stdlib formats
# ===========================================================================


class TestTarVariantRoundTrip:
    """Each tar compression variant extracts a real ELF .so end to end."""

    @pytest.mark.parametrize("fmt", sorted(_TAR_MODES))
    def test_extract_and_discover(self, fmt: str, tmp_path: Path) -> None:
        mode, suffix = _TAR_MODES[fmt]
        pkg = tmp_path / f"libfoo-1.0{suffix}"
        _write_tar(pkg, {"usr/lib/libfoo.so.1": _minimal_elf_so()}, mode)

        ext = detect_extractor(pkg)
        assert isinstance(ext, TarExtractor)

        out = tmp_path / "out"
        out.mkdir()
        result = ext.extract(pkg, out)

        libs = discover_shared_libraries(result.lib_dir)
        assert [p.name for p in libs] == ["libfoo.so.1"]
        assert (out / "usr/lib/libfoo.so.1").read_bytes() == _minimal_elf_so()


class TestWheelRoundTrip:
    def test_extract_and_discover(self, tmp_path: Path) -> None:
        pkg = tmp_path / "foo-1.0-py3-none-any.whl"
        _write_zip(
            pkg,
            {
                "foo/_core.so": _minimal_elf_so(),
                "foo/__init__.py": b"# pure python\n",
                "foo-1.0.dist-info/METADATA": b"Name: foo\n",
            },
        )

        ext = detect_extractor(pkg)
        assert isinstance(ext, WheelExtractor)

        out = tmp_path / "out"
        out.mkdir()
        result = ext.extract(pkg, out)

        libs = discover_shared_libraries(result.lib_dir, include_private=True)
        assert any(p.name == "_core.so" for p in libs)


class TestCondaLegacyRoundTrip:
    """Legacy conda packages are bz2 tarballs — pure stdlib, no zstd needed."""

    def test_extract_and_discover(self, tmp_path: Path) -> None:
        pkg = tmp_path / "foo-1.0-h123_0.tar.bz2"
        _write_tar(
            pkg,
            {
                "lib/libfoo.so": _minimal_elf_so(),
                "info/index.json": b'{"name": "foo"}',
            },
            "w:bz2",
        )

        ext = detect_extractor(pkg)
        assert isinstance(ext, CondaExtractor)

        out = tmp_path / "out"
        out.mkdir()
        result = ext.extract(pkg, out)
        libs = discover_shared_libraries(result.lib_dir)
        assert any(p.name == "libfoo.so" for p in libs)


# ===========================================================================
# Unified malicious-payload matrix
# ===========================================================================

# (member_name, reason_substring) — paths that must be rejected on extract.
_MALICIOUS_PATHS = [
    pytest.param("../escape.so", "traversal", id="parent-traversal"),
    pytest.param("a/b/../../../escape.so", "traversal", id="nested-traversal"),
    pytest.param("/abs/evil.so", "absolute", id="absolute-path"),
    pytest.param("nested/../../escape.so", "traversal", id="mixed-traversal"),
]


class TestTarMaliciousPaths:
    @pytest.mark.parametrize("member,_reason", _MALICIOUS_PATHS)
    def test_rejects(self, member: str, _reason: str, tmp_path: Path) -> None:
        pkg = tmp_path / "evil.tar"
        _write_tar(pkg, {member: b"payload"}, "w")
        out = tmp_path / "out"
        out.mkdir()
        with pytest.raises(ExtractionSecurityError):
            TarExtractor().extract(pkg, out)
        # Nothing escaped the extraction root.
        assert not (tmp_path / "escape.so").exists()
        assert not Path("/abs/evil.so").exists()


class TestWheelMaliciousPaths:
    @pytest.mark.parametrize("member,_reason", _MALICIOUS_PATHS)
    def test_rejects(self, member: str, _reason: str, tmp_path: Path) -> None:
        pkg = tmp_path / "evil-1.0-py3-none-any.whl"
        _write_zip(pkg, {member: b"payload"})
        out = tmp_path / "out"
        out.mkdir()
        with pytest.raises(ExtractionSecurityError):
            WheelExtractor().extract(pkg, out)
        assert not (tmp_path / "escape.so").exists()


class TestTarSymlinkEscape:
    def test_symlink_escaping_root_rejected(self, tmp_path: Path) -> None:
        pkg = tmp_path / "evil.tar"
        with tarfile.open(pkg, "w") as tf:
            info = tarfile.TarInfo(name="link.so")
            info.type = tarfile.SYMTYPE
            info.linkname = "../../../../etc/passwd"
            tf.addfile(info)
        out = tmp_path / "out"
        out.mkdir()
        with pytest.raises(ExtractionSecurityError):
            TarExtractor().extract(pkg, out)

    def test_symlink_within_root_allowed(self, tmp_path: Path) -> None:
        pkg = tmp_path / "ok.tar"
        with tarfile.open(pkg, "w") as tf:
            real = tarfile.TarInfo(name="real.so")
            real.size = len(_minimal_elf_so())
            import io

            tf.addfile(real, io.BytesIO(_minimal_elf_so()))
            link = tarfile.TarInfo(name="alias.so")
            link.type = tarfile.SYMTYPE
            link.linkname = "real.so"
            tf.addfile(link)
        out = tmp_path / "out"
        out.mkdir()
        # Should not raise — target stays inside root.
        TarExtractor().extract(pkg, out)
        assert (out / "real.so").exists()


class TestTarSpecialFileRejection:
    @pytest.mark.parametrize(
        "tartype,label",
        [
            (tarfile.CHRTYPE, "char-device"),
            (tarfile.BLKTYPE, "block-device"),
            (tarfile.FIFOTYPE, "fifo"),
        ],
    )
    def test_device_and_fifo_rejected(
        self, tartype: bytes, label: str, tmp_path: Path
    ) -> None:
        pkg = tmp_path / "evil.tar"
        with tarfile.open(pkg, "w") as tf:
            info = tarfile.TarInfo(name=f"dev/{label}")
            info.type = tartype
            tf.addfile(info)
        out = tmp_path / "out"
        out.mkdir()
        with pytest.raises(ExtractionSecurityError):
            TarExtractor().extract(pkg, out)


class TestExtractorDispatchPriority:
    """Auto-detection picks the right extractor for ambiguous names."""

    def test_conda_legacy_beats_tar_for_info_tarball(self, tmp_path: Path) -> None:
        # A multi-dash .tar.bz2 with an info/ dir is conda, not plain tar.
        pkg = tmp_path / "foo-1.0-h123_0.tar.bz2"
        _write_tar(
            pkg,
            {"lib/libfoo.so": _minimal_elf_so(), "info/index.json": b"{}"},
            "w:bz2",
        )
        assert isinstance(detect_extractor(pkg), CondaExtractor)

    def test_plain_tarbz2_is_tar(self, tmp_path: Path) -> None:
        pkg = tmp_path / "release.tar.bz2"
        _write_tar(pkg, {"libfoo.so": _minimal_elf_so()}, "w:bz2")
        assert isinstance(detect_extractor(pkg), TarExtractor)
