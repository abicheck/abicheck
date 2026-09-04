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

**Review-caught refinements on top of the plain marker scan (Codex, PR
#1042, rounds 1-11):**

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
   tracking token scan (:func:`root_level_artifact_type`) instead of a
   single pattern -- still no full JSON parse, still bounded to the same
   prefix ``bounded_decoded_prefix`` already reads, still no container-
   node budget concern (the input size is already capped).
5. **Order-independence (point 4) only helps if the marker actually falls
   inside the decoded window (Codex review, round 4, fresh evidence).** A
   reordered document with a large member (e.g. a populated
   ``per_library_snapshots``) ahead of ``artifact_type`` could push the
   marker past a small fixed prefix even though point 4's scan would have
   recognized it at any position *within* that prefix. Originally answered
   by asking ``bounded_decoded_prefix`` for a much larger window
   (:data:`MARKER_SCAN_BYTES`, 1 MiB -- matching ``snapshot_io``'s own
   escalating-retry ceiling) *unconditionally* instead of its 4 KiB sniff
   default -- superseded by point 9 below, which requests that large
   window only when a smaller one first proves inconclusive, after that
   unconditional version turned out to have its own failure mode.
6. **The candidate key token must be *decoded*, not compared by its raw
   spelling (Codex review, round 5, fresh evidence).** A conforming JSON
   producer may escape the key (``"artifact\\u005ftype"`` for
   ``artifact_type``) without changing what it means --
   ``load_bundle_facts()`` accepts it fine, since ordinary JSON decoding
   collapses the escape either way. Comparing the raw token bytes against
   the literal spelling ``b'"artifact_type"'`` would reject that
   (technically valid, if unnecessary) escaped key. Answered by
   :func:`decode_json_string_token`, the same ``json.loads``-based
   decoding the marker's *value* already went through from the start --
   now shared by both halves of the check instead of only the value half.
7. **A byte sequence that is a genuine, complete, self-closing JSON object
   from the start can satisfy every token-level check above while never
   being a real ``BundleFacts`` document at all (Codex review, round 6,
   fresh evidence).** A ``.tar``/``.tar.gz`` release package's very first
   bytes are its first member's ``name`` field -- tar has no leading magic
   at all (point 8 below covers zip's own, structurally different variant
   of this same problem), so a member deliberately named e.g.
   ``{"artifact_type":"abicheck.bundle-
   facts"}`` decodes to bytes that are a complete, valid, gap-free JSON
   object satisfying points 1-6 with nothing left to catch it: the marker
   scan finds and returns the value before ever reaching the bytes that
   would reveal this isn't really a JSON document (the rest of the tar
   header). No token-level check can distinguish this from a real
   document, because the offending bytes genuinely *are* a valid encoding
   of exactly the shape being checked for -- the only distinguishing
   signal is structural, at the whole-file level, which token scanning
   never looks at. Answered by checking the decoded prefix against
   ``tarfile.is_tarfile()`` (:func:`decoded_prefix_is_a_real_tar_stream`)
   before the marker scan runs at all -- a genuine tar stream is
   structurally verifiable (checksummed header) and a real JSON document's
   bytes can never accidentally satisfy that check, so this closes the gap
   with no false-negative risk on a real stored-facts document, unlike
   point 2's reverted filename-suffix veto.
8. **The zip format's own variant of point 7 (Codex review, round 7,
   fresh evidence).** Unlike tar, zip *does* have a fixed magic
   (``PK\\x03\\x04``) -- but the format explicitly permits arbitrary bytes
   *before* the first local file header (this is how a self-extracting
   archive works), and a real zip reader (Python's own ``zipfile``, the
   ``WheelExtractor`` this repo already uses) locates entries via the
   central directory at the *end* of the file, not by requiring the magic
   at byte 0. A real ``.whl`` prepended with a crafted ``{"artifact_type":
   "abicheck.bundle-facts"}`` preamble is still a perfectly valid wheel to
   every real zip reader, fails :func:`_looks_like_stored_bundle_facts_
   archive`'s own byte-0-only G40 magic check, and falls through to the
   marker scan on that preamble -- the same class of gap as point 7, not
   closable by a leading-magic check since zip's own magic isn't
   necessarily at the front. Answered by
   :func:`path_is_a_real_zip_container`, ``zipfile.is_zipfile()`` on
   *path* itself (not a bounded prefix -- the central directory it needs
   may be well past :data:`MARKER_SCAN_BYTES` into a real, larger wheel).
