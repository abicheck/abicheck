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

"""Persisted ``BundleFacts`` field validators, its JSON container-node
budget check, and ``load_bundle_facts``'s full dispatch body, split out of
``bundle_facts.py``/``bundle_facts_serialization.py`` purely to stay under
``bundle_facts.py``'s 800-line production cap and
``bundle_facts_serialization.py``'s own leaf-module simplicity. Fits
`storage/`'s own ADR-061 D1 remit ("serialize snapshots/baselines, own
their schemas") and its `model`-only dependency rule: every function here
depends on nothing first-party but ``errors`` (a `public_root_surfaces`
exemption) and `storage.json_budget` (same package) --
``load_bundle_facts_dispatch`` takes its `bundle_facts.py`/`snapshot_io.py`/
`serialization.py` collaborators as injected callables instead of importing
them, since `storage`'s own `may_import: [model]` forbids importing any of
those three directly (each is `workflows`/unclassified, never `model`),
regardless of whether they are themselves classified. A leaf itself: no
import of ``bundle_facts.py``/``bundle_facts_serialization.py``/
``serialization.py``, so importing it introduces no cycle either way.

``validated_alias_map``/``validated_filename_map`` are the canonical
implementations -- ``bundle_facts_serialization.bundle_facts_from_dict``
imports them directly (ADR-061: `bundle_facts_serialization.py` is
`workflows`-classified, and `workflows -> storage` is allowed) rather than
keeping its own duplicate, which is what ``serialization.py``'s own
now-removed ``_validated_alias_map``/``_validated_filename_map`` used to be
before this module existed.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any


def load_bundle_facts_dispatch(
    path: str | Path,
    format: str,
    *,
    read_snapshot_text: Callable[[str | Path], str],
    maybe_read_bundle_facts_archive: Callable[..., Any],
    bundle_facts_from_dict: Callable[[dict[str, Any]], Any],
    snapshot_from_dict: Callable[[dict[str, Any]], Any],
    max_json_object_nodes: int,
) -> Any:
    """Full body of ``serialization.load_bundle_facts`` -- moved here (not
    left in that module) because ``serialization.py`` is an ADR-061
    no-growth-tracked legacy file (``architecture/debt.yaml``): this is a
    genuinely new, additive capability (a container-node budget on the
    plain-JSON path, matching the archive path), not a fix to code already
    there, so it belongs where growth is still allowed.

    Every collaborator (`read_snapshot_text`, `maybe_read_bundle_facts_
    archive`, `bundle_facts_from_dict`, `snapshot_from_dict`) is injected
    by the caller rather than imported here -- ``storage/`` may only import
    `model` (ADR-061), and none of `bundle_facts.py`/`snapshot_io.py`/
    `serialization.py` are classified into a layer yet, so importing any of
    them directly from a migrated `storage/` module would itself be an
    architecture violation. Caller resolves *max_json_object_nodes*'s
    ``None`` default (``bundle_facts.DEFAULT_MAX_JSON_OBJECT_NODES``) before
    calling, since that constant lives in one of those unclassified modules
    too.

    *max_json_object_nodes* is deliberately an aggregate, whole-document
    budget on this path, not a per-library one -- unlike the G40 archive
    path, where each library's blob is a separately zip-stored member and
    can be budget-checked independently before any of them are decoded.
    Splitting the plain-JSON document into per-library slices first would
    mean parsing the untrusted text before checking it, defeating the
    entire point of a pre-scan budget (Codex review: a multi-library
    document whose *individual* snapshots each fit the budget can still
    be rejected in aggregate here, though never the reverse). A caller
    with a legitimately large multi-library bundle has two ways around
    this: pass a larger *max_json_object_nodes*, or use ``format="archive"``
    for real per-library accounting."""
    import json as _json

    archived = maybe_read_bundle_facts_archive(
        path, format, snapshot_from_dict=snapshot_from_dict, max_json_object_nodes=max_json_object_nodes
    )
    if archived is not None:
        return archived
    raw_text = read_snapshot_text(path)
    check_bundle_facts_json_budget(
        raw_text.encode("utf-8"), max_json_object_nodes, path=path, description="bundle facts JSON"
    )
    return bundle_facts_from_dict(_json.loads(raw_text))


def check_bundle_facts_json_budget(
    raw: bytes,
    max_json_object_nodes: int,
    *,
    path: str | Path,
    description: str,
) -> None:
    """Shared container-node/nesting-depth budget check for BundleFacts
    JSON -- one implementation for both the G40 archive path's per-blob
    decode (``bundle_facts.read_bundle_facts_archive``) and
    ``serialization.load_bundle_facts``'s plain ``.json``/``.json.zst``
    path, which previously enforced no budget at all (Codex review, fresh
    evidence: identical bytes were checked one way and not the other).
    Raises :class:`~abicheck.errors.SnapshotError`; never decodes *raw*
    itself. A leaf function (only ``errors``/``json_budget``, both
    dependency-free) -- see this module's own docstring for why it lives
    here rather than in ``bundle_facts.py`` itself."""
    from ..errors import SnapshotError
    from .json_budget import (
        JsonContainerBudgetExceeded,
        JsonNestingTooDeepError,
        check_json_container_budget,
    )

    try:
        check_json_container_budget(raw, max_json_object_nodes)
    except JsonContainerBudgetExceeded:
        raise SnapshotError(
            f"{path}: {description} contains more than "
            f"{max_json_object_nodes} JSON containers -- refusing to decode "
            "(possible container-count amplification attack; pass a larger "
            "max_json_object_nodes if this is a known-large, trusted payload)"
        ) from None
    except JsonNestingTooDeepError:
        raise SnapshotError(f"{path}: {description} is too deeply nested to parse") from None


#: Self-describing marker for the G40 archive *container*'s own
#: ``manifest.json`` -- the archive-format counterpart of
#: ``bundle_facts.BUNDLE_FACTS_ARTIFACT_TYPE`` (CLI cleanup phase two, PR I
#: prerequisite: "The archive container ... is a separate axis and gets
#: the same treatment"). Required, not defaulted, on read by
#: :func:`validate_bundle_archive_artifact_type` below -- the archive
#: format has never shipped in a release, so there is no pre-existing
#: archive lacking it that a back-compat default would need to keep
#: readable. Lives here (not in ``bundle_facts.py``, unlike its plain-JSON
#: sibling) purely for that module's own 800-line production cap; it is
#: re-exported from there for callers that only know that module.
BUNDLE_ARCHIVE_ARTIFACT_TYPE = "abicheck.bundle-facts-archive"


def require_int_schema_version(value: Any, *, field: str, path: str | Path) -> int:
    """Validate a manifest integer field strictly -- a bare ``int()``
    coercion would silently truncate 1.9, accept True/False as 1/0 (bool
    is an int subclass), and leak a raw TypeError for None instead of this
    module's own :class:`~abicheck.errors.SnapshotError` contract (Codex
    review). Moved here from ``bundle_facts.read_bundle_facts_archive``
    for that module's own 800-line production cap."""
    from ..errors import SnapshotError

    if isinstance(value, bool) or not isinstance(value, int):
        raise SnapshotError(f"{path}: manifest {field} must be an integer, got {value!r}")
    return int(value)


