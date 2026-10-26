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
so the reader returned a complete-looking, empty result. A missing or ``null``
``inner`` is a leaf and must keep parsing.

Zip side: a corrupt archive must not read as an empty member list.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

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
def test_null_inner_reads_like_absent_and_empty_inner(
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


@pytest.mark.parametrize("payload", _CORRUPT_ZIPS)
@pytest.mark.xfail(
    strict=True,
    reason="known gap: _zip_entry_names returns [] on a corrupt archive; "
    "its callers read that as no members",
)
def test_corrupt_zip_entry_listing_raises(tmp_path: Path, payload: bytes) -> None:
    pkg = tmp_path / "broken.zip"
    pkg.write_bytes(payload)
    with pytest.raises((OSError, ValueError)):
        _zip_entry_names(pkg)
