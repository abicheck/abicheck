# Copyright 2026 Nikolay Petrov
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

"""Baseline-set resolution (ADR-047 §4/§6, G30 P1.2).

A *baseline-set* is what ``actions/baseline`` produces: ``manifest.json``
plus one ``.abicheck.json`` snapshot per library — and, for a bundle-scoped
baseline (ADR-047 §8's S14 correction, staged by a future G30 P1.6 change to
``actions/baseline``), a ``binaries/`` directory of each member's real ELF
binary, since ``abicheck/bundle.py``'s ``build_bundle_snapshot()`` skips
non-ELF inputs and cannot read a bundle's old side from JSON snapshots alone.

This module is the shared reader/resolver ``actions/resolve-baseline`` uses
(and any future bundle-mode ``check-target`` call would reuse, per the G30
plan's "extract a shared helper rather than duplicating the schema/digest-
check code" note): parse a baseline-set directory's ``manifest.json`` and
resolve ``channel × target/bundle × profile`` down to one of ADR-047 §6's
five typed failure outcomes, or success — **never** a compatibility verdict.

Reads ``manifest.json``'s raw JSON directly with defensive ``.get()`` access,
mirroring ``actions/baseline/build_manifest.py``'s own reading philosophy for
snapshot files (applied one level up, to the manifest itself) — a corrupt or
hand-edited manifest.json produces a structured resolve outcome, never a
Python traceback. Pure: reads files, never runs a tool or fetches anything
(fetching from a baseline channel's storage backend is the calling
workflow's job, per ADR-047 §10).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import serialization
from ..elf_metadata import parse_elf_metadata

#: The only ``manifest_version`` ``actions/baseline/build_manifest.py`` has
#: ever emitted. A resolver that doesn't recognize the value on a real
#: manifest reports ``stale_schema`` instead of guessing at an unfamiliar
#: shape.
SUPPORTED_MANIFEST_VERSIONS = frozenset({1})

#: Filename ``actions/baseline`` writes the baseline-set descriptor to,
#: inside a baseline-set directory. ADR-047 §3's "Filename reconciliation"
#: note: ``baseline-set.json`` is this ADR's schema/doc term for the file's
#: *content*, not a different on-disk name — the real file stays
#: ``manifest.json``, unchanged from what ``actions/baseline`` already
#: produces today.
BASELINE_MANIFEST_FILENAME = "manifest.json"

#: Subdirectory (relative to a baseline-set directory) a bundle-scoped
#: baseline stages member ELF binaries into (ADR-047 §6/§8 S14 correction).
#: Not populated by ``actions/baseline`` yet (G30 P1.6) — a hand-authored
#: fixture directory is how this module's bundle resolution is exercised
#: until then, the same "defines the contract, no producer yet" scoping
#: G30 P1.1 used for ``build-output.json``.
BASELINE_BINARIES_DIRNAME = "binaries"

# Keys that vary between two dumps/replays of otherwise ABI-identical
# content -- timestamps, source-file mtimes, and wall-clock/cache-state
# counters -- so a stable content hash must strip all of them, not hash raw
# file bytes. Ported verbatim from actions/baseline/build_manifest.py's own
# private copy of this list (which now imports compute_snapshot_content_hash
# below instead of keeping its own): this is the ONE place both the
# baseline-set producer and this resolver's digest-verification check
# compute a snapshot's stable content hash, so they can never silently drift
# apart and disagree on what "unchanged content" means.
_VOLATILE_TOP_LEVEL_KEYS = ("created_at", "source_mtime", "source_mtime_epoch")
_VOLATILE_BUILD_SOURCE_MANIFEST_KEYS = ("created_at",)
_VOLATILE_BUILD_SOURCE_PACK_KEYS = ("path_hint",)
_VOLATILE_COVERAGE_KEYS = (
    "cache_lookup_s",
    "extract_s",
    "link_s",
    "elapsed_s",
    "cache_misses",
    "cache_hits",
    "extractor_jobs",
)
_VOLATILE_MANIFEST_COVERAGE_ROW_KEYS = ("detail", "elapsed_s")
_VOLATILE_MANIFEST_EXTRACTOR_ROW_KEYS = ("detail", "started_at", "finished_at")


def _strip_row_keys(rows: Any, volatile_keys: tuple[str, ...]) -> Any:
    if not isinstance(rows, list):
        return rows
    return [
        {k: v for k, v in row.items() if k not in volatile_keys}
        if isinstance(row, dict)
        else row
        for row in rows
    ]


def strip_volatile_snapshot_fields(raw: dict[str, Any]) -> dict[str, Any]:
    """Strip fields that vary run-to-run without the snapshot's actual
    ABI/source-fact content changing (see the constants above) -- the
    stable-content view :func:`compute_snapshot_content_hash` hashes."""
    stable = dict(raw)
    for key in _VOLATILE_TOP_LEVEL_KEYS:
        stable.pop(key, None)

    build_source_pack = stable.get("build_source_pack")
    if isinstance(build_source_pack, dict):
        build_source_pack = dict(build_source_pack)
        for key in _VOLATILE_BUILD_SOURCE_PACK_KEYS:
            build_source_pack.pop(key, None)
        stable["build_source_pack"] = build_source_pack

    build_source = stable.get("build_source")
    if isinstance(build_source, dict):
        build_source = dict(build_source)

        manifest = build_source.get("manifest")
        if isinstance(manifest, dict):
            manifest = dict(manifest)
            for key in _VOLATILE_BUILD_SOURCE_MANIFEST_KEYS:
                manifest.pop(key, None)
            if "coverage" in manifest:
                manifest["coverage"] = _strip_row_keys(
                    manifest["coverage"], _VOLATILE_MANIFEST_COVERAGE_ROW_KEYS
                )
            if "extractors" in manifest:
                manifest["extractors"] = _strip_row_keys(
                    manifest["extractors"], _VOLATILE_MANIFEST_EXTRACTOR_ROW_KEYS
                )
            build_source["manifest"] = manifest

        source_abi = build_source.get("source_abi")
        if isinstance(source_abi, dict):
            source_abi = dict(source_abi)
            coverage = source_abi.get("coverage")
            if isinstance(coverage, dict):
                coverage = dict(coverage)
                for key in _VOLATILE_COVERAGE_KEYS:
                    coverage.pop(key, None)
                source_abi["coverage"] = coverage
            build_source["source_abi"] = source_abi

        stable["build_source"] = build_source

    return stable


def compute_snapshot_content_hash(raw: dict[str, Any]) -> str:
    """The per-artifact ``sha256`` ``manifest.json`` records for a snapshot
    (``actions/baseline/build_manifest.py``) -- hashes the *stable* view
    (volatile fields stripped), not the raw file bytes, so re-dumping
    ABI-identical content on a different run/host doesn't change the digest.
    """
    stable = strip_volatile_snapshot_fields(raw)
    return hashlib.sha256(
        json.dumps(stable, sort_keys=True).encode("utf-8")
    ).hexdigest()


class ResolveOutcome:
    """ADR-047 §6's ``resolve-baseline`` outcome taxonomy, plus ``RESOLVED``.

    Kept as plain string constants (not an ``enum.Enum``) so a Python caller
    and the Action's ``outcome`` output (a bare string written to
    ``GITHUB_OUTPUT``) share one literal vocabulary with no serialization
    step in between.
    """

    RESOLVED = "resolved"
    NOT_FOUND = "not_found"
    AMBIGUOUS = "ambiguous"
    WRONG_PROFILE = "wrong_profile"
    STALE_SCHEMA = "stale_schema"
    INCOMPATIBLE_EVIDENCE = "incompatible_evidence"


#: Every outcome value :func:`resolve_target`/:func:`resolve_bundle` can
#: return — the six branches of ADR-047 §6's table (five failure rows plus
#: the resolved/success case).
ALL_OUTCOMES = frozenset(
    {
        ResolveOutcome.RESOLVED,
        ResolveOutcome.NOT_FOUND,
        ResolveOutcome.AMBIGUOUS,
        ResolveOutcome.WRONG_PROFILE,
        ResolveOutcome.STALE_SCHEMA,
        ResolveOutcome.INCOMPATIBLE_EVIDENCE,
    }
)


@dataclass
class BaselineArtifact:
    """One ``manifest.json`` ``artifacts[]`` entry (``build_manifest.py``).

    Only the fields this module's resolution logic actually reads — not a
    full mirror of every key ``build_manifest.py`` writes (``git_commit``,
    ``created_at``, ``build_id``, ``dump_provenance``, ...), which stay
    whatever the manifest happens to carry and are never round-tripped
    through this dataclass.
    """

    library: str = ""
    artifact: str = ""
    snapshot: str = ""
    #: Path (relative to the baseline-set directory, e.g.
    #: ``"binaries/libpvxs.so.1.5"``) to this member's staged ELF binary —
    #: only present for a bundle-scoped baseline (ADR-047 §8 S14). Empty for
    #: an ordinary (non-bundle) baseline-set entry.
    binary: str = ""
    #: sha256 of the *snapshot's* stable content (``build_manifest.py``'s
    #: ``compute_snapshot_content_hash(raw)``) -- verified against
    #: ``snapshot`` by :func:`_snapshot_digest_issue`.
    sha256: str = ""
    #: sha256 of the staged *binary's* raw bytes -- a separate field from
    #: ``sha256`` above and verified against ``binary`` by
    #: :func:`_binary_digest_issue`, deliberately never conflated with it:
    #: the snapshot and binary are different files with unrelated content,
    #: so reusing one recorded digest for both would compare a JSON
    #: snapshot's hash against an ELF file's hash and (mis)report every
    #: bundle member as a digest mismatch. Empty (the default) for any
    #: manifest that doesn't record one -- today that's every manifest
    #: ``build_manifest.py`` produces, since it has no bundle-binary-staging
    #: step yet (Codex review).
    binary_sha256: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> BaselineArtifact:
        return cls(
            library=str(d.get("library") or ""),
            artifact=str(d.get("artifact") or ""),
            snapshot=str(d.get("snapshot") or ""),
            binary=str(d.get("binary") or ""),
            sha256=str(d.get("sha256") or ""),
            binary_sha256=str(d.get("binary_sha256") or ""),
        )


@dataclass
class BaselineManifest:
    """Parsed ``manifest.json`` (``actions/baseline/build_manifest.py``)."""

    manifest_version: int | None = None
    project_ref: str = ""
    profile: str = ""
    snapshot_schema: int | None = None
    fact_set: dict[str, Any] | None = None
    artifacts: list[BaselineArtifact] = field(default_factory=list)

    def artifact_for(self, library: str) -> BaselineArtifact | None:
        for entry in self.artifacts:
            if entry.library == library:
                return entry
        return None

    def artifact_count_for(self, library: str) -> int:
        """How many ``artifacts[]`` rows declare this library.

        A real ``actions/baseline``-produced manifest can never have more
        than one (``run.sh``'s own input validation already rejects a
        duplicate library name before anything is dumped), so >1 here means
        a hand-edited or corrupted manifest -- :func:`artifact_for` would
        otherwise silently return whichever duplicate happens to appear
        first, letting ``resolve_target``/``resolve_bundle`` report
        ``resolved`` against an arbitrarily-picked one (Codex review).
        """
        return sum(1 for entry in self.artifacts if entry.library == library)


def load_baseline_manifest(baseline_dir: Path | str) -> BaselineManifest | None:
    """Read ``<baseline_dir>/manifest.json``.

    Returns ``None`` if the file doesn't exist — the ordinary "no baseline
    set here" case a caller turns into :data:`ResolveOutcome.NOT_FOUND`, not
    an exception. Raises ``ValueError`` if the file exists but is not a
    readable JSON object: a genuinely corrupt manifest is a different
    problem than "no baseline published yet" and must not be silently
    treated the same way.
    """
    path = Path(baseline_dir) / BASELINE_MANIFEST_FILENAME
    if not path.is_file():
        return None
    try:
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
    # OSError -- e.g. a permission error, or the file disappearing between
    # the is_file() check above and open() (a restored archive/cache race)
    # -- is raised by open() itself, before the inner JSON decode even
    # starts; must be caught here too, or a manifest that exists but can't
    # actually be read escapes this function's documented ValueError
    # contract as an unhandled exception (Codex review).
    except OSError as exc:
        raise ValueError(f"{path} could not be read: {exc}") from exc
    # UnicodeDecodeError (raised by the text-mode read itself, e.g. a
    # truncated/binary-garbage manifest) is a ValueError subclass, not a
    # json.JSONDecodeError -- must be caught alongside it, or a corrupt
    # manifest with invalid UTF-8 bytes escapes as an unhandled
    # exception instead of this function's documented ValueError
    # contract (Codex review).
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object.")

    artifacts_raw = data.get("artifacts")
    artifacts = (
        [BaselineArtifact.from_dict(a) for a in artifacts_raw if isinstance(a, dict)]
        if isinstance(artifacts_raw, list)
        else []
    )
    manifest_version = data.get("manifest_version")
    snapshot_schema = data.get("snapshot_schema")
    fact_set = data.get("fact_set")
    return BaselineManifest(
        manifest_version=manifest_version
        if isinstance(manifest_version, int)
        else None,
        project_ref=str(data.get("project_ref") or ""),
        profile=str(data.get("profile") or ""),
        snapshot_schema=snapshot_schema if isinstance(snapshot_schema, int) else None,
        fact_set=fact_set if isinstance(fact_set, dict) else None,
        artifacts=artifacts,
    )


@dataclass
class ResolveResult:
    """The result of one :func:`resolve_target`/:func:`resolve_bundle` call."""

    outcome: str
    message: str
    #: ``True`` only for the :data:`ResolveOutcome.NOT_FOUND` bootstrap case
    #: (``required=False`` and no baseline set exists yet) — an advisory,
    #: non-fatal outcome. ``False`` for every other outcome, including a
    #: ``required=True`` ``not_found``, which is a hard failure.
    bootstrap: bool = False
    manifest_path: str | None = None
    #: ``kind: target`` only.
    snapshot_path: str | None = None
    #: ``kind: bundle`` only.
    binaries_dir: str | None = None
    binary_paths: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.outcome == ResolveOutcome.RESOLVED

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable form of this result.

        The ``resolve-baseline`` composite Action (this PR) prints its
        fields individually via ``resolve_baseline.py``'s ``_print_outputs``
        rather than going through this method -- ``to_dict()`` is exposed
        for a future direct-Python caller (e.g. the not-yet-built
        ``check-target`` composite Action, G30 P1.3) that wants the whole
        result as one JSON-serializable object instead of discrete
        ``GITHUB_OUTPUT`` keys.
        """
        return {
            "outcome": self.outcome,
            "message": self.message,
            "bootstrap": self.bootstrap,
            "manifest_path": self.manifest_path or "",
            "snapshot_path": self.snapshot_path or "",
            "binaries_dir": self.binaries_dir or "",
            "binary_paths": dict(self.binary_paths),
        }


