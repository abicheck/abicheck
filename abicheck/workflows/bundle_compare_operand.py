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

"""``compare``'s bundle-facts operand classification (CLI cleanup phase two,
PR I).

``compare --old-bundle-facts`` used to be a flag that told the CLI "OLD_INPUT
is a stored :class:`~abicheck.bundle_facts.BundleFacts` document, not a live
library/directory/package" -- a second, explicit selector living alongside
the ordinary directory-vs-package-vs-single-file classification
(:func:`abicheck.cli_resolve.classify_compare_operand`) that every other
``compare`` operand already goes through automatically. This module replaces
that flag with the same kind of automatic classification, using the
self-describing ``artifact_type`` marker
(:data:`abicheck.bundle_facts.BUNDLE_FACTS_ARTIFACT_TYPE`) G38/PR I's own
prerequisite work added specifically so this classification could be cheap
and unambiguous (see ``docs/contribute/plans/cli-cleanup-phase-two.md``'s "PR
I" entry: "the artifact needs a strong discriminator first").

**Deliberately marker-only, not the full :func:`~abicheck.
bundle_facts_serialization.looks_like_bundle_facts_document` two-tier check.**
That function's second tier (a legacy, marker-less v1 document, detected by
``per_library_snapshots`` shape alone) needs the *whole* document parsed to
answer, which is not something every ordinary ``compare`` invocation should
pay for -- unlike the flag it replaces, this classifier runs unconditionally
on every ``compare a b`` call, including the overwhelming majority whose
OLD_INPUT is an ordinary ``AbiSnapshot`` JSON file (about to be parsed as one
right after classification decides it is *not* bundle facts). The marker
tier answers from a small, bounded prefix
(:func:`abicheck.snapshot_io.bounded_decoded_prefix`) -- no full
decompression, no JSON parse, no container-node budget concern -- the same
"classify from a bounded prefix" discipline that function's own docstring
already establishes for the plain/gzip/zstd distinction. Every bundle-facts
document this repository's own writer (``bundle_facts_serialization.
bundle_facts_to_dict``) has produced since the marker was introduced
(``BUNDLE_FACTS_SCHEMA_VERSION`` 2) carries it, unconditionally, as one of
the first fields written -- there is no live production path today that
writes a marker-less bundle-facts document.

**Known, accepted gap: a genuinely pre-marker v1 bundle-facts document (hand-
authored, or persisted before schema_version 2 shipped) cannot be auto-
classified by this module.** Reading one is still fully supported --
``bundle_side_input``/``bundle_facts_serialization.load_bundle_facts`` still
accept the legacy shape -- but *routing* to that reader now depends on this
classifier recognizing the operand as bundle facts in the first place, and a
marker-less document will instead be classified as an ordinary file and
diffed as whatever ``compare``'s normal dispatch makes of it (almost always
a confusing, unrelated error, since a bundle-facts document's own shape does
not resemble an ``AbiSnapshot``). This is a real, narrow regression from the
removed flag (which forced the interpretation unconditionally, regardless of
shape) -- accepted because every bundle-facts document *this build* can
produce already carries the marker, and there is no longer an explicit
per-invocation escape hatch to weigh against a whole-document parse on every
ordinary ``compare`` call (CLAUDE.md's "no deprecation aliases" stance is the
other half of why an escape hatch was not added back in a different shape).

**Four review-caught refinements on top of the plain marker scan (Codex,
PR #1042, three rounds):**

1. **The G40 content-addressed zip archive format
   (``bundle_facts.write_bundle_facts_archive`` / ``save_bundle_facts(...,
   format="archive")``) is a real, supported ``BundleFacts`` encoding too --
   it starts with a zip local-file-header magic, not JSON, so the plain
   marker scan above never matches it.** Checked first, and just as cheaply:
   ``storage.bundle_archive.sniff_bundle_archive_format`` peeks the same
   handful of magic bytes ``bounded_decoded_prefix`` already sniffs, and
   only a real ``"archive"`` verdict pays for opening the file with
   ``BundleArchiveReader`` and reading its ``manifest.json`` member (bounded
   by that reader's own existing safety caps -- size, container-node budget,
   CRC-32 -- unconditionally, not new caps invented here) to check its own
   ``artifact_type`` against ``BUNDLE_ARCHIVE_ARTIFACT_TYPE``. The execution
   engine this classifier routes to already reads either encoding
   transparently (``load_bundle_facts``'s own ``format="auto"`` default,
   unchanged) -- only the *routing* decision was missing archive-shape
   recognition.
2. **A recognized package archive (wheel/deb/rpm/tar/conda) carrying a
   member whose own content happens to contain the marker text (e.g. an
   embedded ``BundleFacts`` fixture in a test package) must not
   misclassify the whole package.** Originally "fixed" by checking
   ``abicheck.package.is_package`` before the marker scan and refusing to
   scan a recognized package at all -- reverted (Codex review, round 2,
   fresh evidence): ``is_package`` classifies purely by filename
   suffix/magic bytes, so a real ``--bundle-facts-out`` document named
   with a package-like suffix (``baseline.tar.gz``, plausible from a
   templated CI naming convention) would have its genuine marker
   suppressed by that pre-check, with no remaining route back to the
   BundleFacts loader at all once ``--old-bundle-facts`` is gone. Point 3
   below closes the *actual* nested-member collision this was meant to
   prevent, without that side effect.
3. **The marker must be a *direct member of the root object* (depth 1),
   not "anywhere in the decoded prefix" (Codex review, round 2, fresh
   evidence).** An unanchored search also matched a *nested* occurrence --
   not just a package member's embedded fixture (point 2's scenario,
   independently closed by depth-scoping alone: a compressed archive's
   decoded bytes never place a string token at depth 1 immediately inside
   a root ``{``, since every supported format's own framing -- a tar
   header block, a zip/RPM/deb magic -- precedes any member content) but
   also an entirely ordinary, single ``AbiSnapshot`` whose own
   ``constants`` mapping happens to define a C constant literally named
   ``artifact_type`` with that exact string value, which JSON-serializes
   as a *nested* object and does not make the document itself bundle
   facts. This is what makes point 2's ``is_package`` pre-check
   unnecessary, not merely one alternative fix for it.
4. **The marker's *position* among the root object's members is not part
   of the schema and must not be required (Codex review, round 3, fresh
   evidence).** ``bundle_facts_to_dict`` always writes ``artifact_type``
   first, but a document re-serialized by another conforming tool (a
   pretty-printer, a key-sorting formatter) can freely reorder it --
   ``bundle_facts_from_dict`` itself never requires a particular order,
   so a routing check that does is stricter than the format it is meant
   to recognize. A plain regex cannot express "this key, this depth,
   anywhere among the siblings" (regex has no notion of nesting), so
   points 3 and 4 together are answered by a small, bounded, depth-
   tracking token scan (:func:`_root_level_artifact_type`) instead of a
   single pattern -- still no full JSON parse, still bounded to the same
   prefix ``bounded_decoded_prefix`` already reads, still no container-
   node budget concern (the input size is already capped).
5. **Order-independence (point 4) only helps if the marker actually falls
   inside the decoded window (Codex review, round 4, fresh evidence).** A
   reordered document with a large member (e.g. a populated
   ``per_library_snapshots``) ahead of ``artifact_type`` could push the
   marker past a small fixed prefix even though point 4's scan would have
   recognized it at any position *within* that prefix. Answered by asking
   ``bounded_decoded_prefix`` for a much larger window
   (:data:`_MARKER_SCAN_BYTES`, 1 MiB -- matching ``snapshot_io``'s own
   escalating-retry ceiling) instead of its 4 KiB sniff default, generous
   enough for any realistic reordered document while remaining one bounded
   read.

**Residual, accepted gap (same shape as the pre-marker-v1 gap above):** a
reordered document whose marker falls beyond :data:`_MARKER_SCAN_BYTES` of
decoded content is still misclassified as an ordinary snapshot -- a fixed
window can shrink this gap but, by construction, cannot close it for an
unbounded document without abandoning the "bounded read, no full parse"
discipline this whole module exists to keep. 1 MiB of *decoded* prefix
ahead of the marker is far beyond what any plausible reordering of a real
``--bundle-facts-out`` document produces (the writer itself always emits
the marker first; the only realistic trigger is a third-party re-
serialization, not this tool's own output), so this is accepted rather
than chased further.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

#: Tokenizes a (possibly truncated) JSON byte prefix for
#: :func:`_root_level_artifact_type`: a whole string literal (consumed as
#: one token, escapes included, so a structural character inside a string
#: value is never mistaken for real JSON structure -- the same "string
#: alternative tried first" discipline ``storage.json_budget``'s own
#: container-token regex already establishes), or one of the six
#: structural characters that matter for depth/key-value tracking.
#: Numbers/``true``/``false``/``null`` are deliberately not tokenized --
#: this scan only ever needs to recognize string keys/values, and
#: ``re.finditer`` simply skips over bytes no alternative matches, which is
#: exactly "ignore" for a token kind this scan doesn't care about.
_JSON_STRUCTURE_TOKEN_RE = re.compile(rb'"(?:[^"\\]|\\.)*"|[{}\[\]:,]', re.DOTALL)

#: How much decoded prefix :func:`looks_like_stored_bundle_facts` asks
#: :func:`abicheck.snapshot_io.bounded_decoded_prefix` for -- deliberately
#: far above that function's own ``_SNIFF_BYTES`` (4 KiB) default (Codex
#: review, PR #1042, round 4, fresh evidence): the order-independent scan
#: (point 4 below) only helps if the marker actually falls inside the
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
_MARKER_SCAN_BYTES = 1024 * 1024


def _root_level_artifact_type(prefix: bytes) -> bytes | None:
    """Scan a bounded, possibly-truncated JSON byte prefix for the root
    object's own ``"artifact_type"`` member and return its raw (still
    JSON-string-escaped) value, or ``None`` if no such *direct* member is
    found before either the root container closes or the prefix runs out.

    Depth-aware (a nested ``artifact_type`` at any deeper level is never
    matched -- this module's own docstring, point 3) and order-independent
    *within the root object* (the marker need not be the first member --
    point 4), unlike a single regex, which can express neither. Still
    anchored to *prefix* actually beginning with a JSON object (only
    whitespace may precede the opening ``{``) -- without that check,
    scanning for tokens anywhere in an unanchored byte string means the
    *first* ``{`` this scan would ever see is whichever one appears
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
    """
    stripped = prefix.lstrip(b" \t\r\n")
    if not stripped.startswith(b"{"):
        return None
    depth = 0
    just_saw_colon = False
    pending_key_is_marker = False
    for m in _JSON_STRUCTURE_TOKEN_RE.finditer(stripped):
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
                # every direct member has already been seen.
                break
            continue
        if tok == b":":
            just_saw_colon = True
            continue
        if tok == b",":
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
                return tok
            just_saw_colon = False
            pending_key_is_marker = False
        else:
            pending_key_is_marker = depth == 1 and tok == b'"artifact_type"'
    return None


def _looks_like_stored_bundle_facts_archive(path: Path) -> bool:
    """``True`` when *path* is a G40 content-addressed zip archive whose own
    ``manifest.json`` declares the archive artifact_type marker. Cheap on
    the common (non-archive) case: bails out on the magic-byte sniff alone,
    before ever opening *path* as a zip. See this module's own docstring
    (point 1)."""
    from ..errors import SnapshotError
    from ..storage.bundle_archive import (
        BundleArchiveReader,
        sniff_bundle_archive_format,
    )
    from ..storage.bundle_facts_validation import BUNDLE_ARCHIVE_ARTIFACT_TYPE

    try:
        if sniff_bundle_archive_format(path) != "archive":
            return False
        with BundleArchiveReader.open(path) as reader:
            manifest = reader.read_manifest()
    except SnapshotError:
        # Zip-shaped but not a well-formed BundleFacts archive (a real
        # .whl/.conda package, a corrupted file, ...) -- not a classification
        # failure, just "not this".
        return False
    return manifest.get("artifact_type") == BUNDLE_ARCHIVE_ARTIFACT_TYPE


def looks_like_stored_bundle_facts(path: Path) -> bool:
    """Cheap, safe classification of *path* as a stored
    :class:`~abicheck.bundle_facts.BundleFacts` document -- either shape
    (plain/compressed JSON, or the G40 zip archive).

    ``False`` for anything that is not a regular file (a directory), or
    that this module's bounded prefix reader cannot decode at all (corrupt,
    or a format ``snapshot_io`` doesn't recognize). A real package archive
    (wheel/deb/rpm/tar/conda) is not special-cased -- the depth-scoped
    marker scan (this module's own docstring, points 3-4) already cannot
    match one, so excluding it separately would only reintroduce point 2's
    false-negative on a genuine stored-facts document with a package-like
    filename suffix. Never raises, never fully decompresses or JSON-parses
    a plain-JSON candidate -- see this module's own docstring for why. A
    ``True`` answer still leaves full validation to the ordinary
    bundle-facts read path (``load_bundle_facts`` / ``bundle_facts_from_
    dict``); this only decides *routing*.
    """
    if not path.is_file():
        return False
    if _looks_like_stored_bundle_facts_archive(path):
        return True
    from ..bundle_facts import BUNDLE_FACTS_ARTIFACT_TYPE
    from ..snapshot_io import bounded_decoded_prefix

    prefix = bounded_decoded_prefix(path, _MARKER_SCAN_BYTES)
    if prefix is None:
        return False
    value = _root_level_artifact_type(prefix)
    if value is None:
        return False
    # *value* is still a raw JSON string token (quotes and any backslash
    # escapes intact) -- decode it the same way json.loads() would rather
    # than comparing escaped bytes to an unescaped constant, so a
    # (technically valid, if unnecessary) escaped spelling still matches.
    import json as _json

    try:
        decoded_value = _json.loads(value)
    except ValueError:
        return False
    return bool(decoded_value == BUNDLE_FACTS_ARTIFACT_TYPE)


@dataclass(frozen=True)
class BundleCompareRequest:
    """Classification of a ``compare`` operand pair for bundle-facts
    routing: which side(s), if any, are stored ``BundleFacts`` documents
    rather than a live library/directory/package.

    Today only the OLD side has an execution engine
    (``frontends.cli.commands.compare_bundle_facts.dispatch``, the former
    ``--old-bundle-facts`` mode) -- ``new_is_stored`` is still classified
    (so a caller gets a precise, actionable answer for it) but ``compare``'s
    own dispatch rejects a stored NEW_INPUT outright rather than silently
    mishandling it. Unifying live/live, stored/live, live/stored and
    stored/stored behind one evaluation/gate/report/dry-run path is real,
    unimplemented follow-up work -- see this module's own docstring and PR
    I's tracking in ``docs/contribute/plans/cli-cleanup-phase-two.md``.
    """

    old_is_stored: bool
    new_is_stored: bool

    @property
    def any_stored(self) -> bool:
        return self.old_is_stored or self.new_is_stored


def classify_bundle_compare_operands(
    old_input: Path, new_input: Path
) -> BundleCompareRequest:
    """Classify *old_input*/*new_input* for ``compare``'s bundle-facts
    routing -- the automatic replacement for the removed
    ``--old-bundle-facts`` flag. See :class:`BundleCompareRequest` and this
    module's own docstring."""
    return BundleCompareRequest(
        old_is_stored=looks_like_stored_bundle_facts(old_input),
        new_is_stored=looks_like_stored_bundle_facts(new_input),
    )
