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

"""Archive/object link provenance — the ``ar`` introspection extractor behind
``ARCHIVE_CONTAINS_OBJECT``/``OBJECT_DEFINES_SYMBOL`` (G29 Phase 5 item 6,
G29.6's fifth open graph family).

``source_graph._fold_link_provenance`` (ADR-041 P1 #2) already creates
``static_library``/``object_file`` nodes from ``BuildEvidence.link_units``,
but it classifies a link input **by filename suffix alone** — its own
docstring says "best-effort textual classification, no archive
introspection" — and left ``archive_member`` plus the two edge kinds above
registered in the schema with nothing populating them. This module is that
missing introspection pass: it opens the archives the graph already names
and answers "which member, and which symbols does that member define",
so a removed-symbol finding can localize to ``cache_dispatch.o`` **in**
``libinternal_dispatch.a`` rather than only to a whole target.

Evidence source: the archive's **own symbol index** — the ``/`` (GNU),
``/SYM64/`` (GNU 64-bit), or ``__.SYMDEF`` (BSD/Mach-O) member that ``ar s``/
``ranlib`` writes and that the linker itself reads to decide which member to
pull in. Deliberately *not* a per-member ELF/COFF/Mach-O symbol-table walk
(see :func:`augment_graph_with_archives` for the full reasoning): the index
is format-agnostic — one parser covers ELF, COFF and Mach-O archives alike —
and it carries exactly the linker's own definition of "this member defines
this symbol", which is the semantics ``OBJECT_DEFINES_SYMBOL`` names.

Same discipline as every other graph producer here: **degrade to no answer,
never to a wrong one** (ADR-028 D3's authority rule — source/build evidence
never aborts collection). An archive that cannot be found on disk, cannot be
read, is not an archive, or carries no symbol index yields fewer edges and a
diagnostic, never an exception out of :func:`augment_graph_with_archives`.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from .graph_facts import CONF_HIGH, CONF_REDUCED, GraphEdge, GraphNode
from .source_graph import _symbol_node_id

if TYPE_CHECKING:
    from .source_graph import SourceGraphSummary

#: ``!<arch>\n`` — the standard Unix ``ar`` archive (GNU, BSD and Mach-O all
#: share it; they differ only in the long-name and symbol-index conventions
#: below). Mirrors ``binary_utils._ARCHIVE_MAGICS``, kept as its own constant
#: here because this module needs to tell the two flavors apart rather than
#: only recognize "some archive".
ARCHIVE_MAGIC = b"!<arch>\n"

#: ``!<thin>\n`` — a GNU *thin* archive (``ar rcT``). Member headers are
#: present and the symbol index still points at them, but a regular member's
#: *data* lives in the original object file on disk rather than inline, so a
#: thin member header is followed immediately by the next one (verified
#: against real ``ar rcsT`` output). The two special members (``/`` index,
#: ``//`` long-name table) do carry their data inline even in a thin archive.
THIN_ARCHIVE_MAGIC = b"!<thin>\n"

FLAVOR_GNU = "gnu"
FLAVOR_THIN = "thin"

_HEADER_SIZE = 60
_HEADER_MAGIC = b"`\n"

#: Special member names. ``/`` and ``/SYM64/`` are the GNU symbol indices,
#: ``//`` the GNU long-name string table, and the ``__.SYMDEF`` spellings the
#: BSD/Mach-O symbol index (``__.SYMDEF SORTED`` is what Apple's ``libtool``/
#: ``ranlib`` writes; ``__.SYMDEF_64`` is the 64-bit-offset variant).
_GNU_INDEX_NAMES = frozenset({"/", "/SYM64/"})
_GNU_LONG_NAME_TABLE = "//"
_BSD_INDEX_NAMES = frozenset({"__.SYMDEF", "__.SYMDEF SORTED", "__.SYMDEF_64"})

#: Cap on how much of one archive this module will read. Only member
#: *headers* and the two small special members are ever read (see
#: :class:`FileReader`), so this is a guard against a pathological
#: member count / corrupt size field looping, not against a large archive:
#: a 500 MiB static library with a thousand members costs ~60 KiB of reads.
_MAX_MEMBERS = 100_000

#: ``provenance`` tag on every node/edge this module creates, so an
#: archive-introspection fact is distinguishable from the ``build_evidence``
#: facts ``_fold_link_provenance`` deposits on the *same* ``static_library``
#: node in the ADR-046 D2 merge.
ARCHIVE_PROVENANCE = "archive_index"

EDGE_ARCHIVE_CONTAINS_OBJECT = "ARCHIVE_CONTAINS_OBJECT"
EDGE_OBJECT_DEFINES_SYMBOL = "OBJECT_DEFINES_SYMBOL"


class ArchiveFormatError(ValueError):
    """*data* is not a well-formed ``ar`` archive.

    Deliberately a module-local ``ValueError`` rather than an
    ``abicheck.errors`` entry: nothing outside this file may raise it, and
    :func:`augment_graph_with_archives` — the only caller a pipeline reaches
    — converts it to a diagnostic. An archive that fails to parse must never
    surface as a user-facing abicheck error, per this module's degrade rule.
    """


class ByteReader(Protocol):
    """Random-access byte source the pure parser reads through.

    A protocol rather than plain ``bytes`` so the parser never needs the
    whole archive resident: it reads 60-byte member headers and seeks past
    member data, which is the difference between ~60 KiB and 500 MiB on a
    large static library. :class:`BytesReader` is the in-memory
    implementation the tests use; :class:`FileReader` the on-disk one.
    """

    def size(self) -> int:
        """Total number of bytes available."""

    def read(self, offset: int, length: int) -> bytes:
        """Bytes ``[offset, offset + length)``, truncated at the end of the
        source (never padded, never an error) — the parser checks the
        returned length itself."""


@dataclass(frozen=True)
class BytesReader:
    """:class:`ByteReader` over an in-memory ``bytes``."""

    data: bytes

    def size(self) -> int:
        return len(self.data)

    def read(self, offset: int, length: int) -> bytes:
        if offset < 0 or length < 0:
            return b""
        return self.data[offset : offset + length]


class FileReader:
    """:class:`ByteReader` over an open file, seeking instead of slurping.

    Used as a context manager so the descriptor is closed even when the
    parser raises :class:`ArchiveFormatError` partway through.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._fh = path.open("rb")
        self._size = path.stat().st_size

    def __enter__(self) -> FileReader:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._fh.close()

    def size(self) -> int:
        return self._size

    def read(self, offset: int, length: int) -> bytes:
        if offset < 0 or length < 0 or offset >= self._size:
            return b""
        self._fh.seek(offset)
        return self._fh.read(length)