def _not_found_result(required: bool, message: str) -> ResolveResult:
    return ResolveResult(
        outcome=ResolveOutcome.NOT_FOUND,
        message=message,
        bootstrap=not required,
    )


def _load_manifest_or_result(
    baseline_dir: Path, required: bool
) -> tuple[BaselineManifest, None] | tuple[None, ResolveResult]:
    """Shared ``manifest.json`` load for :func:`resolve_target`/:func:`resolve_bundle`.

    Converts every way a load can fail into a typed :class:`ResolveResult`
    instead of letting any of them escape as a raw exception or collapse
    into the wrong outcome:

    - ``baseline_dir`` itself does not exist becomes
      :data:`ResolveOutcome.NOT_FOUND` (with the usual bootstrap split) --
      the legitimate "nothing published for this channel yet" case.
    - ``baseline_dir`` exists but has no ``manifest.json`` inside it (e.g.
      an empty/partial ``actions/cache`` restore, or a stripped artifact
      download) becomes :data:`ResolveOutcome.AMBIGUOUS`, not
      ``not_found`` -- a directory a caller went to the trouble of staging
      is a different, more concerning failure than "no baseline exists
      yet" and must not silently bootstrap a `required: false` caller to a
      green run with zero comparison performed (Codex review).
    - a manifest that exists but is corrupt/malformed (not valid JSON, or
      not a JSON object) -- a different failure than either of the above --
      becomes :data:`ResolveOutcome.STALE_SCHEMA`, since this resolver
      cannot understand the shape it's looking at. Without this, a corrupt
      ``manifest.json`` (a truncated download, a hand edit) would raise an
      unhandled ``ValueError`` all the way out of ``resolve_baseline.py``,
      breaking the Action's typed-outcome contract for exactly the kind of
      real baseline-resolution failure ADR-047 §6 exists to name.
    """
    if not baseline_dir.is_dir():
        return None, _not_found_result(
            required, f"No baseline-set found at {baseline_dir} (path does not exist)."
        )
    try:
        manifest = load_baseline_manifest(baseline_dir)
    except ValueError as exc:
        return None, ResolveResult(
            outcome=ResolveOutcome.STALE_SCHEMA,
            message=(
                f"{baseline_dir / BASELINE_MANIFEST_FILENAME} exists but "
                f"could not be read as a baseline-set manifest: {exc} -- "
                "treated as an unrecognized/unparseable schema, the same as "
                "a manifest_version this resolver doesn't understand."
            ),
        )
    if manifest is None:
        return None, ResolveResult(
            outcome=ResolveOutcome.AMBIGUOUS,
            message=(
                f"{baseline_dir} exists but does not contain a "
                f"{BASELINE_MANIFEST_FILENAME} -- this looks like an "
                "empty/partial cache restore or stripped artifact "
                "directory, not simply an unpublished baseline (a "
                "baseline-path that does not exist at all is the "
                "not_found/bootstrap case)."
            ),
        )
    return manifest, None


