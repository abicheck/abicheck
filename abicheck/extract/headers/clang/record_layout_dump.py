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

"""Built-in record layout for the direct-clang L2 backend, from clang itself.

``-ast-dump=json`` carries no computed layout, and the optional LibTooling
companion (:mod:`.layout_tool`) is rarely installed, so a clang snapshot's
record sizes and offsets otherwise come only from DWARF backfill. GCC emits a
dynamic class without a key function in the TU, or a class template reached
only through ``extern template``, as a ``DW_AT_declaration`` stub, so those
records stayed without layout and ``type_size_changed`` /
``type_field_offset_changed`` went missing (known-gaps, 2026-11-04).

This module asks the same clang driver for the layout it computes: a probe
translation unit includes the headers and takes ``sizeof(::Name)`` of every
record the snapshot names, compiled with ``-Xclang -fdump-record-layouts``.
``-fdump-record-layouts-complete`` is deliberately not used: clang 18 crashes
in ``getASTRecordLayout`` on dependent class templates in libstdc++ headers.

A probe the compiler rejects (an incomplete, private nested or unnamed record)
just yields no layout for that record; clang still dumps every layout it did
compute, and those are exact. Output is the same per-record dict shape the
companion tool emits, so :func:`.layout_tool.apply_layout_facts` consumes it
and only ever fills a field that is still empty.
"""

from __future__ import annotations

import re
from typing import Any

#: Stand-alone layout entry header line.
_HEADER = "*** Dumping AST Record Layout"
_SIZE_RE = re.compile(r"sizeof=(\d+)")
_DSIZE_RE = re.compile(r"dsize=(\d+)")
_ALIGN_RE = re.compile(r"\balign=(\d+)")
#: ``  <offset> | <indent><text>`` where offset is ``N`` or ``N:a-b``.
_LINE_RE = re.compile(r"^\s*(\d+)(?::(\d+)-\d+)?\s\|(\s+)(\S.*)$")
_EMPTY_TAG_RE = re.compile(r"\s+\(empty\)$")
#: A base subobject line (its ``(empty)`` tag already removed).
_BASE_RE = re.compile(
    r"^(?:class|struct|union)\s+(.+?)\s+"
    r"\((primary base|base|primary virtual base|virtual base)\)$"
)
_VPTR_RE = re.compile(r"^\((.+) vtable pointer\)$")
#: The tag keyword that opens an entry's record line (``struct ns::X``); in C a
#: typedef naming an unnamed struct prints as the bare typedef name, with none.
_RECORD_KEYWORD_RE = re.compile(r"^(?:class|struct|union)\s+")
#: An unnamed member aggregate (``union X::(anonymous at a.h:3:5)``) with no
#: field name after it: its members are members of the enclosing record.
_ANON_MEMBER_RE = re.compile(
    r"^(?:class|struct|union)\s+\S*\((?:anonymous|unnamed)\b[^)]*\)$"
)


def probeable_record_name(name: str) -> bool:
    """Whether ``sizeof(::name)`` can name the record unambiguously."""
    if not name or name.startswith("::"):
        return False
    lowered = name.lower()
    return not any(
        marker in lowered
        for marker in ("(anonymous", "(unnamed", "(lambda", "<lambda", "::(", " at ")
    )


def probe_source(include_lines: str, names: list[str], *, c_mode: bool = False) -> str:
    """The probe TU: the header includes plus one ``sizeof`` per record.

    C has no scope operator, and a record name there is either a tag
    (``struct P``) or a typedef (``typedef struct {...} P``), which the
    snapshot does not tell apart -- so a C probe names each record all three
    ways. The two that do not resolve are compile errors that cost nothing:
    clang still lays out the one that does.
    """
    if c_mode:
        body = "".join(
            f"static const unsigned long __abicheck_layout_{i}_{j} = sizeof({spelled});\n"
            for i, n in enumerate(names)
            for j, spelled in enumerate((f"struct {n}", f"union {n}", n))
        )
        return f"{include_lines}{body}"
    body = "".join(
        f"constexpr unsigned long __abicheck_layout_{i} = sizeof(::{n});\n"
        for i, n in enumerate(names)
    )
    return f"{include_lines}namespace __abicheck_layout_probe {{\n{body}}}\n"


def _field_name(text: str) -> str | None:
    tokens = text.split()
    if len(tokens) < 2:
        return None
    name = tokens[-1]
    return name if re.fullmatch(r"[A-Za-z_]\w*", name) else None


