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

"""Malformed evidence is an explicit failure, never a silent empty surface.

A clang AST node whose ``inner`` is present but not a list must raise
:class:`MalformedClangAstError` from every clang reader that walks it. The
old ``node.get("inner", []) or []`` coerced a dict or string to an empty walk,
so the reader returned a complete-looking, empty result. A missing ``inner`` is
a leaf and must keep parsing, and so is ``null`` (the readers' existing tests
pin it; the header-graph projection refuses it earlier on its own path).

Zip side: a corrupt archive must not read as an empty member list.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from abicheck.extract.archive_integrity import (
    CorruptPackageArchiveError,
    require_readable_archive,
)
from abicheck.extract.headers.clang.ast_inner import MalformedClangAstError, ast_inner
from abicheck.extract.headers.clang.context import _Decl
from abicheck.extract.headers.clang.enums import parse_enums
from abicheck.extract.headers.clang.functions import parse_functions
from abicheck.extract.headers.clang.records import parse_types
from abicheck.extract.headers.clang.templates import build_specialization_index
from abicheck.extract.packages import _zip_entry_names, detect_extractor

MALFORMED_INNER: list[Any] = [
    pytest.param({"kind": "ParmVarDecl"}, id="dict"),
    pytest.param("abc", id="str"),
    pytest.param(3, id="int"),
]

_FILE = "/usr/include/api.h"


def _decl(node: dict[str, Any]) -> _Decl:
    return _Decl(node, scope=(), file=_FILE, access="public")


def _call_enums(node: dict[str, Any]) -> object:
    return parse_enums([], [_decl(node)], lambda _n: None)


def _call_records(node: dict[str, Any]) -> object:
    return parse_types(
        [_decl(node)],
        [],
        pub_header_segs=[],
        pub_dir_segs=[],
        have_public_set=False,
        base_lookup_index={},
        evaluate_bitfield_int=lambda _n: None,
        field_default_value=lambda _n: None,
    )


def _call_functions(node: dict[str, Any]) -> object:
    return parse_functions(
        [_decl(node)],
        exported_dynamic=set(),
        exported_static=set(),
        virtual_mangled_names=frozenset(),
        target_triple=None,
        default_value=lambda _n: None,
    )


def _call_templates(node: dict[str, Any]) -> object:
    root = {"kind": "TranslationUnitDecl", "inner": [node]}
    return build_specialization_index(root, is_record_definition=lambda _n: False)


# (reader name, reader call, a node of the kind the reader consumes)
READERS: list[Any] = [
    pytest.param(
        _call_enums,
        {"kind": "EnumDecl", "name": "E", "id": "0x1"},
        id="enums",
    ),
    pytest.param(
        _call_records,
        {"kind": "CXXRecordDecl", "name": "R", "tagUsed": "struct", "id": "0x2"},
        id="records",
    ),
    pytest.param(
        _call_functions,
        {"kind": "FunctionDecl", "name": "f", "id": "0x3", "mangledName": "_Z1fv"},
        id="functions",
    ),
    pytest.param(
        _call_templates,
        {"kind": "ClassTemplateDecl", "name": "T", "id": "0x4"},
        id="templates",
    ),
]


@pytest.mark.parametrize("reader, node", READERS)
@pytest.mark.parametrize("malformed", MALFORMED_INNER)
def test_reader_refuses_malformed_inner(
    reader: Callable[[dict[str, Any]], object],
    node: dict[str, Any],
    malformed: Any,
) -> None:
    node = {**node, "inner": malformed}
    with pytest.raises(MalformedClangAstError):
        reader(node)


@pytest.mark.parametrize("reader, node", READERS)
def test_null_and_empty_inner_read_like_absent_inner(
    reader: Callable[[dict[str, Any]], object], node: dict[str, Any]
) -> None:
    """A ``null`` or ``[]`` ``inner`` is the same leaf as a missing key: the
    reader returns the identical result for all three, and does not raise."""
    absent = reader(dict(node))
    assert reader({**node, "inner": None}) == absent
    assert reader({**node, "inner": []}) == absent


def test_enum_with_absent_inner_parses_as_enum() -> None:
    result = _call_enums({"kind": "EnumDecl", "name": "E", "id": "0x1"})
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0].members == []


@pytest.mark.parametrize("malformed", MALFORMED_INNER)
def test_ast_inner_helper_contract(malformed: Any) -> None:
    with pytest.raises(MalformedClangAstError):
        ast_inner({"kind": "X", "inner": malformed})


def test_ast_inner_returns_list_and_empty_for_leaf() -> None:
    children = [{"kind": "ParmVarDecl"}]
    assert ast_inner({"inner": children}) == children
    assert ast_inner({}) == []
    assert ast_inner({"inner": None}) == []


_CORRUPT_ZIPS: list[Any] = [
    pytest.param(b"PK\x03\x04" + b"\x00" * 26, id="truncated-local-header"),
    pytest.param(b"PK\x05\x06" + b"\x00" * 18, id="eocd-only"),
    pytest.param(b"not a zip at all", id="garbage"),
]


@pytest.mark.parametrize("payload", _CORRUPT_ZIPS)
def test_corrupt_zip_is_not_detected_as_a_package(
    tmp_path: Path, payload: bytes
) -> None:
    # The loud failure path: a corrupt archive is not claimed by any extractor,
    # so release_inputs refuses it with ReleaseOperandContentError.
    pkg = tmp_path / "broken.whl"
    pkg.write_bytes(payload)
    assert detect_extractor(pkg) is None


_CORRUPT_ARCHIVES: list[Any] = [
    pytest.param(b"PK\x03\x04" + b"\x00" * 26, "broken.zip", id="truncated-zip"),
    pytest.param(b"PK\x05\x06" + b"\x00" * 10, "broken.whl", id="short-eocd-whl"),
    pytest.param(b"not a zip at all", "broken.zip", id="random-bytes-zip-suffix"),
    pytest.param(b"not a zip at all", "broken.whl", id="random-bytes-whl-suffix"),
]


@pytest.mark.parametrize("payload, name", _CORRUPT_ARCHIVES)
def test_corrupt_zip_entry_listing_raises(
    tmp_path: Path, payload: bytes, name: str
) -> None:
    pkg = tmp_path / name
    pkg.write_bytes(payload)
    with pytest.raises(
        CorruptPackageArchiveError, match="corrupt or truncated archive"
    ):
        _zip_entry_names(pkg)


@pytest.mark.parametrize("payload, name", _CORRUPT_ARCHIVES)
def test_corrupt_archive_is_reported_as_corrupt_on_release_path(
    tmp_path: Path, payload: bytes, name: str
) -> None:
    pkg = tmp_path / name
    pkg.write_bytes(payload)
    with pytest.raises(
        CorruptPackageArchiveError, match="corrupt or truncated archive"
    ):
        require_readable_archive(pkg)


def test_random_txt_is_unrecognized_not_corrupt(tmp_path: Path) -> None:
    pkg = tmp_path / "notes.txt"
    pkg.write_bytes(b"not a zip at all")
    assert _zip_entry_names(pkg) == []
    require_readable_archive(pkg)
    assert detect_extractor(pkg) is None


def test_valid_empty_zip_is_not_corrupt(tmp_path: Path) -> None:
    import zipfile

    pkg = tmp_path / "empty.zip"
    with zipfile.ZipFile(pkg, "w"):
        pass
    assert _zip_entry_names(pkg) == []
    require_readable_archive(pkg)


@pytest.mark.parametrize("name", ["broken.tar.gz", "broken.tar"])
def test_corrupt_tar_family_archive_is_reported_as_corrupt(
    tmp_path: Path, name: str
) -> None:
    pkg = tmp_path / name
    pkg.write_bytes(b"\x1f\x8b\x08 not really gzip at all")
    assert detect_extractor(pkg) is None
    with pytest.raises(
        CorruptPackageArchiveError, match="corrupt or truncated archive"
    ):
        require_readable_archive(pkg)


@pytest.mark.parametrize(
    ("name", "payload", "corrupt"),
    [
        ("lib.whl", b"PK\x03\x04truncated", True),
        ("lib.zip", b"\x00" * 64, True),
        ("lib.tar.gz", b"not a gzip stream", True),
        ("lib.bin", b"\x00" * 64, False),
    ],
)
def test_compare_cli_names_a_corrupt_archive_operand(
    tmp_path: Path, name: str, payload: bytes, corrupt: bool
) -> None:
    """Through the public ``abicheck compare``: a zip/tar-shaped operand that
    cannot be read is reported as corrupt, not as an undetectable format; an
    ordinary unknown file keeps the format message."""
    from click.testing import CliRunner

    from abicheck.cli import main

    operand = tmp_path / name
    operand.write_bytes(payload)
    result = CliRunner().invoke(main, ["compare", str(operand), str(operand)])
    assert result.exit_code != 0
    assert ("corrupt or truncated archive" in result.output) is corrupt
    assert ("Cannot detect format" in result.output) is not corrupt


def _real_tar(path: Path) -> Path:
    import io
    import tarfile

    with tarfile.open(path, "w") as tf:
        data = b"x"
        info = tarfile.TarInfo("usr/lib/libx.so")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    return path


def _extract(
    primary: Path, debug_pkg: Path | None = None, devel_pkg: Path | None = None
) -> Any:
    import tempfile

    from abicheck.extract.packages import is_package
    from abicheck.workflows.release_inputs import extract_if_package

    return extract_if_package(
        primary,
        debug_pkg,
        devel_pkg,
        lambda prefix: Path(tempfile.mkdtemp(prefix=prefix)),
        is_package,
        detect_extractor,
    )


def test_a_readable_archive_under_a_misleading_name_is_not_corrupt(
    tmp_path: Path,
) -> None:
    """Content decides: a valid tar named ``.zip`` is extracted by the tar
    extractor, never reported corrupt by the suffix-based integrity check."""
    pkg = _real_tar(tmp_path / "pkg.zip")
    lib_dir, *_ = _extract(pkg)
    assert any(p.name == "libx.so" for p in lib_dir.rglob("*"))


@pytest.mark.parametrize("role", ["debug_pkg", "devel_pkg"])
def test_a_corrupt_companion_package_is_named_corrupt(
    tmp_path: Path, role: str
) -> None:
    from abicheck.errors import ReleaseOperandContentError

    primary = _real_tar(tmp_path / "pkg.tar")
    bad = tmp_path / "companion.whl"
    bad.write_bytes(b"PK\x03\x04truncated")
    with pytest.raises(
        ReleaseOperandContentError, match="corrupt or truncated archive"
    ):
        _extract(primary, **{role: bad})


def _real_zip(path: Path) -> Path:
    import zipfile

    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("usr/lib/libx.so", "x")
    return path


@pytest.mark.parametrize(
    ("make", "name"),
    [
        (_real_tar, "pkg.zip"),
        (_real_tar, "pkg.whl"),
        (_real_zip, "pkg.tar.gz"),
        (_real_zip, "pkg.tar"),
        (_real_zip, "pkg.tgz"),
    ],
)
def test_a_readable_archive_is_never_corrupt_whatever_its_name(
    tmp_path: Path, make: Any, name: str
) -> None:
    """A file readable as zip or tar is never reported corrupt, even when its
    suffix names the other format; the same bytes truncated under the same
    name still are."""
    good = make(tmp_path / name)
    require_readable_archive(good)
    bad = tmp_path / "bad" / name
    bad.parent.mkdir()
    bad.write_bytes(good.read_bytes()[:40])
    with pytest.raises(CorruptPackageArchiveError):
        require_readable_archive(bad)


def test_unreadable_paths_are_never_archives(tmp_path: Path) -> None:
    """A path that cannot be opened as a file (here a directory with an
    archive name) is neither zip- nor tar-readable, and a non-file operand is
    left to the caller rather than called corrupt."""
    from abicheck.extract.archive_integrity import _head_bytes, _readable_tar

    d = tmp_path / "pkg.tar.gz"
    d.mkdir()
    assert _head_bytes(d, 4) == b""
    assert _readable_tar(d) is False
    require_readable_archive(d)


def test_a_truncated_wheel_is_declined_by_detection(tmp_path: Path) -> None:
    bad = tmp_path / "x-1.0-py3-none-any.whl"
    bad.write_bytes(b"PK\x03\x04truncated")
    assert detect_extractor(bad) is None


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"PK\x03\x04truncated", "corrupt or truncated archive"),
        (None, "Unrecognized package format"),
    ],
)
def test_a_declined_primary_package_names_why(
    tmp_path: Path, payload: bytes | None, message: str
) -> None:
    """A primary operand no extractor accepts is reported corrupt when its
    bytes are an unreadable archive, and unrecognized when they are a
    readable one no extractor claims."""
    from abicheck.errors import ReleaseOperandContentError
    from abicheck.workflows.release_inputs import extract_if_package

    pkg = tmp_path / "x-1.0-py3-none-any.whl"
    if payload is None:
        _real_zip(pkg)
    else:
        pkg.write_bytes(payload)
    with pytest.raises(ReleaseOperandContentError, match=message):
        extract_if_package(
            pkg,
            None,
            None,
            lambda prefix: tmp_path / prefix,
            lambda _path: True,  # a package sniff that claims it
            detect_extractor,
        )


@pytest.mark.parametrize("name", ["pkg.zip", "pkg.whl", "pkg.tar.gz"])
def test_a_zip_with_a_damaged_central_directory_is_corrupt(
    tmp_path: Path, name: str
) -> None:
    """The end record alone (what ``zipfile.is_zipfile`` checks) does not make
    a zip readable: a damaged central directory is corruption, under any
    name."""
    import zipfile

    pkg = _real_zip(tmp_path / name)
    data = bytearray(pkg.read_bytes())
    central = data.rindex(b"PK\x01\x02")
    data[central : central + 4] = b"XXXX"
    pkg.write_bytes(bytes(data))
    assert zipfile.is_zipfile(pkg)
    with pytest.raises(CorruptPackageArchiveError):
        require_readable_archive(pkg)