#: Known aliases between ADR-047 section 2's build-output.json
#: `evidence_producer.kind` vocabulary (`"wrapper"`/`"replay"`/
#: `"clang-plugin"` -- also `actions/collect-facts/run.sh`'s own `producer`
#: input values) and what a real baseline's `fact_set.producer` actually
#: records today: the *extractor implementation's* self-reported id, a
#: different vocabulary that was never reconciled with the ADR's. Verified
#: against the real producers -- only two exist today:
#: `abicheck/buildsource/source_extractors/clang.py`'s `ClangExtractor`
#: stamps `"abicheck-cc-clang-extractor"` for *both* the wrapper and replay
#: collection strategies, and `contrib/abicheck-clang-plugin/
#: AbicheckFactsPlugin.cpp:3101` stamps `"abicheck-clang-plugin"` regardless
#: of collect-facts' own `"clang-plugin"` producer name.
#:
#: **Known, accepted limitation, not an oversight (Codex review):** because
#: there is no third extractor, this check genuinely cannot distinguish
#: "wrapper" from "replay" evidence via fact_set.producer alone -- both
#: alias to the one real value, so a wrapper-produced baseline resolves for
#: a "replay"-declared candidate and vice versa, and that specific
#: cross-mismatch is NOT caught. The alternative -- aliasing "wrapper" and
#: "replay" only to themselves -- was considered and rejected: since no real
#: fact_set ever records the literal string "wrapper" or "replay", that
#: would make every real wrapper/replay resolution report
#: incompatible_evidence unconditionally, rejecting 100% of legitimate
#: source-depth checks to guard against one narrow cross-mismatch. A correct
#: fix needs `fact_set` to record the collection strategy as its own field
#: (plumbed through `dump --sources`/`--build-info`, the wrapper, and
#: replay) -- a schema change to `abicheck/buildsource/source_abi.py`
#: outside this Action's/module's scope, not a resolver-side guess. Extend
#: this table if/when a new extractor's producer id needs a public alias,
#: not by guessing at an unverified mapping.
_PRODUCER_ALIASES: dict[str, frozenset[str]] = {
    "wrapper": frozenset({"wrapper", "abicheck-cc-clang-extractor"}),
    "replay": frozenset({"replay", "abicheck-cc-clang-extractor"}),
    "clang-plugin": frozenset({"clang-plugin", "abicheck-clang-plugin"}),
}


