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

"""Reading an ``ar`` archive's own linker-written symbol index.

Evidence source: the ``/`` (GNU), ``/SYM64/`` (GNU 64-bit), or ``__.SYMDEF``
(BSD/Mach-O) member that ``ar s``/``ranlib`` writes and that the linker
itself reads to decide which member to pull in -- exactly the set of global
(and weak) symbols *defined* by some member object. Two consumers read it:

* the L5 archive/object provenance fold
  (:mod:`abicheck.buildsource.archive_graph`), and
* the ``compare --no-baseline libfoo.a`` declared-vs-exported audit
  (ADR-086), through :func:`read_archive_exports`.

Discipline: **degrade to no answer, never to a wrong one.** An archive with
no index carries *no evidence* of what it defines; that is reported as such
(:attr:`ArchiveContents.has_symbol_index` is ``False``), never as an empty
export set.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

from ..model.elf_facts import ElfMetadata, ElfSymbol, SymbolType
from .ar_archive_format import (
    _BSD_INDEX_PREFIX,
    _GNU_INDEX_NAMES,
    _GNU_LONG_NAME_TABLE,
    _HEADER_SIZE,
    _MAX_INDEXED_SYMBOLS,
    _MAX_MEMBERS,
    _MAX_SPECIAL_MEMBER_BYTES,
    ARCHIVE_MAGIC,
    FLAVOR_GNU,
    FLAVOR_THIN,
    THIN_ARCHIVE_MAGIC,
    ArchiveContents,
    ArchiveFormatError,
    ArMember,
    ArSymbolRef,
    ByteReader,
    FileReader,
    _is_bsd_index_name,
    _is_bsd_index_wide,
    _long_name_at,
    _parse_header,
    _resolve_member_name,
)


def _gnu_symbol_index(
    body: bytes, offsets_by_member: dict[int, str], *, wide: bool
) -> list[ArSymbolRef]:
    """Parse a GNU ``/`` (32-bit) or ``/SYM64/`` (64-bit) index member.

    Layout: a big-endian count, that many big-endian member-*header* offsets,
    then the same number of NUL-terminated symbol names, in order.

    A body too short to even hold the count field is malformed, not empty
    (Codex review, fresh evidence): a real ``ar``/``ranlib`` always writes
    the count field, as ``0``, for a genuinely symbol-free index — a body
    shorter than that is truncation. Silently returning ``[]`` here (an
    earlier revision) was indistinguishable downstream from "confirmed,
    zero symbols", letting :func:`~abicheck.buildsource.
    inline_graph_fold.fold_archive_graph` mark the whole pass complete over
    evidence that was actually corrupt. The same applies to the trailing
    name blob (Codex review, fresh evidence, a separate gap from the body-
    length one above): the offset-array boundary (``end``) only proves the
    *offsets* aren't truncated — a name blob with fewer than ``count``
    NUL-terminated entries (the offset table complete, the name list cut
    short) previously just silently returned however many names ``str.split``
    happened to find, the same "looks like a smaller but complete result"
    failure shape.
    """
    width = 8 if wide else 4
    fmt = ">Q" if wide else ">I"
    if len(body) < width:
        raise ArchiveFormatError(
            f"symbol index body ({len(body)} bytes) is too short to hold its "
            f"own {width}-byte symbol count"
        )
    count = struct.unpack(fmt, body[:width])[0]
    end = width + width * count
    if count > _MAX_INDEXED_SYMBOLS or len(body) < end:
        raise ArchiveFormatError(
            f"symbol index declares {count} symbols but carries {len(body)} bytes"
        )
    offsets = [
        struct.unpack(fmt, body[width + i * width : width + (i + 1) * width])[0]
        for i in range(count)
    ]
    names = body[end:].split(b"\x00")
    # Each of the `count` names is NUL-terminated, so a well-formed blob
    # splits into at least `count` names plus one trailing remainder
    # (possibly empty) after the last terminator — `len(names) < count + 1`
    # means the blob ran out before the declared count did.
    if len(names) < count + 1:
        raise ArchiveFormatError(
            f"symbol index declares {count} names but its name table only "
            f"has {max(len(names) - 1, 0)}"
        )
    refs: list[ArSymbolRef] = []
    for i, offset in enumerate(offsets):
        if i >= len(names):
            break
        member = offsets_by_member.get(offset)
        if member is None:
            continue  # index points at no member we walked — skip, never guess
        symbol = names[i].decode("utf-8", "replace")
        if symbol:
            refs.append(ArSymbolRef(symbol=symbol, member=member, member_offset=offset))
    return refs


def _bsd_symbol_index(
    body: bytes, offsets_by_member: dict[int, str], *, wide: bool
) -> list[ArSymbolRef]:
    """Parse a BSD/Mach-O ``__.SYMDEF`` index member.

    Layout: the ranlib table's byte length, that many bytes of
    ``(string-table offset, member-header offset)`` pairs, then the string
    table's byte length, then the NUL-terminated names. All little-endian —
    the one structural difference from the GNU index above, besides names
    being referenced by offset instead of listed in order.

    A body too short to hold its own table-length field is malformed, not
    empty — same reasoning as :func:`_gnu_symbol_index`'s identical guard.
    """
    width = 8 if wide else 4
    fmt = "<Q" if wide else "<I"
    if len(body) < width:
        raise ArchiveFormatError(
            f"__.SYMDEF body ({len(body)} bytes) is too short to hold its own "
            f"{width}-byte ranlib table length"
        )
    table_bytes = struct.unpack(fmt, body[:width])[0]
    entry_size = width * 2
    start = width
    end = start + table_bytes
    if table_bytes % entry_size or len(body) < end + width:
        raise ArchiveFormatError(
            f"__.SYMDEF ranlib table length {table_bytes} is not usable"
        )
    string_table_len = struct.unpack(fmt, body[end : end + width])[0]
    strings_at = end + width
    strings_end = strings_at + string_table_len
    if strings_end > len(body):
        raise ArchiveFormatError(
            f"__.SYMDEF string table length {string_table_len} runs past the "
            f"member body ({len(body)} bytes available at offset {strings_at})"
        )
    # Bound the string table by its own declared length rather than taking
    # every remaining byte in the member body — a truncated/malformed member
    # would otherwise still "successfully" resolve offsets into whatever
    # trailing bytes happen to follow instead of failing closed.
    strings = body[strings_at:strings_end]
    refs: list[ArSymbolRef] = []
    for pos in range(start, end, entry_size):
        str_off = struct.unpack(fmt, body[pos : pos + width])[0]
        mem_off = struct.unpack(fmt, body[pos + width : pos + entry_size])[0]
        member = offsets_by_member.get(mem_off)
        if member is None or str_off >= len(strings):
            continue
        if str_off != 0 and strings[str_off - 1] != 0:
            # A real ranlib-written entry's str_off always names the START
            # of a string-table entry -- either offset 0 or immediately
            # after a preceding NUL terminator. A malformed/hostile entry
            # pointing into the *middle* of another name (e.g. offset 1
            # into "xfoo\0") would otherwise decode as "foo" -- a
            # plausible-looking but spoofed name that, if it coincidentally
            # matches a real unrelated binary_symbol node, produces a false
            # OBJECT_DEFINES_SYMBOL edge (Codex review, fresh evidence).
            # Reject rather than accept a boundary-violating suffix.
            continue
        terminator = strings.find(b"\x00", str_off)
        if terminator == -1:
            # Every real BSD/Mach-O __.SYMDEF string-table entry is
            # NUL-terminated, including the last one (confirmed against a
            # real llvm-ar --format=bsd archive's raw bytes) -- a missing
            # terminator means the string table itself is truncated or
            # corrupted, not that this symbol's name legitimately runs to
            # the table's own end. Skip it the same way any other
            # individually-unresolvable entry here is skipped, rather than
            # silently accepting a truncated/corrupted run of bytes as a
            # complete symbol name (Codex review, fresh evidence).
            continue
        raw = strings[str_off:terminator]
        symbol = raw.decode("utf-8", "replace")
        if symbol:
            refs.append(
                ArSymbolRef(symbol=symbol, member=member, member_offset=mem_off)
            )
    return refs


def parse_ar_archive(reader: ByteReader) -> ArchiveContents:
    """Walk an ``ar`` archive's member headers and its symbol index.

    Two passes over the header chain, because a GNU symbol index is the
    *first* member but references members by header offset — offsets only
    resolvable once the whole chain (and the ``//`` long-name table, which
    follows the index) has been walked. The first pass therefore records
    ``offset -> name`` and defers every index member; the second decodes
    them.

    **A standard COFF ``.lib`` (MSVC ``lib.exe``, or ``llvm-ar`` in COFF
    mode) writes *two* members both named ``/``** — the "First Linker
    Member" (the traditional big-endian count/offsets/names layout, exactly
    what :func:`_gnu_symbol_index` already decodes — COFF's first linker
    member and GNU's own ``/`` index share this layout by lineage, not
    coincidence) and a "Second Linker Member" with an incompatible,
    little-endian ``(all member offsets, then a symbol count, then one
    2-byte member-table index per symbol)`` layout (Codex review, verified
    against a real ``llvm-ar``-produced COFF archive: parsing the second
    member's leading little-endian count as the GNU big-endian format
    corrupts a small count like 1 into 16,777,216 and raises
    :class:`ArchiveFormatError`, degrading the whole archive). Only the
    *first* occurrence of each special name is decoded — a repeat is the
    COFF second linker member's redundant, differently-shaped index over the
    same member set the first occurrence already resolved (nothing this
    module needs is only in the second member: it adds full-member coverage
    a GNU/COFF archive's own header chain already gives this parser for
    free, not new symbol/member pairs), so it is skipped rather than
    mis-parsed. A genuine second index in the wild that isn't the COFF
    second linker member would degrade the same way any other malformed
    entry does elsewhere in this parser: skipped, not fatal.

    Raises :class:`ArchiveFormatError` for a bad magic, a truncated or
    non-conforming member header, or an index whose declared size doesn't
    fit its member. Individually unresolvable *entries* — a dangling long
    name, an index offset naming no member — are skipped instead, so one
    malformed entry never costs the whole archive its provenance.
    """
    magic = reader.read(0, len(ARCHIVE_MAGIC))
    if magic == ARCHIVE_MAGIC:
        flavor = FLAVOR_GNU
    elif magic == THIN_ARCHIVE_MAGIC:
        flavor = FLAVOR_THIN
    else:
        raise ArchiveFormatError(f"not an ar archive (magic {magic!r})")

    total = reader.size()
    thin = flavor == FLAVOR_THIN

    # Pass 1: the header chain. The long-name table and any index member are
    # captured as raw bytes for pass 2.
    members: list[ArMember] = []
    long_names = b""
    pending_index: list[tuple[str, bytes]] = []
    # Which special index names have already been queued for decoding — a
    # COFF archive's second linker member repeats the name "/" with an
    # incompatible layout (see parse_ar_archive's docstring); only the first
    # occurrence of any given special name is ever decoded.
    seen_index_names: set[str] = set()
    offset = len(ARCHIVE_MAGIC)
    walked = 0
    while offset + _HEADER_SIZE <= total:
        if walked > _MAX_MEMBERS:
            raise ArchiveFormatError(f"more than {_MAX_MEMBERS} members")
        walked += 1
        header_offset = offset
        name_field, size = _parse_header(reader.read(offset, _HEADER_SIZE), offset)
        data_offset = offset + _HEADER_SIZE
        stripped = name_field.rstrip(" ")

        if stripped == _GNU_LONG_NAME_TABLE:
            # A special member's data is always stored inline, thin archive
            # or not; only a *regular* member is bodiless in a thin archive.
            if size > _MAX_SPECIAL_MEMBER_BYTES:
                raise ArchiveFormatError(
                    f"long-name table at offset {offset} declares a size "
                    f"({size}) over the {_MAX_SPECIAL_MEMBER_BYTES}-byte cap"
                )
            long_names = reader.read(data_offset, size)
            stored = size
        elif stripped in _GNU_INDEX_NAMES:
            # GNU never extended-encodes its own index name — it is always
            # the literal "/" or "/SYM64/", so the raw field is authoritative
            # here (unlike the BSD case below).
            if size > _MAX_SPECIAL_MEMBER_BYTES:
                raise ArchiveFormatError(
                    f"{stripped!r} index at offset {offset} declares a size "
                    f"({size}) over the {_MAX_SPECIAL_MEMBER_BYTES}-byte cap"
                )
            if stripped not in seen_index_names:
                seen_index_names.add(stripped)
                pending_index.append((stripped, reader.read(data_offset, size)))
            # else: a repeat of an already-seen GNU index name (the COFF
            # second linker member repeats "/" with an incompatible layout)
            # — bytes still consumed via `stored` so the walk stays in sync,
            # just not queued for decode.
            stored = size
        else:
            # Not a GNU special member by its raw field. Resolve the actual
            # name — via the BSD `#1/<len>` self-referential extended-name
            # form or a GNU `/<index>` long-name-table reference — *before*
            # deciding whether this is a regular member or the BSD symbol
            # index (Codex/CI review, real macOS evidence): a real macOS
            # `ar`/`ranlib` writes its ``__.SYMDEF SORTED`` index member's
            # name via the BSD extended-name form rather than the raw
            # 16-byte field, so classifying on the raw field alone (an
            # earlier revision) silently folded it into `members` instead of
            # the symbol index — root-caused by reproducing the mismatch
            # from the CI failure output, not otherwise observable without a
            # real BSD `ar`.
            name, name_bytes = _resolve_member_name(
                name_field, reader, data_offset, size, long_names
            )
            if name and _is_bsd_index_name(name):
                if size - name_bytes > _MAX_SPECIAL_MEMBER_BYTES:
                    raise ArchiveFormatError(
                        f"__.SYMDEF index at offset {offset} declares a size "
                        f"({size - name_bytes}) over the "
                        f"{_MAX_SPECIAL_MEMBER_BYTES}-byte cap"
                    )
                if _BSD_INDEX_PREFIX not in seen_index_names:
                    seen_index_names.add(_BSD_INDEX_PREFIX)
                    pending_index.append(
                        (name, reader.read(data_offset + name_bytes, size - name_bytes))
                    )
                stored = size  # a BSD index member's data is always inline
            else:
                if name:
                    member_data_offset = data_offset + name_bytes
                    members.append(
                        ArMember(
                            name=name,
                            header_offset=header_offset,
                            data_offset=member_data_offset,
                            size=size - name_bytes,
                            object_magic=reader.read(member_data_offset, 4),
                        )
                    )
                stored = size if not thin else 0
        offset = data_offset + stored + (stored % 2)

    if offset != total:
        # Leftover bytes too small to form another full header: either a
        # genuinely truncated file, or the last member's declared size ran
        # past the actual data. Either way this is not "the archive ends
        # here" (that case leaves offset == total exactly) — it is
        # corruption, and continuing would silently drop real content.
        raise ArchiveFormatError(
            f"{total - offset} trailing byte(s) after the last member header "
            f"do not form a complete member (archive truncated?)"
        )

    # A GNU long-name table follows the index member, so a member whose name
    # was a dangling `/<index>` on the first attempt may resolve now.
    if long_names and any(m.name.startswith("/") for m in members):
        members = [
            (
                m
                if not (m.name.startswith("/") and m.name[1:].isdigit())
                else ArMember(
                    name=_long_name_at(long_names, int(m.name[1:])) or m.name,
                    header_offset=m.header_offset,
                    data_offset=m.data_offset,
                    size=m.size,
                    object_magic=m.object_magic,
                )
            )
            for m in members
        ]

    # Pass 2: decode the index members now that offsets resolve to names.
    offsets_by_member = {m.header_offset: m.name for m in members}
    symbols: list[ArSymbolRef] = []
    index_kind = ""
    for index_name, body in pending_index:
        if index_name in _GNU_INDEX_NAMES:
            symbols.extend(
                _gnu_symbol_index(body, offsets_by_member, wide=index_name == "/SYM64/")
            )
            index_kind = index_kind or "gnu"
        else:
            symbols.extend(
                _bsd_symbol_index(
                    body, offsets_by_member, wide=_is_bsd_index_wide(index_name)
                )
            )
            index_kind = index_kind or "bsd"

    # A BSD/`__.SYMDEF` index format does not by itself prove Mach-O: real
    # `llvm-ar --format=bsd` produces a valid `__.SYMDEF`-indexed archive
    # around ordinary ELF objects too (empirically confirmed -- Codex review,
    # fresh evidence: `llvm-ar --format=bsd rcs lib.a a.o` over a real
    # ELF `a.o` produces exactly this). ``object_magic`` is the first
    # regular member's own leading 4 bytes -- real object-format evidence
    # `augment_graph_with_archives` needs alongside ``index_kind`` before
    # trusting the Mach-O leading-underscore join fallback.
    #
    # Empty (not a wrong guess) for a *thin* archive's first regular member
    # (Codex review, fresh evidence, empirically confirmed: `llvm-ar
    # --format=gnu rcT lib.a a.o` over a real Mach-O `a.o`, then re-parsed,
    # yields `object_magic == b""`): a thin archive's regular members are
    # bodiless (`stored = 0` above) -- their real bytes live in an external
    # file this parser has no path to open, so `member.data_offset` for a
    # thin member points at whatever immediately follows its header (the
    # next member's header, or EOF), never real object content. Reading
    # that offset is therefore never a false Mach-O match (it is ASCII
    # header text, not a 4-byte binary magic sequence), only ever the
    # correct empty/non-matching answer -- so the Mach-O join fallback
    # below silently misses a real Mach-O thin archive's underscore-
    # prefixed symbols (a missed edge, never a wrong one) rather than
    # trusting fabricated evidence. A real fix means resolving each thin
    # member's own external file path (relative to the archive's own
    # directory, ADR-032 D7-redaction-aware, with the same path-escape
    # care `_resolve_archive_path`'s own docstring already gives adjacent
    # inputs) and reading its magic from there instead -- new, non-trivial
    # plumbing this parser doesn't have today, not a one-line extension of
    # this read. Deferred the same way this file already defers its other
    # documented thin-archive gaps (see `_resolve_archive_path`'s own
    # docstring).
    # Reuses the first member's own already-read object_magic (populated at
    # construction time above) rather than re-reading -- same value, one
    # less I/O call.
    object_magic = members[0].object_magic if members else b""

    return ArchiveContents(
        flavor=flavor,
        members=tuple(members),
        symbols=tuple(symbols),
        has_symbol_index=bool(pending_index),
        index_kind=index_kind,
        object_magic=object_magic,
    )


