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
    """Whether the `ProjectSnapshot` package directory at *path* should be
    routed through the release fan-out rather than read directly as one
    snapshot -- true whenever it does *not* declare exactly one artifact
    under exactly one variant, ADR-062 A1.7's disambiguator between a
    genuinely single-artifact package (`cli_resolve.classify_compare_operand`
    keeps reading it directly, A1.3's original "file" shape, unchanged since
    before A1.7) and everything else (routed to the release fan-out instead,
    the same as a loose directory of `.so` files).

    Three shapes route to the fan-out, not just "more than one artifact":

    - **More than one artifact** -- the obvious multi-library release case.
    - **Zero artifacts** -- a real, valid package can declare a variant with
      no member libraries at all (an empty release, or the *unselected*
      side of a multi-variant package where every artifact belongs to a
      different variant). A1.3's single-artifact reader
      (`project_snapshot_legacy.read_legacy_snapshot_document`) hard-requires
      exactly one artifact and raises otherwise -- so a zero-artifact
      package must reach the fan-out (which already handles an empty
      `old_map`/`new_map` as a valid "nothing to compare" release) rather
      than that reader's usage error (Codex review).
    - **More than one variant** -- matters even when exactly one artifact
      exists overall: a package can validly declare two variants where only
      one of them owns that artifact (e.g. `v1` owns it, `v2` is a real,
      deliberately empty variant). The single-artifact reader has no
      `--old-variant`/`--new-variant` selection logic at all -- it always
      reads the package's sole artifact unconditionally -- so treating such
      a package as "file" would silently ignore an explicit
      `--old-variant v2`/`--new-variant v2` and compare `v1`'s artifact
      instead, rather than either honoring the selection (an empty variant,
      correctly comparing zero libraries) or raising on it (Codex review).

    Read-only, best-effort: `False` -- never raises -- for anything that
    fails to parse as a readable manifest; the caller has normally already
    confirmed *path* passes `is_project_snapshot_package_dir`.
    """
    from ..errors import SnapshotError

    try:
        summary = read_manifest_summary(path)
    except (SnapshotError, OSError, ValueError, TypeError):
        return False
    return len(summary.artifact_ids) != 1 or len(summary.variant_ids) != 1


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
    keyed: list[tuple[str, str, Path, ArtifactRef]] = []
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
        keyed.append((key, artifact_id, sub_dir, artifact))

    # Two-phase rename (Codex review, fresh evidence): renaming straight
    # from each raw artifact_id directory to its own display name can
    # target a *different*, still-unrenamed artifact's raw directory --
    # e.g. artifact_id "a" resolving to key "foo" renames to "foo-a",
    # which collides with a real, not-yet-processed artifact_id "foo-a"
    # directory, raising ENOTEMPTY/FileExistsError. Staging every sub-
    # package under a name outside `_display_dirname`'s own output space
    # first (that function's output never starts with `.`) makes the two
    # namespaces disjoint, so no second-phase rename can ever collide with
    # a first-phase staging name or an unprocessed raw directory.
    staged: list[tuple[str, str, Path, ArtifactRef]] = []
    for key, artifact_id, sub_dir, artifact in keyed:
        staging_dir = sub_dir.with_name(f".resolving-{artifact_id}")
        sub_dir.rename(staging_dir)
        staged.append((key, artifact_id, staging_dir, artifact))

    result: dict[str, Path] = {}
    for key, artifact_id, staging_dir, artifact in staged:
        # `_compare_one_library`'s own `entry["library"] = old_path.name`
        # publishes this directory's basename as the release report's
        # display name for this library (JSON/Markdown/JUnit, per-library
        # filenames, removal warnings) -- once `key`'s own uniqueness is
        # already settled above, renamed to something a reader can attribute
        # a finding to instead of an opaque artifact_id. Still suffixed with
        # the full `artifact_id`, so a `key` that only differs from another
        # by a character `_DISPLAY_DIRNAME_UNSAFE` collapses (e.g. a `/` vs
        # `:`) still cannot collide on disk (Codex review).
        display_dir = staging_dir.with_name(_display_dirname(key, artifact_id))
        staging_dir.rename(display_dir)
        result[key] = display_dir
    return result


#: Characters refused as-is in a materialized sub-package's display
#: directory name -- deliberately looser than `storage.ref_ids.safe_ref_id`
#: (which `artifact_id`/`variant_id` must satisfy): this name is never
#: itself an on-disk identity anything reads back, only a release report's
#: display string riding along on a path component, so it only needs to
#: avoid a path separator or traversal segment, not full Windows-reserved-
#: name portability.
_DISPLAY_DIRNAME_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]")

#: A conservative cap for the *whole* generated display directory name,
#: comfortably under the common 255-byte path-component limit
#: `storage.ref_ids.safe_ref_id` itself is sized against (`artifact_id`
#: alone may be up to 250 UTF-8 bytes there -- see `_display_dirname`'s own
#: docstring for why the naive "80-byte key prefix + full artifact_id"
#: shape could exceed 255 on its own).
_MAX_DISPLAY_DIRNAME_BYTES = 200


