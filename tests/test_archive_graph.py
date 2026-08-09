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

"""Tests for :mod:`abicheck.buildsource.archive_graph` (G29 Phase 5 item 6).

Every archive fixture is built byte-for-byte in Python (no ``ar`` binary
required) so this stays a fast unit test — real ``ar``-produced bytes were
hand-verified once while writing the parser (see the module's own review
notes) and are re-verified here structurally, not by re-invoking the tool.
"""

from __future__ import annotations

import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from abicheck.buildsource.archive_graph import (
    ARCHIVE_MAGIC,
    THIN_ARCHIVE_MAGIC,
    ArchiveFormatError,
    ArchiveGraphResult,
    BytesReader,
    archive_member_node_id,
    augment_graph_with_archives,
    defining_members,
    parse_ar_archive,
    read_archive,
)
from abicheck.buildsource.source_graph import GraphNode, SourceGraphSummary


def _header(name: str, size: int) -> bytes:
    h = name.ljust(16).encode("ascii")
    h += b"0".ljust(12)  # mtime
    h += b"0".ljust(6)  # uid
    h += b"0".ljust(6)  # gid
    h += b"100644".ljust(8)  # mode
    h += str(size).ljust(10).encode("ascii")  # size
    h += b"`\n"
    assert len(h) == 60
    return h


def _pad(data: bytes) -> bytes:
    return data + (b"\n" if len(data) % 2 else b"")


def build_gnu_archive(
    members: list[tuple[str, bytes]],
    *,
    thin: bool = False,
    long_names: bool = False,
    with_index: bool = True,
) -> bytes:
    """Hand-assemble a GNU ``ar`` archive with a real ``/`` symbol index.

    ``members`` is ``[(name, data), ...]``; every symbol name in a member's
    data on a line by itself starting with ``SYM:`` is registered against
    that member in the index — a tiny fixture convention, not a real object
    format.
    """
    magic = THIN_ARCHIVE_MAGIC if thin else ARCHIVE_MAGIC

    def extract_symbols(data: bytes) -> list[str]:
        out = []
        for line in data.split(b"\n"):
            if line.startswith(b"SYM:"):
                out.append(line[4:].decode("ascii"))
        return out

    long_name_table = b""
    long_name_offsets: dict[str, int] = {}
    if long_names:
        for name, _ in members:
            if len(name) + 1 > 16:
                long_name_offsets[name] = len(long_name_table)
                long_name_table += name.encode("ascii") + b"/\n"

    # First lay out members to learn header offsets (needed for the index).
    # Two passes: sizes only depend on names+data, not offsets, so a single
    # pass with placeholder offsets works, then we patch the index in place.
    def member_header(name: str) -> bytes:
        if name in long_name_offsets:
            return _header(f"/{long_name_offsets[name]}", len(data_of[name]))
        return _header(name + "/", len(data_of[name]))

    data_of = dict(members)

    body_start = len(magic)
    # Reserve space for the index member (patched after we know offsets).
    symbol_entries: list[tuple[str, str]] = []  # (symbol, member_name)
    for name, data in members:
        for sym in extract_symbols(data):
            symbol_entries.append((sym, name))

    names_blob = b"\x00".join(s.encode("ascii") for s, _ in symbol_entries) + (
        b"\x00" if symbol_entries else b""
    )
    index_body_placeholder = (
        struct.pack(">I", len(symbol_entries))
        + (b"\x00\x00\x00\x00" * len(symbol_entries))
        + names_blob
    )
    index_total = 60 + len(index_body_placeholder) + (len(index_body_placeholder) % 2)

    pos = body_start + (index_total if with_index else 0)
    long_name_total = 0
    if long_names:
        long_name_total = 60 + len(long_name_table) + (len(long_name_table) % 2)
    pos += long_name_total

    header_offsets: dict[str, int] = {}
    member_blob = b""
    for name, data in members:
        header_offsets[name] = pos
        h = member_header(name)
        stored = b"" if (thin and not name.startswith("/")) else data
        member_blob += h + _pad(stored)
        pos += 60 + (len(stored) + (len(stored) % 2))

    out = magic
    if with_index:
        offsets = struct.pack(
            f">{len(symbol_entries)}I",
            *(header_offsets[m] for _, m in symbol_entries),
        )
        index_body = struct.pack(">I", len(symbol_entries)) + offsets + names_blob
        out += _header("/", len(index_body)) + _pad(index_body)
    if long_names:
        out += _header("//", len(long_name_table)) + _pad(long_name_table)
    out += member_blob
    return out