def _parse_entry(lines: list[str]) -> dict[str, Any] | None:
    record: dict[str, Any] | None = None
    member_indent: int | None = None
    # Indents whose lines are the record's own members: its direct member
    # indent, then -- while inside one -- the member indent of each unnamed
    # member aggregate, whose fields castxml and the clang AST both flatten
    # into the record. A stack, so a later by-value member's own members at
    # the same depth are not read as the record's.
    member_indents: list[int] = []
    anon_parent: int | None = None
    fields: list[dict[str, Any]] = []
    bases: list[dict[str, Any]] = []
    vptr: int | None = None
    sizes: dict[str, int] = {}
    for line in lines:
        if "sizeof=" in line or "align=" in line:
            for key, rx in (
                ("size", _SIZE_RE),
                ("dsize", _DSIZE_RE),
                ("align", _ALIGN_RE),
            ):
                m = rx.search(line)
                if m and key not in sizes:
                    sizes[key] = int(m.group(1))
            continue
        m = _LINE_RE.match(line)
        if m is None:
            continue
        byte_off, bit_start, indent, text = m.groups()
        offset_bits = int(byte_off) * 8 + int(bit_start or 0)
        if record is None:
            # ``struct Tag (empty)`` names ``Tag``; ``T`` (C typedef) names ``T``.
            record_name = _EMPTY_TAG_RE.sub(
                "", _RECORD_KEYWORD_RE.sub("", text.strip())
            )
            record = {"qualified_name": record_name.strip()}
            continue
        depth = len(indent)
        if member_indent is None:
            member_indent = depth
            member_indents.append(depth)
        if anon_parent is not None:
            # First line after an unnamed member aggregate: its members'
            # indent, when it is deeper (an empty one has no members).
            if depth > anon_parent:
                member_indents.append(depth)
            anon_parent = None
        while len(member_indents) > 1 and depth < member_indents[-1]:
            member_indents.pop()
        if depth != member_indents[-1]:
            continue  # a nested base's or by-value member's own members
        # clang tags an empty base or by-value member ``(empty)`` after it.
        text = _EMPTY_TAG_RE.sub("", text.strip())
        if depth == member_indent:
            vm = _VPTR_RE.match(text)
            if vm is not None:
                vptr = offset_bits if vptr is None else vptr
                continue
            bm = _BASE_RE.match(text)
            if bm is not None:
                bases.append(
                    {
                        "name": bm.group(1).strip(),
                        "offset_bits": offset_bits,
                        "is_virtual": "virtual" in bm.group(2),
                    }
                )
                continue
        if _ANON_MEMBER_RE.match(text):
            anon_parent = depth
            continue
        name = _field_name(text)
        if name is not None:
            fields.append({"name": name, "offset_bits": offset_bits})
    if record is None or "size" not in sizes:
        return None
    record["size_bits"] = sizes["size"] * 8
    if "align" in sizes:
        record["alignment_bits"] = sizes["align"] * 8
    if "dsize" in sizes:
        record["data_size_bits"] = sizes["dsize"] * 8
    if vptr is not None:
        record["vptr_offset_bits"] = vptr
    record["fields"] = fields
    record["bases"] = bases
    return record


def parse_record_layout_dump(text: str) -> list[dict[str, Any]]:
    """Parse ``-fdump-record-layouts`` stdout into per-record layout facts.

    Records clang laid out more than once (the same type reached twice) keep
    the first entry; an entry that does not parse is skipped, never guessed.
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    chunks = text.split(_HEADER)
    for chunk in chunks[1:]:
        entry = _parse_entry(chunk.splitlines())
        if entry is None or entry["qualified_name"] in seen:
            continue
        seen.add(entry["qualified_name"])
        out.append(entry)
    return out


def normalized_record_name(name: str) -> str:
    """Spelling-insensitive key: whitespace dropped, ``class``/``struct`` tags removed."""
    return re.sub(r"\s+", "", re.sub(r"\b(?:class|struct|union|enum)\s+", "", name))


def unscoped_record_name(name: str) -> str:
    """Last-resort key with every scope qualifier dropped, inside template
    arguments too: newer clang prints an explicit instantiation's arguments
    as written (``B<task::tag>``) where the AST spelling is canonical
    (``B<lib::task::tag>``). Used only for a match unique on both sides."""
    return re.sub(r"\b\w+::", "", normalized_record_name(name))
