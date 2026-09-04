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

import os
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from ..model.source_graph import _symbol_node_id
from .graph_facts import CONF_HIGH, CONF_REDUCED, GraphEdge, GraphNode

if TYPE_CHECKING:
    from ..model.source_graph import SourceGraphSummary

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
#: ``//`` the GNU long-name string table.
_GNU_INDEX_NAMES = frozenset({"/", "/SYM64/"})
_GNU_LONG_NAME_TABLE = "//"

#: Mach-O magic numbers (both byte orders + fat/universal binaries) — an
#: independent copy of ``macho_metadata._MACHO_MAGICS`` (same reasoning as
#: this module's other independent-copy constants: this module and
#: ``macho_metadata.py`` are unrelated packages with no import dependency
#: between them, and duplicating one small frozenset is cheaper than a
#: cross-package coupling for it).
_MACHO_MAGICS = frozenset(
    {
        b"\xfe\xed\xfa\xce",  # MH_MAGIC (32-bit)
        b"\xce\xfa\xed\xfe",  # MH_CIGAM (32-bit, swapped)
        b"\xfe\xed\xfa\xcf",  # MH_MAGIC_64 (64-bit)
        b"\xcf\xfa\xed\xfe",  # MH_CIGAM_64 (64-bit, swapped)
        b"\xca\xfe\xba\xbe",  # FAT_MAGIC (universal binary)
        b"\xbe\xba\xfe\xca",  # FAT_CIGAM (universal, swapped)
        b"\xca\xfe\xba\xbf",  # FAT_MAGIC_64 (fat64 universal binary)
        b"\xbf\xba\xfe\xca",  # FAT_CIGAM_64 (fat64, swapped)
    }
)

#: The BSD/Mach-O symbol index is matched by the fixed magic prefix plus a
#: bounded set of known suffixes (real macOS CI evidence, Codex/CI review):
#: Apple's ``libtool``/``ranlib`` writes ``__.SYMDEF`` (unsorted) or
#: ``__.SYMDEF SORTED`` (sorted) for a 32-bit index and the ``_64``
#: spellings for 64-bit, and the 16-byte ``ar`` name field silently
#: truncates a longer one (``__.SYMDEF_64 SORTED`` is 19 bytes and
#: truncates to ``__.SYMDEF_64 SOR``) — an exact-string allowlist an
#: earlier revision used missed that case, and a real macOS ``ar`` run in
#: CI additionally produced a plain ``__.SYMDEF SORTED`` this parser
#: failed to recognize as special at all (folding it into ``members``
#: instead of the symbol index — confirmed by the macOS integration lane,
#: root cause not otherwise reproducible without a real BSD ``ar``).
#: **Not** matched by unbounded prefix (Codex review, fresh evidence): an
#: earlier revision's bare ``stripped.startswith(_BSD_INDEX_PREFIX)``
#: also matched an ordinary, unrelated member merely *named* with this
#: prefix (``__.SYMDEFECT.o`` is a legal ar member name, and
#: ``"__.SYMDEFECT.o".startswith("__.SYMDEF")`` is true), misrouting a
#: real object file's bytes into the BSD symbol-index decoder and raising
#: ``ArchiveFormatError`` for what is otherwise a perfectly valid archive.
#: Every real spelling above is either exactly one of the two bare magic
#: names or that name immediately followed by a space (the sorted variant,
#: truncated or not) — never any other trailing byte — so requiring an
#: exact match or a match through that literal space closes the false
#: positive without reintroducing the truncated-name misses the original
#: exact-string allowlist had.
_BSD_INDEX_PREFIX = "__.SYMDEF"
_BSD_INDEX_WIDE_PREFIX = "__.SYMDEF_64"


