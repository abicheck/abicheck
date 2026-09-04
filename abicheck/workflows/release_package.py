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

"""ADR-062 A1.7's workflows-layer half: turning a stored, possibly
multi-artifact `ProjectSnapshot` package directory into the
`old_map`/`new_map: dict[str, Path]` shape `cli_compare_release.py`'s
per-library fan-out already builds from a loose directory of `.so` files.

Split out from `project_snapshot_legacy.py` (storage-classified, may import
only `model` per `storage/AGENTS.md`) because matching a stored artifact to
a live directory's own filename/SONAME needs
`binary_utils._canonical_library_key` (`extract`-classified) -- a `storage
-> extract` edge `scripts/check_architecture.py` forbids, and would also
close a real `extract -> storage -> extract` responsibility cycle (`extract`
already depends on `storage`). `workflows` may import both `storage` and
`extract` (ADR-061's task-routing table), which is exactly what this
coordination needs: `project_snapshot_legacy.materialize_release_variant_
artifacts` does the storage-layer unpacking (returns `{artifact_id: (Path,
ArtifactRef)}`), and this module re-keys that by the same canonical
library-matching key a live directory operand's own `_build_match_map`
computes, so a stored-side map matches a live-side one for the same
library. A `frontends` command (`cli_resolve.py`/`cli_compare_release_
matrix.py`) reaches this module directly rather than either `storage`-
classified module -- `frontends.may_import` lists `workflows`, not
`storage`.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from ..project_snapshot_legacy import (
    is_project_snapshot_package_dir,
    materialize_release_variant_artifacts,
)
from ..project_snapshot_store import read_manifest_summary

if TYPE_CHECKING:
    from ..storage.package import ArtifactRef

__all__ = [
    "is_project_snapshot_package_dir",
    "is_multi_artifact_package",
    "resolve_release_package_map",
]

#: The two `ArtifactRef.native_identity` keys used, independently, by both of
#: today's not-yet-reconciled multi-artifact package writers
#: (`bundle_facts_store.py`'s `_NATIVE_IDENTITY_FILENAME_KEY`/
#: `_NATIVE_IDENTITY_LIBRARY_NAME_KEY` and `storage/import_bundle_facts.py`'s
#: `_LIBRARY_NAME_KEY`) to record a library's real on-disk name -- see
#: `storage-format-v2.md`'s A1.4 entry for why there are two writers at all.
#: The string values themselves are the real cross-writer contract, not
#: either module's own private name for it.
_NATIVE_IDENTITY_FILENAME_KEY = "library_filename"
_NATIVE_IDENTITY_LIBRARY_NAME_KEY = "library_name"


def is_multi_artifact_package(path: str | Path) -> bool:
    """Whether the `ProjectSnapshot` package directory at *path* declares
    more than one artifact -- ADR-062 A1.7's disambiguator between a
    single-artifact package (`cli_resolve.classify_compare_operand` keeps
    reading it directly as one snapshot, A1.3's original "file" shape,
    unchanged since before A1.7) and a real multi-library release (routed
    to the release fan-out instead, the same as a loose directory of `.so`
    files). Read-only, best-effort: `False` -- never raises -- for anything
    that fails to parse as a readable manifest; the caller has normally
    already confirmed *path* passes `is_project_snapshot_package_dir`.
    """
    from ..errors import SnapshotError

    try:
        summary = read_manifest_summary(path)
    except (SnapshotError, OSError, ValueError, TypeError):
        return False
    return len(summary.artifact_ids) > 1


def _release_match_key(artifact: ArtifactRef) -> str:
    """The canonical `cli_compare_release` matching key for *artifact*,
    matching the same `binary_utils._canonical_library_key()` a live
    directory-of-`.so`-files operand's own `_build_match_map` uses -- the
    property a stored/live A1.7 comparison depends on: a package-sourced
    map and a live-directory-sourced map must agree on the key for the same
    library, or the two can never match via `_match_release_keys`'s plain
    `set(old_map) & set(new_map)`.

    Prefers the real on-disk filename (`_NATIVE_IDENTITY_FILENAME_KEY`,
    e.g. `libfoo.so.1.2`, when the writer captured it) over the bare SONAME/
    library name (`_NATIVE_IDENTITY_LIBRARY_NAME_KEY`) since the filename is
    what `_canonical_library_key` is actually built to canonicalize (a
    version suffix, a vendored hash, ...); falls back to the library name,
    then -- for a package whose writer recorded neither -- to the artifact's
    own opaque `artifact_id`, which at least keeps matching deterministic
    (if unable to pair with a differently-produced package) rather than
    raising.
    """
    from ..binary_utils import _canonical_library_key

    name = artifact.native_identity.get(
        _NATIVE_IDENTITY_FILENAME_KEY
    ) or artifact.native_identity.get(_NATIVE_IDENTITY_LIBRARY_NAME_KEY)
    if name:
        return _canonical_library_key(Path(name))
    return artifact.artifact_id


def resolve_release_package_map(
    root: str | Path,
    *,
    variant_id: str | None,
    dest_root: str | Path,
) -> dict[str, Path]:
    """`project_snapshot_legacy.materialize_release_variant_artifacts`'s
    `{artifact_id: (Path, ArtifactRef)}`, re-keyed by `_release_match_key`
    into the `old_map`/`new_map: dict[str, Path]` shape
    `cli_compare_release.py`'s per-library fan-out already builds from a
    live directory of `.so` files (ADR-062 A1.7).

    Raises `ValueError` (propagated from `materialize_release_variant_
    artifacts`, or raised fresh here) if the variant selection is
    ambiguous/invalid, or if two of the selected variant's artifacts
    resolve to the same `_release_match_key` -- a genuine identity
    collision (two real libraries sharing one canonical name), not a
    directory-naming implementation detail: `materialize_release_variant_
    artifacts` itself never collides, since it keys sub-package
    directories by the already-unique `artifact_id`.
    """
    by_artifact_id = materialize_release_variant_artifacts(
        root, variant_id=variant_id, dest_root=dest_root
    )
    result: dict[str, Path] = {}
    owners: dict[str, str] = {}
    for artifact_id, (sub_dir, artifact) in by_artifact_id.items():
        key = _release_match_key(artifact)
        existing_owner = owners.setdefault(key, artifact_id)
        if existing_owner != artifact_id:
            raise ValueError(
                f"{root} has two artifacts ({existing_owner!r} and "
                f"{artifact_id!r}) that both resolve to release-matching "
                f"key {key!r} -- their real library names/filenames must "
                "be distinguishable for compare-release's matching logic "
                "to tell them apart"
            )
        # `_compare_one_library`'s own `entry["library"] = old_path.name`
        # publishes this directory's basename as the release report's
        # display name for this library (JSON/Markdown/JUnit, per-library
        # filenames, removal warnings) -- renamed here from the artifact_id
        # `materialize_release_variant_artifacts` names it for collision
        # safety (an opaque hash) to something a reader can actually
        # attribute a finding to, once `key`'s own uniqueness is already
        # settled above. Still suffixed with a short `artifact_id` prefix,
        # so a `key` that only differs from another by a character
        # `_DISPLAY_DIRNAME_UNSAFE` collapses (e.g. a `/` vs `:`) still
        # cannot collide on disk (Codex review).
        display_dir = sub_dir.with_name(_display_dirname(key, artifact_id))
        if display_dir != sub_dir:
            sub_dir.rename(display_dir)
            sub_dir = display_dir
        result[key] = sub_dir
    return result


#: Characters refused as-is in a materialized sub-package's display
#: directory name -- deliberately looser than `storage.ref_ids.safe_ref_id`
#: (which `artifact_id`/`variant_id` must satisfy): this name is never
#: itself an on-disk identity anything reads back, only a release report's
#: display string riding along on a path component, so it only needs to
#: avoid a path separator or traversal segment, not full Windows-reserved-
#: name portability.
_DISPLAY_DIRNAME_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]")


def _display_dirname(key: str, artifact_id: str) -> str:
    sanitized = _DISPLAY_DIRNAME_UNSAFE.sub("_", key).strip(". ")[:80]
    if not sanitized or sanitized in (".", ".."):
        sanitized = "lib"
    return f"{sanitized}-{artifact_id[:12]}"
