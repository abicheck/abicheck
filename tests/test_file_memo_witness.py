# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""A file-dependent memo answers for the file as it is now, not as it was.

The header text scans and the compiler probes are memoized on the path they read. A
rewrite of that path within one process must change the answer. Each test rewrites the
path and checks the new answer; the text-scan rewrite keeps the byte count and restores
the old timestamp, so only the content can tell the two versions apart.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from abicheck.extract.builtin_abi import plain_char_is_unsigned
from abicheck.extract.headers.castxml.clang_semantics_join import (
    _file_mentions_exception_spec,
)
from abicheck.extract.headers.castxml.function_type_cc import (
    _file_mentions_calling_convention,
)
from abicheck.model.file_revision import content_witness


def _rewrite_same_size_keeping_mtime(path: Path, text: str) -> None:
    before = path.stat()
    assert len(text) == before.st_size, "a same-size rewrite is the case under test"
    path.write_text(text, encoding="utf-8")
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))


def test_exception_spec_scan_follows_a_same_size_rewrite(tmp_path: Path) -> None:
    header = tmp_path / "api.h"
    header.write_text("void f() noexcept;", encoding="utf-8")
    assert _file_mentions_exception_spec(str(header)) is True
    _rewrite_same_size_keeping_mtime(header, "void f() xxxxxxxx;")
    assert _file_mentions_exception_spec(str(header)) is False


def test_calling_convention_scan_follows_a_same_size_rewrite(tmp_path: Path) -> None:
    header = tmp_path / "api.h"
    header.write_text("void __stdcall f();", encoding="utf-8")
    assert _file_mentions_calling_convention(str(header)) is True
    _rewrite_same_size_keeping_mtime(header, "void xxxxxxxxx f();")
    assert _file_mentions_calling_convention(str(header)) is False


@pytest.mark.skipif(
    sys.platform == "win32", reason="a POSIX shell stands in for the compiler"
)
def test_compiler_probe_follows_a_replaced_compiler(tmp_path: Path) -> None:
    cc = tmp_path / "fakecc"
    cc.write_text("#!/bin/sh\necho '#define __CHAR_UNSIGNED__'\n", encoding="utf-8")
    cc.chmod(0o755)
    assert plain_char_is_unsigned(str(cc), ()) is True
    # A different compiler at the same name: a different size, so the revision moves.
    cc.write_text("#!/bin/sh\necho 'no signedness macro here'\n", encoding="utf-8")
    cc.chmod(0o755)
    assert plain_char_is_unsigned(str(cc), ()) is False


def test_content_witness_is_none_for_an_unreadable_path(tmp_path: Path) -> None:
    assert content_witness(tmp_path / "missing.h") is None


def test_content_witness_follows_the_bytes_not_the_stamp(tmp_path: Path) -> None:
    header = tmp_path / "api.h"
    header.write_text("void f();", encoding="utf-8")
    first = content_witness(header)
    _rewrite_same_size_keeping_mtime(header, "void g();")
    assert content_witness(header) != first
    _rewrite_same_size_keeping_mtime(header, "void f();")
    assert content_witness(header) == first