def _is_bsd_index_name(stripped: str) -> bool:
    """Whether *stripped* (an ``ar`` header name field, right-padding
    already removed) is a BSD/Mach-O symbol-index member — see
    :data:`_BSD_INDEX_PREFIX`. Matches only the bare magic name or that
    name followed by a literal space (the " SORTED" variant, truncated or
    not) — not an unbounded prefix, so an ordinary member merely named
    with this prefix (``__.SYMDEFECT.o``) is never misidentified."""
    if stripped in (_BSD_INDEX_PREFIX, _BSD_INDEX_WIDE_PREFIX):
        return True
    return stripped.startswith(_BSD_INDEX_PREFIX + " ") or stripped.startswith(
        _BSD_INDEX_WIDE_PREFIX + " "
    )


def _is_bsd_index_wide(stripped: str) -> bool:
    """Whether a BSD index member (see :func:`_is_bsd_index_name`) is the
    64-bit-offset ``__.SYMDEF_64`` variant rather than the 32-bit one."""
    return stripped.startswith(_BSD_INDEX_WIDE_PREFIX)


#: Cap on how much of one archive this module will read. Only member
#: *headers* and the special members (bounded by :data:`_MAX_SPECIAL_MEMBER_BYTES`
#: below, not assumed small — Codex review, fresh evidence) are ever read
#: (see :class:`FileReader`), so this is a guard against a pathological
#: member count looping, not against a large archive: a 500 MiB static
#: library with a thousand members costs ~60 KiB of header reads.
_MAX_MEMBERS = 100_000

#: A separate, much more generous sanity cap for the *symbol* count declared
#: in a GNU ``/``/``/SYM64/`` index body — deliberately not
#: :data:`_MAX_MEMBERS` (Codex review, fresh evidence): a real C++ static
#: library routinely indexes far more global symbols (templates, inline
#: functions, every exported specialization) than it has object-file
#: members, so reusing the member cap rejected — and discarded all
#: provenance for — a large but entirely legitimate archive's symbol index.
#: The actual corruption guard is ``len(body) < end`` just below (an ``end``
#: computed from a corrupt/COFF-misread count is `O(1)` to compute and
#: reliably exceeds any real body before the expensive per-entry loop
#: runs), so this cap only needs to stop a pathologically large *declared*
#: count from building an oversized Python list when the body genuinely is
#: that large. Reading ``body`` itself into memory in the first place is
#: bounded separately, by :data:`_MAX_SPECIAL_MEMBER_BYTES` below, checked
#: *before* the read (not "already bounded by real file size" — a sparse
#: file's reported size can match an inflated header claim while consuming
#: almost no real disk, so that assumption alone doesn't hold; Codex
#: review, fresh evidence).
_MAX_INDEXED_SYMBOLS = 50_000_000

#: Hard ceiling on the declared ``size`` of a special member (the ``//``
#: long-name table, or a GNU/BSD symbol index) this module reads in full via
#: a single ``reader.read(data_offset, size)`` call, checked *before* that
#: read (Codex review, fresh evidence): the "bounded by real file size, not
#: an amplification attack" assumption the count cap above relies on does
#: not hold for a *sparse* file, whose ``os.fstat``-reported logical size
#: (what :class:`FileReader` uses as its own ``size()``) can legitimately
#: match an inflated header claim while consuming almost no real disk —
#: `truncate --size=10G evil.a` costs near-zero disk space but reports a
#: real 10 GiB length, so a "declares a size that fits within the file"
#: check alone doesn't catch it. Comfortably above what a real archive ever
#: needs (even :data:`_MAX_INDEXED_SYMBOLS` worth of GNU index entries plus
#: generous per-symbol name bytes fits well under this), but finite: a
#: declared size this large can only be a corrupt or hostile header, so it's
#: rejected before the read that would otherwise attempt to materialize
#: that many bytes as a single Python `bytes` object.
_MAX_SPECIAL_MEMBER_BYTES = 1024 * 1024 * 1024