@dataclass(frozen=True)
class ArMember:
    """One archive member.

    ``name`` is already long-name-resolved (GNU ``//`` string table or BSD
    ``#1/<len>`` inline name). ``header_offset`` is what a symbol index
    entry points at, which is why it — not ``data_offset`` — is the join key
    between the two parsing passes.
    """

    name: str
    header_offset: int
    data_offset: int
    size: int


@dataclass(frozen=True)
class ArSymbolRef:
    """One ``symbol defined by member`` entry from the archive's own index.

    A list of these rather than a ``{symbol: member}`` mapping on purpose: an
    archive may legitimately index the same symbol against more than one
    member (two members defining it — the linker resolves to whichever it
    pulls in first), and collapsing that into a mapping would silently drop
    one member's real claim.
    """

    symbol: str
    member: str


@dataclass(frozen=True)
class ArchiveContents:
    """The result of introspecting one archive.

    ``has_symbol_index`` distinguishes the two "no symbols" cases that must
    never read alike: an archive built without an index (``ar rc``, no ``s``)
    carries **no evidence** about which member defines what, whereas an
    indexed archive with an empty index genuinely defines no global symbols.
    Only the second is a fact; the first is an absence.
    """

    flavor: str
    members: tuple[ArMember, ...] = ()
    symbols: tuple[ArSymbolRef, ...] = ()
    has_symbol_index: bool = False