def _evidence_incompatibility(
    manifest: BaselineManifest, candidate_evidence_producer: dict[str, Any] | None
) -> str | None:
    """ADR-047 §6's ``incompatible_evidence`` check.

    Compares the baseline's recorded ``fact_set.producer`` (``build_manifest
    .py``'s own ADR-038 C.8-derived identity) against the candidate build's
    ``build-output.json`` ``evidence_producer.kind`` (ADR-047 §2:
    ``{"kind", "tool", "version"}``), via :data:`_PRODUCER_ALIASES` since the
    two are different vocabularies today. Only compares when *both* sides
    declare an evidence identity — a plain header/binary-depth check on
    either side has nothing to compare and is never penalized for a producer
    mismatch that doesn't actually affect it.

    Deliberately does **not** also compare ``evidence_producer.version``
    against ``fact_set.producer_version`` — re-raised in later review as a
    "scanner/recipe version desync" gap; re-verified against **both** real
    producers before staying with this decision, not just the one checked
    originally. ADR-047 §2's own example styles ``evidence_producer.version``
    as a package release version (``"0.x.y"``), but ``fact_set
    .producer_version`` is an independent internal producer-build version in
    both cases that exist: ``CLANG_EXTRACTOR_VERSION`` (``"0.7"`` today, per
    ``source_extractors/clang.py``) for the Python clang extractor, and
    ``kPluginVersion`` (``contrib/abicheck-clang-plugin/
    AbicheckFactsPlugin.cpp``) for the C++ plugin — neither corresponds to a
    package release number. Comparing the two directly would reject nearly
    every real resolution on a coincidental mismatch between two
    incommensurable version schemes, which is worse than not checking at all
    — a real version-compatibility check needs a producer-side fix
    (recording the same identity in both places, e.g. build-output.json's
    emitter stamping the real producer-build version instead of the package
    version), not a resolver-side guess at a mapping that doesn't exist
    anywhere in the codebase today.
    """
    if not candidate_evidence_producer:
        return None
    candidate_kind = str(candidate_evidence_producer.get("kind") or "")
    if not candidate_kind:
        return None
    baseline_fact_set = manifest.fact_set
    if not baseline_fact_set:
        return None
    baseline_producer = str(baseline_fact_set.get("producer") or "")
    if not baseline_producer:
        return None
    aliases = _PRODUCER_ALIASES.get(candidate_kind, frozenset({candidate_kind}))
    if baseline_producer not in aliases:
        return (
            f"baseline's evidence producer is {baseline_producer!r} but the "
            f"candidate build's evidence producer is {candidate_kind!r} -- "
            "comparing source-depth evidence across different producers "
            "(e.g. wrapper vs. replay) is an infrastructure incompatibility, "
            "not an ABI finding (ADR-047 section 6)."
        )
    return None


