"""``decl_file_path`` resolves DW_AT_decl_file for DWARF 4 and DWARF 5 tables.

pyelftools exposes the two layouts differently: DWARF 4 files are ``file_entry``
entries with ``name``/``dir_index`` (1-based, 0 = compilation directory), DWARF 5
files are ``file_names`` entries with ``DW_LNCT_path``/``DW_LNCT_directory_index``
(0-based), and directories are bytes in both. The resolver used to read ``name``
for both, so every DWARF 5 declaration (GCC's default) came back ``None``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from abicheck.extract.dwarf_decl_file import decl_file_path, decl_header_location

COMP_DIR = b"/work/build"


class _Attr:
    def __init__(self, value: int) -> None:
        self.value = value


class _Die:
    def __init__(self, decl_file: int, decl_line: int | None) -> None:
        self.attributes = {"DW_AT_decl_file": _Attr(decl_file)}
        if decl_line is not None:
            self.attributes["DW_AT_decl_line"] = _Attr(decl_line)


def _cu(version: int, files: list, dirs: list) -> SimpleNamespace:
    header = {
        "file_names" if version >= 5 else "file_entry": files,
        "include_directory": dirs,
    }
    top = SimpleNamespace(attributes={"DW_AT_comp_dir": _Attr(0)})
    top.attributes["DW_AT_comp_dir"].value = COMP_DIR
    line_program = SimpleNamespace(header=header)
    return SimpleNamespace(
        header={"version": version},
        dwarfinfo=SimpleNamespace(line_program_for_CU=lambda _cu: line_program),
        get_top_DIE=lambda: top,
    )


@pytest.mark.parametrize(
    ("version", "files", "dirs"),
    [
        pytest.param(
            5,
            [
                {"DW_LNCT_path": b"lib.cpp", "DW_LNCT_directory_index": 1},
                {"DW_LNCT_path": b"api.h", "DW_LNCT_directory_index": 1},
                {"DW_LNCT_path": b"simd.h", "DW_LNCT_directory_index": 2},
            ],
            [COMP_DIR, b"lib", b"dep/fakevec"],
            id="dwarf5",
        ),
        pytest.param(
            4,
            [
                {"name": b"lib.cpp", "dir_index": 1},
                {"name": b"api.h", "dir_index": 1},
                {"name": b"simd.h", "dir_index": 2},
            ],
            [b"lib", b"dep/fakevec"],
            id="dwarf4",
        ),
    ],
)
def test_resolves_relative_directory_for_each_dwarf_layout(
    version, files, dirs
) -> None:
    cu = _cu(version, files, dirs)
    # DWARF 5 index 2 and DWARF 4 index 3 both name simd.h in dep/fakevec.
    file_index = 3 if version == 4 else 2
    located = decl_file_path(_Die(file_index, 6), cu)
    assert located == "/work/build/dep/fakevec/simd.h:6"


def test_absolute_directory_is_kept_and_line_optional() -> None:
    files = [{"DW_LNCT_path": b"api.h", "DW_LNCT_directory_index": 1}]
    dirs = [COMP_DIR, b"/usr/include/mylib"]
    cu = _cu(5, files, dirs)
    # DWARF 5 index 0 is a real file, not the sentinel.
    assert decl_file_path(_Die(0, None), cu) == "/usr/include/mylib/api.h"


def test_dwarf4_zero_index_is_no_file_and_missing_attribute_is_none() -> None:
    cu = _cu(4, [{"name": b"api.h", "dir_index": 0}], [])
    assert decl_file_path(_Die(0, 4), cu) is None
    no_attribute = SimpleNamespace(attributes={})
    assert decl_file_path(no_attribute, cu) is None


def test_out_of_range_file_index_resolves_to_none() -> None:
    cu = _cu(5, [{"DW_LNCT_path": b"api.h", "DW_LNCT_directory_index": 0}], [COMP_DIR])
    assert decl_file_path(_Die(9, 4), cu) is None


@pytest.mark.parametrize(
    ("version", "files", "dirs", "file_index", "expected"),
    [
        pytest.param(
            5,
            [{"DW_LNCT_path": b"lib.cpp", "DW_LNCT_directory_index": 1}],
            [COMP_DIR, b"lib"],
            0,
            None,
            id="source-file-is-not-a-header-location",
        ),
        pytest.param(
            5,
            [{"DW_LNCT_path": b"api.h", "DW_LNCT_directory_index": 1}],
            [COMP_DIR, b"lib"],
            0,
            "/work/build/lib/api.h:6",
            id="header-keeps-its-location",
        ),
        pytest.param(
            4,
            [{"name": b"stdlib_vector", "dir_index": 1}],
            [b"/usr/include/c++/12/vector"],
            1,
            "/usr/include/c++/12/vector/stdlib_vector:6",
            id="suffixless-header-is-a-header-location",
        ),
    ],
)
def test_declaration_location_is_recorded_only_for_a_header(
    version, files, dirs, file_index, expected
) -> None:
    # A source-file declaration recorded as a location would make the snapshot's
    # is_impl_source treat the type as opaque and downgrade its layout change.
    cu = _cu(version, files, dirs)
    assert decl_header_location(_Die(file_index, 6), cu) == expected


def test_missing_line_program_resolves_to_none() -> None:
    cu = _cu(5, [], [COMP_DIR])
    cu.dwarfinfo.line_program_for_CU = lambda _cu: None
    assert decl_file_path(_Die(0, 4), cu) is None


def test_line_program_error_is_swallowed_as_none() -> None:
    # Resolution is best-effort evidence: a broken line table is no location.
    cu = _cu(5, [], [COMP_DIR])

    def _broken(_cu):
        raise ValueError("corrupt line table")

    cu.dwarfinfo.line_program_for_CU = _broken
    assert decl_file_path(_Die(0, 4), cu) is None


def test_dwarf5_index_past_the_file_table_resolves_to_none() -> None:
    cu = _cu(5, [{"DW_LNCT_path": b"api.h", "DW_LNCT_directory_index": 0}], [COMP_DIR])
    assert decl_file_path(_Die(5, 4), cu) is None


def test_dwarf4_index_past_the_file_table_resolves_to_none() -> None:
    cu = _cu(4, [{"name": b"api.h", "dir_index": 0}], [])
    assert decl_file_path(_Die(9, 4), cu) is None


def test_directory_index_past_the_table_falls_back_to_compilation_directory() -> None:
    cu = _cu(5, [{"DW_LNCT_path": b"api.h", "DW_LNCT_directory_index": 7}], [COMP_DIR])
    assert decl_file_path(_Die(0, None), cu) == "/work/build/api.h"


def test_dwarf4_directory_index_past_the_table_falls_back_to_compilation_directory() -> (
    None
):
    cu = _cu(4, [{"name": b"api.h", "dir_index": 9}], [b"lib"])
    assert decl_file_path(_Die(1, None), cu) == "/work/build/api.h"


def test_keyed_directory_entry_is_read_by_its_path_key() -> None:
    # Some producers key a directory entry rather than yielding bytes.
    cu = _cu(
        5,
        [{"DW_LNCT_path": b"api.h", "DW_LNCT_directory_index": 1}],
        [COMP_DIR, {"DW_LNCT_path": b"lib"}],
    )
    assert decl_file_path(_Die(0, None), cu) == "/work/build/lib/api.h"


def test_file_entry_without_a_name_resolves_to_none() -> None:
    cu = _cu(5, [{"DW_LNCT_directory_index": 0}], [COMP_DIR])
    assert decl_file_path(_Die(0, 4), cu) is None


def test_zero_declaration_line_is_recorded_without_a_line() -> None:
    cu = _cu(
        5,
        [{"DW_LNCT_path": b"api.h", "DW_LNCT_directory_index": 1}],
        [COMP_DIR, b"lib"],
    )
    assert decl_file_path(_Die(0, 0), cu) == "/work/build/lib/api.h"