# ── pure ar-format parsing ───────────────────────────────────────────────────


def _parse_header(raw: bytes, offset: int) -> tuple[str, int]:
    """``(raw 16-byte name field, member size)`` from a 60-byte header."""
    if len(raw) < _HEADER_SIZE:
        raise ArchiveFormatError(f"truncated member header at offset {offset}")
    if raw[58:60] != _HEADER_MAGIC:
        raise ArchiveFormatError(
            f"member header at offset {offset} lacks the '`\\n' terminator"
        )
    try:
        name_field = raw[:16].decode("ascii")
        size_text = raw[48:58].decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise ArchiveFormatError(
            f"member header at offset {offset} is not ASCII: {exc}"
        ) from exc
    if not size_text.isdigit():
        raise ArchiveFormatError(
            f"member header at offset {offset} has a non-numeric size {size_text!r}"
        )
    return name_field, int(size_text)


def _long_name_at(table: bytes, index: int) -> str | None:
    """Resolve GNU long-name reference ``/<index>`` against the ``//`` table.

    Entries are terminated by ``/\\n`` (GNU) or a bare ``\\n`` (some
    producers write only the newline); a final entry may be unterminated.
    Returns ``None`` for an out-of-range index rather than raising — a
    single unresolvable name degrades that one member, it does not make the
    archive unparseable.
    """
    if index < 0 or index >= len(table):
        return None
    end = table.find(b"\n", index)
    raw = table[index:] if end == -1 else table[index:end]
    try:
        name = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return name[:-1] if name.endswith("/") else name


def _resolve_member_name(
    name_field: str, reader: ByteReader, data_offset: int, size: int, table: bytes
) -> tuple[str | None, int]:
    """``(resolved name, bytes of member data consumed by an inline name)``.

    ``None`` marks a member whose name could not be resolved (a dangling
    GNU ``/<index>``); the member is still counted and skipped over, so one
    bad name never desynchronizes the walk.
    """
    stripped = name_field.rstrip(" ")
    if stripped.startswith("#1/"):
        # BSD/Mach-O inline long name: the first N bytes of the member's own
        # data are the name, NUL-padded. Those bytes are part of `size`.
        digits = stripped[3:]
        if not digits.isdigit():
            return None, 0
        n = min(int(digits), size)
        raw = reader.read(data_offset, n)
        return raw.split(b"\x00", 1)[0].decode("utf-8", "replace"), n
    if stripped.startswith("/") and stripped[1:].isdigit():
        return _long_name_at(table, int(stripped[1:])), 0
    # GNU terminates a short name with '/'; BSD pads with spaces only.
    return (stripped[:-1] if stripped.endswith("/") else stripped), 0