def _schema_and_profile_check(
    manifest: BaselineManifest, profile: str, manifest_path: str
) -> ResolveResult | None:
    """Shared ``stale_schema``/``wrong_profile`` checks for target and bundle
    resolution -- returns ``None`` when both pass."""
    if manifest.manifest_version not in SUPPORTED_MANIFEST_VERSIONS:
        return ResolveResult(
            outcome=ResolveOutcome.STALE_SCHEMA,
            message=(
                f"baseline-set manifest_version {manifest.manifest_version!r} "
                "is not one this resolver understands (supported: "
                f"{sorted(SUPPORTED_MANIFEST_VERSIONS)}) -- upgrade the "
                "resolve-baseline Action, or regenerate the baseline-set "
                "with a compatible actions/baseline version."
            ),
            manifest_path=manifest_path,
        )
    if manifest.profile != profile:
        return ResolveResult(
            outcome=ResolveOutcome.WRONG_PROFILE,
            message=(
                f"baseline-set was built for profile {manifest.profile!r}, "
                f"not the requested {profile!r} -- never compare across "
                "profiles."
            ),
            manifest_path=manifest_path,
        )
    # manifest_version above is this resolver's own manifest.json shape;
    # snapshot_schema is the *separate* schema of the .abicheck.json
    # snapshots it references (serialization.SCHEMA_VERSION) -- a baseline
    # built by a newer abicheck than this checkout's installed reader would
    # otherwise still report `resolved` here (only manifest_version was
    # checked) and fail opaquely in the later compare step instead of
    # surfacing the typed stale_schema outcome resolve-baseline exists to
    # give callers (Codex review). A missing snapshot_schema (an
    # older/hand-authored manifest) has nothing to compare, so it no-ops,
    # same as the digest checks below.
    if (
        manifest.snapshot_schema is not None
        and manifest.snapshot_schema > serialization.SCHEMA_VERSION
    ):
        return ResolveResult(
            outcome=ResolveOutcome.STALE_SCHEMA,
            message=(
                f"baseline-set's snapshot_schema {manifest.snapshot_schema!r} "
                "is newer than this checkout's installed reader supports "
                f"(up to schema_version {serialization.SCHEMA_VERSION}) -- "
                "upgrade abicheck before resolving against this baseline-set."
            ),
            manifest_path=manifest_path,
        )
    return None


def _snapshot_digest_issue(
    target: str, snapshot_path: Path, expected_sha256: str
) -> tuple[str, str] | None:
    """Verify a resolved snapshot is well-formed, not a newer schema than
    this reader understands, and (when the manifest recorded one) matches
    its digest -- ``None`` when every check passes, else ``(outcome,
    message)``.

    The JSON-shape check (readable, valid JSON, a JSON object) always runs,
    even when the manifest recorded no ``sha256`` (an older/hand-authored
    manifest): without it, a truncated/corrupted/non-JSON snapshot file
    would still resolve as ``resolved`` purely because a file with the
    right name exists on disk, for any baseline-set whose manifest happens
    to have no recorded digest -- letting ``compare`` consume garbage
    old-side content instead of getting this resolver's typed ``ambiguous``
    outcome (Codex review). The digest comparison itself stays conditional
    on ``expected_sha256`` being present, since older manifests never
    recorded one.

    The schema-version check also always runs, reading the *snapshot's
    own* ``schema_version`` -- distinct from ``_schema_and_profile_check``,
    which only looks at the manifest's aggregate ``snapshot_schema`` field.
    An older/hand-authored manifest with no ``snapshot_schema`` has nothing
    for that check to compare, but the snapshot file itself always carries
    its own ``schema_version``; without checking it here too, a
    forward-schema snapshot would still resolve as ``resolved`` and fail
    opaquely in the later ``compare`` step instead of returning this
    resolver's typed ``stale_schema`` outcome (Codex review). Returns the
    ``stale_schema`` outcome for that case; every other issue here returns
    ``ambiguous``, matching this function's previous behavior.
    """
    try:
        with snapshot_path.open(encoding="utf-8") as fh:
            raw = json.load(fh)
    # UnicodeDecodeError (the text-mode read itself, e.g. the snapshot was
    # replaced by non-UTF-8/binary garbage) is a ValueError subclass, not a
    # json.JSONDecodeError -- must be caught alongside it, or exactly the
    # corrupted-baseline case this check exists to catch escapes as an
    # unhandled exception instead of this typed ambiguous outcome (Codex
    # review).
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return ResolveOutcome.AMBIGUOUS, (
            f"target {target!r}'s snapshot {snapshot_path.name!r} could not "
            f"be read to verify its digest: {exc} -- the baseline-set is "
            "corrupt or was truncated."
        )
    if not isinstance(raw, dict):
        return ResolveOutcome.AMBIGUOUS, (
            f"target {target!r}'s snapshot {snapshot_path.name!r} does not "
            "contain a JSON object -- the baseline-set is corrupt."
        )
    schema_version = raw.get("schema_version")
    if (
        isinstance(schema_version, int)
        and schema_version > serialization.SCHEMA_VERSION
    ):
        return ResolveOutcome.STALE_SCHEMA, (
            f"target {target!r}'s snapshot {snapshot_path.name!r} has "
            f"schema_version {schema_version!r}, newer than this resolver "
            f"understands (up to schema_version "
            f"{serialization.SCHEMA_VERSION}) -- upgrade abicheck before "
            "resolving against this baseline-set."
        )
    if not expected_sha256:
        return None
    actual_sha256 = compute_snapshot_content_hash(raw)
    if actual_sha256 != expected_sha256:
        return ResolveOutcome.AMBIGUOUS, (
            f"target {target!r}'s snapshot {snapshot_path.name!r} content "
            f"digest does not match the manifest (expected "
            f"{expected_sha256!r}, got {actual_sha256!r}) -- the baseline-"
            "set is corrupt, was tampered with, or was truncated/replaced "
            "after the manifest was written."
        )
    return None


