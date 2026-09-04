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

"""Content-level primitives behind :mod:`abicheck.workflows.
bundle_compare_operand`'s ``artifact_type`` marker scan -- split out from
that module (ADR-061's 800-line production file cap) rather than inlined
there. See that module's own docstring for the full account of *why* each
of these exists (the numbered list of Codex review rounds); this module
holds only the *how*:

- :func:`_root_level_artifact_type` / :func:`_decode_json_string_token` --
  the bounded, depth-tracking JSON token scan for the marker itself.
- :func:`_decoded_prefix_is_a_real_tar_stream` /
  :func:`_path_is_a_real_zip_container` -- the two archive-format
  structural checks that rule out a forged marker embedded in a tar
  member name or zip preamble/central-directory metadata.

Every name here is private to this pair of modules -- :mod:`bundle_compare_
operand` is the only importer, and this split is purely a file-size
mechanism, not a new public boundary.
"""

from __future__ import annotations

import io
import json as _json
import os
import re
import tarfile
import zipfile
from pathlib import Path

#: Tokenizes a (possibly truncated) JSON byte prefix for
#: :func:`_root_level_artifact_type`: a whole string literal (consumed as
#: one token, escapes included, so a structural character inside a string
#: value is never mistaken for real JSON structure -- the same "string
#: alternative tried first" discipline ``storage.json_budget``'s own
#: container-token regex already establishes), an unterminated string that
#: runs to the literal end of the scanned prefix (Codex review, round 13,
#: fresh evidence -- see below), one of the six structural characters that
#: matter for depth/key-value tracking, or a bare number/``true``/
#: ``false``/``null`` scalar (matching ``storage.json_budget``'s own
#: number pattern -- loose, not a strict JSON-number grammar, since
#: validating syntax is ``json.loads()``'s job, not this scan's). Scalars
#: are tokenized -- even though the scan never treats one as a key or
#: value candidate -- purely so :func:`root_level_artifact_type` can
#: require *every* byte between one recognized token and the next to be
#: JSON whitespace (Codex review, round 6, fresh evidence -- see that
#: function's own docstring for why "any byte re.finditer doesn't match is
#: just skipped" is not safe to leave unchecked).
#:
#: **The unterminated-string alternative closes a gap that same
#: discipline opened (Codex review, round 13, fresh evidence).** When the
#: scanned prefix ends in the middle of a long string value (a genuine,
#: valid JSON document simply truncated by the bounded-read window, not
#: malformed content), the *complete*-string alternative can't match at
#: all -- there is no closing quote left in the buffer -- so without this
#: alternative, ``re.finditer`` would skip the opening quote as unmatched
#: and resume scanning *inside* the string's own raw characters, where a
#: coincidental digit run, ``true``/``false``/``null`` spelling, or
#: literal structural character (all realistic in a template string, a
#: constant's spelling, or free-form text) could match a scalar or
#: structural token in its own right. The whitespace-only gap check would
#: then very likely reject the *surrounding* string content as a stray
#: non-whitespace byte, misreporting a truncated-but-otherwise-valid
#: document as definitively invalid (Codex review, round 6's own
#: docstring: "any byte ``re.finditer`` doesn't match is just skipped" was
#: exactly the assumption this case violates). Matching the unterminated
#: string as a single token all the way to the prefix's own end instead
#: means no sub-content inside it is ever separately tokenized, so no
#: spurious gap violation is possible; the caller's own end-of-loop
#: fallback (this scan simply runs out of tokens with nothing more to
#: examine) then correctly reports the answer as inconclusive
#: (``definitive=False``) rather than a false structural violation.
_JSON_STRUCTURE_TOKEN_RE = re.compile(
    rb'"(?:[^"\\]|\\.)*"|"(?:[^"\\]|\\.)*\Z|[{}\[\]:,]|-?\d[\d.eE+-]*|true|false|null',
    re.DOTALL,
)

#: JSON's own whitespace set (RFC 8259 -- space, tab, CR, LF). The *only*
#: bytes :func:`_root_level_artifact_type` permits between two recognized
#: tokens once scanning is underway.
_JSON_WHITESPACE_RE = re.compile(rb"[ \t\r\n]*")

