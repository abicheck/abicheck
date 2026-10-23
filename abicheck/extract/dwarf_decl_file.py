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

"""Resolve a DWARF DIE's ``DW_AT_decl_file`` to the declaring file's path.

DWARF 4 and DWARF 5 lay out the line-program file table differently. A DWARF 4
entry has ``name`` and a 1-based ``dir_index`` into ``include_directory``, where
index 0 means the compilation directory. A DWARF 5 entry has ``DW_LNCT_path``
and ``DW_LNCT_directory_index``, the file index is 0-based, and directory 0 is
the compilation directory listed in the table itself.

The resolver used to read ``entry.name`` for both. A pyelftools DWARF 5 entry
has no ``name`` key, so the lookup raised and the failure was swallowed: every
DWARF 5 declaration (GCC's default) carried ``decl_file=None``. That blinded
every consumer of the declaring file, including dependency classification.

The result is the full path joined with the directory, so a dependency root
can be matched against it by path segments, and ``:<line>`` when the DIE
records a declaration line.
"""

from __future__ import annotations

import os
import posixpath
from typing import Any

__all__ = ["decl_file_path", "decl_header_location"]

#: Suffixes of a declaring file the snapshot records as a header location.
#: A suffix-less path (a standard-library header) counts as a header too.
_HEADER_LOCATION_SUFFIXES = frozenset(
    {".h", ".hh", ".hpp", ".hxx", ".h++", ".ipp", ".tpp", ".inc", ".inl", ".tcc"}
)


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _entry_field(entry: Any, *keys: str) -> Any:
    """The first of *keys* present on a pyelftools file/directory entry."""
    for key in keys:
        try:
            return entry[key]
        except (KeyError, TypeError, IndexError):
            continue
    return None


def _dir_name(entry: Any) -> str:
    """A directory-table entry: pyelftools yields bytes/str for DWARF 5 and
    DWARF 4 directories, but a keyed entry for some DWARF 5 producers."""
    if isinstance(entry, (bytes, str)):
        return _text(entry)
    return _text(_entry_field(entry, "DW_LNCT_path", "name") or "")


def _comp_dir(CU: Any) -> str:
    top = CU.get_top_DIE()
    if "DW_AT_comp_dir" not in top.attributes:
        return ""
    return _text(top.attributes["DW_AT_comp_dir"].value)


def _under(base: str, name: str) -> str:
    return name if os.path.isabs(name) or not base else os.path.join(base, name)


def decl_file_path(die: Any, CU: Any) -> str | None:
    """``path`` or ``path:line`` for the file *die* was declared in, or ``None``.

    ``None`` when the DIE has no ``DW_AT_decl_file``, when a DWARF 4 index is
    the "no file" sentinel 0, or when the line program does not name the file.
    """
    attrs = die.attributes
    if "DW_AT_decl_file" not in attrs:
        return None
    file_idx = attrs["DW_AT_decl_file"].value
    version = CU.header.get("version", 4) if hasattr(CU, "header") else 4
    # Index 0 is the "no file" sentinel before DWARF 5; in DWARF 5 it is the
    # primary source file, a valid declaration site.
    if file_idx == 0 and version < 5:
        return None
    try:
        line_prog = CU.dwarfinfo.line_program_for_CU(CU)
        if line_prog is None:
            return None
        path = _file_path(line_prog.header, file_idx, _comp_dir(CU), CU)
    except Exception:  # noqa: BLE001 -- decl_file is best-effort evidence
        return None
    if path is None:
        return None
    if "DW_AT_decl_line" in attrs:
        line = attrs["DW_AT_decl_line"].value
        if line:
            return f"{path}:{line}"
    return path


def decl_header_location(die: Any, CU: Any) -> str | None:
    """:func:`decl_file_path`, kept only when the declaring file is a header.

    A declaration whose file is a source file (``lib.cpp``) is the library's
    own definition, not a header it declares its surface in. Recording that
    file as the declaration's ``source_location`` would make the snapshot's
    ``is_impl_source`` treat the type as an implementation detail and mark it
    opaque, which downgrades a real layout change to a non-break. Such a
    declaration keeps no location, exactly as before DWARF declaration files
    were resolved, and a header-declared one carries its location.
    """
    location = decl_file_path(die, CU)
    if location is None:
        return None
    head, sep, tail = location.rpartition(":")
    path = head if sep and tail.isdigit() else location
    suffix = posixpath.splitext(path)[1].lower()
    if suffix == "" or suffix in _HEADER_LOCATION_SUFFIXES:
        return location
    return None


def _file_path(header: Any, file_idx: int, comp_dir: str, CU: Any) -> str | None:
    # pyelftools names the DWARF 5 table "file_names" and the DWARF 4 one
    # "file_entry"; directories are "include_directory" in both.
    files = header.get("file_names") or header.get("file_entry") or []
    dirs = header.get("include_directory") or []
    version = CU.header.get("version", 4) if hasattr(CU, "header") else 4
    if version >= 5:
        if file_idx >= len(files):
            return None
        entry = files[file_idx]
        name = _entry_field(entry, "DW_LNCT_path", "name")
        dir_idx = _entry_field(entry, "DW_LNCT_directory_index", "dir_index") or 0
        if dir_idx < len(dirs):
            base = _under(comp_dir, _dir_name(dirs[dir_idx]))
        else:
            base = comp_dir
    else:
        if file_idx - 1 >= len(files):
            return None
        entry = files[file_idx - 1]
        name = _entry_field(entry, "name", "DW_LNCT_path")
        dir_idx = _entry_field(entry, "dir_index", "DW_LNCT_directory_index") or 0
        if dir_idx == 0 or dir_idx - 1 >= len(dirs):
            base = comp_dir
        else:
            base = _under(comp_dir, _dir_name(dirs[dir_idx - 1]))
    if name is None:
        return None
    return os.path.normpath(_under(base, _text(name)))