def _binary_digest_issue(
    member: str, binary_path: Path, expected_sha256: str
) -> str | None:
    """Verify a resolved bundle member's staged binary against the
    manifest's recorded digest -- ``None`` when it matches (or the manifest
    recorded no digest to check).

    Takes ``artifacts[].binary_sha256``, a field distinct from
    ``artifacts[].sha256`` (the one :func:`_snapshot_digest_issue` checks
    for a target's snapshot): a bundle-scoped manifest row can carry both a
    ``snapshot`` and a ``binary`` field at once, so the two need separate
    recorded digests -- reusing one field for both would compare a JSON
    snapshot's content hash against an ELF binary's raw-byte hash and
    (mis)report every bundle member as corrupt, since the two will never
    coincidentally match (Codex review). No producer populates
    ``binary_sha256`` for a staged binary yet (G30 P1.6, not built here), so
    this is the contract this resolver defines, not one it validates
    against real output. Unlike a snapshot's content hash, a binary's
    digest is a plain whole-file SHA-256 -- no volatile-field stripping
    needed, since dumper.py's timestamp-stamping doesn't apply to an
    unmodified ELF file. Without this, a truncated/tampered staged binary
    would still resolve purely because a file with the right name exists
    under binaries/.
    """
    if not expected_sha256:
        return None
    try:
        h = hashlib.sha256()
        with binary_path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
    except OSError as exc:
        return (
            f"bundle member {member!r}'s staged binary {binary_path.name!r} "
            f"could not be read to verify its digest: {exc} -- the "
            "baseline-set is corrupt or was truncated."
        )
    actual_sha256 = h.hexdigest()
    if actual_sha256 != expected_sha256:
        return (
            f"bundle member {member!r}'s staged binary {binary_path.name!r} "
            f"content digest does not match the manifest (expected "
            f"{expected_sha256!r}, got {actual_sha256!r}) -- the baseline-"
            "set is corrupt, was tampered with, or was truncated/replaced "
            "after the manifest was written."
        )
    return None


_ELF_MAGIC = b"\x7fELF"


def _not_elf_issue(member: str, binary_path: Path) -> str | None:
    """Check whether ``build_bundle_snapshot()`` would actually keep
    *binary_path* -- ``None`` when it would, a problem string otherwise.

    ``build_bundle_snapshot()`` (``abicheck/bundle.py``) silently skips any
    staged input that isn't a real, parseable ELF file rather than erroring,
    so a non-ELF file staged under ``binaries/`` (e.g. a JSON snapshot
    placed at the wrong path) -- or a truncated/corrupted one that still
    happens to start with the ELF magic and match its recorded digest --
    would otherwise resolve as ``resolved`` here and then silently vanish
    from the bundle-scoped comparison downstream, reporting success on a
    comparison that never actually consulted this member. A bare magic-byte
    sniff alone doesn't catch the truncated case, so this mirrors
    ``build_bundle_snapshot()``'s own skip criteria exactly (magic check,
    then a real parse, then the same "essentially empty" emptiness check)
    so a ``resolved`` outcome here actually predicts survival there (Codex
    review, two rounds).
    """
    try:
        with binary_path.open("rb") as fh:
            header = fh.read(len(_ELF_MAGIC))
    except OSError as exc:
        return (
            f"bundle member {member!r}'s staged binary {binary_path.name!r} "
            f"could not be read to verify it is an ELF file: {exc} -- the "
            "baseline-set is corrupt or was truncated."
        )
    if header != _ELF_MAGIC:
        return (
            f"bundle member {member!r}'s staged binary {binary_path.name!r} "
            "is not an ELF file (missing the \\x7fELF magic) -- "
            "build_bundle_snapshot() silently skips non-ELF inputs, so this "
            "member would otherwise vanish from the bundle comparison "
            "despite resolve-baseline reporting success."
        )
    try:
        meta = parse_elf_metadata(binary_path)
    except Exception as exc:
        return (
            f"bundle member {member!r}'s staged binary {binary_path.name!r} "
            f"starts with the ELF magic but could not be parsed as a valid "
            f"ELF file ({exc}) -- it is truncated or corrupted, and "
            "build_bundle_snapshot() would silently skip it, so this "
            "member would vanish from the bundle comparison despite "
            "resolve-baseline reporting success."
        )
    if meta is None or (
        not meta.soname and not meta.symbols and not meta.imports and not meta.needed
    ):
        return (
            f"bundle member {member!r}'s staged binary {binary_path.name!r} "
            "parses as an essentially empty ELF file (no soname, symbols, "
            "imports, or needed entries) -- build_bundle_snapshot() would "
            "silently skip it, so this member would vanish from the bundle "
            "comparison despite resolve-baseline reporting success."
        )
    return None