#: The root marker key, as JSON actually compares it -- decoded, not the
#: raw token spelling. See :func:`_decode_json_string_token`.
_ARTIFACT_TYPE_KEY = "artifact_type"

#: How much decoded prefix :func:`abicheck.workflows.bundle_compare_operand.
#: looks_like_stored_bundle_facts` asks :func:`abicheck.snapshot_io.
#: bounded_decoded_prefix` for -- deliberately far above that function's
#: own ``_SNIFF_BYTES`` (4 KiB) default (Codex review, PR #1042, round 4,
#: fresh evidence): the order-independent scan (that module's own
#: docstring, point 4) only helps if the marker actually falls inside the
#: window it decodes. A reordered document with a large ``per_library_
#: snapshots`` member ahead of ``artifact_type`` -- unlikely from this
#: repo's own writer, which always emits the marker first, but not ruled
#: out for a document re-serialized by another conforming tool -- could
#: place the marker well past 4 KiB. Matches ``snapshot_io``'s own
#: ``_BOUNDED_PREFIX_MAX_RAW_BYTES`` ceiling (1 MiB): generous enough for
#: any realistic reordered document while staying a single bounded read,
#: not a full decompression -- the same "cheap but not exact" trade-off
#: ``bounded_decoded_prefix`` itself already makes explicit for its own
#: escalating-retry ceiling.
MARKER_SCAN_BYTES = 1024 * 1024

#: The *first*-phase decoded-prefix size ``looks_like_stored_bundle_facts``
#: tries, before ever falling back to :data:`MARKER_SCAN_BYTES` (Codex
#: review, round 8, fresh evidence -- see that module's own docstring for
#: the two-phase probe this constant drives). Mirrors ``snapshot_io``'s own
#: private ``_SNIFF_BYTES`` default (4 KiB) rather than importing it
#: directly (that name is private to its own module); kept as a literal
#: local constant instead so this module doesn't reach into another
#: module's implementation detail for a value it can just as well own
#: itself.
SMALL_MARKER_SCAN_BYTES = 4096


def decode_json_string_token(token: bytes) -> str | None:
    """Decode a raw, still-quoted-and-escaped JSON string *token* (as
    matched by :data:`_JSON_STRUCTURE_TOKEN_RE`) into its real string
    value, or ``None`` if it isn't valid JSON string syntax after all
    (the token regex accepts any ``\\.`` escape pair, including one
    ``json.loads`` itself would reject, e.g. ``\\q`` -- rejecting rather
    than raising keeps this scan's own "never raises" contract). Used for
    *both* a candidate key and the marker's value, so an escaped spelling
    of either (``"artifact\\u005ftype"`` for the key,
    ``"abicheck.bundle\\u002dfacts"`` for the value) is compared the same
    way ``json.loads()`` itself would compare it -- a raw-byte comparison
    against the token's literal spelling would silently reject a
    (technically valid, if unnecessary) escaped key or value that
    ``load_bundle_facts()`` accepts fine (Codex review, round 5, fresh
    evidence, for the key half; the value half was already decoded this
    way from the start)."""
    try:
        decoded = _json.loads(token)
    except ValueError:
        return None
    return decoded if isinstance(decoded, str) else None


