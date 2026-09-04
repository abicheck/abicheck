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

"""``BundleFacts`` <-> JSON (de)serialization (G38 Phase 2).

Split out of ``serialization.py`` (ADR-061): ``BundleFacts`` is owned by
``bundle_facts.py``, which is classified ``workflows`` in
``architecture/modules.yaml`` -- keeping its own (de)serialization inside
``serialization.py`` produced a real ``storage -> workflows`` edge the
moment ``serialization.py`` itself was classified ``storage``.
``bundle_facts.py`` is already at its own 800-line production cap, so this
is a new sibling rather than growing that module -- the same
"oversized owner gets a sibling, not a bigger body" shape
``service_render.py``/``service_dump_pipeline.py`` already established for
``service.py``.

Historically these functions lived in ``serialization.py`` specifically
*because* neither ``bundle_facts.py`` nor ``serialization.py`` had a settled
ADR-061 layer yet, so importing one from the other risked a real cycle --
``storage/bundle_facts_validation.py``'s own module docstring records that
reasoning (and, as a workaround, duplicated ``validated_alias_map``/
``validated_filename_map`` rather than importing them). Now that
``bundle_facts.py`` is classified, the cycle resolves the ordinary way: this
module (``workflows``-shaped, alongside ``bundle_facts.py``) imports
``BundleFacts`` from ``bundle_facts.py`` and ``snapshot_to_dict``/
``snapshot_from_dict`` from ``serialization.py`` -- both allowed
``workflows -> *`` edges -- and nothing imports this module back at load
time, so the historical duplication in ``storage/bundle_facts_validation.py``
is retired in favor of calling its ``validated_alias_map``/
``validated_filename_map`` directly.

``serialization.py`` re-exports the four public names below unchanged, so
every existing ``from abicheck.serialization import bundle_facts_to_dict``
(etc.) call site -- including the test suite -- is unaffected.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from .errors import IncompatibleSnapshotSchemaError

if TYPE_CHECKING:
    from .bundle_facts import BundleFacts
    from .snapshot_io import SnapshotWriteResult


def looks_like_bundle_facts_document(data: Any) -> bool:
    """Classify a decoded JSON object as a stored :class:`~abicheck.
    bundle_facts.BundleFacts` document (CLI cleanup phase two, PR I
    prerequisite).

    This is the strong discriminator `BUNDLE_FACTS_SCHEMA_VERSION`'s own
    docstring (`bundle_facts.py`) calls for -- a pure, read-only classifier
    over already-decoded JSON, with no side effects and no dependency on
    `bundle_facts_from_dict`'s own (more permissive) reading rules. Two
    tiers, in order:

    1. **Explicit marker**: `data["artifact_type"]` is present -- trusted
       outright, whichever way it answers. A document that names a
       *different* artifact type must never be reclassified as bundle
       facts just because it happens to also carry a
       `per_library_snapshots`-shaped key; the explicit marker is the whole
       point of having one.
    2. **Shape fallback** (true v1 documents only): `artifact_type` is
       absent *and* `schema_version` is absent, or *normalizes* (the same
       `int(...)` coercion :func:`bundle_facts_from_dict` applies, so
       `"1"`/`True` keep classifying exactly as they did before this
       marker existed -- Codex review, fresh evidence: an earlier,
       unnormalized comparison rejected a legitimate `"schema_version":
       "1"` baseline) to exactly `1` -- the only signal available before
       the marker existed (schema_version 2) is `per_library_snapshots`
       being present and mapping-shaped, mirroring
       `bundle_facts_from_dict`'s own mandatory-key check. This is a real,
       accepted false-positive surface (an unrelated document that
       happens to define that one key) inherited from the v1 format
       itself, not introduced here -- closing it further would mean
       rejecting genuine v1 baselines already persisted in users' CI,
       which `BUNDLE_FACTS_SCHEMA_VERSION`'s own docstring rules out. A
       document *explicitly* declaring `schema_version` 2+ but omitting
       the marker gets neither tier -- schema_version 2 is exactly where
       the marker became mandatory, so an explicit 2+ with no marker is
       malformed, not legacy, and must not silently pass through the
       fallback meant for documents that predate the marker's existence.

    Does not itself decode JSON, open a file, or validate the document
    beyond this one question -- a caller wanting the parsed
    :class:`~abicheck.bundle_facts.BundleFacts` (or its validation errors)
    still goes through :func:`bundle_facts_from_dict`."""
    from .bundle_facts import BUNDLE_FACTS_ARTIFACT_TYPE

    if not isinstance(data, dict):
        return False
    if "artifact_type" in data:
        return data.get("artifact_type") == BUNDLE_FACTS_ARTIFACT_TYPE
    if "schema_version" not in data:
        is_legacy_v1 = True
    else:
        try:
            # Same coercion bundle_facts_from_dict applies, so a legacy
            # document spelling schema_version as "1" or 1.0 classifies
            # identically to one spelling it as the bare int 1 (Codex
            # review). An explicit `null` is *not* the same as the key
            # being absent -- checking key presence rather than
            # `.get(...) is None` keeps that distinction, matching
            # bundle_facts_from_dict's own equivalent check (Codex
            # review, fresh evidence: `int(None)` is exactly the
            # TypeError this reader itself would raise for that value).
            is_legacy_v1 = int(data["schema_version"]) == 1
        except (TypeError, ValueError, OverflowError):
            # OverflowError: a JSON exponent like 1e999 decodes to float
            # inf, and int(inf) raises OverflowError rather than
            # TypeError/ValueError (Codex review, fresh evidence) -- this
            # pure classifier must answer False for malformed input, not
            # crash a future operand dispatcher calling it.
            is_legacy_v1 = False
    if not is_legacy_v1:
        return False
    return isinstance(data.get("per_library_snapshots"), dict)


def bundle_facts_to_dict(facts: BundleFacts) -> dict[str, Any]:
    """Serialize a :class:`~abicheck.bundle_facts.BundleFacts` to a
    JSON-able dict (G38 Phase 2).

    ``artifact_type`` is always :data:`~abicheck.bundle_facts.
    BUNDLE_FACTS_ARTIFACT_TYPE` -- the constant, not ``facts.artifact_type``.
    ``init=False`` keeps the field out of the constructor, but the dataclass
    isn't frozen, so ``facts.artifact_type = "other"`` after construction is
    still possible; reading the constant here (matching how
    ``write_bundle_facts_archive`` already writes its own marker) means a
    mutated instance still round-trips correctly instead of silently
    producing a document ``bundle_facts_from_dict`` would reject.
    ``schema_version`` is written the same way, for the same reason:
    ``facts.schema_version`` records whatever version a *loaded* document
    claimed (useful introspection -- "what version did this originate
    from"), but this function always emits the *current* shape (it just
    wrote the v2+-only ``artifact_type`` key unconditionally above), so a
    round-tripped v1 document must not still declare ``schema_version: 1``
    while carrying a v2 field -- that combination is exactly the malformed,
    self-contradictory document schema_version 2's own introduction was
    meant to make impossible (Codex review, fresh evidence)."""
    from .bundle_facts import BUNDLE_FACTS_ARTIFACT_TYPE, BUNDLE_FACTS_SCHEMA_VERSION
    from .bundle_manifest import manifest_to_dict
    from .serialization import snapshot_to_dict

    return {
        "artifact_type": BUNDLE_FACTS_ARTIFACT_TYPE,
        "schema_version": BUNDLE_FACTS_SCHEMA_VERSION,
        "variant_fingerprint": facts.variant_fingerprint,
        "per_library_snapshots": {
            name: snapshot_to_dict(snap)
            for name, snap in facts.per_library_snapshots.items()
        },
        "filesystem_aliases": {
            name: list(aliases) for name, aliases in facts.filesystem_aliases.items()
        },
        "library_filenames": dict(facts.library_filenames),
        "manifest": manifest_to_dict(facts.manifest) if facts.manifest else None,
    }


def bundle_facts_from_dict(d: dict[str, Any]) -> BundleFacts:
    """Inverse of :func:`bundle_facts_to_dict`.

    Rejects a container ``schema_version`` newer than this reader's own
    :data:`~abicheck.bundle_facts.BUNDLE_FACTS_SCHEMA_VERSION` outright,
    mirroring :func:`abicheck.serialization.snapshot_from_dict`'s hard
    rejection of a too-new-to-read-safely snapshot. Unlike that function's
    own warn-below/hard-reject-above-threshold split (justified there by
    many already-shipped versions with a documented, field-by-field
    forward-compatible history), ``BundleFacts`` has had exactly one shape
    so far -- there is no "this reader has no code path that looks for a
    field introduced after some known-safe version" nuance to draw a softer
    line at yet. Warn-and-continue would silently score a comparison
    against a newer container whose fields (e.g. a future per-variant
    comparability gate) this reader's ``compare_bundle_from_facts()``
    doesn't know to consult (Codex review).
    """
    from .bundle_facts import (
        BUNDLE_FACTS_ARTIFACT_TYPE,
        BUNDLE_FACTS_SCHEMA_VERSION,
        DEFAULT_VARIANT_FINGERPRINT,
        BundleFacts,
    )
    from .bundle_manifest import manifest_from_dict
    from .serialization import snapshot_from_dict
    from .storage.bundle_facts_validation import (
        validated_alias_map,
        validated_filename_map,
        validated_variant_fingerprint,
    )

    schema_version = int(d.get("schema_version", BUNDLE_FACTS_SCHEMA_VERSION))
    if schema_version > BUNDLE_FACTS_SCHEMA_VERSION:
        raise IncompatibleSnapshotSchemaError(
            f"Bundle facts schema_version {schema_version} is newer than this "
            f"abicheck (supports up to schema_version "
            f"{BUNDLE_FACTS_SCHEMA_VERSION}). Upgrade abicheck to read this "
            "bundle facts file."
        )
    # A true v1 document -- schema_version absent entirely, or *normalizing*
    # (the same `int(...)` coercion just above, so a pre-marker document
    # spelling it "1" or `True` keeps loading exactly as it did before this
    # marker existed -- Codex review, fresh evidence: a raw, unnormalized
    # comparison rejected a legitimate `"schema_version": "1"` baseline the
    # old `int(...)`-based reader accepted) to exactly `1` -- carries no
    # `artifact_type` key at all: defaults to the current value. A document
    # that *does* carry the key but names a different artifact type is
    # rejected outright rather than silently accepted: whoever built it
    # declared it as something else, and reading it as bundle facts anyway
    # would score a comparison against a document nobody asked to be read
    # this way (CLI cleanup phase two, PR I prerequisite -- the whole point
    # of the explicit marker is that it is trusted, not advisory). A
    # document explicitly declaring schema_version 2+ but omitting the key
    # gets neither default nor fallback: schema_version 2 is exactly where
    # the marker became mandatory, so an explicit 2+ with no marker is
    # malformed, not legacy -- silently defaulting it would let the
    # discriminator schema_version 2 exists to enforce be bypassed by the
    # exact documents it's meant to catch.
    # `artifact_type` is never passed into the `BundleFacts(...)` call below:
    # the field is `init=False` (always `BUNDLE_FACTS_ARTIFACT_TYPE`), and
    # every path that doesn't raise here has already proven the document's
    # own value equals that constant -- there is nothing left to carry
    # through (Codex review, fresh evidence: passing a caller-suppliable
    # value into an `init=False` field isn't even possible, so this also
    # keeps the constructor call honest about that).
    if "artifact_type" in d:
        given_artifact_type = d["artifact_type"]
        if given_artifact_type != BUNDLE_FACTS_ARTIFACT_TYPE:
            raise ValueError(
                f"bundle facts: unexpected artifact_type {given_artifact_type!r} "
                f"(expected {BUNDLE_FACTS_ARTIFACT_TYPE!r})"
            )
        # Codex review, fresh evidence (twice): even the *correct* marker
        # is self-contradictory on a document declaring a schema_version
        # below 2 -- artifact_type was added in schema_version 2, so no
        # genuinely-pre-marker document could ever carry it. The first cut
        # of this check only rejected exactly schema_version == 1, letting
        # 0, a negative value, or a fractional value int(...) normalizes
        # below 1 slip through unrejected; use < 2 so every such value is
        # covered, not just the one legacy encoding. A writer never
        # produces any of these combinations (bundle_facts_to_dict()
        # always pairs the marker with the current schema_version);
        # reaching here means a malformed or hand-edited document, not a
        # real legacy one.
        if schema_version < 2:
            raise ValueError(
                f"bundle facts: schema_version {schema_version} predates "
                "artifact_type (added in schema_version 2) -- such a "
                "document may not declare it"
            )
    else:
        # No try/except needed here (unlike looks_like_bundle_facts_document's
        # own copy of this check below): by this point `schema_version` is
        # already the normalized int the top-of-function `int(d.get(...))`
        # call produced -- if that hadn't been coercible, it would already
        # have raised before reaching here. "schema_version" absent from
        # *d* is the one case that int(...) call defaulted rather than
        # parsed, so it's checked for directly rather than re-deriving it
        # from the (already-defaulted, no-longer-"absent"-shaped)
        # `schema_version` value.
        is_legacy_v1 = "schema_version" not in d or schema_version == 1
        if not is_legacy_v1:
            raise ValueError(
                f"bundle facts: schema_version {schema_version} requires an "
                "'artifact_type' key (added in schema_version 2); none was given"
            )
    # "per_library_snapshots" is mandatory, not merely defaulted: a
    # malformed or unrelated JSON object (e.g. ``{}``) omitting it entirely
    # must not silently load as a valid, current-schema *empty* bundle --
    # a later compare_bundle_from_facts() would then score every new
    # library against an invented empty baseline instead of the caller
    # ever finding out the input was invalid (Codex review, fresh
    # evidence). A present-but-wrong-shaped value (not a mapping) is
    # rejected the same way, rather than raising an opaque AttributeError
    # out of the dict comprehension below.
    if "per_library_snapshots" not in d:
        raise ValueError(
            "bundle facts: missing required top-level 'per_library_snapshots' mapping"
        )
    raw_snapshots = d["per_library_snapshots"]
    if not isinstance(raw_snapshots, dict):
        raise ValueError(
            "bundle facts: 'per_library_snapshots' must be a mapping, got "
            f"{type(raw_snapshots).__name__}"
        )
    raw_manifest = d.get("manifest")
    return BundleFacts(
        schema_version=schema_version,
        variant_fingerprint=validated_variant_fingerprint(
            d.get("variant_fingerprint", DEFAULT_VARIANT_FINGERPRINT)
        ),
        per_library_snapshots={
            name: snapshot_from_dict(sd) for name, sd in raw_snapshots.items()
        },
        filesystem_aliases=validated_alias_map(d.get("filesystem_aliases", {})),
        library_filenames=validated_filename_map(d.get("library_filenames", {})),
        manifest=manifest_from_dict(raw_manifest) if raw_manifest is not None else None,
    )


def load_bundle_facts(
    path: str | Path, *, format: str = "auto", max_json_object_nodes: int | None = None
) -> BundleFacts:
    """Load a BundleFacts; see ``storage.bundle_facts_validation.load_bundle_facts_dispatch``
    for the ``format="auto"``/G40-archive dispatch and the ``max_json_object_nodes`` budget
    override."""
    from . import bundle_facts as _bundle_facts
    from .serialization import snapshot_from_dict
    from .snapshot_io import read_snapshot_text
    from .storage.bundle_facts_validation import load_bundle_facts_dispatch

    budget = (
        _bundle_facts.DEFAULT_MAX_JSON_OBJECT_NODES
        if max_json_object_nodes is None
        else max_json_object_nodes
    )
    return cast(
        "BundleFacts",
        load_bundle_facts_dispatch(
            path,
            format,
            read_snapshot_text=read_snapshot_text,
            maybe_read_bundle_facts_archive=_bundle_facts.maybe_read_bundle_facts_archive,
            bundle_facts_from_dict=bundle_facts_from_dict,
            snapshot_from_dict=snapshot_from_dict,
            max_json_object_nodes=budget,
        ),
    )


def save_bundle_facts(
    facts: BundleFacts,
    path: str | Path,
    *,
    format: str = "json",
    compression: str = "auto",
) -> SnapshotWriteResult:
    """Save *facts*; ``format="archive"`` writes G40's zip container, see
    ``bundle_facts.maybe_write_bundle_facts_archive`` (``compression`` is
    JSON-only; ``"auto"``/``"none"`` no-op for it, only ``"gzip"``/``"zstd"``
    reject -- Codex)."""
    from .bundle_facts import maybe_write_bundle_facts_archive
    from .serialization import snapshot_to_dict
    from .snapshot_io import SnapshotCompression, write_snapshot_text

    if format == "archive" and SnapshotCompression(compression) not in (
        SnapshotCompression.AUTO,
        SnapshotCompression.NONE,
    ):
        raise ValueError('compression= is JSON-only; format="archive" is always zstd')
    archived = maybe_write_bundle_facts_archive(
        facts, path, format, snapshot_to_dict=snapshot_to_dict
    )
    if archived is not None:
        return archived

    # sort_keys=True (unlike other writers here) would re-sort a manifest
    # entry's own instantiations dict, whose order IS the C++ template
    # argument order -- corrupting a "T, U" contract (Codex review).
    return write_snapshot_text(
        json.dumps(bundle_facts_to_dict(facts), indent=2),
        path,
        compression=SnapshotCompression(compression),
    )
