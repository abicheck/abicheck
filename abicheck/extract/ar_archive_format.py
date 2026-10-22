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

"""The ``ar`` container format: constants, readers, and header/name decoding.

The leaf half of :mod:`abicheck.extract.ar_archive` (the symbol-index
decoders and :func:`~abicheck.extract.ar_archive.parse_ar_archive` live
there). Moved out of ``buildsource/archive_graph.py`` (ADR-061: reading an
archive is an ``extract`` responsibility) so that the L5 archive-graph fold
and the ``compare --no-baseline`` static-archive export read (ADR-086) share
one parser instead of growing a second one.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

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