def _resolve_under_baseline_dir(baseline_dir: Path, rel: str) -> Path | None:
    """Resolve *rel* under *baseline_dir*, refusing an absolute path or an
    escape (e.g. ``"../../etc/passwd"``) -- ``None`` if refused.

    A ``manifest.json``'s ``snapshot``/``binary`` fields are untrusted
    content from a restored archive/cache entry (a hand-edited or corrupt
    manifest, or a compromised baseline artifact); ``Path``'s own ``/``
    operator silently *discards the left operand entirely* when the right
    side is an absolute path (a well-known pathlib gotcha), so an absolute
    or ``..``-escaping value must be checked explicitly rather than trusted
    -- otherwise a corrupt manifest could point a "resolved" snapshot/binary
    path at an arbitrary file outside the baseline-set, which a downstream
    ``compare`` would then silently read as the old side (Codex review).
    Mirrors :func:`~.build_output._resolve_under_root`'s identical guard for
    ``build-output.json``.
    """
    if Path(rel).is_absolute():
        return None
    candidate = (baseline_dir / rel).resolve()
    root_resolved = baseline_dir.resolve()
    if candidate != root_resolved and not candidate.is_relative_to(root_resolved):
        return None
    return baseline_dir / rel


def resolve_target(
    baseline_dir: Path | str,
    *,
    target: str,
    profile: str,
    required: bool = True,
    candidate_evidence_producer: dict[str, Any] | None = None,
) -> ResolveResult:
    """Resolve ``channel × target × profile`` (ADR-047 §6) to one snapshot.

    ``channel`` itself is not a parameter here: this module trusts the
    caller already selected the right physical ``baseline_dir`` for the
    requested channel (see ``actions/resolve-baseline/action.yml``'s
    ``baseline-path`` input doc) — this function only resolves *within* that
    directory.
    """
    baseline_dir = Path(baseline_dir)
    manifest, failure = _load_manifest_or_result(baseline_dir, required)
    if failure is not None:
        return failure
    assert manifest is not None
    manifest_path = str(baseline_dir / BASELINE_MANIFEST_FILENAME)

    schema_or_profile_failure = _schema_and_profile_check(
        manifest, profile, manifest_path
    )
    if schema_or_profile_failure is not None:
        return schema_or_profile_failure

    artifact = manifest.artifact_for(target)
    if artifact is None:
        known = sorted(a.library for a in manifest.artifacts if a.library)
        return ResolveResult(
            outcome=ResolveOutcome.AMBIGUOUS,
            message=(
                f"target {target!r} is not in this baseline-set's manifest "
                f"(known targets: {known})."
            ),
            manifest_path=manifest_path,
        )
    if manifest.artifact_count_for(target) > 1:
        return ResolveResult(
            outcome=ResolveOutcome.AMBIGUOUS,
            message=(
                f"target {target!r} has multiple artifacts[] entries in "
                "this baseline-set's manifest -- ambiguous which one is "
                "authoritative; a real actions/baseline-produced manifest "
                "never has duplicate library entries, so this manifest is "
                "hand-edited or corrupted."
            ),
            manifest_path=manifest_path,
        )

    incompatible = _evidence_incompatibility(manifest, candidate_evidence_producer)
    if incompatible:
        return ResolveResult(
            outcome=ResolveOutcome.INCOMPATIBLE_EVIDENCE,
            message=incompatible,
            manifest_path=manifest_path,
        )

    if not artifact.snapshot:
        return ResolveResult(
            outcome=ResolveOutcome.AMBIGUOUS,
            message=f"target {target!r}'s manifest entry has no snapshot filename.",
            manifest_path=manifest_path,
        )
    snapshot_path = _resolve_under_baseline_dir(baseline_dir, artifact.snapshot)
    if snapshot_path is None:
        return ResolveResult(
            outcome=ResolveOutcome.AMBIGUOUS,
            message=(
                f"target {target!r}'s manifest entry names snapshot "
                f"{artifact.snapshot!r}, which is an absolute path or "
                "escapes the baseline-set directory -- refusing to resolve "
                "it."
            ),
            manifest_path=manifest_path,
        )
    if not snapshot_path.is_file():
        return ResolveResult(
            outcome=ResolveOutcome.AMBIGUOUS,
            message=(
                f"target {target!r}'s manifest entry names snapshot "
                f"{artifact.snapshot!r}, but that file does not exist under "
                f"{baseline_dir}."
            ),
            manifest_path=manifest_path,
        )

    digest_issue = _snapshot_digest_issue(target, snapshot_path, artifact.sha256)
    if digest_issue:
        issue_outcome, issue_message = digest_issue
        return ResolveResult(
            outcome=issue_outcome,
            message=issue_message,
            manifest_path=manifest_path,
        )

    return ResolveResult(
        outcome=ResolveOutcome.RESOLVED,
        message=f"resolved target {target!r} at profile {profile!r}.",
        manifest_path=manifest_path,
        snapshot_path=str(snapshot_path),
    )