def root_level_artifact_type(prefix: bytes) -> tuple[bytes | None, bool]:
    """Scan a bounded, possibly-truncated JSON byte prefix for the root
    object's own ``"artifact_type"`` member. Returns ``(value, definitive)``:
    *value* is the *last* root-level ``artifact_type`` member's raw (still
    JSON-string-escaped) value seen so far (matching ``json.loads()``'s own
    last-key-wins duplicate handling -- point 13), or ``None`` if none was
    seen at all; *definitive* is ``True`` when *value* cannot change no
    matter how much more of the document a caller might decode (the root
    object closed within *prefix*, with or without a match; or *prefix* was
    proven not to be a JSON object at all), and ``False`` only when the
    scan ran out of *prefix* before the root object closed -- a genuinely
    truncated view, where a larger *prefix* could still reveal a later
    duplicate that overrides *value*. Even then, *value* is still whatever
    was found so far, not discarded -- a caller with no larger window left
    to try can still use it as a best-effort answer (used by
    ``looks_like_stored_bundle_facts`` both to decide whether re-scanning a
    larger decoded window is worth the extra decode cost -- Codex review,
    round 8, fresh evidence -- and, once no larger window is left, as that
    final fallback -- Codex review, round 11, fresh evidence, see the
    closing-brace-less branch's own comment below for why).

    Depth-aware (a nested ``artifact_type`` at any deeper level is never
    matched -- ``bundle_compare_operand``'s own docstring, point 3) and
    order-independent *within the root object* (the marker need not be the
    first member -- point 4), unlike a single regex, which can express
    neither. Still anchored to *prefix* actually beginning with a JSON
    object (only whitespace may precede the opening ``{``) -- without that
    check, scanning for tokens anywhere in an unanchored byte string means
    the *first* ``{`` this scan would ever see is whichever one appears
    earliest, which for a real, non-JSON binary blob (a compressed
    archive's own framing bytes, say) can easily be a nested member's own
    object rather than a real document root at all (Codex review, fresh
    evidence: an earlier draft without this check reintroduced point 2's
    nested-package-member false positive by trading root-anchoring away
    for order-independence, instead of keeping both). Bounded to *prefix*'s
    own length -- a marker appearing only past the read window is a real,
    accepted "cannot classify from this little" limitation of a
    bounded-prefix design, the same shape as :func:`abicheck.snapshot_io.
    bounded_decoded_prefix`'s own "corrupt or unrecognized -> None"
    contract; never raises, never assumes truncation means absence
    (returning ``None`` here means "not found in the visible window", the
    caller does not distinguish that from a genuine absence -- both simply
    fail to classify as stored bundle facts).

    Requires every byte between one recognized token and the next to be
    JSON whitespace, not merely "whatever ``re.finditer`` happened to skip
    over" (Codex review, round 6, fresh evidence). Without this, a crafted
    non-JSON byte stream carrying the marker's token *sequence* embedded in
    otherwise-arbitrary bytes -- e.g. a ``.tar.gz`` release package whose
    first tar member is deliberately named ``{"artifact_type":"abicheck.
    bundle-facts"}`` -- would still satisfy this scan: a tar header starts
    with the member name, so those exact header bytes read as a root JSON
    object to a scan that only checks token *order*, not that the bytes
    *between* tokens are legal JSON separators. Rejecting any stray
    (non-whitespace) byte between tokens is what a real JSON document (a
    from-`json.dumps` or pretty-printed encoding of a real ``BundleFacts``
    document alike) never produces in the first place -- it closes the gap
    without reintroducing a suffix-only package veto (point 2's own
    rejected fix).

    A prefix that ends in the middle of a long, otherwise-legal JSON
    string is genuinely inconclusive, not a structural violation (Codex
    review, round 13, fresh evidence) -- see :data:`_JSON_STRUCTURE_TOKEN_
    RE`'s own docstring for the full account of why the previous paragraph's
    whitespace-only gap check would otherwise misreport this exact case as
    definitively invalid.
    """
    stripped = prefix.lstrip(b" \t\r\n")
    if not stripped.startswith(b"{"):
        # Not even the shape of a JSON object -- no amount of additional
        # decoded content changes that.
        return None, True
    depth = 0
    just_saw_colon = False
    pending_key_is_marker = False
    last_marker_value: bytes | None = None
    expected_pos = 0
    for m in _JSON_STRUCTURE_TOKEN_RE.finditer(stripped):
        if m.start() != expected_pos:
            gap = stripped[expected_pos : m.start()]
            if not _JSON_WHITESPACE_RE.fullmatch(gap):
                # A byte between two recognized tokens that isn't JSON
                # whitespace -- not a real JSON document (any legal
                # encoding, minified or pretty-printed, separates tokens
                # with whitespace only), so stop trusting anything this
                # scan has matched so far. Definitive: the violating byte's
                # position doesn't move with a larger prefix.
                return None, True
        expected_pos = m.end()
        tok = m.group(0)
        if tok in (b"{", b"["):
            depth += 1
            just_saw_colon = False
            pending_key_is_marker = False
            continue
        if tok in (b"}", b"]"):
            depth -= 1
            just_saw_colon = False
            pending_key_is_marker = False
            if depth <= 0:
                # The root container closed within the visible prefix --
                # every direct member has already been seen, definitively
                # -- including every duplicate, so *last_marker_value* is
                # already whichever one a real decoder would have kept.
                return last_marker_value, True
            continue
        if tok == b":":
            just_saw_colon = True
            continue
        if tok == b",":
            just_saw_colon = False
            pending_key_is_marker = False
            continue
        if not tok.startswith(b'"'):
            # A bare number/true/false/null scalar -- always a value
            # (JSON has no unquoted key syntax), so it can never complete
            # the marker (whose value is a JSON string) and never starts a
            # pending key.
            just_saw_colon = False
            pending_key_is_marker = False
            continue
        # A string literal -- a value if it immediately follows ':', a key
        # otherwise (JSON's own key/value alternation, tracked explicitly
        # rather than assumed from position, so "artifact_type" appearing
        # as some *other* field's value is never mistaken for a key --
        # Codex review, fresh evidence on an earlier draft of this scan).
        if just_saw_colon:
            if pending_key_is_marker:
                # Record, don't return -- a *later* root-level
                # "artifact_type" member (a duplicate key, legal JSON
                # syntax even though this repo's own writer never emits
                # one) must win, the same way json.loads()'s own
                # last-key-wins dict construction would (Codex review,
                # round 10, fresh evidence). Returning on the first match
                # made this scanner disagree with the real decoder for a
                # document with a duplicate marker key.
                last_marker_value = tok
            just_saw_colon = False
            pending_key_is_marker = False
        else:
            pending_key_is_marker = (
                depth == 1 and decode_json_string_token(tok) == _ARTIFACT_TYPE_KEY
            )
    # Ran out of prefix before the root object closed -- genuinely
    # inconclusive, not "absent"/"final" (Codex review, round 8: requesting
    # a large prefix directly can itself fail to decode for a legitimate
    # but pathologically-encoded compressed document, where a smaller
    # prefix -- tried first by the caller -- reliably would have; a
    # duplicate marker later in the document, not yet seen, could still
    # override *last_marker_value* the same way it would for the root-
    # closed case above, so an inconclusive truncation must not report a
    # value found so far as *definitive*). Still returns whatever
    # *last_marker_value* was seen, though, rather than discarding it
    # (Codex review, round 11, fresh evidence): a real, large
    # ``--bundle-facts-out`` document -- the writer always emits the
    # marker first, so this is the ordinary case for any sufficiently
    # large library, nothing to do with a duplicate key at all -- can
    # exceed even :data:`MARKER_SCAN_BYTES` before its own root object
    # closes. Discarding an already-found value here (an earlier version
    # of this function, from the round-10 duplicate-key fix, did exactly
    # that) silently broke every such document once neither probe window
    # could reach the closing brace; the caller now uses this "best
    # candidate so far" as its own final fallback once it has no larger
    # window left to try (see ``bundle_compare_operand.
    # looks_like_stored_bundle_facts``), accepting the same narrow,
    # already-documented residual gap as the closing-brace case (a
    # duplicate marker positioned beyond the largest window that would
    # override this one is not detected) rather than a full document
    # scan.
    return last_marker_value, False