def _gnu_symbol_index(
    body: bytes, offsets_by_member: dict[int, str], *, wide: bool
) -> list[ArSymbolRef]:
    """Parse a GNU ``/`` (32-bit) or ``/SYM64/`` (64-bit) index member.

    Layout: a big-endian count, that many big-endian member-*header* offsets,
    then the same number of NUL-terminated symbol names, in order.
    """
    width = 8 if wide else 4
    fmt = ">Q" if wide else ">I"
    if len(body) < width:
        return []
    count = struct.unpack(fmt, body[:width])[0]
    end = width + width * count
    if count > _MAX_MEMBERS or len(body) < end:
        raise ArchiveFormatError(
            f"symbol index declares {count} symbols but carries {len(body)} bytes"
        )
    offsets = [
        struct.unpack(fmt, body[width + i * width : width + (i + 1) * width])[0]
        for i in range(count)
    ]
    names = body[end:].split(b"\x00")
    refs: list[ArSymbolRef] = []
    for i, offset in enumerate(offsets):
        if i >= len(names):
            break
        member = offsets_by_member.get(offset)
        if member is None:
            continue  # index points at no member we walked — skip, never guess
        symbol = names[i].decode("utf-8", "replace")
        if symbol:
            refs.append(ArSymbolRef(symbol=symbol, member=member))
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
    """
    width = 8 if wide else 4
    fmt = "<Q" if wide else "<I"
    if len(body) < width:
        return []
    table_bytes = struct.unpack(fmt, body[:width])[0]
    entry_size = width * 2
    start = width
    end = start + table_bytes
    if table_bytes % entry_size or len(body) < end + width:
        raise ArchiveFormatError(
            f"__.SYMDEF ranlib table length {table_bytes} is not usable"
        )
    strings_at = end + width
    strings = body[strings_at:]
    refs: list[ArSymbolRef] = []
    for pos in range(start, end, entry_size):
        str_off = struct.unpack(fmt, body[pos : pos + width])[0]
        mem_off = struct.unpack(fmt, body[pos + width : pos + entry_size])[0]
        member = offsets_by_member.get(mem_off)
        if member is None or str_off >= len(strings):
            continue
        terminator = strings.find(b"\x00", str_off)
        raw = strings[str_off:] if terminator == -1 else strings[str_off:terminator]
        symbol = raw.decode("utf-8", "replace")
        if symbol:
            refs.append(ArSymbolRef(symbol=symbol, member=member))
    return refs


def parse_ar_archive(reader: ByteReader) -> ArchiveContents:
    """Walk an ``ar`` archive's member headers and its symbol index.

    Two passes over the header chain, because a GNU symbol index is the
    *first* member but references members by header offset — offsets only
    resolvable once the whole chain (and the ``//`` long-name table, which
    follows the index) has been walked. The first pass therefore records
    ``offset -> name`` and defers every index member; the second decodes
    them.

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
        # A special member's data is always stored inline, thin archive or
        # not; only a *regular* member is bodiless in a thin archive.
        special = (
            stripped in _GNU_INDEX_NAMES
            or stripped == _GNU_LONG_NAME_TABLE
            or stripped in _BSD_INDEX_NAMES
        )
        if stripped == _GNU_LONG_NAME_TABLE:
            long_names = reader.read(data_offset, size)
        elif stripped in _GNU_INDEX_NAMES or stripped in _BSD_INDEX_NAMES:
            pending_index.append((stripped, reader.read(data_offset, size)))
        else:
            name, name_bytes = _resolve_member_name(
                name_field, reader, data_offset, size, long_names
            )
            if name:
                members.append(
                    ArMember(
                        name=name,
                        header_offset=header_offset,
                        data_offset=data_offset + name_bytes,
                        size=size - name_bytes,
                    )
                )
        stored = size if (special or not thin) else 0
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
                )
            )
            for m in members
        ]

    # Pass 2: decode the index members now that offsets resolve to names.
    offsets_by_member = {m.header_offset: m.name for m in members}
    symbols: list[ArSymbolRef] = []
    for index_name, body in pending_index:
        if index_name in _GNU_INDEX_NAMES:
            symbols.extend(
                _gnu_symbol_index(body, offsets_by_member, wide=index_name == "/SYM64/")
            )
        else:
            symbols.extend(
                _bsd_symbol_index(
                    body, offsets_by_member, wide=index_name == "__.SYMDEF_64"
                )
            )
    return ArchiveContents(
        flavor=flavor,
        members=tuple(members),
        symbols=tuple(symbols),
        has_symbol_index=bool(pending_index),
    )


def read_archive(path: Path) -> ArchiveContents:
    """:func:`parse_ar_archive` over a file on disk.

    Propagates ``OSError`` (unreadable, a directory, gone) and
    :class:`ArchiveFormatError` (present but not an archive) unchanged — the
    caller decides which becomes which diagnostic.
    """
    with FileReader(path) as reader:
        return parse_ar_archive(reader)