def load_bundle_facts_blob_json(
    raw: bytes, *, max_json_object_nodes: int, path: str | Path, description: str
) -> Any:
    """Decode one G40 archive blob's JSON, translating every failure mode
    into this module's own error vocabulary -- neither invalid JSON nor a
    ``RecursionError`` may surface raw (mirrors ``read_manifest``'s own
    translation). The shared pre-scan (:func:`check_bundle_facts_json_budget`)
    bounds both container-node count and nesting depth before ``json.loads()``
    ever runs, since relying on that call's own ``RecursionError`` isn't
    portable (Python 3.14 parses 10,000 levels of ``[[[...]]]`` with none).
    Moved here from ``bundle_facts.read_bundle_facts_archive`` for that
    module's own 800-line production cap."""
    import json as _json

    from ..errors import SnapshotError

    check_bundle_facts_json_budget(raw, max_json_object_nodes, path=path, description=description)
    try:
        return _json.loads(raw)
    except (UnicodeDecodeError, ValueError) as exc:
        raise SnapshotError(f"{path}: {description} is not valid JSON: {exc}") from exc
    except RecursionError as exc:
        raise SnapshotError(f"{path}: {description} is too deeply nested to parse") from exc


def validate_bundle_archive_artifact_type(
    manifest: dict[str, Any],
    *,
    expected: str = BUNDLE_ARCHIVE_ARTIFACT_TYPE,
    path: str | Path,
) -> None:
    """Reject a G40 archive manifest whose ``artifact_type`` marker is
    missing or doesn't match *expected* -- the archive container's own
    counterpart to
    ``bundle_facts_serialization.looks_like_bundle_facts_document``'s
    plain-JSON marker check."""
    from ..errors import IncompatibleSnapshotSchemaError

    artifact_type = manifest.get("artifact_type")
    if artifact_type != expected:
        raise IncompatibleSnapshotSchemaError(
            f"{path}: manifest artifact_type {artifact_type!r} is not "
            f"{expected!r} -- not a BundleArchiveWriter-produced bundle "
            "facts archive."
        )


def validated_alias_map(raw: object) -> dict[str, tuple[str, ...]]:
    """Validate and convert a persisted ``filesystem_aliases`` mapping --
    rejects a non-mapping container, a non-list value, and a list with a
    non-string element, rather than silently iterating a stray string's
    characters into single-letter "aliases"."""
    if not isinstance(raw, dict):
        raise ValueError(
            f"bundle facts: 'filesystem_aliases' must be a mapping, got "
            f"{type(raw).__name__}"
        )
    aliases: dict[str, tuple[str, ...]] = {}
    for name, values in raw.items():
        if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
            raise ValueError(
                f"bundle facts: 'filesystem_aliases[{name!r}]' must be a list of "
                f"strings, got {values!r}"
            )
        aliases[name] = tuple(values)
    return aliases


def validated_filename_map(raw: object) -> dict[str, str]:
    """Validate and convert a persisted ``library_filenames`` mapping --
    rejects a non-string value instead of silently coercing it
    (``str(None)`` -> ``"None"``)."""
    if not isinstance(raw, dict):
        raise ValueError(
            f"bundle facts: 'library_filenames' must be a mapping, got "
            f"{type(raw).__name__}"
        )
    filenames: dict[str, str] = {}
    for name, filename in raw.items():
        if not isinstance(filename, str):
            raise ValueError(
                f"bundle facts: 'library_filenames[{name!r}]' must be a string, "
                f"got {filename!r}"
            )
        filenames[name] = filename
    return filenames