#: Hard ceiling on a BSD ``#1/<len>`` inline extended-name length -- the
#: same read-before-validating shape as the special-member cap above, but
#: reachable on *any* member (not just an index), since every member's own
#: name can use this encoding (Codex review, fresh evidence). A real
#: filesystem path is at most a few thousand bytes; this is far more
#: generous than that while still bounding the read.
_MAX_BSD_NAME_BYTES = 65536

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
        # Read the size off the already-open descriptor (os.fstat), not a
        # fresh path.stat() -- the latter re-resolves the path a second
        # time, so a replaced file between the open() above and the stat()
        # would record the wrong size, and if stat() itself raised, the
        # already-open handle would leak (this constructor raising means
        # __enter__/__exit__ never run to close it) (Codex review).
        self._size = os.fstat(self._fh.fileno()).st_size

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

    ``object_magic`` is *this specific member's own* leading 4 bytes — real,
    per-member object-format evidence, not the whole archive's (Codex
    review, fresh evidence): a mixed-format archive (an ELF object and a
    Mach-O object archived together, e.g. via a cross-toolchain build) has
    members whose own object format genuinely differs member-to-member,
    and `augment_graph_with_archives`'s Mach-O leading-underscore join
    fallback previously gated on `ArchiveContents.object_magic` — the
    *first* regular member's magic alone — applied uniformly to every
    symbol in the archive regardless of which member actually defines it.
    That misclassifies a later member of the *other* format: if the first
    member is Mach-O but a later one is ELF, the later member's own
    already-correctly-spelled `_foo` symbol gets the stripped-underscore
    treatment applied anyway and can false-join onto an unrelated exported
    `foo`; the reverse ordering just as wrongly skips a real Mach-O join.
    Same limitation as the archive-wide field for a *thin* archive's
    bodiless regular members (empty, not fabricated — see
    :func:`parse_ar_archive`'s own docstring)."""

    name: str
    header_offset: int
    data_offset: int
    size: int
    object_magic: bytes = b""


@dataclass(frozen=True)
class ArSymbolRef:
    """One ``symbol defined by member`` entry from the archive's own index.

    A list of these rather than a ``{symbol: member}`` mapping on purpose: an
    archive may legitimately index the same symbol against more than one
    member (two members defining it — the linker resolves to whichever it
    pulls in first), and collapsing that into a mapping would silently drop
    one member's real claim.

    ``member_offset`` is the member's own ``header_offset`` (``-1`` when
    unknown, e.g. a hand-built ``ArSymbolRef`` in a test that only cares
    about ``symbol``/``member``) — the join key ``augment_graph_with_archives``
    actually uses to attribute a symbol to a graph node. ``ar`` permits two
    members sharing one name in the same archive (e.g. ``ar rc lib.a
    sub1/util.o sub2/util.o``), and a name-keyed join collapses them onto one
    node, silently misattributing every symbol either member defines to
    whichever one a name-keyed dict happened to keep last (Codex review,
    fresh evidence). ``member`` itself stays the resolved display name — it's
    what a human-readable diagnostic/label wants — while ``member_offset`` is
    what disambiguates identity.
    """

    symbol: str
    member: str
    member_offset: int = -1


@dataclass(frozen=True)
class ArchiveContents:
    """The result of introspecting one archive.

    ``has_symbol_index`` distinguishes the two "no symbols" cases that must
    never read alike: an archive built without an index (``ar rc``, no ``s``)
    carries **no evidence** about which member defines what, whereas an
    indexed archive with an empty index genuinely defines no global symbols.
    Only the second is a fact; the first is an absence.

    ``index_kind`` (``"gnu"``/``"bsd"``/``""``) records which symbol-index
    *format* was actually decoded — independent of ``flavor``, which is
    about the archive *container*'s own magic (GNU vs. thin), not which
    ranlib wrote the index inside it. A GNU/``/``/``/SYM64/`` index never
    carries the Mach-O leading-underscore convention (Codex review, fresh
    evidence: a real GNU/ELF archive's own symbol table can legitimately
    contain a name that already starts with ``_``, e.g. ``_foo``, so
    blindly trying the underscore-stripped spelling risked a false join
    onto an unrelated binary export sharing the stripped name). ``""`` when
    the archive carries no index at all.

    ``index_kind == "bsd"`` alone is **not** proof of Mach-O either, though
    (Codex review, second round, fresh evidence): a real ``llvm-ar
    --format=bsd`` produces a valid ``__.SYMDEF``-indexed archive around
    ordinary ELF objects too (empirically confirmed: `llvm-ar --format=bsd
    rcs lib.a a.o` over a real ELF `a.o`). ``object_magic`` is the first
    regular member's own leading 4 bytes — real object-format evidence.
    ``b""`` when the archive has no regular members (index-only) or a
    truncated first member.

    The converse also holds, and the join fallback (``augment_graph_with_
    archives``) relies on ``object_magic`` **alone**, not a conjunction with
    ``index_kind == "bsd"`` (Codex review, third round, fresh evidence): a
    real Mach-O object cross-compiled with clang and archived via
    ``llvm-ar --format=gnu`` produces a genuinely GNU-encoded index
    (``index_kind == "gnu"``) around real Mach-O member content
    (empirically confirmed the same way). The archive container's chosen
    index format and the member's own object format are independent
    choices one tool run cannot infer from the other, so ``object_magic``
    — direct evidence of the member's own bytes — is the correct,
    sufficient gate on its own; requiring ``index_kind == "bsd"`` in
    addition only narrowed which real Mach-O archives were recognized.
    """

    flavor: str
    members: tuple[ArMember, ...] = ()
    symbols: tuple[ArSymbolRef, ...] = ()
    has_symbol_index: bool = False
    index_kind: str = ""
    object_magic: bytes = b""


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

    *index* must name the *start* of an entry -- offset 0, or the byte
    immediately after a preceding entry's own ``\\n`` terminator -- not an
    arbitrary mid-entry byte position (Codex review, fresh evidence,
    confirmed empirically): a real archive's own ``/<index>`` references
    always name a genuine entry start, but a crafted one pointing into the
    *middle* of another entry (e.g. offset 1 into ``b"xfoo/\\n"``) previously
    resolved to a fabricated ``"foo"`` -- a real-looking, but entirely
    smuggled, member name silently accepted rather than flagged as
    malformed. The identical boundary check `_bsd_symbol_index` already
    applies to a BSD ``str_off`` reference, applied here to its GNU
    counterpart."""
    if index < 0 or index >= len(table):
        return None
    if index != 0 and table[index - 1] != 0x0A:
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
    GNU ``/<index>``, or a BSD ``#1/<len>`` declaring a name longer than the
    member's own data — Codex review, fresh evidence: a real BSD header
    never encodes that, since the name is a strict prefix of the member's
    own ``size``, so a declared length exceeding it is malformed rather
    than a legitimately oversized name); the member is still counted and
    skipped over, so one bad name never desynchronizes the walk.
    """
    stripped = name_field.rstrip(" ")
    if stripped.startswith("#1/"):
        # BSD/Mach-O inline long name: the first N bytes of the member's own
        # data are the name, NUL-padded. Those bytes are part of `size`.
        digits = stripped[3:]
        if not digits.isdigit():
            return None, 0
        n = int(digits)
        if n > size or n > _MAX_BSD_NAME_BYTES:
            # A declared name length that overruns the member's own data
            # would previously silently clamp to `size` (`min(n, size)`),
            # fabricating a truncated name out of what is actually the
            # member's real content instead of reporting the malformed
            # header. Treat it the same as any other unresolvable name.
            # The separate _MAX_BSD_NAME_BYTES bound (Codex review, fresh
            # evidence) closes a real resource-exhaustion path this `n >
            # size` check alone doesn't: `size` itself is attacker/
            # corruption-controlled too, so a member declaring both a huge
            # `size` and a matching huge `#1/<len>` name would otherwise
            # pass this check and reach the read below with an unbounded
            # length.
            return None, 0
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


# ── graph augmentation ───────────────────────────────────────────────────────


def archive_member_node_id(
    archive_path: str, member: str, discriminator: int = 0
) -> str:
    """Node id for one member of one archive.

    Scoped by the owning archive, not global: two static libraries routinely
    contain a member with the same short name (``util.o``), and collapsing
    them onto one node would claim a symbol is defined in an archive that
    never contained it.

    *discriminator*, when non-zero, additionally disambiguates two members
    of the *same* archive sharing one name — ``ar`` permits this (e.g.
    ``ar rc lib.a sub1/util.o sub2/util.o``), and a name-only id would
    collapse them onto one node, misattributing whichever member's symbols
    got recorded second (Codex review, fresh evidence). It must be the
    member's zero-based *occurrence index* among same-named members in file
    order, never a raw byte offset: an earlier revision used
    :attr:`ArMember.header_offset` directly, but that shifts for *every*
    later member whenever any earlier member's own size changes, even when
    the later member's own content is byte-identical — false-positively
    reporting an unrelated member (and its edges) as removed-and-re-added
    on a version-over-version structural graph diff (Codex review, second
    round).

    *member* is length-prefixed in the returned id (Codex review, third
    round, fresh evidence), not simply concatenated with ``#discriminator``
    or embedded plain: ``#`` is a legal ``ar`` member-name character (no
    format-level restriction on it), so a uniquely-named member literally
    called ``"foo#1"`` (discriminator 0, the plain-name branch an earlier
    revision kept) and an ordinary member named ``"foo"`` at occurrence
    index 1 both produced the identical id ``"archive_member://<archive>::
    foo#1"`` — two genuinely distinct members merged onto one graph node,
    misattributing whichever member's symbols got recorded second, the
    same failure mode this discriminator exists to prevent. Length-
    prefixing *member* the same way Itanium mangling length-prefixes a
    source-name (this module already relies on that technique's safety
    elsewhere) makes the encoding unambiguous regardless of what
    characters *member* itself contains: two distinct ``(member,
    discriminator)`` pairs can never produce the identical string, since a
    matching id requires the length digits to match first (fixing where
    the member text ends), then that exact substring to match (fixing
    *member*), then the trailing ``#<discriminator>`` to match. Applied
    uniformly regardless of whether *discriminator* is zero, rather than
    keeping a separate unprefixed "common case" format that the same
    ambiguity could reach from the other direction (an unusual literal
    member name that happens to *look like* a length-prefixed entry).

    *archive_path* is length-prefixed too (Codex review, fourth round,
    fresh evidence), for the identical reason one level up: leaving it
    plain still let a crafted ``(archive_path, member)`` pair collide with
    a distinct pair once *member* itself contained a substring that looks
    like this format's own ``archive::LEN:member`` encoding — confirmed
    empirically: ``archive_member_node_id("x", "a::1:b")`` and
    ``archive_member_node_id("x::6:a", "b")`` both returned the identical
    ``"archive_member://x::6:a::1:b#0"``, merging two members of two
    distinct archives onto one graph node. Length-prefixing *archive_path*
    the same way closes it: an id match now requires *archive_path*'s own
    length digits to match first (fixing where it ends), independently of
    where *member*'s own length-prefix starts.
    """
    return (
        f"archive_member://{len(archive_path)}:{archive_path}::"
        f"{len(member)}:{member}#{discriminator}"
    )


def _symbol_node_ids(graph: SourceGraphSummary) -> frozenset[str]:
    return frozenset(n.id for n in graph.nodes if n.kind == "binary_symbol")


@dataclass(frozen=True)
class _ArchiveLookup:
    """The result of resolving a ``static_library`` node's label to disk.

    ``path`` is ``None`` for both "not found anywhere" and "found in more
    than one distinct location" (:attr:`ambiguous`) — the two cases share
    the same downstream handling (no data attached to the node) but earn
    different diagnostics, so they stay distinguishable here rather than
    collapsing to a bare ``Path | None`` the way an earlier revision did.
    """

    path: Path | None
    ambiguous: bool = False
    candidates: tuple[Path, ...] = ()


def _file_identity(path: Path) -> tuple[int, int] | None:
    """``(st_dev, st_ino)`` for *path*, or ``None`` on a stat failure (a TOCTOU
    race — the file existed for ``is_file()`` above but is gone now). Two
    paths sharing an identity are the same file (a hardlink, or two search
    roots that overlap), never a genuine collision."""
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_dev, st.st_ino)