# ── graph augmentation ───────────────────────────────────────────────────────


def archive_member_node_id(archive_path: str, member: str) -> str:
    """Node id for one member of one archive.

    Scoped by the owning archive, not global: two static libraries routinely
    contain a member with the same short name (``util.o``), and collapsing
    them onto one node would claim a symbol is defined in an archive that
    never contained it.
    """
    return f"archive_member://{archive_path}::{member}"


def _symbol_node_ids(graph: SourceGraphSummary) -> frozenset[str]:
    return frozenset(n.id for n in graph.nodes if n.kind == "binary_symbol")


def _resolve_archive_path(label: str, search_roots: tuple[Path, ...]) -> Path | None:
    """The on-disk archive a ``static_library`` node's label names, or ``None``.

    Link-input paths in build evidence are as the build system wrote them —
    absolute from a CMake/Ninja link line, relative to the build directory
    from others — so an absolute path is tried as-is and a relative one is
    tried under each search root in order. Only an existing regular file is
    accepted; a symlink to one is fine (a build tree's ``lib*.a`` is often
    one), since this module only ever *reads*.
    """
    candidate = Path(label)
    if candidate.is_absolute():
        return candidate if candidate.is_file() else None
    for root in search_roots:
        resolved = root / candidate
        if resolved.is_file():
            return resolved
    return None


@dataclass
class ArchiveGraphResult:
    """What one :func:`augment_graph_with_archives` pass did, for the
    coverage-honesty stamp and the extractor row its caller records."""

    archives_seen: int = 0
    archives_read: int = 0
    members: int = 0
    symbol_edges: int = 0
    #: Index symbols that named no ``binary_symbol`` node in the graph, so no
    #: edge was emitted (see :func:`augment_graph_with_archives`). Counted
    #: rather than dropped silently: a pass whose every symbol was skipped
    #: looks identical to a pass that found none without it.
    unjoined_symbols: int = 0
    diagnostics: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        """Every ``static_library`` node the graph named was found, read, and
        carried a symbol index — the only shape that may claim a confirmed
        ``extractor_passes`` entry."""
        return (
            self.archives_seen > 0
            and self.archives_read == self.archives_seen
            and not self.diagnostics
        )


