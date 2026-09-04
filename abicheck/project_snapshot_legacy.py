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

"""ADR-063 Phase 8's `dump`/`compare`/`scan` storage-v2 wiring: the legacy
`AbiSnapshot` document <-> real, directory-backed `ProjectSnapshot` package
round trip, built on `project_snapshot_store.py`'s `DirectoryObjectStore`/
`write_project_manifest`/`read_manifest_summary`/`read_artifact_ref` and
`storage.import_v1`'s `import_legacy_snapshot`/`export_legacy_snapshot`.

Kept as its own sibling module, not added to `project_snapshot_store.py`
itself, purely for that module's own architecture-gate line budget (already
close to the 800-line production cap before this landed) — the same
mechanical-split reasoning `AGENTS.md`'s "Files that are large" section
gives for `diff_types_vtable.py` and similar splits: move responsibility
out, don't trim the file to fit.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from .errors import SnapshotError
from .project_snapshot_store import (
    DirectoryObjectStore,
    read_artifact_ref,
    read_manifest_summary,
    read_variant_artifact_pair,
    read_variant_ref,
    write_project_manifest,
)
from .storage.import_v1 import export_legacy_snapshot, import_legacy_snapshot
from .storage.package import (
    ArtifactRef,
    InMemoryObjectStore,
    PackageManifest,
    VariantRef,
)

__all__ = [
    "is_project_snapshot_package_dir",
    "materialize_release_variant_artifacts",
    "package_declares_full_dependency_scope",
    "read_legacy_snapshot_document",
    "write_legacy_snapshot_package",
]

#: D6's fixed, package-relative object-store directory name
#: (`objects/sha256/<aa>/<digest>.json[.zst]`, `storage/package.py`'s
#: `object_relpath`) -- not exported as a public constant there, but a
#: stable part of the documented D6 layout every writer agrees on.
_OBJECTS_DIRNAME = "objects"


def is_project_snapshot_package_dir(path: str | Path) -> bool:
    """Whether *path* is a directory holding a real, readable
    `ProjectSnapshot` package `manifest.json` -- the disambiguator a CLI
    operand classifier (`cli_resolve.classify_compare_operand`) needs, since
    a plain directory-of-libraries `compare`/`scan` operand and a
    `BuildSourcePack`'s own `manifest.json` (`buildsource/pack_io.py`) both
    use the identical filename at their own directory root.

    Reads and validates `manifest.json` (`read_manifest_summary`, D2's
    version-compatibility check included), not just a bare
    `(path / "manifest.json").exists()` -- a `BuildSourcePack` manifest
    parses as JSON too but fails `StorageVersions.from_dict`/
    `check_reader_compatibility` (no `versions`/`variant_ids`/`artifact_ids`
    keys, and no `package_format_version` value this reader recognizes), so
    the distinction is real content, not guessed from the filename alone.
    Returns `False` -- never raises -- for anything that doesn't parse as
    one: a missing/malformed/unreadable/incompatible manifest is simply "not
    a ProjectSnapshot package here", the classifier's other branches decide
    what it *is* instead.
    """
    path = Path(path)
    if not (path / "manifest.json").is_file():
        return False
    try:
        read_manifest_summary(path)
    except (SnapshotError, OSError, ValueError, TypeError):
        return False
    return True


def package_declares_full_dependency_scope(path: str | Path) -> bool:
    """Whether the `ProjectSnapshot` package directory at *path* was dumped
    with `dependency_scope="full"` (`dump --include-system-declarations`).

    A cheap, best-effort read of the already-persisted document -- used by
    `scan_engine._scan_candidate_include_dependencies` so a `scan --against`
    given a package directory (rather than a JSON file, which that function
    handles by content-sniffing) matches the baseline's own scope instead of
    silently defaulting to filtered and hitting the comparability gate's
    `NOT_COMPARABLE` rejection (Codex review). Returns `False` -- never
    raises -- for anything that isn't a readable, "full"-tagged package.
    """
    path = Path(path)
    if not is_project_snapshot_package_dir(path):
        return False
    try:
        document = read_legacy_snapshot_document(path)
    except Exception:
        return False
    return bool(document.get("dependency_scope") == "full")


def write_legacy_snapshot_package(
    document: Mapping[str, Any],
    root: str | Path,
    *,
    artifact_id: str,
    max_known_schema_version: int,
    variant_id: str = "default",
    artifact_kind: str | None = None,
) -> PackageManifest:
    """*document* (a `serialization.snapshot_to_dict()`-shaped mapping — the
    same document a real `dump` invocation already produces) written as a
    real, directory-backed `ProjectSnapshot` package at *root*, alongside
    whatever legacy `.abi.json` output the caller also writes.

    A thin composition of three already-independently-tested primitives —
    `storage.import_v1.import_legacy_snapshot` (build the manifest, populate
    an in-memory object buffer), `DirectoryObjectStore` (the real filesystem
    object store), `write_project_manifest` (fan the manifest out across
    D6's directory tree) — with one twist: `import_legacy_snapshot` writes
    into whatever `ObjectStore` it is given, so this function `put()`s every
    object *twice*: once into a throwaway `InMemoryObjectStore` (to let
    `import_legacy_snapshot`'s own content-addressing decide the manifest's
    digests before anything touches disk), and once for real into a
    `DirectoryObjectStore` rooted at *root* using those same digests --
    deliberately, not a wasted step: `write_project_manifest` itself
    requires every object it is about to publish a reference to be already
    durable and valid in the *target* store before it will write
    `manifest.json` (see its own docstring), so the manifest cannot be built
    and validated against a store that was never actually persisted.

    Refuses to write into an already-nonempty *root* -- `write_project_
    manifest`'s own docstring names this exact gap as "a known, deliberately
    deferred gap": its ref-then-manifest publish order makes *first*
    publication of a set of ids safe, but not *republishing* changed content
    under ids that are already live (a concurrent reader, or an interruption
    partway through, can observe a manifest still naming the old artifact
    while a `refs/*.json` document already names the new one). That gap was
    left open because nothing called `write_project_manifest` a second time
    against a live path; this function called twice against the same
    *root* for two different libraries is exactly that caller now. Rather
    than reactively building the atomic staged-directory-
    then-swap publish protocol that gap's own docstring names as the real
    fix (a separately-scoped design decision, not something to improvise
    under review pressure), this refuses the unsafe case outright: every
    call here is a *first* publication into a fresh or empty directory
    (Codex review).
    """
    root_path = Path(root)
    if root_path.exists() and any(root_path.iterdir()):
        raise ValueError(
            f"{root_path} already exists and is not empty -- "
            "write_legacy_snapshot_package only supports a first publication "
            "into a fresh or empty directory, not republishing into an "
            "existing ProjectSnapshot package (see write_project_manifest's "
            "own docstring for why an in-place republish is not yet safe). "
            "Pass a different, empty, or nonexistent directory."
        )
    staging = InMemoryObjectStore()
    manifest = import_legacy_snapshot(
        document,
        store=staging,
        artifact_id=artifact_id,
        max_known_schema_version=max_known_schema_version,
        variant_id=variant_id,
        artifact_kind=artifact_kind,
    )
    directory_store = DirectoryObjectStore(root)
    for artifact in manifest.artifact_refs:
        for ref in artifact.sections.values():
            directory_store.put(staging.get(ref.digest))
    write_project_manifest(root, manifest)
    return manifest


def read_legacy_snapshot_document(
    root: str | Path, *, artifact_id: str | None = None
) -> dict[str, Any]:
    """The inverse of `write_legacy_snapshot_package`: the single artifact's
    document (`serialization.snapshot_from_dict()`-shaped), read back from a
    real directory-backed `ProjectSnapshot` package at *root*.

    *artifact_id* names which artifact to read; omitted (the default), the
    package must publish exactly one artifact -- ADR-062 A1.3's
    "single-library snapshot as a one-artifact project" shape, the only one
    `write_legacy_snapshot_package`/`storage.import_v1.import_legacy_snapshot`
    ever produce today. Raises `ValueError` if *artifact_id* is omitted and
    the package holds zero or more than one artifact -- multi-artifact
    projects (a real multi-library `ProjectSnapshot`) are real,
    separately-scoped future work this function does not guess at.

    Validates the selected artifact's two-way variant membership
    (`read_variant_artifact_pair`), not just its own ref document's
    self-consistency -- a package with a stale or corrupted `refs/variants/
    *.json` can otherwise publish an artifact whose declared `variant_id`
    names a variant that does not itself list it back, which
    `read_project_manifest`/`read_variant_artifact_pair` already refuse
    elsewhere in this same package; `export_legacy_snapshot` must not
    silently accept that contradictory membership graph here just because
    it never asked (Codex review).
    """
    summary = read_manifest_summary(root)
    if artifact_id is None:
        if len(summary.artifact_ids) != 1:
            raise ValueError(
                f"{root} publishes {len(summary.artifact_ids)} artifact(s) "
                f"({sorted(summary.artifact_ids)}) -- pass an explicit "
                "artifact_id to select one, or use a package with exactly "
                "one artifact"
            )
        artifact_id = summary.artifact_ids[0]
    # `read_artifact_ref` alone only validates the ref document's own
    # self-consistency (its embedded artifact_id matches the one requested)
    # -- it says nothing about whether the variant it names actually
    # declares this artifact back. Load it once to learn its own
    # `variant_id`, then re-validate the pair through
    # `read_variant_artifact_pair`, which checks both directions and is
    # this package's own established integrity path for exactly this.
    artifact = read_artifact_ref(root, artifact_id)
    variant, artifact = read_variant_artifact_pair(
        root, artifact.variant_id, artifact_id
    )
    # `export_legacy_snapshot` only ever looks at the sections *present* in
    # `artifact.sections` -- a section a stale/corrupted artifact ref has
    # dropped simply isn't iterated, and `join_legacy_document` then fills
    # its declarations/types with empty defaults, turning lost evidence into
    # confirmed absence (a real false addition/removal downstream, not a
    # loud failure). `manifest.json`'s own `section_schema_versions` is the
    # ground truth for which sections this package's (sole, today) artifact
    # was actually written with, at import time -- cross-check against it
    # before export, not after (Codex review). Gated on a genuinely
    # single-artifact package: with more than one artifact, the manifest-
    # level map is the union across all of them, so a per-artifact subset
    # is expected and not itself a corruption signal -- multi-artifact
    # packages are documented above as out of this function's scope anyway.
    if len(summary.artifact_ids) == 1:
        # `variant.sections`/`summary.project_sections` are real, legitimate
        # entries in `section_schema_versions` too (ADR-062 A1.4/A1.5's
        # project/variant-level evidence -- an instantiation manifest, a
        # `storage.import_bundle_facts`-sourced bundle-composition object,
        # ...), but they describe the variant/project as a whole, never one
        # `ArtifactRef`'s own `sections`. A single-artifact sub-package
        # `materialize_release_variant_artifacts` cut from a larger package
        # legitimately carries both kinds side by side -- excluded here so
        # this artifact-only integrity check isn't misapplied to evidence it
        # was never scoped to check in the first place (Codex review, fresh
        # evidence: this exact shape made every stored-release comparison
        # sourced from `storage.import_bundle_facts` read as corrupted).
        non_artifact_kinds = set(variant.sections) | set(summary.project_sections)
        advertised = set(summary.versions.section_schema_versions) - non_artifact_kinds
        actual = set(artifact.sections)
        missing_sections = advertised - actual
        if missing_sections:
            raise SnapshotError(
                f"{root} artifact {artifact_id!r} is missing section(s) "
                f"{sorted(missing_sections)} that manifest.json's "
                "section_schema_versions advertises -- the artifact ref is "
                "stale or corrupted; refusing to silently synthesize empty "
                "defaults for lost evidence"
            )
        # The inverse contradiction (Codex review, fresh evidence): a stale
        # or corrupted artifact ref could also carry a section
        # manifest.json never advertised at all -- e.g. a `semantic_ir`
        # object copied in from another package -- which `export_legacy_
        # snapshot` would still merge into the rebuilt document. Reject
        # that too, not just the missing-section direction.
        extra_sections = actual - advertised
        if extra_sections:
            raise SnapshotError(
                f"{root} artifact {artifact_id!r} has section(s) "
                f"{sorted(extra_sections)} that manifest.json's "
                "section_schema_versions does not advertise -- the artifact "
                "ref is stale or corrupted; refusing to merge unaccounted-for "
                "section content"
            )
    store = DirectoryObjectStore(root)
    return export_legacy_snapshot(
        artifact,
        store=store,
        source_schema_version=summary.versions.source_schema_version,
    )


def _populate_objects_dir(sub_dir: Path, objects_source: Path) -> None:
    """Make *objects_source* (*root*'s own `objects/` directory) reachable
    from *sub_dir* -- preferring a symlink (cheap: D7 content addressing
    means every object a sub-package's own `write_project_manifest`
    validation reads through it resolves to the identical bytes *root*
    already stores, so copying would only duplicate potentially large
    section content for no benefit), but falling back to a real recursive
    copy when the host/filesystem cannot create one -- a Windows host
    without Developer Mode/elevated symlink privileges, or a filesystem
    without directory-symlink support, must still be able to materialize a
    stored-release comparison (Codex review).
    """
    import shutil

    dest = sub_dir / _OBJECTS_DIRNAME
    try:
        dest.symlink_to(objects_source, target_is_directory=True)
    except OSError:
        shutil.copytree(objects_source, dest)


def materialize_release_variant_artifacts(
    root: str | Path,
    *,
    variant_id: str | None,
    dest_root: str | Path,
) -> dict[str, tuple[Path, ArtifactRef]]:
    """Unpack the (possibly multi-artifact) `ProjectSnapshot` package
    directory at *root* into one materialized, real, independently-readable
    single-artifact sub-package directory per member library of the
    selected variant, written under *dest_root* -- the storage-layer half
    of ADR-062 A1.7's stored-release comparison; `workflows.
    release_package.resolve_release_package_map` is the workflows-layer
    wrapper that re-keys this function's return value by a
    live-directory-comparable canonical library name (this module may only
    import `model`, per `storage/AGENTS.md`, so it cannot itself reach
    `binary_utils._canonical_library_key`).

    Returns `{artifact_id: (sub_package_dir, artifact_ref)}` -- keyed by
    the artifact's own `artifact_id`, which is already validated safe and
    unique as a filesystem path component (`storage.ref_ids.safe_ref_id`,
    enforced by `ArtifactRef.__post_init__`), so `sub_package_dir` is
    always `dest_root / artifact_id` and two artifacts can never collide on
    one directory (Codex review) -- unlike a name derived from mutable,
    caller-controlled `native_identity` content.

    *variant_id* selects which of *root*'s `VariantRef`s to resolve; `None`
    requires *root* to declare exactly one, raising `ValueError` otherwise --
    ambiguity is a hard usage error, not a silent first match, the same
    discipline `SymbolIdentityIndex.unique_alias_match` already establishes
    elsewhere in this codebase (see `storage-format-v2.md`'s A1.7 design
    note). Each artifact is validated against *root*'s own membership graph
    via `read_variant_artifact_pair` (not a bare `read_artifact_ref`), so a
    stale or hand-edited `refs/variants/<id>.json` naming an artifact
    `manifest.json` never actually published cannot be republished into a
    sub-package here (Codex review).

    Each sub-package directory is a real, independently-readable
    single-artifact `ProjectSnapshot` package -- written via
    `write_project_manifest`, the identical writer/validator
    `write_legacy_snapshot_package` uses -- so every existing consumer of a
    package-shaped `compare`/`compare-release` operand
    (`workflows.input_resolution._resolve_project_snapshot_directory` via
    `read_legacy_snapshot_document`) reads it completely unchanged; this
    function is purely a *source* for the map, not a new code path through
    comparison.
    """
    root_path = Path(root)
    summary = read_manifest_summary(root_path)
    if variant_id is None:
        if len(summary.variant_ids) != 1:
            raise ValueError(
                f"{root_path} declares {len(summary.variant_ids)} variant(s) "
                f"({sorted(summary.variant_ids)}) -- pass an explicit "
                "variant id (--old-variant/--new-variant) to select one"
            )
        variant_id = summary.variant_ids[0]
    elif variant_id not in summary.variant_ids:
        raise ValueError(
            f"{variant_id!r} is not a variant_id in {root_path} (known: "
            f"{sorted(summary.variant_ids)})"
        )
    variant = read_variant_ref(root_path, variant_id)
    # `read_variant_artifact_pair`'s own docstring names this exact gap as
    # deliberately deferred: it validates the "declared" direction (every id
    # `variant.artifact_ids` names is itself published and self-consistent)
    # but not "owned" (a *different* published artifact whose own
    # `variant_id` also names this variant, yet `variant.artifact_ids`
    # simply omits it). Left unchecked, a stale or hand-edited package could
    # omit exactly the one artifact carrying a real ABI break from
    # `variant.artifact_ids`, and this function would silently compare
    # every *other* artifact clean -- excluding the break from the release
    # gate entirely rather than raising (Codex review, security finding).
    # This is the real caller `read_variant_artifact_pair`'s docstring
    # names as the reason to eventually close that gap: reading every
    # artifact ref in the package to check the reverse direction.
    owned_elsewhere = sorted(
        artifact_id
        for artifact_id in summary.artifact_ids
        if artifact_id not in variant.artifact_ids
        and read_artifact_ref(root_path, artifact_id).variant_id == variant_id
    )
    if owned_elsewhere:
        raise ValueError(
            f"{root_path} variant {variant_id!r} omits artifact_id(s) "
            f"{owned_elsewhere} from its own artifact_ids even though each "
            f"names {variant_id!r} as its own variant_id -- the package's "
            "membership graph is self-contradictory (refusing to silently "
            "exclude a real library from the comparison)"
        )

    dest_root_path = Path(dest_root)
    dest_root_path.mkdir(parents=True, exist_ok=True)
    objects_source = (root_path / _OBJECTS_DIRNAME).resolve()

    result: dict[str, tuple[Path, ArtifactRef]] = {}
    for artifact_id in variant.artifact_ids:
        full_variant, artifact = read_variant_artifact_pair(
            root_path, variant_id, artifact_id
        )

        sub_dir = dest_root_path / artifact_id
        sub_dir.mkdir(parents=True, exist_ok=True)
        if objects_source.is_dir():
            _populate_objects_dir(sub_dir, objects_source)
        # Every section kind this sub-package's manifest ends up
        # referencing needs a matching `section_schema_versions` entry
        # (`write_project_manifest`'s own validation) -- not just the
        # artifact's own sections, but also the *variant*-level and
        # *project*-level ones carried through below (Codex review, fresh
        # evidence: `storage.import_bundle_facts`' own writer stores
        # filenames/aliases/manifest under `VariantRef.sections`, and
        # `bundle_facts_store.write_bundle_facts_package` stores its
        # InstantiationManifest under `PackageManifest.project_sections` --
        # neither lives on the artifact itself, so dropping them here
        # silently discarded that bundle-wide evidence from every stored
        # release comparison sourced from either writer).
        relevant_kinds = (
            set(artifact.sections)
            | set(full_variant.sections)
            | set(summary.project_sections)
        )
        trimmed_versions = replace(
            summary.versions,
            section_schema_versions={
                kind: version
                for kind, version in summary.versions.section_schema_versions.items()
                if kind in relevant_kinds
            },
        )
        # `full_variant.sections`/`summary.project_sections` are carried
        # through unchanged, on every single-artifact sub-package cut from
        # this variant -- they describe the *whole* original variant/
        # project, not this one library, but that is the correct shape
        # here: `build_bundle_snapshot_mixed` (and any other stored-release
        # reader) needs this bundle-wide evidence available from whichever
        # one sub-package it happens to read first, since nothing else
        # would carry it once the original multi-artifact package's own
        # `refs/variants/<id>.json` is gone. Every `ObjectRef` referenced
        # stays resolvable: `_populate_objects_dir` links/copies the
        # *entire* `objects/` store above, not just this artifact's own
        # digests.
        trimmed_variant = VariantRef(
            variant_id=variant_id,
            artifact_ids=(artifact_id,),
            sections=full_variant.sections,
        )
        sub_manifest = PackageManifest(
            versions=trimmed_versions,
            variant_refs=(trimmed_variant,),
            artifact_refs=(artifact,),
            project_sections=summary.project_sections,
        )
        write_project_manifest(sub_dir, sub_manifest)
        result[artifact_id] = (sub_dir, artifact)
    return result