def _resolve_archive_path(label: str, search_roots: tuple[Path, ...]) -> _ArchiveLookup:
    """The on-disk archive a ``static_library`` node's label names.

    Link-input paths in build evidence are as the build system wrote them —
    absolute from a CMake/Ninja link line, relative to the build directory
    from others — so an absolute path is tried as-is and a relative one is
    tried under each search root in order. Only an existing regular file is
    accepted; a symlink to one is fine (a build tree's ``lib*.a`` is often
    one), since this module only ever *reads*.

    Both *label* and each *search_roots* entry are passed through
    :func:`~abicheck.buildsource.source_extractors._argv.unredact_home`
    first (Codex review, fresh evidence): build evidence is redacted before
    persisting (ADR-032 D7) — every adapter's ``CompileUnit.directory`` and
    a link unit's own input paths pass through
    :class:`~abicheck.buildsource.redaction.RedactionPolicy`, which rewrites
    the caller's home-directory prefix to a literal ``~`` — and
    ``Path("~/build/libfoo.a").is_absolute()`` is ``False`` (``~`` is not
    ``/``), so an absolute redacted label would silently fall through to the
    relative branch and never resolve against any root, and a redacted
    default search root (``_default_archive_search_roots`` reads the same
    redacted ``cu.directory``) would never contain a literal ``~``
    subdirectory either way. The same reversal every other replay path in
    this package already applies (``source_replay.py``,
    ``include_graph.py``, ``preprocessor_scan.py``) before touching disk.

    **Ambiguity is a "no answer", not a coin flip** (Codex review, fresh
    evidence): a *relative* archive label is resolved against every search
    root in *search_roots* — normally one compile unit's directory per
    distinct build target/subdirectory (:func:`~abicheck.buildsource.
    inline_graph_fold._default_archive_search_roots`) — and
    ``source_graph._fold_link_provenance`` coalesces every link unit's
    same-labeled input onto **one** ``static_library://<label>`` graph node
    regardless of which directory it actually came from. In a multi-target
    build where two different subdirectories each produce their own,
    unrelated ``libcommon.a``, every root before this fix was tried in
    registration order and the *first* match silently won — attaching one
    target's real member/symbol data to a node that (via the coalesced id)
    also represents the other target's completely different archive, which
    is exactly the "incorrect localization... rather than degraded
    evidence" failure ADR-028 D3's authority rule exists to rule out. When
    more than one *distinct* file (different ``(st_dev, st_ino)`` — a
    hardlink or an overlapping root naming the same file is not a
    collision) matches across the roots, resolution now refuses to guess:
    the node gets no archive data at all, with a diagnostic naming every
    candidate, rather than a wrong-but-confident answer. Closing this for
    real needs the archive/object node identity itself to be scoped by the
    owning link unit's directory, not only by label — a change to
    ``source_graph.py``'s existing (pre-dating this module) coalescing
    behavior that reaches every consumer of a ``static_library`` node, not
    a fix local to this lookup; deferred as its own scoped follow-up, the
    same way this file already defers the deeper COFF/thin-archive gaps it
    documents elsewhere.
    """
    from .source_extractors._argv import unredact_home

    candidate = Path(unredact_home(label))
    if candidate.is_absolute():
        return _ArchiveLookup(candidate if candidate.is_file() else None)
    found: list[Path] = []
    seen: set[tuple[int, int]] = set()
    for root in search_roots:
        resolved = Path(unredact_home(str(root))) / candidate
        if not resolved.is_file():
            continue
        identity = _file_identity(resolved)
        if identity is not None and identity in seen:
            continue  # same file via another root — not a second candidate
        found.append(resolved)
        if identity is not None:
            seen.add(identity)
    if not found:
        return _ArchiveLookup(None)
    if len(found) > 1:
        return _ArchiveLookup(None, ambiguous=True, candidates=tuple(found))
    return _ArchiveLookup(found[0])


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
        lookup = _resolve_archive_path(label, search_roots)
        if lookup.path is None:
            if lookup.ambiguous:
                # Re-redact each resolved candidate before it goes anywhere
                # persisted (Codex review, fresh evidence): `_resolve_archive_path`
                # deliberately un-redacts a "~"-placeholder path to find the
                # real file on disk, but that real, absolute path must never
                # reach `ExtractorRecord.detail` — this diagnostic is
                # embedded straight into build evidence / a persisted
                # snapshot's `build_source` payload, so an un-redacted
                # candidate here would reintroduce the exact home-directory
                # leak ADR-032 D7's redaction exists to prevent. Mirrors
                # `inline.py`'s identical "resolve for the operation, redact
                # for the record" pattern for a compile-DB path.
                from .redaction import DEFAULT_REDACTION

                names = ", ".join(
                    DEFAULT_REDACTION.path(str(c)) for c in lookup.candidates
                )
                result.diagnostics.append(
                    f"{label}: ambiguous — {len(lookup.candidates)} distinct files "
                    f"match across the search roots ({names}); skipped rather than "
                    "guessing which link unit's archive this node's coalesced "
                    "label refers to"
                )
            else:
                result.diagnostics.append(f"{label}: not found under any search root")
            continue
        path = lookup.path
        try:
            contents = read_archive(path)
        except ArchiveFormatError as exc:
            result.diagnostics.append(f"{label}: {exc}")
            continue
        except OSError as exc:
            # OSError.__str__ typically embeds the real, resolved (i.e.
            # un-redacted) filename ("[Errno 13] Permission denied:
            # '/home/user/...'") — re-redact it before persisting, the same
            # leak class as the ambiguous-archive diagnostic above, just
            # triggered by a permissions/TOCTOU failure instead of a
            # multi-root collision (Codex review, fresh evidence).
            from .redaction import DEFAULT_REDACTION

            result.diagnostics.append(
                f"{label}: unreadable: {DEFAULT_REDACTION.path(str(exc))}"
            )
            continue
        except MemoryError:
            # This function's own docstring promises "never raises" (ADR-028
            # D3), but _MAX_SPECIAL_MEMBER_BYTES only bounds a single
            # special-member read at 1 GiB -- still large enough to raise
            # MemoryError on a memory-constrained host (CodeRabbit review).
            # Uncaught, that would abort the whole L5 graph fold over one
            # archive. Degrade the same way any other unreadable archive
            # does rather than let it propagate.
            result.diagnostics.append(f"{label}: unreadable: out of memory")
            continue
        result.archives_read += 1
        if not contents.has_symbol_index:
            result.diagnostics.append(
                f"{label}: no symbol index (built without 'ar s'/ranlib); "
                "members recorded, no symbol edges"
            )
        # The *lookup* is keyed by header_offset, not name -- `ar` permits
        # two members sharing one name in the same archive (e.g. `ar rc
        # lib.a sub1/util.o sub2/util.o`), and a name-keyed dict would
        # collapse them onto one node, misattributing every symbol either
        # member defines to whichever one was recorded last (Codex review,
        # fresh evidence). The *node id itself* discriminates by occurrence
        # index among same-named members instead of the offset directly --
        # a raw offset shifts for every later member whenever an earlier
        # one's size changes, which would false-positively report an
        # unrelated, byte-identical member as removed-and-re-added on a
        # structural graph diff (Codex review, second round).
        member_ids: dict[int, str] = {}
        # Per-member object-format evidence, keyed the same way as
        # member_ids -- a mixed-format archive (Codex review, fresh
        # evidence: an ELF object and a Mach-O object archived together)
        # has members whose own format genuinely differs, so the Mach-O
        # underscore-stripping join fallback below must gate on *this*
        # symbol's own defining member, not the archive-wide first-member
        # magic (see ArMember.object_magic's own docstring).
        member_magic: dict[int, bytes] = {}
        name_occurrences: dict[str, int] = {}
        for member in contents.members:
            occurrence = name_occurrences.get(member.name, 0)
            name_occurrences[member.name] = occurrence + 1
            mid = archive_member_node_id(label, member.name, occurrence)
            member_ids[member.header_offset] = mid
            member_magic[member.header_offset] = member.object_magic
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
            member_id = member_ids.get(ref.member_offset)
            if member_id is None:
                continue
            sid = _symbol_node_id(ref.symbol)
            if (
                sid not in known_symbols
                and ref.symbol.startswith("_")
                and member_magic.get(ref.member_offset, b"") in _MACHO_MAGICS
            ):
                # A Mach-O/BSD ranlib index keeps the platform's C-symbol
                # leading underscore (`_foo`), but `macho_metadata.py`'s own
                # export-trie walk strips exactly one before a `binary_symbol`
                # node is minted from it (Codex review, fresh evidence: the
                # exact-name join above therefore missed every Mach-O archive
                # symbol, counting each as unjoined and emitting no
                # OBJECT_DEFINES_SYMBOL edges at all on that platform). Exact
                # match is still tried first. Gated on the archive's own
                # decoded index *format* (BSD/Mach-O ``__.SYMDEF``, not the
                # container's ``flavor``) rather than applied unconditionally
                # (Codex review, second round, fresh evidence): a real GNU/ELF
                # archive's own symbol table can legitimately contain a name
                # that already starts with `_` (reproduced with a real `cc`+
                # `ar` build), and trying the stripped spelling there risked a
                # false join onto an unrelated binary export merely sharing
                # the stripped name. ``index_kind == "bsd"`` alone still isn't
                # sufficient (Codex review, third round, fresh evidence): a
                # real `llvm-ar --format=bsd` writes a valid `__.SYMDEF` index
                # around ordinary ELF objects too (empirically reproduced),
                # so the first regular member's own magic bytes must also
                # confirm genuine Mach-O object content before trusting the
                # underscore convention. Gated on ``object_magic`` **alone**,
                # not also ``index_kind == "bsd"`` (Codex review, fourth
                # round, fresh evidence): a real Mach-O object cross-compiled
                # with clang and archived via `llvm-ar --format=gnu` produces
                # a genuinely GNU-encoded index (`index_kind="gnu"`) around
                # real Mach-O member content (confirmed empirically —
                # `object_magic` decodes to a real ``MH_MAGIC_64`` byte
                # sequence while the index member itself is the GNU
                # big-endian layout) — the object format and the archive's
                # own index encoding are independent choices, and requiring
                # both silently dropped every Mach-O archive built with a
                # non-BSD `ar`. ``object_magic`` is already the strictly
                # stronger signal (direct evidence of the member's own
                # bytes, not an inference from how the *index* happened to
                # be written), so it is sufficient on its own.
                #
                # Gated on *this ref's own defining member's* magic
                # (`member_magic`), not the archive-wide first-member magic
                # (Codex review, fifth round, fresh evidence): a mixed-
                # format archive genuinely exists (an ELF object and a
                # Mach-O object archived together, e.g. a cross-toolchain
                # build combining both) — applying one member's format to
                # every symbol in the archive either strips a real ELF
                # symbol's already-correct spelling (risking a false join
                # onto an unrelated export merely sharing the stripped
                # name) or skips a real Mach-O join, depending on which
                # member happened to come first.
                stripped_sid = _symbol_node_id(ref.symbol[1:])
                if stripped_sid in known_symbols:
                    sid = stripped_sid
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