def decoded_prefix_is_a_real_tar_stream(prefix: bytes) -> bool:
    """``True`` when *prefix* is the start of a genuine tar stream (Codex
    review, PR #1042, round 6, fresh evidence).

    Every *other* archive format ``bundle_compare_operand`` rules out by
    fixed magic (deb/rpm/conda, and the G40/wheel zip container -- see
    this function's own sibling, :func:`path_is_a_real_zip_container`, for
    why zip needs its own dedicated, whole-file check rather than a
    magic-byte veto) cannot begin with ``{`` at all -- but a tar stream's
    very first bytes *are* its first member's ``name`` field, attacker- or
    tool-controlled content with no leading magic at all. A
    ``.tar``/``.tar.gz``/``.tar.bz2``/``.tar.xz`` release package whose
    first member is deliberately named a complete, valid, self-closing
    JSON object (e.g. ``{"artifact_type":"abicheck.bundle-facts"}``)
    satisfies every check :func:`root_level_artifact_type` can make from
    content alone -- there is no gap, no reordering, nothing syntactically
    wrong with those exact bytes as a JSON prefix, because they *are* a
    complete, valid encoding of exactly the shape being checked for. The
    only way to tell it apart from a real ``BundleFacts`` document is a
    signal :func:`root_level_artifact_type` cannot see at all: that the
    *whole file* is independently, structurally a tar archive.
    ``tarfile.is_tarfile()`` checks exactly that (the first 512-byte header
    block's own checksum, well within :data:`MARKER_SCAN_BYTES`), and never
    accepts real JSON text as a tar header -- so this has no collision risk
    with a genuine stored-facts document the way an ``is_package()``
    *filename*-suffix veto did (point 2, reverted): this checks the decoded
    content structurally, not the path's suffix.
    """
    try:
        return tarfile.is_tarfile(io.BytesIO(prefix))
    except Exception:
        # is_tarfile() is documented to raise on some malformed inputs
        # rather than only ever returning False -- never let a corrupt or
        # truncated candidate turn a classification helper into a crash.
        return False


