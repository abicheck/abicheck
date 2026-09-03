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
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


#: Matches ``"artifact_type": "abicheck.bundle-facts"`` (any amount of
#: whitespace around the ``:``, matching how both ``json.dumps(...,
#: indent=2)`` -- the pretty-printed shape ``save_bundle_facts`` writes --
#: and a compact ``json.dumps(...)`` render it) anywhere in a decoded byte
#: string. Built once at import time from the real constant rather than
#: hand-duplicated, so a future rename of the artifact-type value can't
#: silently desync this pattern from what the writer actually emits.
def _artifact_type_marker_pattern() -> re.Pattern[bytes]:
    from ..bundle_facts import BUNDLE_FACTS_ARTIFACT_TYPE

    return re.compile(
        rb'"artifact_type"\s*:\s*"' + re.escape(BUNDLE_FACTS_ARTIFACT_TYPE.encode("ascii")) + rb'"'
    )


def looks_like_stored_bundle_facts(path: Path) -> bool:
    """Cheap, safe, marker-only classification of *path* as a stored
    :class:`~abicheck.bundle_facts.BundleFacts` document.

    ``False`` for anything that is not a regular file (a directory, a
    package archive -- those already have their own ``compare`` operand
    kinds) or that this module's bounded prefix reader cannot decode at all
    (corrupt, or a format ``snapshot_io`` doesn't recognize). Never raises,
    never fully decompresses or JSON-parses *path* -- see this module's own
    docstring for why. A ``True`` answer still leaves full validation to
    the ordinary bundle-facts read path (``load_bundle_facts`` /
    ``bundle_facts_from_dict``); this only decides *routing*.
    """
    if not path.is_file():
        return False
    from ..snapshot_io import bounded_decoded_prefix

    prefix = bounded_decoded_prefix(path)
    if prefix is None:
        return False
    return _artifact_type_marker_pattern().search(prefix) is not None


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
