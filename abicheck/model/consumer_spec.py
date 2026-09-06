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

"""Consumer specification — Workstream D-S1 (vision-api-abi-evolution.md
"D. Optional prebuilt-consumer lifecycle").

Before this module, a supplied ``--used-by`` consumer was a bare binary
:class:`~pathlib.Path` -- no identity beyond "the file at this path", no way
to say a consumer is merely nice-to-have evidence versus a required gate
input, and an unreadable binary was always a hard error regardless of which
of those two the caller meant. This module adds the shape:

- :class:`ConsumerSpec` -- a consumer input with optional manifest/digest/
  platform/profile/provider-baseline provenance and an advisory/required
  distinction (:class:`ConsumerRequirement`), on top of the one field every
  consumer must still carry: its binary ``path``.
- :func:`parse_consumer_manifest` -- reads a small JSON document describing
  one or more consumers (the ``--used-by-manifest`` CLI input) into a list
  of :class:`ConsumerSpec`.
- :exc:`ConsumerUnreadableError` / :exc:`ConsumerDigestMismatchError` --
  typed failures a caller can catch to apply the advisory/required split,
  distinct from an arbitrary :class:`ValueError` elsewhere in the consumer
  path.

This is identity/provenance bookkeeping only -- no consumer code is ever
executed (ADR-060 stays deferred; the only subprocess adjacency anywhere in
this path remains the demangler prewarm noted there).
"""

from __future__ import annotations

import enum
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class ConsumerRequirement(enum.Enum):
    """Whether an unreadable/unresolvable consumer aborts the run.

    ``REQUIRED`` is the default and preserves the historical behavior: a
    consumer that cannot be parsed (unrecognized binary format, digest
    mismatch) is a hard error, exactly as a bare ``--used-by <path>`` has
    always been. ``ADVISORY`` downgrades that same failure to a recorded,
    reported skip -- the run continues, and the consumer is counted as
    "unreadable" in the N-of-M consumer-impact summary rather than aborting
    it.
    """

    REQUIRED = "required"
    ADVISORY = "advisory"


class ConsumerUnreadableError(ValueError):
    """A consumer binary's format could not be recognized/parsed.

    Subclasses :class:`ValueError` so every existing catch site written
    against :func:`~abicheck.appcompat.parse_app_requirements`'s previous
    bare ``ValueError`` keeps working unchanged.
    """


class ConsumerDigestMismatchError(ConsumerUnreadableError):
    """A consumer's expected :attr:`ConsumerSpec.digest` did not match the
    actual content at :attr:`ConsumerSpec.path`.

    A subclass of :class:`ConsumerUnreadableError` (not a sibling): a digest
    mismatch means "this is not, provably, the consumer the caller named" --
    exactly the same *unreadable-as-specified* failure mode as an
    unrecognized binary format, just caught earlier. Advisory/required
    handling treats the two identically.
    """


@dataclass(frozen=True)
class ConsumerSpec:
    """One ``--used-by`` consumer input, beyond a bare binary path.

    *path* is the only field every consumer must carry. Every other field is
    optional identity/provenance a caller may supply to sharpen what "this
    consumer" means, and is carried through into the reported
    ``used_by[]``/``consumer_scope`` JSON purely as provenance -- none of it
    is re-verified against a registry or fetched; *digest*, when present, is
    the one field this module itself checks (see :func:`verify_digest`).

    manifest:
        Path to the consumer-manifest document this spec was resolved from
        (``--used-by-manifest``), when it did not come from a bare
        ``--used-by <path>``. Provenance only -- never re-parsed here.
    digest:
        An expected content digest for *path*, ``"<algorithm>:<hexdigest>"``
        (e.g. ``"sha256:abcd..."``). When set, :func:`verify_digest` checks
        it against the real file content before the consumer is read, so a
        supplied consumer binary is provably the one the caller meant, not a
        stale or substituted artifact.
    platform:
        The consumer's target platform/triple, when known (e.g.
        ``"linux-x86_64"`` or ``"win32-msvc"``), reported as provenance.
    profile:
        The build profile/configuration the consumer was compiled under,
        when known (e.g. ``"release"``, ``"asan"``).
    provider_baseline:
        A free-form label for where this consumer artifact came from (a
        registry, a CI run id, a package release tag). Provenance only.
    requirement:
        :class:`ConsumerRequirement` -- ADVISORY or REQUIRED. Defaults to
        REQUIRED, matching the pre-existing hard-error behavior for a bare
        ``--used-by`` path.
    """

    path: Path
    manifest: Path | None = None
    digest: str | None = None
    platform: str | None = None
    profile: str | None = None
    provider_baseline: str | None = None
    requirement: ConsumerRequirement = ConsumerRequirement.REQUIRED

    @classmethod
    def from_path(cls, path: Path) -> ConsumerSpec:
        """Wrap a bare path exactly as every pre-S1 caller supplied one."""
        return cls(path=path)

    @property
    def is_advisory(self) -> bool:
        return self.requirement is ConsumerRequirement.ADVISORY

    def provenance(self) -> dict[str, str]:
        """A JSON-safe provenance dict, omitting unset fields."""
        out: dict[str, str] = {}
        if self.manifest is not None:
            out["manifest"] = str(self.manifest)
        if self.digest is not None:
            out["digest"] = self.digest
        if self.platform is not None:
            out["platform"] = self.platform
        if self.profile is not None:
            out["profile"] = self.profile
        if self.provider_baseline is not None:
            out["provider_baseline"] = self.provider_baseline
        out["requirement"] = self.requirement.value
        return out


