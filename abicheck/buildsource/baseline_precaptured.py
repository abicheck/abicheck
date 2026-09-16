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

"""Validate an **already-captured** baseline-set before it is published.

``publish-baseline.yml``'s original path is capture-then-publish: download a
profile's ``build-output.json``, run ``actions/baseline`` over it, publish
what that produced. A consumer that already holds a captured set -- from an
earlier job in the same workflow, or from a historical build it can no
longer reproduce -- had no supported way in, which is why a downstream
project ended up maintaining its own publisher (and its own manifest
interpreter) to close the gap.

Publishing a set this workflow did **not** just build changes what has to be
checked. The capture-then-publish path can trust the manifest it is handed,
because it wrote it moments earlier from bytes it read itself; an
already-captured set is an input, and every claim in its manifest is a claim
until this module has checked it against the files actually present.

So the checks here are deliberately the *producer-side* mirror of what a
consumer's resolver (:mod:`abicheck.buildsource.baseline_set`'s
``resolve_target``/``resolve_bundle``) will later demand of the published
asset: understandable manifest and snapshot schema, the requested profile
and capture revision, the declared generation, portable in-tree member
paths, and every declared digest recomputed from the bytes on disk. A set
that would be rejected at resolution time must be rejected here, before it
is written to an immutable channel -- publishing it and finding out later is
the one outcome this module exists to prevent.

What it deliberately does **not** do: build, extract, dump, or compare
anything. Validating a captured set is a read of files that already exist.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from .. import serialization
from .baseline_set import (
    BASELINE_MANIFEST_FILENAME,
    SUPPORTED_MANIFEST_VERSIONS,
    BaselineManifest,
    compute_snapshot_content_hash,
    load_baseline_manifest,
)

#: Windows reserves these, and a tar member carrying one cannot be extracted
#: on a runner that has to. Checked alongside the traversal rules below
#: because "portable path" is a property of the *published* archive, not of
#: the machine that happened to capture it.
_UNPORTABLE_CHARS = frozenset('\\:*?"<>|')


@dataclass
class PrecapturedValidation:
    """The outcome of one :func:`validate_precaptured_baseline_set` call.

    ``ok`` is ``False`` whenever ``errors`` is non-empty and never
    otherwise: a caller gating on one is gating on the other, so the two
    cannot disagree about whether publication may proceed.
    """

    root: Path | None = None
    profile: str = ""
    project_ref: str = ""
    baseline_generation: int | None = None
    libraries: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "root": str(self.root) if self.root is not None else "",
            "profile": self.profile,
            "project_ref": self.project_ref,
            "baseline_generation": self.baseline_generation,
            "libraries": list(self.libraries),
            "errors": list(self.errors),
        }


def locate_baseline_set(directory: Path | str) -> Path | None:
    """The baseline-set root at or immediately below *directory*.

    ``actions/download-artifact`` extracts flat when its pattern matched one
    artifact and nests under ``<name>/`` when it matched several, so a
    caller handing over a download path cannot know which shape it has. One
    level of nesting is searched for exactly that reason, and no more: a
    deeper walk would start finding a ``manifest.json`` that belongs to
    something else.

    Returns ``None`` when no ``manifest.json`` is found, and raises
    ``ValueError`` when more than one candidate exists -- picking one would
    be choosing which baseline-set to publish by directory-iteration order.
    """
    base = Path(directory)
    if (base / BASELINE_MANIFEST_FILENAME).is_file():
        return base
    if not base.is_dir():
        return None
    candidates = sorted(
        child
        for child in base.iterdir()
        if child.is_dir() and (child / BASELINE_MANIFEST_FILENAME).is_file()
    )
    if len(candidates) > 1:
        raise ValueError(
            f"{base} contains {len(candidates)} baseline-sets "
            f"({', '.join(c.name for c in candidates)}) -- name exactly one."
        )
    return candidates[0] if candidates else None


def _path_issue(role: str, library: str, rel: str, root: Path) -> str | None:
    """Why *rel* may not be published as an archive member, or ``None``.

    Every rule here is about the *archive*, so each is checked on the
    declared string rather than on what the local filesystem happens to
    resolve it to: a backslash is a separator on the machine that will
    extract this even when it is an ordinary filename character on the one
    that captured it.
    """
    if not rel:
        return f"library {library!r} declares no {role} path."
    if rel.startswith("/") or (len(rel) > 1 and rel[1] == ":"):
        return f"library {library!r} declares an absolute {role} path {rel!r}."
    if any(ch in _UNPORTABLE_CHARS for ch in rel):
        return (
            f"library {library!r} declares a {role} path {rel!r} containing a "
            "character that cannot be extracted portably."
        )
    parts = rel.split("/")
    if ".." in parts:
        return (
            f"library {library!r} declares a {role} path {rel!r} that escapes "
            "the baseline-set directory."
        )
    path = root / rel
    # Resolved against the real root, because a symlink *inside* the set can
    # point out of it without the declared path ever saying ".." -- and an
    # archive member that is a symlink is refused outright, matching the
    # structural rule publish-baseline.yml already applies to an existing
    # asset.
    if path.is_symlink():
        return f"library {library!r}'s {role} {rel!r} is a symlink."
    try:
        path.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (OSError, ValueError):
        return (
            f"library {library!r} declares a {role} {rel!r} that is missing "
            "from the baseline-set, or resolves outside it."
        )
    if not path.is_file():
        return f"library {library!r}'s {role} {rel!r} is not a regular file."
    return None


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _snapshot_content_sha256(path: Path) -> str:
    """The *stable content* hash a resolver will later verify.

    Not the file's raw bytes: a snapshot carries fields a dump stamps fresh
    every run, so the published manifest records the volatile-stripped hash
    and so must this. Imported lazily for the same reason
    ``build_manifest.py`` does -- this module's own import surface stays
    what its docstring says it is.
    """
    from ..snapshot_io import read_snapshot_bytes
    from ..storage.sectioned_document import (
        from_sectioned_document,
        is_sectioned_document,
    )

    raw = json.loads(read_snapshot_bytes(path).decode("utf-8"))
    if is_sectioned_document(raw):
        raw = from_sectioned_document(raw)
    return compute_snapshot_content_hash(raw)


def _schema_errors(manifest: BaselineManifest) -> list[str]:
    errors: list[str] = []
    if manifest.manifest_version not in SUPPORTED_MANIFEST_VERSIONS:
        errors.append(
            f"manifest_version {manifest.manifest_version!r} is not one this "
            f"build understands ({sorted(SUPPORTED_MANIFEST_VERSIONS)}) -- "
            "publishing it would mint an asset its own resolver rejects as "
            "stale_schema."
        )
    schema = manifest.snapshot_schema
    if schema is not None and schema > serialization.SCHEMA_VERSION:
        errors.append(
            f"snapshot_schema {schema} is newer than this build understands "
            f"({serialization.SCHEMA_VERSION})."
        )
    return errors


def validate_precaptured_baseline_set(
    directory: Path | str,
    *,
    expected_profile: str = "",
    expected_project_ref: str = "",
    expected_generation: int | None = None,
) -> PrecapturedValidation:
    """Check a captured baseline-set against what it is being published as.

    Every expectation is optional and each is checked only when stated --
    but a *stated* expectation the set does not meet is always an error,
    never a warning and never silently adopted from the set itself. The
    asset name, the release tag and the generation a caller publishes under
    are how a consumer will later ask for this set; a set whose own manifest
    disagrees with any of them is mislabelled at the moment it is written,
    and an immutable channel gives no second chance to notice.

    All failures are collected rather than raised one at a time: a publisher
    run that reports one problem per attempt turns a mislabelled capture into
    several round trips through a workflow.
    """
    root = None
    try:
        root = locate_baseline_set(directory)
    except ValueError as exc:
        return PrecapturedValidation(errors=[str(exc)])
    if root is None:
        return PrecapturedValidation(
            errors=[
                f"no {BASELINE_MANIFEST_FILENAME} found at or directly below "
                f"{directory} -- this path holds no baseline-set."
            ]
        )
    try:
        manifest = load_baseline_manifest(root)
    except ValueError as exc:
        return PrecapturedValidation(root=root, errors=[str(exc)])
    assert manifest is not None  # locate_baseline_set found the file

    errors = _schema_errors(manifest)
    if expected_profile and manifest.profile != expected_profile:
        errors.append(
            f"the set declares profile {manifest.profile!r}, but it is being "
            f"published as {expected_profile!r}."
        )
    if expected_project_ref and manifest.project_ref != expected_project_ref:
        errors.append(
            f"the set records capture revision (project_ref) "
            f"{manifest.project_ref!r}, but it is being published against "
            f"{expected_project_ref!r} -- a consumer supplying that ref as "
            "resolve-baseline's expected-project-ref would reject this asset."
        )
    if expected_generation is not None and manifest.baseline_generation != (
        expected_generation
    ):
        errors.append(
            f"the set declares baseline_generation "
            f"{manifest.baseline_generation!r}, but it is being published as "
            f"generation {expected_generation}."
        )

    if not manifest.artifacts:
        errors.append(
            "the set declares no artifacts -- publishing it would mint an "
            "asset that resolves nothing."
        )
    libraries: list[str] = []
    for artifact in manifest.artifacts:
        library = artifact.library or "<unnamed>"
        if not artifact.library:
            errors.append("an artifacts[] row declares no library name.")
        elif manifest.artifact_count_for(artifact.library) > 1:
            if artifact.library not in libraries:
                errors.append(
                    f"library {artifact.library!r} is declared by more than "
                    "one artifacts[] row."
                )
        libraries.append(artifact.library)

        issue = _path_issue("snapshot", library, artifact.snapshot, root)
        if issue:
            errors.append(issue)
        elif artifact.sha256:
            try:
                actual = _snapshot_content_sha256(root / artifact.snapshot)
            except (OSError, ValueError) as exc:
                errors.append(
                    f"library {library!r}'s snapshot {artifact.snapshot!r} "
                    f"could not be read: {exc}"
                )
            else:
                if actual != artifact.sha256:
                    errors.append(
                        f"library {library!r}'s snapshot content hashes to "
                        f"{actual!r}, but the manifest declares "
                        f"{artifact.sha256!r} -- the set is self-inconsistent."
                    )
        else:
            errors.append(
                f"library {library!r} declares no snapshot sha256, so its "
                "content cannot be verified."
            )

        if artifact.binary:
            issue = _path_issue("binary", library, artifact.binary, root)
            if issue:
                errors.append(issue)
            elif artifact.binary_sha256:
                actual = _file_sha256(root / artifact.binary)
                if actual != artifact.binary_sha256:
                    errors.append(
                        f"library {library!r}'s staged binary hashes to "
                        f"{actual!r}, but the manifest declares "
                        f"{artifact.binary_sha256!r}."
                    )

    return PrecapturedValidation(
        root=root,
        profile=manifest.profile,
        project_ref=manifest.project_ref,
        baseline_generation=manifest.baseline_generation,
        libraries=[lib for lib in libraries if lib],
        errors=errors,
    )