# ── pure parser tests ────────────────────────────────────────────────────────


def test_parse_simple_gnu_archive_with_index() -> None:
    data = build_gnu_archive(
        [("a.o", b"SYM:alpha\n"), ("b.o", b"SYM:beta\nSYM:gamma\n")]
    )
    contents = parse_ar_archive(BytesReader(data))
    assert contents.flavor == "gnu"
    assert [m.name for m in contents.members] == ["a.o", "b.o"]
    assert contents.has_symbol_index
    assert sorted((s.symbol, s.member) for s in contents.symbols) == [
        ("alpha", "a.o"),
        ("beta", "b.o"),
        ("gamma", "b.o"),
    ]


def test_parse_archive_without_index() -> None:
    data = build_gnu_archive([("a.o", b"SYM:alpha\n")], with_index=False)
    contents = parse_ar_archive(BytesReader(data))
    assert [m.name for m in contents.members] == ["a.o"]
    assert not contents.has_symbol_index
    assert contents.symbols == ()


def test_parse_archive_with_gnu_long_names() -> None:
    long_name = "a_very_long_object_file_name_exceeding_sixteen_chars.o"
    data = build_gnu_archive(
        [(long_name, b"SYM:alpha\n"), ("b.o", b"SYM:beta\n")], long_names=True
    )
    contents = parse_ar_archive(BytesReader(data))
    assert set(m.name for m in contents.members) == {long_name, "b.o"}
    assert sorted((s.symbol, s.member) for s in contents.symbols) == [
        ("alpha", long_name),
        ("beta", "b.o"),
    ]


def test_parse_thin_archive() -> None:
    data = build_gnu_archive(
        [("a.o", b"SYM:alpha\n"), ("b.o", b"SYM:beta\n")], thin=True
    )
    contents = parse_ar_archive(BytesReader(data))
    assert contents.flavor == "thin"
    assert [m.name for m in contents.members] == ["a.o", "b.o"]
    assert sorted((s.symbol, s.member) for s in contents.symbols) == [
        ("alpha", "a.o"),
        ("beta", "b.o"),
    ]


def test_parse_rejects_bad_magic() -> None:
    with pytest.raises(ArchiveFormatError, match="not an ar archive"):
        parse_ar_archive(BytesReader(b"not an archive at all!!"))


def test_parse_rejects_truncated_header() -> None:
    with pytest.raises(ArchiveFormatError, match="truncated"):
        parse_ar_archive(BytesReader(ARCHIVE_MAGIC + b"short"))


def test_parse_rejects_header_missing_terminator() -> None:
    bad = ARCHIVE_MAGIC + (b"a.o".ljust(58) + b"XX")
    with pytest.raises(ArchiveFormatError, match="terminator"):
        parse_ar_archive(BytesReader(bad))


def test_parse_rejects_non_numeric_size() -> None:
    h = _header("a.o", 0)
    bad_size = h[:48] + b"notanum   " + h[58:]
    with pytest.raises(ArchiveFormatError, match="non-numeric"):
        parse_ar_archive(BytesReader(ARCHIVE_MAGIC + bad_size))