def read_archive(path: Path) -> ArchiveContents:
    """:func:`parse_ar_archive` over a file on disk.

    Propagates ``OSError`` (unreadable, a directory, gone) and
    :class:`ArchiveFormatError` (present but not an archive) unchanged — the
    caller decides which becomes which diagnostic.
    """
    with FileReader(path) as reader:
        return parse_ar_archive(reader)


# ── the static-archive export read (ADR-086) ────────────────────────────────

_ELF_MAGIC = b"\x7fELF"
#: ``e_ident`` + ``e_type`` + ``e_machine``: everything this read needs.
_ELF_IDENT_BYTES = 20

#: :attr:`ArchiveExportRead.status` values.
ARCHIVE_EXPORTS_READ = "read"
ARCHIVE_EXPORTS_NO_INDEX = "no_index"
ARCHIVE_EXPORTS_UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class ArchiveExportRead:
    """What a static archive's own symbol index says it *defines*.

    ``status`` is :data:`ARCHIVE_EXPORTS_READ` only when the archive carries
    a symbol index and every regular member is an ELF object of one target;
    then ``names`` is the exact index (global and weak definitions alike --
    the linker's own "this archive can satisfy that reference" set) and the
    ``machine``/``elf_class``/``ei_data``/``osabi`` fields are the members'
    shared ELF identity. Any other status carries no names at all and a
    ``reason`` the caller surfaces verbatim: an archive without an index is
    *no evidence* about what it defines, never an empty export set.
    """

    status: str
    reason: str = ""
    names: frozenset[str] = frozenset()
    member_count: int = 0
    index_kind: str = ""
    machine: str = ""
    elf_class: int = 0
    ei_data: str = ""
    osabi: str = ""