def path_is_a_real_zip_container(path: Path) -> bool:
    """``True`` when *path* is a genuine zip container carrying at least
    one real member -- a wheel, the G40 archive, or any other zip-based
    format -- *anywhere* in the file, not just at byte 0 (Codex review,
    PR #1042, round 7, fresh evidence; the *member-count* refinement is
    round 9's, see below).

    The ZIP format's central directory sits at the *end* of the file and is
    what a real zip reader (Python's own ``zipfile``, the ``WheelExtractor``
    this repo already uses) actually trusts; the format explicitly permits
    arbitrary bytes *before* the first local file header (this is how a
    self-extracting archive or an executable-prefixed zip works). A real
    ``.whl`` prepended with a hand-crafted ``{"artifact_type":"abicheck.
    bundle-facts"}`` preamble is still a perfectly valid zip to every real
    zip reader, and -- since ``_looks_like_stored_bundle_facts_archive``
    only recognizes the G40 archive by its *own* magic at byte 0, this
    prepended file fails that check and falls through here -- would satisfy
    :func:`root_level_artifact_type`'s own scan on the raw (uncompressed,
    so undecoded-prefix-equals-raw-file) leading bytes exactly the way
    round 6's crafted tar member name did. Checked against *path* itself
    (unlike the tar check, this cannot be answered from a bounded prefix
    alone -- the central directory it needs may be well past
    :data:`MARKER_SCAN_BYTES` into a real, larger wheel), which
    ``zipfile.is_zipfile()`` already does correctly regardless of file
    size: it seeks from the end of the file to locate the end-of-central-
    directory record, the same way a real zip reader does, rather than
    reading forward from byte 0.

    **Requires at least one real central-directory entry, not merely a
    structurally plausible EOCD (Codex review, round 9, fresh evidence).**
    Round 8 taught the reverse lesson about ``zipfile.is_zipfile()``: a
    coincidental EOCD-shaped byte sequence (a crafted gzip ``FEXTRA``
    sub-field) can satisfy it for a file with no real zip content at all,
    which is why ``looks_like_stored_bundle_facts`` no longer trusts a bare
    ``is_zipfile()`` verdict as sufficient reason to *reject* an otherwise
    valid gzip/zstd-compressed ``BundleFacts`` document either -- but round
    9 showed the same coin has another face: a genuine, structurally valid
    zip (a real wheel, with real members) can *itself* be prefixed with a
    complete, independently-decodable gzip/zstd stream as its own permitted
    "arbitrary preamble," where that preamble decodes to JSON carrying the
    marker. A bare ``detect_snapshot_compression() != NONE`` skip (round
    8's own fix) would then wrongly treat this as "already gzip, never
    zip-shaped" and let the marker scan run and misclassify a real wheel as
    stored facts. The actual distinguishing signal between round 8's
    coincidence and round 9's genuine archive is not "does the raw file
    start with a compression magic" but whether the zip *has any real
    entries*.

    **A ``namelist()`` name is still not proof of a real member (Codex
    review, round 10, fresh evidence).** The previous paragraph's claim
    that a fake central-directory entry has "no legitimate reason to
    exist" inside a gzip ``FEXTRA`` sub-field was wrong -- ``FEXTRA`` has
    no practical size limit that would stop it (a full central-directory
    record is ~46 bytes plus a filename, comfortably inside ``FEXTRA``'s
    own 65535-byte ceiling), and a crafted one satisfies both
    ``is_zipfile()`` *and* ``namelist()`` with zero real zip content
    anywhere: ``ZipFile.__init__`` only ever reads the *central directory*
    to build ``namelist()``/``infolist()``, never validating that a real
    local file header actually exists at each entry's declared offset --
    that validation happens lazily, only when a member is actually opened.
    Answered by opening (not reading -- ``ZipFile.open()`` validates the
    30-byte local file header's own magic immediately, before ever
    touching a member's compressed payload, so this stays a cheap, bounded
    check regardless of how large a real member's content is) at least one
    declared member and requiring that to succeed: a genuine wheel's first
    member has a real local file header by construction, while a
    central-directory-only forgery -- crafted entirely within a
    compressed envelope's own header metadata, with no matching local file
    header anywhere in the actual byte stream -- fails this immediately
    with ``BadZipFile``.

    **Preflights the central directory's own claimed size/entry count
    before ever constructing ``zipfile.ZipFile`` (Codex review, round 14,
    fresh evidence).** ``ZipFile.__init__`` eagerly parses the *entire*
    central directory -- unbounded by this function's own design, which
    otherwise only ever touches a small, fixed-size window (the EOCD tail,
    one member's local file header). An otherwise-ordinary file (an ELF
    binary, say) with an appended ZIP whose central directory claims an
    enormous entry count or byte size would reach that unbounded parse on
    every single automatic operand classification, before either operand
    is known to be a bundle-facts document at all. ``_looks_like_stored_
    bundle_facts_archive`` (this module's sibling in ``bundle_compare_
    operand.py``) already guards its own ``ZipFile`` construction this way,
    via ``BundleArchiveReader.open`` -> ``reject_absurd_central_directory``;
    this general zip probe reuses the identical guard directly (same
    ``MAX_ARCHIVE_MEMBERS`` cap ``bundle_archive.py`` itself enforces, so a
    real, valid wheel or the G40 archive is never rejected here that
    wouldn't already be rejected by opening it for real) rather than a
    second, independently-tuned bound.
    """
    from ..errors import SnapshotError
    from ..storage.bundle_archive import MAX_ARCHIVE_MEMBERS
    from ..storage.bundle_archive_cd_guard import reject_absurd_central_directory

    try:
        if not zipfile.is_zipfile(path):
            return False
        # A single shared, already-open fd carried from the preflight
        # below through to ZipFile's own construction -- the same
        # path-substitution-race defense BundleArchiveReader.open applies
        # (reject_absurd_central_directory's own docstring), rather than
        # reopening *path* a second time for ZipFile.
        with open(path, "rb") as fp:
            try:
                validated_size = reject_absurd_central_directory(
                    fp, path, max_entries=MAX_ARCHIVE_MEMBERS
                )
            except SnapshotError:
                # Claims an absurd entry count/byte size -- refuse to hand
                # this to ZipFile's own unbounded parse at all. Not
                # "definitely not a stored bundle-facts document" (an
                # oversized real wheel is conceivable), but this
                # classification helper's whole contract is a cheap,
                # bounded probe -- an operand this expensive to even
                # examine is treated the same as "not a zip container"
                # rather than paying the unbounded cost to find out.
                return False
            # Narrows (not closes -- see reject_absurd_central_directory's
            # own docstring) the window between that preflight and
            # ZipFile's own independent, unbounded scan below.
            if os.fstat(fp.fileno()).st_size != validated_size:
                return False
            fp.seek(0)
            with zipfile.ZipFile(fp) as zf:
                names = zf.namelist()
                if not names:
                    return False
                try:
                    with zf.open(names[0]):
                        pass
                except zipfile.BadZipFile:
                    # A declared member with no real local file header
                    # behind it -- the central directory (and therefore
                    # namelist()) can be entirely forged without one
                    # (round 10's own finding); this is what actually
                    # proves real zip content.
                    return False
                return True
    except Exception:
        # zipfile.is_zipfile()/ZipFile()/open() are documented (or known
        # in practice) to raise on some malformed inputs rather than only
        # ever returning False/empty -- never let a corrupt or truncated
        # candidate turn a classification helper into a crash.
        return False
