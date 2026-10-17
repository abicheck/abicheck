# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""``DebExtractor.detect`` must not claim static archives.

A ``.deb`` and a static archive (``.a``, Rust ``.rlib``) share the
``!<arch>`` ar magic. Matching the magic alone sent every static archive to
the Debian extractor, which then failed with "No data.tar.* found in Deb
package" instead of the archive-specific rejection. A ``.deb`` is told apart
by its first member, always ``debian-binary`` (deb-format(5)).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.extract.packages import DebExtractor, detect_extractor


def _ar(first_member: bytes) -> bytes:
    # 60-byte ar member header; detection only reads the 16-byte name field.
    return b"!<arch>\n" + first_member.ljust(60)


@pytest.mark.parametrize("suffix", [".deb", ""])
def test_deb_detected_by_first_member(tmp_path: Path, suffix: str) -> None:
    f = tmp_path / f"pkg{suffix}"
    f.write_bytes(_ar(b"debian-binary") + b"2.0\n")
    assert DebExtractor().detect(f)


@pytest.mark.parametrize(
    "name", [b"lib.o/", b"__.SYMDEF", b"foo.o", b"debian-binary.o/", b"debian-binary-x"]
)
def test_static_archive_is_not_a_deb(tmp_path: Path, name: bytes) -> None:
    f = tmp_path / "libfoo.a"
    f.write_bytes(_ar(name) + b"\x00" * 64)
    assert not DebExtractor().detect(f)
    assert not isinstance(detect_extractor(f), DebExtractor)


def test_truncated_archive_magic_is_not_a_deb(tmp_path: Path) -> None:
    f = tmp_path / "short.deb"
    f.write_bytes(b"!<arch>\n" + b"deb")
    assert not DebExtractor().detect(f)