9. **Requesting the large window directly (point 5's original fix) can
   itself fail to decode for a valid but pathologically-encoded compressed
   document (Codex review, round 8, fresh evidence).** ``gzip``/``zstd``
   support concatenated members, and a stream with unusually high
   per-member overhead (many tiny members) can need far more *compressed*
   input to produce :data:`MARKER_SCAN_BYTES` of *decoded* output than
   ``bounded_decoded_prefix``'s own raw-read escalation cap allows --
   failing outright even when the marker sits in the first few hundred
   decoded bytes, exactly where it normally does.
   ``load_bundle_facts()`` reads such a document fine (Python's ``gzip``
   handles concatenated members transparently); this classifier must not
   fail where the loader wouldn't. Answered by trying a much smaller
   decoded window first (:data:`SMALL_MARKER_SCAN_BYTES`, matching
   ``snapshot_io``'s own sniff default) -- a target low enough that even a
   badly-overhead-heavy stream reliably decodes -- and escalating to
   :data:`MARKER_SCAN_BYTES` only when :func:`root_level_artifact_type`
   reports that small probe as genuinely inconclusive (truncated before
   the root object closed), never merely because the marker wasn't found;
   see :func:`_marker_lookup_at_window` and that function's own return
   contract.
10. **``zipfile.is_zipfile()`` (point 8) reads *path*'s raw bytes, which for
   a genuinely gzip/zstd-compressed ``BundleFacts`` document are the
   *compressed* envelope, not JSON (Codex review, round 8, fresh
   evidence).** ``storage/bundle_archive.py`` already documents this exact
   risk for its own, structurally similar zip-tail probe: a gzip
   ``FEXTRA`` sub-field is attacker- (or, per that module's own comment,
   even innocuously-) controlled bytes that can coincidentally land an
   ``EOCD``-shaped sequence at the file's tail, which has nothing to do
   with whether the file is really a zip container. Point 11 below is the
   *actual* answer landed for this -- an earlier version of this point
   answered it by skipping the zip check whenever ``detect_snapshot_
   compression`` reported the candidate as already gzip/zstd-compressed,
   which round 9 (fresh evidence, immediately below) proved wrong in the
   opposite direction and superseded.
11. **The "skip when already compressed" answer to point 10 was itself
   exploitable, the other way (Codex review, round 9, fresh evidence).**
   ZIP's own "arbitrary bytes before the first local header" allowance
   (point 8) can *itself be* a complete, independently-decodable
   gzip/zstd stream -- a real ``.whl`` (with real members, a genuine
   central directory, everything a real zip reader needs) can legitimately
   carry such a stream as its own permitted preamble, with that stream
   decoding to JSON carrying the marker. Point 10's "skip whenever already
   gzip/zstd-compressed" gate would misidentify this exact file as "not
   zip-shaped, already gzip" and let the marker scan run on the decoded
   preamble, misclassifying a genuine wheel as stored facts (routing
   ``compare`` to the gzip ``BundleFacts`` loader, which then fails
   reading the zip's own trailing bytes as a bogus second gzip member).
   The real distinguishing signal was never "does the raw file start with
   a compression magic" -- it is whether the zip *has any real members* at
   all. Answered (at the time) by dropping the compression-based gate
   entirely and instead checking ``ZipFile(path).namelist()`` for at least
   one entry -- point 12 below is the refinement that answer itself
   needed, once "namelist() reports a name" turned out not to mean "a real
   member exists" either.
12. **A ``namelist()`` entry is still not proof of a real zip member
   (Codex review, round 10, fresh evidence).** Point 11's own claim that
   "there is no reason to embed a whole fake central directory just to
   satisfy this check" was wrong: ``FEXTRA`` has no practical size limit
   that would stop it (a full central-directory record is ~46 bytes plus a
   filename, comfortably inside ``FEXTRA``'s own 65535-byte ceiling), and
   a crafted one satisfies both ``is_zipfile()`` *and* ``namelist()`` with
   zero real zip content anywhere -- ``ZipFile.__init__`` only ever reads
   the *central directory* to build ``namelist()``/``infolist()``, never
   validating that a real local file header actually exists at each
   entry's declared offset (that validation happens lazily, only when a
   member is actually opened). Answered by opening (not reading -- this
   stays a cheap, bounded check regardless of a real member's content
   size) at least one declared member and requiring that to succeed: a
   genuine wheel's first member has a real local file header by
   construction, while a central-directory-only forgery has none anywhere
   in the byte stream and fails immediately with ``BadZipFile`` (see
   :func:`path_is_a_real_zip_container`'s own updated docstring for the
   full account).
13. **The scanner returned on the *first* root-level ``artifact_type``
   member, but a document with a duplicate root key decodes to its
   *last* occurrence (Codex review, round 10, fresh evidence).** Legal
   (if discouraged) JSON syntax, and ``json.loads()`` -- and therefore
   ``load_bundle_facts()`` -- resolves a duplicate key by simple
   last-assignment-wins dict construction. Returning immediately on the
   first match (as every earlier round's version of this scanner did)
   could disagree with that: a document like ``{"artifact_type":"other",
   "artifact_type":"abicheck.bundle-facts",...}`` decodes as real,
   accepted stored facts, but the scanner would have reported the first
   occurrence's value (``"other"``) and rejected it. Answered by
   *recording* rather than returning a matching value and continuing the
   scan, so the value in hand when the root object actually closes is
   whichever occurrence a real decoder would have kept -- this repo's own
   writer never emits a duplicate key at all, so the common case (a
   single occurrence) is unaffected either way.
14. **Point 13's own fix discarded an already-found marker for any
   document too large for the root object to close within either probe
   window (Codex review, round 11, fresh evidence, P1).** Recording
   rather than returning a match (point 13) means the scan must reach the
   root object's closing brace to know its answer is final -- but a real
   ``--bundle-facts-out`` document (the writer always emits the marker
   first; this has nothing to do with a duplicate key) can be larger than
   :data:`MARKER_SCAN_BYTES` before its own root closes, simply by having
   enough ordinary snapshot facts. Point 13's version of
   :func:`root_level_artifact_type` returned ``None`` whenever the scan
   ran out of prefix, discarding *last_marker_value* outright -- silently
   breaking every such document once neither probe window could reach the
   closing brace, even though the marker was found in the first few
   bytes. Answered by returning *last_marker_value* (not ``None``) from
   the inconclusive branch too, and having
   :func:`looks_like_stored_bundle_facts` use the widest probe's own
   answer as an explicit final fallback once no larger window is left to
   try, whether or not that probe was itself definitive -- restoring the
   pre-point-13 behavior for the (overwhelmingly common) undecorated
   case, while keeping point 13's duplicate-key correctness intact for
   any document small enough for either probe to actually prove it.
   Accepted, narrow residual gap of the same shape as the paragraph
   below: a duplicate marker positioned beyond :data:`MARKER_SCAN_BYTES`
   that would override this fallback answer is not detected.
15. **Point 14's own fallback still lost a real candidate when the
   *larger* probe fails to decode at all rather than merely running out
   before the root closed (Codex review, round 12, fresh evidence).** A
   valid ``BundleFacts`` document over :data:`SMALL_MARKER_SCAN_BYTES` but
   encoded with the same pathologically-high per-member compression
   overhead point 9 answered can decode fine at the small probe's target
   (finding the marker) while the *large* probe's own raw-read escalation
   fails outright at its own larger target, returning no information at
   all -- point 14's fallback used that probe's own answer unconditionally,
   coercing "no information" straight to ``False`` and silently discarding
   the small probe's real, already-found candidate. Answered by
   :func:`root_level_artifact_type`
   -- via :func:`_marker_lookup_at_window` -- reporting "no information"
   as ``None`` (not a hardcoded negative) for a total decode failure too,
   and having :func:`looks_like_stored_bundle_facts` fall back to the
   *small* probe's own candidate specifically when the large probe itself
   returns ``None`` -- a decode failure is a strictly worse data point
   than an actual (if inconclusive) scan, never a better one, so it must
   never be allowed to override an earlier probe's real finding.
16. **A prefix truncated in the middle of a long, otherwise-legal JSON
   string was misreported as a definitive structural violation, not an
   inconclusive truncation (Codex review, round 13, fresh evidence).**
   When the scanned window ends mid-string, the token scan's own
   *complete*-string pattern can't match at all (there is no closing
   quote left to find), so ``re.finditer`` would skip the opening quote
   as unmatched and resume scanning *inside* the string's own raw
   characters -- where a coincidental digit run, ``true``/``false``/
   ``null`` spelling, or literal structural character (realistic in a
   template string, a constant's spelling, or free-form text before the
   marker) could match a scalar or structural token in its own right,
   which point 6's own whitespace-only gap check would then very likely
   reject as a stray non-whitespace byte -- misreporting a genuinely
   valid document, simply truncated by the bounded-read window, as
   definitively invalid. Answered by adding a dedicated token-regex
   alternative that matches an unterminated string running all the way to
   the scanned prefix's own end as a single token
   (:data:`_JSON_STRUCTURE_TOKEN_RE`'s own updated docstring has the full
   account) -- no sub-content inside it is ever separately tokenized, so
   no spurious gap violation is possible, and the scan's own end-of-loop
   fallback correctly reports the answer as inconclusive instead.

17. **``path_is_a_real_zip_container``'s ``zipfile.ZipFile(path)``
   construction itself parsed the whole central directory unbounded,
   unlike this classifier's every other check (Codex review, round 14,
   fresh evidence).** An otherwise-ordinary operand (an ELF binary, say)
   with an appended ZIP whose central directory claims an absurd entry
   count or byte size reached that unbounded parse on *every* automatic
   operand classification, before either operand is even known to carry
   the marker at all -- unlike :func:`_looks_like_stored_bundle_facts_
   archive`, whose own ``ZipFile`` construction (via ``BundleArchiveReader.
   open``) already runs behind ``reject_absurd_central_directory``'s bounded
   preflight. Answered by applying that identical guard (same
   ``MAX_ARCHIVE_MEMBERS`` cap, same shared-fd race narrowing) to this
   general zip probe too, before it ever constructs ``ZipFile`` --
   see :func:`path_is_a_real_zip_container`'s own updated docstring.

18. **A duplicate root-level marker key whose later value isn't itself a
   JSON string left an earlier string match in place (Codex review, round
   15, fresh evidence).** Point 13's last-key-wins fix only updated
   ``last_marker_value`` when the *winning* value was itself a string
   token; a later occurrence whose value is a scalar (``null``, a bool, a
   number) or a nested object/array left whatever an earlier string
   occurrence had already recorded untouched, so a document like
   ``{"artifact_type":"abicheck.bundle-facts",...,"artifact_type":null}``
   -- for which a real decoder's final value is ``null``, never equal to
   the marker string -- still reported the first, superseded match.
   Answered by clearing ``last_marker_value`` whenever a duplicate key's
   value turns out not to be a string, for every non-string value shape
   (scalar and nested-container alike), not only the scalar case the
   review itself reported.

19. **Point 16's own unterminated-string fix had the identical gap one
   level down: a scan window ending right after a lone trailing backslash
   (Codex review, round 16, fresh evidence).** ``\\.`` requires both the
   backslash and its escaped byte, so a scanned prefix ending immediately
   after a bare backslash inside a string couldn't match that alternative
   either -- the same failure mode point 16 fixed for a string ending
   mid-character, one byte earlier. Answered by letting the unterminated-
   string alternative also consume a trailing lone backslash with no
   escaped byte left to pair it with (``_JSON_STRUCTURE_TOKEN_RE``'s own
   updated docstring has the full account).

20. **The scalar alternatives didn't cover every scalar the real decoder
   accepts (Codex review, round 17, fresh evidence).** ``json.dumps()``'s
   default ``allow_nan=True`` (this repo's own writer never overrides it)
   lets a real ``AbiSnapshot`` field holding a non-finite float serialize
   as the bare literals ``NaN``/``Infinity``/``-Infinity`` -- valid input
   to ``json.loads()`` (and ``load_bundle_facts()``) by that same default,
   but not one of this scan's own recognized scalar tokens, so one
   appearing anywhere in an otherwise-ordinary nested value created the
   same false structural-violation gap every other unrecognized-token
   finding above describes, discarding an already-found root marker.
   Answered by adding ``NaN``/``-?Infinity`` as their own token
   alternatives alongside ``true``/``false``/``null``.

**Residual, accepted gap (zip/gzip nesting, not chased further):** a gzip
stream's ``FEXTRA`` header sub-field (or, structurally analogously, a zstd
skippable frame) can embed not just a forged central-directory record
(point 12, closed) but a complete, minimal, genuinely valid zip member --
real local file header included -- entirely within the compression
envelope's own header metadata, before its compressed payload even begins.
Such a file satisfies :func:`path_is_a_real_zip_container`'s own
member-open check (Codex review, round 12, fresh evidence) while still
being a real, independently-decodable envelope carrying the actual
marker. Closing this fully would mean determining, for *any* raw byte
offset a candidate zip member's local file header claims, whether that
offset falls before or after the compression envelope's own true payload
boundary -- which this module's own bounded-prefix design (this module's
top docstring: "no full decompression, no JSON parse") cannot answer
without either fully decompressing the file (defeating that design for
every ordinary, non-adversarial invocation) or reaching into
``gzip``/``zstandard``'s own private framing internals in a way this
module has deliberately avoided elsewhere. Accepted as a routing-only
gap, not a security one: `load_bundle_facts()` remains the actual
validator of any document this classifier routes to it, and a document
constructed purely to exploit this nesting serves no purpose beyond
producing a confusing routing error, the same class of failure the
pre-marker-v1 gap above already accepts.

**Residual, accepted gap (same shape as the pre-marker-v1 gap above):** a
reordered document whose marker falls beyond :data:`MARKER_SCAN_BYTES` of
decoded content is still misclassified as an ordinary snapshot -- a fixed
window can shrink this gap but, by construction, cannot close it for an
unbounded document without abandoning the "bounded read, no full parse"
discipline this whole module exists to keep. 1 MiB of *decoded* prefix
ahead of the marker is far beyond what any plausible reordering of a real
``--bundle-facts-out`` document produces (the writer itself always emits
the marker first; the only realistic trigger is a third-party re-
serialization, not this tool's own output), so this is accepted rather
than chased further.

**Residual, accepted gap (a stored BundleFacts document supplied through a
FIFO/named pipe as OLD_INPUT, Codex review, round 18, fresh evidence):** the
removed ``--old-bundle-facts`` flag always called
:func:`~abicheck.serialization.load_bundle_facts` directly, which reads its
input exactly once and is therefore safe against a single-producer FIFO (see
``tests/test_bundle_archive.py::TestSniffBundleArchiveFormatNonRegularSource
::test_end_to_end_load_through_a_fifo_with_format_auto``, unaffected by this
change). :func:`looks_like_stored_bundle_facts` itself is equally FIFO-safe
-- ``path.is_file()`` rejects a FIFO immediately, before any read is
attempted (verified: this function returns ``False`` for a FIFO in native
time, never blocks) -- but a ``False`` classification sends the operand
through ``compare``'s *ordinary* live-input resolution
(``abicheck.workflows.input_resolution.resolve_input``) instead, and that
path was never FIFO-safe to begin with: verified directly (a background
writer thread + a live 8s ``SIGALRM`` deadline) that ``resolve_input()``
hangs indefinitely on FIFO input carrying a stored BundleFacts document --
and identically for an *ordinary* ``AbiSnapshot`` JSON document supplied the
same way, with zero relation to BundleFacts or this module. That hang is a
pre-existing property of ``resolve_input()``'s own multi-probe, multiple-
separate-``open()`` detection sequence (binary-magic sniff, then a
independent text-format sniff, then the real snapshot load, each reopening
*path* on its own) against a stream that can only be consumed once -- this
module's own change does not create that hang, it only stops diverting one
specific input shape (a FIFO-supplied stored BundleFacts document) around
it. Closing this for real would mean either teaching ``resolve_input()``
itself to spool a non-regular source once and reuse the spooled bytes
across every probe (a general fix belonging to that module, well outside
automatic *BundleFacts* operand classification), or having this module
spool and fully parse a non-regular OLD_INPUT during classification and
thread the already-parsed result through to
``compare_bundle_facts.dispatch()`` instead of letting it reopen *path* a
second time (a public-contract change to this module's ``bool``-returning
classification and to the CLI dispatch layer that consumes it) -- either is
a genuine, separately-scoped feature addition, not a narrow fix to this
change. Accepted as a known limitation: no CLI-level test or documentation
ever established FIFO-supplied `compare` operands (BundleFacts or
otherwise) as a guaranteed capability before this PR, and the removed
flag's own FIFO-safety was an incidental side effect of always calling
:func:`load_bundle_facts` directly, not a deliberately maintained contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .bundle_compare_operand_marker import (
    MARKER_SCAN_BYTES,
    SMALL_MARKER_SCAN_BYTES,
    decode_json_string_token,
    decoded_prefix_is_a_real_tar_stream,
    path_is_a_real_zip_container,
    root_level_artifact_type,
)


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


def _marker_lookup_at_window(path: Path, n: int) -> tuple[bool | None, bool]:
    """One probe of :func:`looks_like_stored_bundle_facts`'s marker check
    at decoded-prefix size *n*. Returns ``(is_stored, definitive)``:
    *is_stored* is ``True``/``False`` for the *last* root-level marker
    match seen so far (matching ``json.loads()``'s own last-key-wins
    duplicate handling), or ``None`` if none was seen at all -- either
    because the scan never found one, or because *n* couldn't be decoded
    at all (Codex review, round 12, fresh evidence: these two ``None``
    cases share the same meaning, "no information from this probe," and
    must be treated alike by the caller rather than one of them silently
    acting as a negative classification). *definitive* mirrors
    :func:`root_level_artifact_type`'s own meaning -- ``False`` only when
    a larger *n* might still change the answer (a genuinely truncated
    decode, whether or not a candidate was already found); a decode
    failure is itself definitive *for this n* (retrying at the same size
    would just fail the same way), but still carries no information, so a
    non-``None`` *is_stored* does **not** imply ``definitive`` is ``True``
    (round 11) and a ``None`` *is_stored* does **not** imply the document
    is unclassifiable (round 12) -- the caller decides whether to trust an
    inconclusive or absent candidate as a final fallback once it has no
    larger window left to try (see :func:`looks_like_stored_bundle_facts`).
    """
    from ..snapshot_io import bounded_decoded_prefix

    prefix = bounded_decoded_prefix(path, n)
    if prefix is None:
        # Cannot decode any prefix of this size at all. Not the same as a
        # confirmed absence: for a large n this can happen even when a
        # smaller n decoded fine (a valid but pathologically-encoded
        # compressed document -- point 9's own many-tiny-members scenario,
        # scaled up until even the large window's own raw-read escalation
        # cap can't decode anything at all) -- silently discarding an
        # earlier, smaller probe's own real finding by treating this as a
        # negative classification broke exactly that document (Codex
        # review, round 12, fresh evidence). ``None`` here carries no
        # information either way; "needs a bigger window" doesn't apply
        # (bounded_decoded_prefix already escalates its own raw read
        # internally before giving up), but *definitive* stays ``True``
        # since retrying at this exact *n* would just fail the same way --
        # the caller's own fallback to a smaller probe's candidate (see
        # :func:`looks_like_stored_bundle_facts`) is what actually
        # resolves this, not widening further.
        return None, True
    if decoded_prefix_is_a_real_tar_stream(prefix):
        # A genuine tar stream can never be a real BundleFacts document
        # (see decoded_prefix_is_a_real_tar_stream's own docstring) --
        # ruled out before the marker scan even runs, so a tar member
        # deliberately named to look like the marker is never trusted.
        return False, True
    value, definitive = root_level_artifact_type(prefix)
    if value is None:
        return (False if definitive else None), definitive
    # *value* is still a raw JSON string token (quotes and any backslash
    # escapes intact) -- decode it the same way json.loads() would rather
    # than comparing escaped bytes to an unescaped constant, so a
    # (technically valid, if unnecessary) escaped spelling still matches.
    from ..bundle_facts import BUNDLE_FACTS_ARTIFACT_TYPE

    decoded_value = decode_json_string_token(value)
    # *definitive* is propagated as-is, not hardcoded True (Codex review,
    # round 11, fresh evidence): root_level_artifact_type() can now return
    # a non-None *value* -- the last marker seen so far -- even when the
    # scan was truncated before the root object closed (its own
    # duplicate-key-safe "best candidate" contract), so a match here is
    # not automatically final; the caller decides whether to trust it or
    # escalate to a larger window.
    return decoded_value == BUNDLE_FACTS_ARTIFACT_TYPE, definitive


def looks_like_stored_bundle_facts(path: Path) -> bool:
    """Cheap, safe classification of *path* as a stored
    :class:`~abicheck.bundle_facts.BundleFacts` document -- either shape
    (plain/compressed JSON, or the G40 zip archive).

    ``False`` for anything that is not a regular file (a directory), or
    that this module's bounded prefix reader cannot decode at all (corrupt,
    or a format ``snapshot_io`` doesn't recognize). A real package archive
    (deb/rpm/conda) is not special-cased by *filename* -- the depth-scoped
    marker scan (this module's own docstring, points 3-4) already cannot
    match one by content, so excluding it by suffix would only reintroduce
    point 2's false-negative on a genuine stored-facts document with a
    package-like filename suffix. Tar- and zip-based archives (wheels
    included) are the exceptions, each ruled out by structural *content*
    rather than filename (points 7-8,
    :func:`decoded_prefix_is_a_real_tar_stream` and
    :func:`path_is_a_real_zip_container`) -- filename-blind checks, so
    neither carries point 2's false-negative risk. Never raises, never
    fully decompresses or JSON-parses a
    plain-JSON candidate -- see this module's own docstring for why. A
    ``True`` answer still leaves full validation to the ordinary
    bundle-facts read path (``load_bundle_facts`` / ``bundle_facts_from_
    dict``); this only decides *routing*.
    """
    if not path.is_file():
        return False
    if _looks_like_stored_bundle_facts_archive(path):
        return True
    if path_is_a_real_zip_container(path):
        # Already ruled out being the one legitimate zip-shaped encoding
        # (the G40 archive, just above) -- any other zip container with
        # real members reaching here (a wheel, possibly with a crafted or
        # even a genuinely compressed preamble -- see this function's own
        # docstring, round 9) is never a real BundleFacts document.
        # Deliberately *not* gated on "is this candidate already gzip/zstd-
        # compressed" (an earlier round's fix, reverted): that gate was
        # itself wrong the other way -- a real wheel can legitimately carry
        # a complete, independently-decodable compressed stream as its own
        # permitted zip preamble, which would have been misidentified as
        # "already gzip, not zip" and let through. Checking for real
        # members (not just a bare is_zipfile() verdict) is what correctly
        # separates that genuine case from round 8's own coincidental,
        # zero-member EOCD collision without needing any such gate at all.
        # Checked before decoding a prefix at all, since this check needs
        # the true end of the file, not a bounded window of it (see
        # path_is_a_real_zip_container's own docstring).
        return False
    # Two-phase probe (Codex review, round 8, fresh evidence): try a small
    # decoded window first, escalating to MARKER_SCAN_BYTES only when that
    # small probe was genuinely inconclusive (truncated before the root
    # object closed), never merely because the marker wasn't found in it.
    # Requesting the large window directly (as an earlier round did)
    # created its own failure mode: for a valid but pathologically-encoded
    # compressed document (many tiny concatenated gzip members, each
    # carrying real per-member overhead), decoding the first
    # MARKER_SCAN_BYTES of *output* can require more *compressed* input
    # than bounded_decoded_prefix's own internal raw-read escalation cap
    # allows, failing outright even though the marker sits in the first
    # few hundred bytes -- a small probe's much lower output target avoids
    # that failure mode entirely for the overwhelmingly common case (the
    # marker near the front). The larger decode still runs for any
    # ordinary document -- stored bundle facts or not -- whose small probe
    # doesn't close its own root object within SMALL_MARKER_SCAN_BYTES
    # (CodeRabbit review: this is not rare, an ordinary AbiSnapshot larger
    # than the small window escalates too), not only for the reordered-
    # document scenario this module's own docstring, point 5, names as the
    # motivating case -- what the small probe actually saves is the
    # failure mode above, not the escalation itself.
    is_stored, definitive = _marker_lookup_at_window(path, SMALL_MARKER_SCAN_BYTES)
    if definitive:
        return bool(is_stored)
    small_probe_candidate = is_stored
    is_stored, _definitive = _marker_lookup_at_window(path, MARKER_SCAN_BYTES)
    # Whether or not *this* probe was itself definitive, its own answer is
    # the best one available -- no larger window is left to try, so it is
    # used as the final fallback rather than discarded (Codex review,
    # round 11, fresh evidence). A real --bundle-facts-out document with
    # the marker first (the ordinary case for any sufficiently large
    # library, nothing to do with a duplicate key) can exceed even
    # MARKER_SCAN_BYTES before its own root object closes; treating that
    # as "inconclusive, so not stored" silently broke every such document.
    if is_stored is None:
        # The large probe carries no information of its own -- either it
        # never saw a candidate before running out, or it couldn't decode
        # any prefix of that size at all (a pathologically-encoded
        # compressed document whose per-member overhead defeats even the
        # large window's own raw-read escalation, even though the small
        # window decoded fine -- Codex review, round 12, fresh evidence).
        # A larger window normally reprocesses everything a smaller one
        # saw, but a *decode failure* is a strictly worse data point than
        # an actual (if inconclusive) scan, not a better one -- falling
        # back to the small probe's own candidate here, instead of
        # coercing this "no information" answer straight to False, is
        # what keeps round 11's own fix from being undone by exactly the
        # failure mode round 11 was itself answering.
        is_stored = small_probe_candidate
    # Accepted, narrow residual gap, same shape as the reordered-marker
    # gap this module's own docstring already documents: a duplicate
    # marker positioned beyond MARKER_SCAN_BYTES that would override
    # either probe's own candidate is not detected.
    return bool(is_stored)


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