def test_parse_index_declaring_more_symbols_than_bytes_raises() -> None:
    # A GNU index claiming 1000 symbols but carrying only a handful of bytes.
    bogus_index_body = struct.pack(">I", 1000) + b"\x00" * 8
    data = ARCHIVE_MAGIC + _header("/", len(bogus_index_body)) + _pad(bogus_index_body)
    with pytest.raises(ArchiveFormatError, match="declares"):
        parse_ar_archive(BytesReader(data))


def test_large_legitimate_symbol_count_does_not_hit_the_member_cap() -> None:
    """A real C++ static library routinely indexes far more global symbols
    (templates, inline functions, every exported specialization) than it has
    object-file members — the symbol-count sanity check must not reuse
    ``_MAX_MEMBERS`` (Codex review, fresh evidence: a legitimate archive with
    >100,000 indexed symbols and a correctly-sized body was previously
    rejected outright, discarding all of its provenance).
    """
    count = 150_000
    index_body = struct.pack(">I", count) + (b"\x00\x00\x00\x00" * count)
    data = ARCHIVE_MAGIC + _header("/", len(index_body)) + _pad(index_body)
    contents = parse_ar_archive(BytesReader(data))
    assert contents.has_symbol_index
    assert contents.symbols == ()  # no member/name blob to resolve against


def test_gnu_symbol_index_body_too_short_for_count_field_raises() -> None:
    """A ``/`` member whose declared size can't even hold the 4-byte count
    field is truncated/malformed, not a confirmed-empty index (Codex review,
    fresh evidence: an earlier revision silently returned no symbols here,
    which read identically to "confirmed, zero symbols" downstream)."""
    data = ARCHIVE_MAGIC + _header("/", 2) + _pad(b"\x00\x00")
    with pytest.raises(ArchiveFormatError, match="too short"):
        parse_ar_archive(BytesReader(data))


def test_bsd_symdef_body_too_short_for_table_length_raises() -> None:
    data = ARCHIVE_MAGIC + _header("__.SYMDEF", 2) + _pad(b"\x00\x00")
    with pytest.raises(ArchiveFormatError, match="too short"):
        parse_ar_archive(BytesReader(data))


def test_parse_index_offset_naming_no_member_is_skipped_not_raised() -> None:
    # A well-formed index whose one entry's offset resolves to nothing (e.g.
    # a stale index after member reordering) — the symbol is dropped, not an
    # error.
    names_blob = b"ghost\x00"
    index_body = struct.pack(">I", 1) + struct.pack(">I", 99999) + names_blob
    data = ARCHIVE_MAGIC + _header("/", len(index_body)) + _pad(index_body)
    contents = parse_ar_archive(BytesReader(data))
    assert contents.symbols == ()
    assert contents.has_symbol_index


def test_bsd_symdef_index() -> None:
    strtab = b"alpha\x00beta\x00"
    entries_placeholder = b"\x00" * 16
    body = (
        struct.pack("<I", 16)
        + entries_placeholder
        + struct.pack("<I", len(strtab))
        + strtab
    )
    symdef_header = _header("__.SYMDEF", len(body))
    a_data, b_data = b"OBJA", b"OBJB"
    member_start = 8 + 60 + len(_pad(body))
    a_offset = member_start
    b_offset = a_offset + 60 + len(_pad(a_data))
    entries = struct.pack("<II", 0, a_offset) + struct.pack(
        "<II", len(b"alpha\x00"), b_offset
    )
    body = struct.pack("<I", 16) + entries + struct.pack("<I", len(strtab)) + strtab
    symdef_header = _header("__.SYMDEF", len(body))
    data = (
        ARCHIVE_MAGIC
        + symdef_header
        + _pad(body)
        + _header("a.o", len(a_data))
        + _pad(a_data)
        + _header("b.o", len(b_data))
        + _pad(b_data)
    )
    contents = parse_ar_archive(BytesReader(data))
    assert {m.name for m in contents.members} == {"a.o", "b.o"}
    assert sorted((s.symbol, s.member) for s in contents.symbols) == [
        ("alpha", "a.o"),
        ("beta", "b.o"),
    ]


