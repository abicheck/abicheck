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

**Two review-caught refinements on top of the plain marker scan (Codex,
PR #1042):**

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
3. **The marker match is anchored to the JSON document's own root object,
   not "anywhere in the decoded prefix" (Codex review, round 2, fresh
   evidence).** An unanchored search also matched a *nested* occurrence --
   not just a package member's embedded fixture (point 2's scenario,
   independently closed by anchoring alone: a compressed archive's
   decoded bytes never begin with ``{"artifact_type"`` at position 0, since
   every supported format's own framing -- a tar header block, a zip/RPM/
   deb magic -- always precedes any member content) but also an entirely
   ordinary, single ``AbiSnapshot`` whose own ``constants`` mapping happens
   to define a C constant literally named ``artifact_type`` with that exact
   string value, which JSON-serializes as a nested object elsewhere in the
   same document and does not make the document itself bundle facts.
   Requiring the marker to be the root object's *own* first field (the
   exact shape ``bundle_facts_to_dict`` always writes it in) rules out
   both. This is what makes point 2's ``is_package`` pre-check
   unnecessary, not merely one alternative fix for it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


#: Matches ``{"artifact_type": "abicheck.bundle-facts"`` anchored to the very
#: start of a decoded byte string (only whitespace may precede the ``{`` and
#: separate each token -- matching how both ``json.dumps(..., indent=2)``
#: -- the pretty-printed shape ``save_bundle_facts`` writes -- and a compact
#: ``json.dumps(...)`` render it), never merely *containing* the marker
#: text somewhere in a nested value (Codex review, round 2 -- see this
#: module's own docstring, point 3, for the two false-positive shapes an
#: unanchored search let through). Built once at import time from the real
#: constant rather than hand-duplicated, so a future rename of the
#: artifact-type value can't silently desync this pattern from what the
#: writer actually emits.
def _artifact_type_marker_pattern() -> re.Pattern[bytes]:
    from ..bundle_facts import BUNDLE_FACTS_ARTIFACT_TYPE

    return re.compile(
        rb'\A\s*\{\s*"artifact_type"\s*:\s*"'
        + re.escape(BUNDLE_FACTS_ARTIFACT_TYPE.encode("ascii"))
        + rb'"'
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


def looks_like_stored_bundle_facts(path: Path) -> bool:
    """Cheap, safe classification of *path* as a stored
    :class:`~abicheck.bundle_facts.BundleFacts` document -- either shape
    (plain/compressed JSON, or the G40 zip archive).

    ``False`` for anything that is not a regular file (a directory), or
    that this module's bounded prefix reader cannot decode at all (corrupt,
    or a format ``snapshot_io`` doesn't recognize). A real package archive
    (wheel/deb/rpm/tar/conda) is not special-cased -- the root-anchored
    marker match (this module's own docstring, point 3) already cannot
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
    from ..snapshot_io import bounded_decoded_prefix

    prefix = bounded_decoded_prefix(path)
    if prefix is None:
        return False
    return _artifact_type_marker_pattern().match(prefix) is not None


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