def _enum_name(table: Mapping[str, int], value: int, prefix: str) -> str:
    return next(
        (k for k, v in table.items() if v == value and not k.startswith("_")),
        f"{prefix}{value}",
    )


def _elf_identity(ident: bytes) -> tuple[str, int, str, str] | None:
    """``(machine, class, data, osabi)`` from an ELF header prefix, else None."""
    if len(ident) < _ELF_IDENT_BYTES or not ident.startswith(_ELF_MAGIC):
        return None
    from elftools.elf.enums import ENUM_E_MACHINE, ENUM_EI_OSABI

    elf_class = {1: 32, 2: 64}.get(ident[4])
    data = {1: "LSB", 2: "MSB"}.get(ident[5])
    if elf_class is None or data is None:
        return None
    (e_machine,) = struct.unpack("<H" if data == "LSB" else ">H", ident[18:20])
    return (
        _enum_name(ENUM_E_MACHINE, e_machine, "EM_"),
        elf_class,
        data,
        _enum_name(ENUM_EI_OSABI, ident[7], "ELFOSABI_"),
    )


def read_archive_exports(path: Path) -> ArchiveExportRead:
    """Read *path*'s symbol index as an export set (ADR-086).

    Raises ``OSError``/:class:`ArchiveFormatError` like :func:`read_archive`;
    every *well-formed* archive this read cannot vouch for comes back as a
    non-``read`` status with a reason, never as an empty ``names``.
    """
    with FileReader(path) as reader:
        contents = parse_ar_archive(reader)
        thin = contents.flavor == FLAVOR_THIN
        idents = [
            None
            if thin
            else _elf_identity(reader.read(m.data_offset, _ELF_IDENT_BYTES))
            for m in contents.members
        ]
    count, kind = len(contents.members), contents.index_kind
    if thin:
        return ArchiveExportRead(
            ARCHIVE_EXPORTS_UNSUPPORTED,
            "a thin archive (`ar T`) keeps its members outside the file; "
            "pass a regular archive",
            member_count=count,
            index_kind=kind,
        )
    distinct = {i for i in idents if i is not None}
    if not idents or len(distinct) != 1 or None in idents:
        return ArchiveExportRead(
            ARCHIVE_EXPORTS_UNSUPPORTED,
            "only archives whose members are all ELF objects for one target "
            "are supported (Mach-O, COFF/.lib, LLVM-bitcode or mixed-target "
            "members are not)",
            member_count=count,
            index_kind=kind,
        )
    if not contents.has_symbol_index:
        return ArchiveExportRead(
            ARCHIVE_EXPORTS_NO_INDEX,
            "the archive has no symbol index (written with `ar S`, or by a tool that "
            "omits it), so what it defines is unknown; run `ranlib <archive>` "
            "(or `ar s <archive>`) and retry",
            member_count=count,
        )
    machine, elf_class, data, osabi = next(iter(distinct))
    return ArchiveExportRead(
        ARCHIVE_EXPORTS_READ,
        names=frozenset(s.symbol for s in contents.symbols if s.symbol),
        member_count=count,
        index_kind=kind,
        machine=machine,
        elf_class=elf_class,
        ei_data=data,
        osabi=osabi,
    )


def archive_elf_metadata(read: ArchiveExportRead) -> ElfMetadata:
    """The export facts a :data:`ARCHIVE_EXPORTS_READ` *read* establishes.

    Only what the index and the members' ELF header prove: the defined
    names and the target identity (``machine`` set, so an archive that
    defines nothing still reads as a parsed, empty table rather than an
    unread one). The index records neither binding nor symbol type, so
    every row is ``NOTYPE`` with the default binding, and no row carries a
    version; the dynamic-section fields (SONAME, ``DT_NEEDED``, version
    definitions) keep their "not observed" defaults -- an archive has no
    dynamic section, and :attr:`AbiSnapshot.static_archive` tells readers
    so.
    """
    if read.status != ARCHIVE_EXPORTS_READ:
        raise ValueError(f"archive exports were not read: {read.reason}")
    return ElfMetadata(
        symbols=[
            ElfSymbol(name=n, sym_type=SymbolType.NOTYPE) for n in sorted(read.names)
        ],
        machine=read.machine,
        elf_class=read.elf_class,
        pointer_size=read.elf_class // 8,
        ei_data=read.ei_data,
        osabi=read.osabi,
    )