def test_bsd_symdef_via_extended_name_is_recognized_as_index_not_a_member() -> None:
    """A real macOS ``ar``/``ranlib`` writes its ``__.SYMDEF SORTED`` index
    member's name via the BSD ``#1/<len>`` self-referential extended-name
    form (the member's own data starts with its name), not the raw 16-byte
    header field — confirmed by a macOS CI integration-lane failure: an
    earlier revision classified special-vs-regular from the *raw* field
    alone, so this member was silently folded into ``members`` (a fake
    object file named ``__.SYMDEF SORTED``) instead of being decoded as the
    symbol index (Codex/CI review).
    """
    a_data, b_data = b"OBJA", b"OBJB"
    a_offset = len(ARCHIVE_MAGIC)
    b_offset = a_offset + 60 + len(_pad(a_data))

    strtab = b"alpha\x00beta\x00"
    entries = struct.pack("<II", 0, a_offset) + struct.pack(
        "<II", len(b"alpha\x00"), b_offset
    )
    ranlib_body = (
        struct.pack("<I", len(entries))
        + entries
        + struct.pack("<I", len(strtab))
        + strtab
    )
    name = "__.SYMDEF SORTED"
    member_data = name.encode("ascii") + ranlib_body

    data = (
        ARCHIVE_MAGIC
        + _header("a.o", len(a_data))
        + _pad(a_data)
        + _header("b.o", len(b_data))
        + _pad(b_data)
        + _header(f"#1/{len(name)}", len(member_data))
        + _pad(member_data)
    )

    contents = parse_ar_archive(BytesReader(data))
    assert {m.name for m in contents.members} == {"a.o", "b.o"}
    assert contents.has_symbol_index
    assert sorted((s.symbol, s.member) for s in contents.symbols) == [
        ("alpha", "a.o"),
        ("beta", "b.o"),
    ]


def test_coff_second_linker_member_is_skipped_not_misparsed() -> None:
    """A standard COFF ``.lib`` writes *two* members named ``/`` — the GNU-
    compatible "first linker member" this parser already decodes, and a
    "second linker member" with an incompatible little-endian layout
    (Codex review, verified against a real ``llvm-ar``-produced COFF
    archive). Feeding the second member's leading little-endian byte
    ``\\x01\\x00\\x00\\x00`` through the GNU big-endian decoder corrupts the
    symbol count into 16,777,216 and used to raise ``ArchiveFormatError``,
    degrading the whole archive. Only the first ``/`` occurrence is decoded
    now; the second is skipped, not mis-parsed.
    """
    a_data = b"OBJDATA"
    a_offset = len(ARCHIVE_MAGIC)  # placed first, so its header offset is fixed
    a_full = _header("a.o/", len(a_data)) + _pad(a_data)

    first_index_body = struct.pack(">I", 1) + struct.pack(">I", a_offset) + b"alpha\x00"
    first_index_full = _header("/", len(first_index_body)) + _pad(first_index_body)

    # A COFF second-linker-member-shaped payload: little-endian member count
    # (1) that a big-endian read would corrupt into a huge, unusable count.
    second_body = (
        struct.pack("<I", 1)  # member count
        + struct.pack("<I", a_offset)  # member offset table
        + struct.pack("<I", 1)  # symbol count
        + struct.pack("<H", 1)  # 1-based member-table index per symbol
        + b"alpha\x00"
    )
    second_index_full = _header("/", len(second_body)) + _pad(second_body)

    data = ARCHIVE_MAGIC + a_full + first_index_full + second_index_full

    contents = parse_ar_archive(BytesReader(data))
    assert contents.has_symbol_index
    assert {m.name for m in contents.members} == {"a.o"}
    assert {(s.symbol, s.member) for s in contents.symbols} == {("alpha", "a.o")}