def _display_dirname(key: str, artifact_id: str) -> str:
    """A readable display directory name for *artifact_id*, prefixed by a
    sanitized form of *key*.

    Suffixed with the **full** `artifact_id`, not a truncated prefix of it
    -- collision-freedom must not depend on `key`'s own sanitization being
    injective (two distinct canonical keys, e.g. `lib:a.so` and `lib?a.so`,
    both collapse to `lib_a.so` under `_DISPLAY_DIRNAME_UNSAFE`) nor on a
    truncated `artifact_id` prefix staying unique (two distinct, individually
    unique artifact_ids could in principle share a short prefix). Since
    `artifact_id` itself is already validated globally unique and
    filesystem-safe (`ArtifactRef.__post_init__`/`storage.ref_ids.
    safe_ref_id` -- the same guarantee `resolve_release_package_map`'s own
    docstring already leans on for `materialize_release_variant_artifacts`
    never colliding), keeping it in full makes every generated name
    collision-free regardless of what `key` sanitizes to (Codex review,
    fresh evidence on this same guard, twice).

    The sanitized `key` prefix's own budget is whatever's left of
    `_MAX_DISPLAY_DIRNAME_BYTES` after reserving room for the full
    `artifact_id` plus its separator -- `artifact_id` alone may be up to
    250 UTF-8 bytes (`storage.ref_ids.safe_ref_id`'s own ceiling), so an
    unconditional 80-byte prefix could push the combined name past a
    common 255-byte path-component limit and make the materializing
    `rename()` fail with `ENAMETOOLONG` (Codex review, fresh evidence: a
    long but individually valid `artifact_id`). When that budget leaves no
    room for any prefix at all, the name degrades to the bare
    `artifact_id` -- still valid and collision-free, just less readable.
    """
    sanitized = _DISPLAY_DIRNAME_UNSAFE.sub("_", key).strip(". ")
    artifact_bytes = len(artifact_id.encode("utf-8"))
    prefix_budget = max(0, _MAX_DISPLAY_DIRNAME_BYTES - artifact_bytes - 1)
    # `sanitized` is pure ASCII after the substitution above, so a
    # character-count slice is also a byte-count slice.
    sanitized = sanitized[:prefix_budget]
    if not sanitized or sanitized in (".", ".."):
        return artifact_id
    return f"{sanitized}-{artifact_id}"


def dso_only_package_map(pkg_map: dict[str, Path]) -> dict[str, Path]:
    """*pkg_map* (a `resolve_release_package_map` result), restricted to
    members whose materialized `ArtifactRef.kind` is `"elf"` and whose
    stored `ElfMetadata` reads as a real shared object -- `--dso-only`'s
    stored-side counterpart to `package._is_elf_shared_object` filtering a
    live directory's discovered files (Codex review: checking `kind` alone
    admitted a PIE/application executable, since `import_v1` derives
    `"elf"` for both alike).

    `ElfMetadata` carries no `e_type`, so a traditional non-PIE `ET_EXEC`
    can't be told apart from a real DSO by `is_pie` alone (fresh evidence,
    a second Codex round): `is_pie` alone only rejects a *PIE* executable.
    Mirrors the live predicate's own remaining two cases instead: no
    `PT_INTERP` (`ElfMetadata.interpreter` empty) is a real shared object
    outright; `PT_INTERP` present but not PIE is ambiguous (an ordinary
    executable, or a deliberately-invocable distro DSO like `libc.so.6`)
    and falls back to the identical filename heuristic
    (`package._has_shared_object_name`) the live path applies for that
    same ambiguous case.

    Best-effort per member: a sub-package whose own kind or ELF metadata
    cannot be determined is *excluded*, not included -- `--dso-only`'s
    whole contract is "only compare what is confirmed to be a DSO", so
    uncertainty must not silently widen it.
    """
    from ..bundle import _stored_elf_metadata
    from ..package import _has_shared_object_name
    from ..project_snapshot_store import read_artifact_ref, read_manifest_summary

    filtered: dict[str, Path] = {}
    for key, sub_dir in pkg_map.items():
        try:
            summary = read_manifest_summary(sub_dir)
            (artifact_id,) = summary.artifact_ids
            if read_artifact_ref(sub_dir, artifact_id).kind != "elf":
                continue
        except Exception:
            continue
        elf = _stored_elf_metadata(sub_dir)
        if elf is None or elf.is_pie:
            continue
        if elf.interpreter and not _has_shared_object_name(key):
            continue
        filtered[key] = sub_dir
    return filtered


def dso_only_filter_pair(
    old_pkg_map: dict[str, Path] | None, new_pkg_map: dict[str, Path] | None
) -> tuple[dict[str, Path] | None, dict[str, Path] | None]:
    """`dso_only_package_map` applied to whichever of *old_pkg_map*/
    *new_pkg_map* is not `None` -- the pair shape
    `cli_compare_release_matrix._prepare_compare_release_inputs` needs for
    its own two stored-side maps in one call."""
    return (
        dso_only_package_map(old_pkg_map) if old_pkg_map is not None else None,
        dso_only_package_map(new_pkg_map) if new_pkg_map is not None else None,
    )
