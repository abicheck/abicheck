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
``libinternal_dispatch.a`` rather than only to a whole target
(:func:`abicheck.model.source_graph_query.defining_members`, read by
``localize_symbol``).

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

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from ..extract.ar_archive import read_archive
from ..extract.ar_archive_format import _MACHO_MAGICS, ArchiveFormatError
from ..model.graph_facts import CONF_HIGH, CONF_REDUCED, GraphEdge, GraphNode
from ..model.source_graph import _symbol_node_id

if TYPE_CHECKING:
    from ..model.source_graph import SourceGraphSummary


#: ``provenance`` tag on every node/edge this module creates, so an
#: archive-introspection fact is distinguishable from the ``build_evidence``
#: facts ``_fold_link_provenance`` deposits on the *same* ``static_library``
#: node in the ADR-046 D2 merge.
ARCHIVE_PROVENANCE = "archive_index"

EDGE_ARCHIVE_CONTAINS_OBJECT = "ARCHIVE_CONTAINS_OBJECT"
EDGE_OBJECT_DEFINES_SYMBOL = "OBJECT_DEFINES_SYMBOL"


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
    ``include_graph.py``, ``preprocessor_facts.py``) before touching disk.

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