def test_read_archive_missing_file_raises_oserror(tmp_path) -> None:
    with pytest.raises(OSError):
        read_archive(tmp_path / "does_not_exist.a")


def test_read_archive_from_disk(tmp_path) -> None:
    data = build_gnu_archive([("a.o", b"SYM:alpha\n")])
    path = tmp_path / "lib.a"
    path.write_bytes(data)
    contents = read_archive(path)
    assert [m.name for m in contents.members] == ["a.o"]


# ── graph augmentation tests ─────────────────────────────────────────────────


def _graph_with_archive(
    archive_label: str, symbol_ids: list[str]
) -> SourceGraphSummary:
    g = SourceGraphSummary()
    g.add_node(
        GraphNode(
            id=f"static_library://{archive_label}",
            kind="static_library",
            label=archive_label,
            provenance="build_evidence",
            confidence="reduced",
        )
    )
    for sid in symbol_ids:
        g.add_node(
            GraphNode(
                id=f"binary_symbol://{sid}",
                kind="binary_symbol",
                label=sid,
                provenance="binary",
                confidence="high",
            )
        )
    return g


def test_augment_graph_creates_members_and_symbol_edges(tmp_path) -> None:
    data = build_gnu_archive(
        [("a.o", b"SYM:alpha\n"), ("b.o", b"SYM:beta\nSYM:gamma\n")]
    )
    (tmp_path / "libtest.a").write_bytes(data)
    g = _graph_with_archive("libtest.a", ["alpha", "beta"])  # gamma not exported

    result = augment_graph_with_archives(g, search_roots=(tmp_path,))

    assert result.archives_seen == 1
    assert result.archives_read == 1
    assert result.members == 2
    assert result.symbol_edges == 2
    assert result.unjoined_symbols == 1  # gamma has no binary_symbol node
    assert result.diagnostics == []
    assert result.complete

    member_ids = {n.id for n in g.nodes if n.kind == "archive_member"}
    assert member_ids == {
        archive_member_node_id("libtest.a", "a.o"),
        archive_member_node_id("libtest.a", "b.o"),
    }
    contains_edges = {
        (e.src, e.dst) for e in g.edges if e.kind == "ARCHIVE_CONTAINS_OBJECT"
    }
    assert contains_edges == {
        ("static_library://libtest.a", archive_member_node_id("libtest.a", "a.o")),
        ("static_library://libtest.a", archive_member_node_id("libtest.a", "b.o")),
    }
    defines_edges = {
        (e.src, e.dst) for e in g.edges if e.kind == "OBJECT_DEFINES_SYMBOL"
    }
    assert defines_edges == {
        (archive_member_node_id("libtest.a", "a.o"), "binary_symbol://alpha"),
        (archive_member_node_id("libtest.a", "b.o"), "binary_symbol://beta"),
    }


def test_augment_graph_defining_members_localizes_symbol(tmp_path) -> None:
    data = build_gnu_archive(
        [("cache_dispatch.o", b"SYM:internal_dispatch\n")], long_names=True
    )
    (tmp_path / "libinternal_dispatch.a").write_bytes(data)
    g = _graph_with_archive("libinternal_dispatch.a", ["internal_dispatch"])

    augment_graph_with_archives(g, search_roots=(tmp_path,))

    assert defining_members(g, "internal_dispatch") == [
        ("libinternal_dispatch.a", "cache_dispatch.o")
    ]
    assert defining_members(g, "nonexistent") == []


def test_augment_graph_missing_archive_is_a_diagnostic_not_an_exception(
    tmp_path,
) -> None:
    g = _graph_with_archive("libghost.a", [])
    result = augment_graph_with_archives(g, search_roots=(tmp_path,))
    assert result.archives_seen == 1
    assert result.archives_read == 0
    assert result.diagnostics
    assert not result.complete
    # No archive_member nodes were minted for the unreadable archive.
    assert not any(n.kind == "archive_member" for n in g.nodes)


