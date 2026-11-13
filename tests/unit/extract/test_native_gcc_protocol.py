# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""The native GCC projection never reads an absent protocol key as ``False``.

Bug class ``evidence.unread_producer_read_as_confirmed_absence``: the
projection read ``r.get("virtual", False)``-style defaults, so a plugin that
did not emit a key (a mismatched build, a truncated row) produced a confirmed
non-virtual, non-deleted, external... declaration instead of a failed dump.

The fixtures are real schema-v2 rows captured from GCC 14's C and C++ plugins
(``tests/fixtures/native_gcc/``). The oracle is the protocol itself, stated
independently of ``projection.protocol_flag``: every boolean the plugin
emitted is required; only C++-only properties may be absent, and only from
the C plugin, where they do not apply.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from abicheck.errors import SnapshotError
from abicheck.extract.headers.backend_protocol import HeaderParseRequest
from abicheck.extract.headers.gcc import backend as gcc_backend
from abicheck.extract.headers.gcc.projection import project
from tests._native_gcc_fixtures import fixture_header, fixture_rows

#: Booleans a projected row of each kind carries, as the plugin emits them.
_FUNCTION_FLAGS = ("artificial", "inline")
_VARIABLE_FLAGS = ("artificial", "external_linkage", "const")
_CXX_FUNCTION_FLAGS = (
    "const",
    "volatile",
    "static_member",
    "pure_virtual",
    "deleted",
    "virtual",
    "extern_c",
)


_rows = fixture_rows
_header = fixture_header


def _project(rows: list[dict[str, Any]], header: Path, language: str) -> Any:
    return project(rows, header, language=language)


def _projected_functions(rows: list[dict[str, Any]]) -> list[int]:
    return [
        i
        for i, r in enumerate(rows)
        if r["kind"] == "function_decl" and r.get("mangled") and not r["artificial"]
    ]


@pytest.mark.parametrize(("name", "language"), [("c", "c"), ("cpp", "c++")])
def test_real_rows_project(tmp_path: Path, name: str, language: str) -> None:
    header = _header(tmp_path, name)
    fields = _project(_rows(name, header), header, language)
    assert fields.functions, "the fixture must project at least one function"


def test_cpp_rows_carry_the_flags_they_declare(tmp_path: Path) -> None:
    header = _header(tmp_path, "cpp")
    fields = _project(_rows("cpp", header), header, "c++")
    by_name = {f.name: f for f in fields.functions}
    assert by_name["n::B::v"].is_pure_virtual is True
    assert by_name["n::B::v"].is_virtual is True
    assert by_name["n::B::v"].is_const is True
    assert by_name["n::D::s"].is_static is True
    assert by_name["n::free_fn"].is_virtual is False


@pytest.mark.parametrize("key", _FUNCTION_FLAGS + _CXX_FUNCTION_FLAGS)
def test_a_missing_cpp_flag_fails_the_projection(tmp_path: Path, key: str) -> None:
    header = _header(tmp_path, "cpp")
    rows = _rows("cpp", header)
    targets = _projected_functions(rows)
    assert targets
    for index in targets:
        broken = copy.deepcopy(rows)
        assert key in broken[index], (key, broken[index]["name"])
        del broken[index][key]
        with pytest.raises(ValueError, match=key):
            _project(broken, header, "c++")


@pytest.mark.parametrize("key", _FUNCTION_FLAGS + _CXX_FUNCTION_FLAGS)
@pytest.mark.parametrize("value", [None, 0, 1, "true", "false", []])
def test_a_non_boolean_flag_fails_the_projection(
    tmp_path: Path, key: str, value: object
) -> None:
    header = _header(tmp_path, "cpp")
    rows = _rows("cpp", header)
    index = _projected_functions(rows)[0]
    rows[index][key] = value
    with pytest.raises(ValueError, match=key):
        _project(rows, header, "c++")


@pytest.mark.parametrize("key", _FUNCTION_FLAGS)
def test_c_rows_still_require_the_flags_c_emits(tmp_path: Path, key: str) -> None:
    header = _header(tmp_path, "c")
    rows = _rows("c", header)
    index = _projected_functions(rows)[0]
    del rows[index][key]
    with pytest.raises(ValueError, match=key):
        _project(rows, header, "c")


def test_c_rows_omit_cxx_only_flags_and_read_them_as_not_applicable(
    tmp_path: Path,
) -> None:
    # Negative control: the C plugin never emits C++-only properties; their
    # absence there is the language, not a protocol gap.
    header = _header(tmp_path, "c")
    rows = _rows("c", header)
    for index in _projected_functions(rows):
        assert not set(_CXX_FUNCTION_FLAGS) & rows[index].keys()
    (fn,) = _project(rows, header, "c").functions
    assert (fn.is_virtual, fn.is_pure_virtual, fn.is_deleted, fn.is_static) == (
        False,
        False,
        False,
        False,
    )
    assert fn.is_extern_c is True


@pytest.mark.parametrize(("name", "language"), [("c", "c"), ("cpp", "c++")])
@pytest.mark.parametrize("key", _VARIABLE_FLAGS)
def test_a_missing_variable_flag_fails_the_projection(
    tmp_path: Path, name: str, language: str, key: str
) -> None:
    header = _header(tmp_path, name)
    rows = _rows(name, header)
    targets = [
        i
        for i, r in enumerate(rows)
        if r["kind"] == "var_decl" and r.get("mangled") and not r["artificial"]
    ]
    assert targets
    for index in targets:
        broken = copy.deepcopy(rows)
        del broken[index][key]
        with pytest.raises(ValueError, match=key):
            _project(broken, header, language)


def _malformed(rows: list[dict[str, Any]]) -> list[list[Any]]:
    index = _projected_functions(rows)[0]
    out = []
    for mutate in (
        lambda r: r[index].pop("kind"),
        lambda r: r[index].pop("parameters"),
        lambda r: r[index].__setitem__("parameters", None),
        lambda r: r[index].__setitem__("parameters", [7]),
        lambda r: r[index].pop("access"),
        lambda r: r[index].__setitem__("access", "friendly"),
        lambda r: r[index].pop("virtual"),
    ):
        broken = copy.deepcopy(rows)
        mutate(broken)
        out.append(broken)
    return out


def test_malformed_rows_are_a_failed_dump_never_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    header = _header(tmp_path, "cpp")
    rows = _rows("cpp", header)
    monkeypatch.setattr(gcc_backend, "resolve_compiler", lambda cc: "/usr/bin/g++")
    monkeypatch.setattr(gcc_backend, "find_plugin", lambda cc, lang: tmp_path)
    request = HeaderParseRequest(headers=[header], extra_includes=[], lang="c++")
    for broken in _malformed(rows):
        monkeypatch.setattr(
            gcc_backend, "acquire", lambda *a, _rows=broken, **k: copy.deepcopy(_rows)
        )
        with pytest.raises(SnapshotError, match="No Clang fallback"):
            gcc_backend.GccBackend().parse(request)


def test_native_gcc_is_never_a_fallback_target(tmp_path: Path) -> None:
    header = _header(tmp_path, "cpp")
    request = HeaderParseRequest(headers=[header], extra_includes=[], lang="c++")
    with pytest.raises(SnapshotError, match="never a fallback"):
        gcc_backend.GccBackend().parse(request, fallback_reason="castxml failed")