def augment_graph_with_archives(
    graph: SourceGraphSummary,
    *,
    search_roots: tuple[Path, ...] = (),
) -> ArchiveGraphResult:
    """Populate ``ARCHIVE_CONTAINS_OBJECT``/``OBJECT_DEFINES_SYMBOL`` for every
    ``static_library`` node in *graph* whose archive can be found and read.

    Runs over the nodes ``_fold_link_provenance`` already created rather than
    over ``BuildEvidence`` directly, so this pass introspects exactly the
    archives the graph claims are link inputs — it can never introduce an
    archive the build evidence didn't name.

    **An ``OBJECT_DEFINES_SYMBOL`` edge is only emitted for a symbol the graph
    already carries a ``binary_symbol://`` node for.** That is the same
    "one shared node id is the whole join mechanism" rule ADR-057 D1 sets for
    the consumer join, and it is what keeps the graph compact (ADR-031 D7): a
    real static library indexes thousands of internal symbols that the shared
    library never exports and no finding can ever be about, and minting a node
    for each would inflate every snapshot carrying a graph for no analytical
    gain. The localization use case is unaffected — a finding is about a
    symbol the diff saw, which is a symbol some side's export table named,
    which is a ``binary_symbol`` node.

    **Not a per-member ELF/COFF/Mach-O symbol-table walk.** Two reasons, in
    order: the archive index is format-agnostic (one parser serves all three
    object formats, where a symtab walk would need three), and it is the
    linker's *own* record of which member defines which symbol — a symtab
    walk would additionally surface file-local symbols the linker never
    considers for member selection, which is not what this edge kind means.
    The cost is that an archive built without an index (``ar rc``, no ``s``,
    or a stripped ``ranlib``-less one) contributes members but no symbol
    edges; that is recorded as a diagnostic, never inferred around.

    Never raises: every per-archive failure (missing, unreadable, not an
    archive, malformed) becomes a diagnostic on the returned
    :class:`ArchiveGraphResult`, per ADR-028 D3.
    """
    result = ArchiveGraphResult()
    known_symbols = _symbol_node_ids(graph)

    archives = [n for n in graph.nodes if n.kind == "static_library"]
    for node in archives:
        result.archives_seen += 1
        label = node.label or node.id.split("://", 1)[-1]
        path = _resolve_archive_path(label, search_roots)
        if path is None:
            result.diagnostics.append(f"{label}: not found under any search root")
            continue
        try:
            contents = read_archive(path)
        except ArchiveFormatError as exc:
            result.diagnostics.append(f"{label}: {exc}")
            continue
        except OSError as exc:
            result.diagnostics.append(f"{label}: unreadable: {exc}")
            continue
        result.archives_read += 1
        if not contents.has_symbol_index:
            result.diagnostics.append(
                f"{label}: no symbol index (built without 'ar s'/ranlib); "
                "members recorded, no symbol edges"
            )
        member_ids: dict[str, str] = {}
        for member in contents.members:
            mid = archive_member_node_id(label, member.name)
            member_ids[member.name] = mid
            graph.add_node(
                GraphNode(
                    id=mid,
                    kind="archive_member",
                    label=member.name,
                    provenance=ARCHIVE_PROVENANCE,
                    confidence=CONF_HIGH,
                    attrs={"archive": label, "size": member.size},
                )
            )
            graph.add_edge(
                GraphEdge(
                    src=node.id,
                    dst=mid,
                    kind=EDGE_ARCHIVE_CONTAINS_OBJECT,
                    provenance=ARCHIVE_PROVENANCE,
                    confidence=CONF_HIGH,
                )
            )
            result.members += 1
        for ref in contents.symbols:
            member_id = member_ids.get(ref.member)
            if member_id is None:
                continue
            sid = _symbol_node_id(ref.symbol)
            if sid not in known_symbols:
                result.unjoined_symbols += 1
                continue
            graph.add_edge(
                GraphEdge(
                    src=member_id,
                    dst=sid,
                    kind=EDGE_OBJECT_DEFINES_SYMBOL,
                    provenance=ARCHIVE_PROVENANCE,
                    # The index proves the member defines the symbol; that the
                    # symbol node it joins onto is the *same* entity is a
                    # name match, not a proof — the shared library's export
                    # could have come from elsewhere. CONF_REDUCED keeps this
                    # from outranking a real producer's fact on that node in
                    # the ADR-046 D2 merge.
                    confidence=CONF_REDUCED,
                )
            )
            result.symbol_edges += 1
    return result


def defining_members(graph: SourceGraphSummary, symbol: str) -> list[tuple[str, str]]:
    """``(archive label, member name)`` for every archive member *graph* records
    as defining *symbol* — the localization read view
    ("``cache_dispatch.o`` in ``libinternal_dispatch.a``").

    Returns every match rather than one: an archive set may genuinely define
    a symbol in more than one member, and picking one would be a guess.
    Empty when the pass never ran, the archive wasn't readable, or the
    symbol isn't index-backed — an absence of evidence, which a caller must
    not render as "defined nowhere".
    """
    target = _symbol_node_id(symbol)
    by_id = {n.id: n for n in graph.nodes}
    out: list[tuple[str, str]] = []
    for edge in graph.edges:
        if edge.kind != EDGE_OBJECT_DEFINES_SYMBOL or edge.dst != target:
            continue
        member = by_id.get(edge.src)
        if member is None:
            continue
        archive = str((member.resolved or member.attrs).get("archive", ""))
        out.append((archive, member.label or ""))
    return sorted(set(out))