def test_augment_graph_malformed_archive_is_a_diagnostic_not_an_exception(
    tmp_path,
) -> None:
    (tmp_path / "libbad.a").write_bytes(b"not an archive")
    g = _graph_with_archive("libbad.a", [])
    result = augment_graph_with_archives(g, search_roots=(tmp_path,))
    assert result.archives_read == 0
    assert result.diagnostics
    assert not result.complete


def test_augment_graph_no_index_archive_yields_members_but_no_edges(tmp_path) -> None:
    data = build_gnu_archive([("a.o", b"SYM:alpha\n")], with_index=False)
    (tmp_path / "libnoindex.a").write_bytes(data)
    g = _graph_with_archive("libnoindex.a", ["alpha"])
    result = augment_graph_with_archives(g, search_roots=(tmp_path,))
    assert result.members == 1
    assert result.symbol_edges == 0
    assert result.diagnostics  # "no symbol index" diagnostic
    assert not result.complete  # can't claim confirmed coverage without one


def test_augment_graph_absolute_archive_path_ignores_search_roots(tmp_path) -> None:
    data = build_gnu_archive([("a.o", b"SYM:alpha\n")])
    archive_path = tmp_path / "libabs.a"
    archive_path.write_bytes(data)
    g = _graph_with_archive(str(archive_path), ["alpha"])
    # No search_roots at all — the label is already absolute.
    result = augment_graph_with_archives(g)
    assert result.archives_read == 1
    assert result.symbol_edges == 1


def test_augment_graph_no_static_library_nodes_is_a_noop() -> None:
    g = SourceGraphSummary()
    g.add_node(
        GraphNode(
            id="target://foo", kind="target", label="foo", provenance="build_evidence"
        )
    )
    result = augment_graph_with_archives(g)
    assert result == ArchiveGraphResult()


def test_two_archives_same_member_name_do_not_collide(tmp_path) -> None:
    data_a = build_gnu_archive([("util.o", b"SYM:foo_impl\n")])
    data_b = build_gnu_archive([("util.o", b"SYM:bar_impl\n")])
    (tmp_path / "liba.a").write_bytes(data_a)
    (tmp_path / "libb.a").write_bytes(data_b)
    g = SourceGraphSummary()
    for label in ("liba.a", "libb.a"):
        g.add_node(
            GraphNode(
                id=f"static_library://{label}",
                kind="static_library",
                label=label,
                provenance="build_evidence",
            )
        )
    for sym in ("foo_impl", "bar_impl"):
        g.add_node(
            GraphNode(id=f"binary_symbol://{sym}", kind="binary_symbol", label=sym)
        )

    result = augment_graph_with_archives(g, search_roots=(tmp_path,))

    assert result.archives_read == 2
    member_ids = {n.id for n in g.nodes if n.kind == "archive_member"}
    assert member_ids == {
        archive_member_node_id("liba.a", "util.o"),
        archive_member_node_id("libb.a", "util.o"),
    }
    assert defining_members(g, "foo_impl") == [("liba.a", "util.o")]
    assert defining_members(g, "bar_impl") == [("libb.a", "util.o")]


def test_augment_graph_expands_redacted_home_placeholder(tmp_path, monkeypatch) -> None:
    """Build evidence is redacted before persisting (ADR-032 D7): a link
    input's home-directory prefix becomes a literal ``~``.
    ``Path("~/...").is_absolute()`` is ``False``, so both an absolute
    redacted label and a redacted default search root must be unredacted
    before resolution or the archive is never found (Codex review).
    """
    fake_home = tmp_path / "home"
    build_dir = fake_home / "build"
    build_dir.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))
    data = build_gnu_archive([("a.o", b"SYM:alpha\n")])
    (build_dir / "libtest.a").write_bytes(data)

    # A redacted *absolute* label.
    g_abs = _graph_with_archive("~/build/libtest.a", ["alpha"])
    result_abs = augment_graph_with_archives(g_abs)
    assert result_abs.archives_read == 1
    assert result_abs.symbol_edges == 1

    # A redacted *search root* (e.g. a redacted compile-unit directory) with
    # a relative label.
    g_rel = _graph_with_archive("libtest.a", ["alpha"])
    result_rel = augment_graph_with_archives(g_rel, search_roots=(Path("~/build"),))
    assert result_rel.archives_read == 1
    assert result_rel.symbol_edges == 1


