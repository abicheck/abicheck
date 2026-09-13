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

"""The ``BundleFacts`` value type — the bundle-level counterpart to
:class:`~abicheck.model.snapshot.AbiSnapshot` for a whole multi-library
release (G38 Phase 2, amendment to ADR-023).

ADR-061 gap E: this is the value half of what used to be one flat
``bundle_facts.py`` conflating a value type, its persistence, and capture/
comparison orchestration. This module owns only the shape and construction
invariants of ``BundleFacts`` itself — what fields it has, what schema
version it declares, and that ``degraded_members`` never names a library
``per_library_snapshots`` doesn't carry. It does not read or write JSON
(``storage.bundle_facts_codec``/``storage.bundle_facts_archive``/
``storage.bundle_facts_package``), reconstruct a live comparable snapshot,
or run a comparison (``workflows.bundle_facts_capture``/
``workflows.bundle_facts_compare``) — see this package's own siblings for
the compatibility facade unifying all of these under their historical
import path.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from ..bundle_manifest import InstantiationManifest
from .snapshot import AbiSnapshot

#: Schema version for the persisted `BundleFacts` container itself --
#: independent of `AbiSnapshot.SCHEMA_VERSION` (each per-library snapshot
#: already carries its own), since the container's own shape (what fields
#: `BundleFacts` has) can evolve on its own timeline. Bumped to 2 for the
#: `BUNDLE_FACTS_ARTIFACT_TYPE` marker below; to 3 (ADR-065 D8) for the
#: decision-bearing `degraded_members` marker. This is the *reader's* max;
#: `document_schema_version` below chooses what a writer declares.
BUNDLE_FACTS_SCHEMA_VERSION = 3
#: What a document with no degraded member declares (a pre-S2 reader still
#: opens it); one *with* degraded members declares 3, so a reader that cannot
#: honor the marker rejects it instead of misreading an ELF-only stand-in.
BUNDLE_FACTS_BASE_SCHEMA_VERSION = 2


def document_schema_version(facts: BundleFacts) -> int:
    """The ``schema_version`` a writer declares for *facts* (see above)."""
    return (
        BUNDLE_FACTS_SCHEMA_VERSION
        if facts.degraded_members
        else BUNDLE_FACTS_BASE_SCHEMA_VERSION
    )


#: Self-describing document-type marker; see `storage.bundle_facts_codec.
#: looks_like_bundle_facts_document` for the classifier built on it.
BUNDLE_FACTS_ARTIFACT_TYPE = "abicheck.bundle-facts"

#: The fingerprint value used when no multibuild variant applies (every
#: caller today) -- G38 Phase 3 populates a real per-variant fingerprint;
#: Phase 2 only needs the field to always be present so a future
#: Phase-3-aware comparability check has something to compare against
#: unconditionally, never `None`/absent on an older-shaped facts file.
DEFAULT_VARIANT_FINGERPRINT = "default"


def require_degraded_members_known(
    degraded_members: Mapping[str, str],
    members: Iterable[str],
    *,
    what: str = "bundle facts",
) -> None:
    """Reject a ``degraded_members`` key naming no stored member: a marker
    on a misspelled or absent library would be re-keyed away by a later
    comparison and silently vanish, leaving the real members compared as
    complete evidence despite a persisted capture-failure signal (Codex
    review). Applied at :class:`BundleFacts` construction and, again, by
    every ``storage`` reader that validates a document's own
    ``degraded_members`` field before handing it to this constructor
    (``storage.bundle_facts_validation``, ``storage.variant_composition``,
    ``storage.import_bundle_facts``) -- a pure, dependency-free invariant
    of the value type itself, not a persistence concern, so it lives here
    rather than in ``storage`` (which those three modules import it from,
    same as any other ``storage -> model`` reference)."""
    unknown = sorted(set(degraded_members) - set(members))
    if unknown:
        raise ValueError(
            f"{what}: 'degraded_members' names {len(unknown)} library(ies) absent "
            f"from 'per_library_snapshots' ({', '.join(unknown)}) -- a capture-"
            "failure marker must name a stored member (ADR-065 D8)"
        )


@dataclass
class BundleFacts:
    """Serializable projection of everything a bundle-level comparison
    needs, decoupled from live ``.so`` files -- the bundle-level counterpart
    to :class:`~abicheck.model.snapshot.AbiSnapshot` for a single library.

    ``per_library_snapshots`` is mandatory, not optional: a bundle-level
    comparison's cross-DSO findings are each keyed off a *per-library*
    ``DiffResult``, so a ``BundleFacts`` carrying only resolution-graph-level
    data would give ``workflows.bundle_facts_compare.compare_bundle_from_facts``
    nothing to diff when the *old* side is a stored dump rather than a live
    directory.

    ``filesystem_aliases``/``library_filenames`` record, per library, the
    real on-disk soname spellings and basename captured while the files
    still existed, so the metadata-only reconstruction resolves ``DT_NEEDED``
    edges and SONAME skew without the filesystem (Codex review); empty for a
    caller that passed no real paths. ``artifact_type`` is ``init=False``,
    an invariant a caller cannot break by construction (Codex review).
    ``degraded_members`` may name stored members only, checked at
    construction so every reader and capture path shares the one rule."""

    def __post_init__(self) -> None:
        require_degraded_members_known(
            self.degraded_members, self.per_library_snapshots
        )

    schema_version: int = BUNDLE_FACTS_BASE_SCHEMA_VERSION
    variant_fingerprint: str = DEFAULT_VARIANT_FINGERPRINT
    per_library_snapshots: dict[str, AbiSnapshot] = field(default_factory=dict)
    manifest: InstantiationManifest | None = None
    filesystem_aliases: dict[str, tuple[str, ...]] = field(default_factory=dict)
    library_filenames: dict[str, str] = field(default_factory=dict)
    #: ADR-065 D8: ``{library: failure reason}`` for a member whose dump
    #: failed at capture (its snapshot is the ELF-only degradation).
    degraded_members: dict[str, str] = field(default_factory=dict)
    #: ADR-065 D2: the capture's own assertion that ``per_library_snapshots`` is
    #: the whole release; ``False`` (every pre-field document) proves nothing.
    inventory_complete: bool = False
    artifact_type: str = field(default=BUNDLE_FACTS_ARTIFACT_TYPE, init=False)


#: Default container-node budget for one blob's JSON decode -- json.loads()
#: has no cap on *node count*; many small containers under an ignored key
#: inflate real memory regardless of container shape (~150MB RSS from a 6MB
#: payload of ~2M empty objects; an array-only payload bypassed an
#: earlier, object-only cap identically -- both confirmed empirically).
#: See `storage.json_budget` for the shared object+array pre-scan (Codex).
#: Kept conservative (Codex review, PR #1174): a real oneDAL-scale blob
#: can need ~5.8-17M nodes (one-comparison-product.md §4.1/§3 #21), but
#: raising the *default* would widen every untrusted run's decode-bomb
#: ceiling. Set `resource_limits.max_bundle_facts_decode_nodes` instead.
DEFAULT_MAX_JSON_OBJECT_NODES = 1_000_000