#: A ``--used-by`` consumer input as any caller in this codebase may supply
#: it: either a bare path (the pre-S1 shape, still fully supported) or a
#: full :class:`ConsumerSpec`.
ConsumerAppInput = Path | ConsumerSpec


def as_consumer_spec(app: ConsumerAppInput) -> ConsumerSpec:
    """Normalize a bare path or an already-built spec into a
    :class:`ConsumerSpec`, so every downstream reader has one shape."""
    if isinstance(app, ConsumerSpec):
        return app
    return ConsumerSpec.from_path(app)


def verify_digest(spec: ConsumerSpec) -> None:
    """Check *spec*'s expected :attr:`ConsumerSpec.digest` against the real
    file content at :attr:`ConsumerSpec.path`.

    No-op when *spec* carries no digest. Raises
    :exc:`ConsumerDigestMismatchError` (subclass of :exc:`ValueError`) on a
    mismatch or an unreadable file/algorithm; never returns anything on
    success.
    """
    if spec.digest is None:
        return
    algorithm, _, expected_hex = spec.digest.partition(":")
    if not expected_hex:
        raise ConsumerDigestMismatchError(
            f"Malformed consumer digest {spec.digest!r} for {spec.path} -- "
            "expected '<algorithm>:<hexdigest>' (e.g. 'sha256:abcd...')."
        )
    try:
        hasher = hashlib.new(algorithm)
    except ValueError as exc:
        raise ConsumerDigestMismatchError(
            f"Unsupported digest algorithm {algorithm!r} for {spec.path}."
        ) from exc
    try:
        with open(spec.path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                hasher.update(chunk)
    except OSError as exc:
        raise ConsumerUnreadableError(
            f"Could not read consumer binary {spec.path} to verify its digest: {exc}."
        ) from exc
    actual_hex = hasher.hexdigest()
    if actual_hex.lower() != expected_hex.lower():
        raise ConsumerDigestMismatchError(
            f"Consumer {spec.path} content digest mismatch: expected "
            f"{spec.digest}, got {algorithm}:{actual_hex}."
        )


def _consumer_spec_from_entry(
    entry: dict[str, Any], manifest_path: Path
) -> ConsumerSpec:
    if "path" not in entry:
        raise ValueError(
            f"Consumer manifest {manifest_path}: entry missing required 'path' field: "
            f"{entry!r}"
        )
    raw_path = Path(entry["path"])
    # A relative path in the manifest resolves against the manifest's own
    # directory, not the process's current working directory -- the same
    # convention a project config file's own relative paths use, so a
    # manifest stays portable when checked out into a different tree.
    path = raw_path if raw_path.is_absolute() else (manifest_path.parent / raw_path)
    requirement_raw = entry.get("requirement", ConsumerRequirement.REQUIRED.value)
    try:
        requirement = ConsumerRequirement(requirement_raw)
    except ValueError as exc:
        raise ValueError(
            f"Consumer manifest {manifest_path}: entry for {raw_path} has "
            f"unknown requirement {requirement_raw!r} -- expected "
            f"{[r.value for r in ConsumerRequirement]!r}."
        ) from exc
    return ConsumerSpec(
        path=path,
        manifest=manifest_path,
        digest=entry.get("digest"),
        platform=entry.get("platform"),
        profile=entry.get("profile"),
        provider_baseline=entry.get("provider_baseline"),
        requirement=requirement,
    )


def parse_consumer_manifest(manifest_path: Path) -> list[ConsumerSpec]:
    """Parse a ``--used-by-manifest`` JSON document into consumer specs.

    Shape::

        {
          "consumers": [
            {
              "path": "bin/myapp",
              "digest": "sha256:...",
              "platform": "linux-x86_64",
              "profile": "release",
              "provider_baseline": "nightly-2026-09-05",
              "requirement": "advisory"
            }
          ]
        }

    Every field but ``path`` is optional. A relative ``path`` resolves
    against the manifest's own parent directory. Raises :class:`ValueError`
    (or a subclass) on a malformed document -- this is a *usage* error, not
    a consumer-readability one, so it is never subject to the advisory/
    required distinction the parsed specs themselves carry.
    """
    try:
        raw_text = manifest_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(
            f"Could not read consumer manifest {manifest_path}: {exc}"
        ) from exc
    try:
        document = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Consumer manifest {manifest_path} is not valid JSON: {exc}"
        ) from exc
    if not isinstance(document, dict) or not isinstance(
        document.get("consumers"), list
    ):
        raise ValueError(
            f"Consumer manifest {manifest_path} must be a JSON object with a "
            "'consumers' array."
        )
    return [
        _consumer_spec_from_entry(entry, manifest_path)
        for entry in document["consumers"]
    ]


@dataclass
class ConsumerImpactSummary:
    """ "N of M consumers affected" statistics across every supplied
    ``--used-by`` consumer in one run (Workstream D-S1).

    *total* counts every supplied consumer, including one that turned out
    unreadable. *evaluated* is *total* minus *unreadable_advisory* (an
    advisory-unreadable consumer contributes no verdict, so it is excluded
    from *affected*'s denominator concern -- "N of M" always means "N of the
    M this run could actually assess"). A required-but-unreadable consumer
    never reaches this summary at all: it raises and aborts the run before
    one is built, exactly as an unreadable bare ``--used-by`` path always
    has.
    """

    total: int = 0
    evaluated: int = 0
    affected: int = 0
    unreadable_advisory: int = 0
    unreadable_paths: tuple[str, ...] = field(default_factory=tuple)

    def to_json(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "evaluated": self.evaluated,
            "affected": self.affected,
            "unreadable_advisory": self.unreadable_advisory,
            "unreadable_paths": list(self.unreadable_paths),
        }