def test_augment_graph_ambiguous_relative_archive_is_a_diagnostic_not_a_guess(
    tmp_path,
) -> None:
    """Two distinct subprojects each linking their own, unrelated
    ``libcommon.a`` by a relative path coalesce onto **one**
    ``static_library://libcommon.a`` graph node
    (``source_graph._fold_link_provenance``). Picking whichever search root
    happens to match first would attach one target's real archive data to a
    node that also represents the other target's different archive —
    "incorrect localization... rather than degraded evidence" (Codex
    review). Resolution must refuse to guess.
    """
    root_a, root_b = tmp_path / "a", tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()
    data_a = build_gnu_archive([("x.o", b"SYM:foo_a\n")])
    data_b = build_gnu_archive([("x.o", b"SYM:foo_b\n")])
    (root_a / "libcommon.a").write_bytes(data_a)
    (root_b / "libcommon.a").write_bytes(data_b)
    g = _graph_with_archive("libcommon.a", ["foo_a", "foo_b"])

    result = augment_graph_with_archives(g, search_roots=(root_a, root_b))

    assert result.archives_seen == 1
    assert result.archives_read == 0  # neither candidate was opened
    assert any("ambiguous" in d for d in result.diagnostics)
    assert not result.complete
    assert not any(n.kind == "archive_member" for n in g.nodes)
    assert defining_members(g, "foo_a") == []
    assert defining_members(g, "foo_b") == []


def test_augment_graph_ambiguous_diagnostic_is_redacted(tmp_path, monkeypatch) -> None:
    """The resolved candidate paths embedded in an "ambiguous" diagnostic
    must be re-redacted before reaching ``ExtractorRecord.detail`` (Codex
    review, fresh evidence): `_resolve_archive_path` deliberately un-redacts
    a "~"-placeholder path to find the real file on disk, but that real,
    absolute path must never reach persisted build evidence — this
    diagnostic is embedded straight into build evidence / a persisted
    snapshot's ``build_source`` payload, so leaving it un-redacted here would
    reintroduce the exact home-directory leak ADR-032 D7's redaction exists
    to prevent.
    """
    from abicheck.buildsource import redaction as redaction_mod

    root_a, root_b = tmp_path / "proja", tmp_path / "projb"
    root_a.mkdir()
    root_b.mkdir()
    monkeypatch.setattr(
        redaction_mod,
        "DEFAULT_REDACTION",
        redaction_mod.RedactionPolicy(
            home_replacements={str(tmp_path): "~project-root~"}
        ),
    )
    data_a = build_gnu_archive([("x.o", b"SYM:foo_a\n")])
    data_b = build_gnu_archive([("x.o", b"SYM:foo_b\n")])
    (root_a / "libcommon.a").write_bytes(data_a)
    (root_b / "libcommon.a").write_bytes(data_b)
    g = _graph_with_archive("libcommon.a", ["foo_a", "foo_b"])

    result = augment_graph_with_archives(g, search_roots=(root_a, root_b))

    assert result.diagnostics
    diag = result.diagnostics[0]
    assert str(tmp_path) not in diag
    assert diag.count("~project-root~") == 2