def resolve_bundle(
    baseline_dir: Path | str,
    *,
    bundle: str,
    members: list[str],
    profile: str,
    required: bool = True,
    candidate_evidence_producer: dict[str, Any] | None = None,
) -> ResolveResult:
    """Resolve ``channel × bundle × profile`` (ADR-047 §6/§8 S14 correction).

    Unlike :func:`resolve_target`, a bundle's resolution unit is not a single
    snapshot: ``abicheck/bundle.py``'s ``build_bundle_snapshot()`` builds its
    cross-library graph from real ELF binaries and explicitly skips non-ELF
    (including JSON snapshot) inputs, so this returns paths to every member's
    **staged binary** under the baseline-set's ``binaries/`` directory
    instead. Every listed *member* must have one, or the whole bundle
    resolution reports ``ambiguous`` — a partially-staged bundle baseline
    would otherwise silently produce a bundle report missing one member's
    old-side data.
    """
    baseline_dir = Path(baseline_dir)
    manifest, failure = _load_manifest_or_result(baseline_dir, required)
    if failure is not None:
        return failure
    assert manifest is not None
    manifest_path = str(baseline_dir / BASELINE_MANIFEST_FILENAME)

    schema_or_profile_failure = _schema_and_profile_check(
        manifest, profile, manifest_path
    )
    if schema_or_profile_failure is not None:
        return schema_or_profile_failure

    incompatible = _evidence_incompatibility(manifest, candidate_evidence_producer)
    if incompatible:
        return ResolveResult(
            outcome=ResolveOutcome.INCOMPATIBLE_EVIDENCE,
            message=incompatible,
            manifest_path=manifest_path,
        )

    binaries_dir = baseline_dir / BASELINE_BINARIES_DIRNAME
    # Tracks *why* each problem member was rejected, not just its name --
    # three genuinely different causes (no manifest entry, an escaping/
    # missing/mis-located binary, a digest mismatch) previously all
    # collapsed into one generic "have no staged binary" sentence, pointing
    # an operator diagnosing e.g. a corrupt-but-present binary at the wrong
    # fix (CodeRabbit review).
    problems: dict[str, str] = {}
    binary_paths: dict[str, str] = {}
    for member in members:
        # Check for duplicate artifacts[] entries before touching any
        # artifact-specific field: artifact_for() returns only the first
        # matching row, so if that row happens to lack .binary while a
        # later duplicate has one, checking artifact.binary first would
        # misreport "no staged binary declared" instead of the more
        # accurate duplicate-entry ambiguity -- silently depending on
        # manifest row order, the exact class of bug artifact_count_for was
        # introduced to eliminate. Mirrors resolve_target's ordering
        # (CodeRabbit review).
        if manifest.artifact_count_for(member) > 1:
            problems[member] = (
                "multiple artifacts[] entries in this baseline-set's "
                "manifest -- ambiguous which one is authoritative"
            )
            continue
        artifact = manifest.artifact_for(member)
        # Two distinct causes, not one generic message: "not in the
        # manifest at all" (a bundle-members typo, or a target never
        # staged into this baseline-set) points an operator at a
        # different fix than "in the manifest but has no binary field"
        # (a pre-P1.6 baseline-set that only ever staged snapshots) --
        # mirrors resolve_target's own not-in-manifest branch, which
        # already lists the known targets (code review).
        if artifact is None:
            known = sorted(a.library for a in manifest.artifacts if a.library)
            problems[member] = (
                f"not in this baseline-set's manifest (known targets: {known})"
            )
            continue
        if not artifact.binary:
            problems[member] = "no staged binary declared in the manifest"
            continue
        resolved = _resolve_under_baseline_dir(baseline_dir, artifact.binary)
        # The documented bundle contract is that every member's binary lives
        # under binaries_dir (the same directory the resolved binaries-dir
        # output advertises) -- accepting any relative path elsewhere in the
        # baseline-set would let binary-paths point outside binaries-dir
        # while the output still claims that directory holds every member,
        # and a downstream bundle compare using binaries-dir directly (as
        # opposed to the per-member binary-paths) would then miss it or
        # pick up an unrelated file (Codex review). Path.is_relative_to()
        # is true for a path relative to *itself* too, so a manifest entry
        # whose "binary" field is exactly "binaries" (equal to
        # BASELINE_BINARIES_DIRNAME) would satisfy is_relative_to() against
        # binaries_dir without actually being a child of it -- reject that
        # explicitly, not just non-containment, so binaries-dir can never
        # resolve to something other than a directory of member files
        # (Codex review, fourth round).
        if (
            resolved is None
            or resolved.resolve() == binaries_dir.resolve()
            or not resolved.resolve().is_relative_to(binaries_dir.resolve())
            or not resolved.is_file()
        ):
            problems[member] = (
                f"binary {artifact.binary!r} is missing, unreadable, or "
                f"outside {BASELINE_BINARIES_DIRNAME}/"
            )
            continue
        elf_issue = _not_elf_issue(member, resolved)
        if elf_issue:
            problems[member] = elf_issue
            continue
        digest_issue = _binary_digest_issue(member, resolved, artifact.binary_sha256)
        if digest_issue:
            problems[member] = digest_issue
            continue
        binary_paths[member] = str(resolved)

    if problems:
        detail = "; ".join(f"{m!r}: {problems[m]}" for m in sorted(problems))
        return ResolveResult(
            outcome=ResolveOutcome.AMBIGUOUS,
            message=(
                f"bundle {bundle!r} could not be resolved -- {detail} -- a "
                "bundle-scoped baseline must stage every member's ELF "
                "binary under a validated binaries/ directory (ADR-047 "
                "section 6/section 8 S14), not just its snapshot."
            ),
            manifest_path=manifest_path,
        )

    return ResolveResult(
        outcome=ResolveOutcome.RESOLVED,
        message=f"resolved bundle {bundle!r} ({len(binary_paths)} member(s)) at profile {profile!r}.",
        manifest_path=manifest_path,
        binaries_dir=str(binaries_dir),
        binary_paths=binary_paths,
    )
