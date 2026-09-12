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

"""Real package-archive fixtures: content that each format's own detector
recognises, with the *name* left entirely to the caller.

Shared (not a `test_` module) because plan Phase 7n made every extractor in
`abicheck.package` route on content rather than on a suffix, so both
`test_package.py` and `test_evidence_transport_roles.py` need archives that
are genuinely what they claim -- an empty file named `test.tar` no longer
proves anything, and a fixture builder that quietly produced one would make
a name-independence test pass for the wrong reason.
"""

from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

import pytest


def _make_tar_mode(archive_path: Path, mode: str) -> Path:
    """A real, one-member tar in *mode* (`w`, `w:gz`, `w:xz`, `w:bz2`)."""
    with tarfile.open(archive_path, mode) as tf:  # type: ignore[call-overload]
        info = tarfile.TarInfo(name="lib/libfoo.so")
        info.size = 3
        tf.addfile(info, io.BytesIO(b"elf"))
    return archive_path


def _write_zstd_tar(archive_path: Path) -> None:
    """A real zstd-compressed tar -- the one codec `tarfile` cannot open."""
    zstandard = pytest.importorskip("zstandard")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        info = tarfile.TarInfo(name="lib/libfoo.so")
        info.size = 3
        tf.addfile(info, io.BytesIO(b"elf"))
    archive_path.write_bytes(zstandard.ZstdCompressor().compress(buf.getvalue()))


def _make_wheel(archive_path: Path, files: dict[str, bytes]) -> Path:
    """Create a real wheel: a zip carrying PEP 427's `dist-info/WHEEL`.

    That member is what `WheelExtractor.detect` reads since plan Phase 7n
    made every extractor route on content rather than on a suffix.
    """
    with zipfile.ZipFile(archive_path, "w") as zf:
        zf.writestr("test-1.0.dist-info/WHEEL", "Wheel-Version: 1.0\n")
        for name, content in files.items():
            zf.writestr(name, content)
    return archive_path


def _make_conda_v2(archive_path: Path, files: dict[str, bytes]) -> Path:
    """Create a real `.conda` v2 container: metadata.json + a zstd payload."""
    with zipfile.ZipFile(archive_path, "w") as zf:
        zf.writestr("metadata.json", "{}")
        zf.writestr("pkg-test-1.0.tar.zst", "\x28\xb5\x2f\xfd")
        for name, content in files.items():
            zf.writestr(name, content)
    return archive_path