def test_augment_graph_same_archive_via_two_roots_is_not_ambiguous(tmp_path) -> None:
    """Two search roots that resolve to the *same* file (a duplicate root, or
    a symlinked/overlapping build directory) are the identical file, not a
    collision — ambiguity is about distinct files, not distinct paths."""
    data = build_gnu_archive([("x.o", b"SYM:foo\n")])
    (tmp_path / "libcommon.a").write_bytes(data)
    g = _graph_with_archive("libcommon.a", ["foo"])

    result = augment_graph_with_archives(g, search_roots=(tmp_path, tmp_path))

    assert result.archives_read == 1
    assert result.symbol_edges == 1
    assert not result.diagnostics


# ── real-toolchain regression (integration marker: needs gcc + ar) ──────────


@pytest.mark.integration
def test_real_gnu_ar_archive_parses(tmp_path) -> None:
    """Round-trips a real ``gcc``-compiled, ``ar rcs``-indexed archive —
    confirms this module's hand-rolled GNU index parser against actual
    binutils output, not just the byte-shape fixtures above.
    """
    if shutil.which("ar") is None:
        pytest.skip("ar not found in PATH")
    (tmp_path / "a.c").write_text(
        "int alpha(void){return 1;}\nstatic int hidden(void){return 2;}\n"
    )
    (tmp_path / "b.c").write_text("int beta(void){return 3;}\nint gamma_sym;\n")
    subprocess.run(
        ["gcc", "-c", "a.c", "b.c"], cwd=tmp_path, check=True, capture_output=True
    )
    subprocess.run(
        ["ar", "rcs", "libtest.a", "a.o", "b.o"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    contents = read_archive(tmp_path / "libtest.a")
    assert contents.flavor == "gnu"
    assert {m.name for m in contents.members} == {"a.o", "b.o"}
    assert contents.has_symbol_index
    got = {(s.symbol, s.member) for s in contents.symbols}
    # `hidden` is file-local (static) — a real ar index never lists it.
    assert got == {("alpha", "a.o"), ("beta", "b.o"), ("gamma_sym", "b.o")}


@pytest.mark.integration
def test_real_gnu_ar_thin_archive_parses(tmp_path) -> None:
    if shutil.which("ar") is None:
        pytest.skip("ar not found in PATH")
    (tmp_path / "a.c").write_text("int alpha(void){return 1;}\n")
    subprocess.run(["gcc", "-c", "a.c"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["ar", "rcsT", "libthin.a", "a.o"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    archive_path = tmp_path / "libthin.a"
    # GNU's `-T` (thin archive) modifier is a binutils extension; macOS's
    # cctools `ar` (and other non-GNU implementations) silently ignore it
    # and write an ordinary archive instead of erroring (real macOS CI
    # evidence — Codex/CI review). Detect that up front and skip rather than
    # asserting GNU-only semantics against whatever `ar` the host actually
    # has, the same "feature-detect, don't assume" approach `shutil.which`
    # already uses just above for `ar`'s presence at all.
    if archive_path.read_bytes()[: len(THIN_ARCHIVE_MAGIC)] != THIN_ARCHIVE_MAGIC:
        pytest.skip("this ar does not produce GNU thin archives (non-GNU ar)")
    contents = read_archive(archive_path)
    assert contents.flavor == "thin"
    assert [m.name for m in contents.members] == ["a.o"]
    assert {(s.symbol, s.member) for s in contents.symbols} == {("alpha", "a.o")}


@pytest.mark.integration
def test_real_gnu_ar_archive_without_index(tmp_path) -> None:
    if shutil.which("ar") is None:
        pytest.skip("ar not found in PATH")
    (tmp_path / "a.c").write_text("int alpha(void){return 1;}\n")
    subprocess.run(["gcc", "-c", "a.c"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["ar", "rcS", "libnoindex.a", "a.o"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    contents = read_archive(tmp_path / "libnoindex.a")
    assert [m.name for m in contents.members] == ["a.o"]
    assert not contents.has_symbol_index
