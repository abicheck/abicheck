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

"""Baseline publishing (G30 P1.6, ADR-047 section 6/section 10).

Bridges a project's already-validated ``build-output.json`` (G30 P1.1,
:mod:`~.build_output`) to ``actions/baseline``'s ``libraries`` JSON input --
the caller (``publish-baseline.yml``/``update-main-baseline.yml``) never
re-reads ``.abicheck.yml`` for this: every ``BuildOutputTarget`` this build
produced, and whether it is a release-bundle member (``target.bundle``, set
directly on the target -- ADR-047 section 2), is already recorded in
``build-output.json`` itself.

Also provides the Actions-cache key helpers ADR-047 section 10 requires for
``accepted-main`` refreshes: GitHub Actions cache entries are immutable once
written, so ``update-main-baseline.yml`` must write a new key on every
refresh (``<key_prefix>-<profile_id>-<head_sha>``) and ``resolve-baseline``
must restore from the latest entry matching a stable prefix
(``<key_prefix>-<profile_id>-``). A workflow that reused one stable key
across refreshes would silently stop updating after the first write (the
cache action reports that as a hit, not an error) -- exactly the kind of
silent-shallow-success failure mode this ADR exists to close.

Pure: no file I/O beyond what :class:`~.build_output.BuildOutput` already
carries in memory, no subprocess, no cache/release API calls (those stay the
calling workflow's job, per ADR-047 section 10's "this Action never fetches"
boundary already established by ``actions/baseline``/``actions/
resolve-baseline``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .build_output import BuildOutput, BuildOutputTarget

#: Default ``key_prefix`` (ADR-047 section 10) when a baseline channel's
#: ``.abicheck.yml`` entry doesn't set one -- matches the ADR's own example
#: (``key_prefix: "abicheck-baseline-main"``).
DEFAULT_ACCEPTED_MAIN_KEY_PREFIX = "abicheck-baseline-main"


def _resolve_under_root(root: Path, rel: str) -> Path | None:
    """Resolve *rel* under *root*, refusing an absolute path or an escape.

    A local copy of :func:`~.build_output._resolve_under_root`'s identical
    guard -- mirrors :mod:`~.baseline_set`'s own precedent of keeping this
    small safety check duplicated per-module (see
    ``_resolve_under_baseline_dir``'s docstring there) rather than importing
    a leading-underscore helper across a module boundary.
    """
    if Path(rel).is_absolute():
        return None
    candidate = (root / rel).resolve()
    root_resolved = root.resolve()
    if candidate != root_resolved and not candidate.is_relative_to(root_resolved):
        return None
    return root / rel


@dataclass
class BaselineLibrariesReport:
    """Result of :func:`derive_baseline_libraries`."""

    entries: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "entries": list(self.entries),
            "errors": list(self.errors),
        }


def _target_headers(
    root: Path, target: BuildOutputTarget
) -> tuple[list[str], list[str]]:
    """Returns ``(resolved_roots, errors)`` for *target*'s public + generated
    header roots, resolved to absolute paths under *root* -- the paths
    ``actions/baseline``'s ``libraries[].header`` (``abicheck dump -H``,
    which accepts a directory) expects, since ``dump`` runs from wherever the
    calling workflow happens to have its working directory, not necessarily
    *root* itself."""
    resolved: list[str] = []
    errors: list[str] = []
    for rel in (*target.public_header_roots, *target.generated_header_roots):
        r = _resolve_under_root(root, rel)
        if r is None:
            errors.append(
                f"target {target.id!r}: header root {rel!r} is absolute or "
                "escapes the build-output root -- refusing to resolve it."
            )
            continue
        resolved.append(str(r))
    return resolved, errors


def derive_baseline_libraries(
    build_output: BuildOutput, root: Path | str
) -> BaselineLibrariesReport:
    """Derive ``actions/baseline``'s ``libraries`` JSON input from
    *build_output* (already-loaded ``build-output.json``), resolving every
    path against *root* (the downloaded ``abicheck-build-<profile>/``
    directory build-output.json's own paths are relative to).

    One entry per :class:`~.build_output.BuildOutputTarget`, in declaration
    order. ``stage_binary`` is set for any target whose own ``bundle`` field
    is non-empty (ADR-047 section 8 S14 correction) -- a release-bundle
    member's real ELF binary must be staged for bundle-scoped analysis to
    have an old side at all, while a standalone (non-bundle) target only
    ever needs its ``.abicheck.json`` snapshot.

    Never raises: a missing/escaping binary or header path is reported in
    ``report.errors`` (mirroring :func:`~.build_output.validate_build_output`
    /:func:`~.run_plan.generate_run_plan`'s own "report, don't throw"
    contract) rather than aborting after only some targets were processed --
    the caller decides whether ``report.ok`` should be a hard failure.
    """
    root = Path(root)
    report = BaselineLibrariesReport()
    for target in build_output.targets:
        if not target.id:
            report.errors.append("a build-output.json target has no id set.")
            continue
        if not target.binary:
            report.errors.append(f"target {target.id!r} has no binary declared.")
            continue
        resolved_binary = _resolve_under_root(root, target.binary)
        if resolved_binary is None:
            report.errors.append(
                f"target {target.id!r}: binary {target.binary!r} is absolute "
                "or escapes the build-output root -- refusing to resolve it."
            )
            continue
        if not resolved_binary.is_file():
            report.errors.append(
                f"target {target.id!r}: binary {target.binary!r} does not "
                f"exist at {resolved_binary}."
            )
            continue
        header_roots, header_errors = _target_headers(root, target)
        report.errors.extend(header_errors)
        entry: dict[str, Any] = {"name": target.id, "artifact": str(resolved_binary)}
        if header_roots:
            entry["header"] = " ".join(header_roots)
        if target.bundle:
            entry["stage_binary"] = True
        report.entries.append(entry)
    if not build_output.targets:
        report.errors.append(
            "build-output.json declares no targets -- nothing to dump."
        )
    return report


def _fold_generation(key_prefix: str, generation: int | None) -> str:
    """Shared ``-g<generation>`` folding both functions below apply to
    ``key_prefix`` -- must match ``update-main-baseline.yml``'s own
    "Compute cache key" step's bash exactly (``[[ -n "$BASELINE_GENERATION"
    ]] && KEY_PREFIX="${KEY_PREFIX}-g${BASELINE_GENERATION}"``), or a
    consumer computing an expected key/restore-prefix from this pure-Python
    mirror would silently disagree with what that workflow actually wrote."""
    if generation is None:
        return key_prefix
    return f"{key_prefix}-g{generation}"


def accepted_main_cache_key(
    key_prefix: str,
    profile_id: str,
    head_sha: str,
    *,
    generation: int | None = None,
) -> str:
    """The per-refresh ``accepted-main`` Actions-cache key (ADR-047
    section 10): ``<key_prefix>-<profile_id>-<head_sha>`` -- or, when
    ``generation`` is given, ``<key_prefix>-g<generation>-<profile_id>-
    <head_sha>``. Must be unique on every ``update-main-baseline.yml`` run
    -- a cache key is immutable once written, so a caller that reused a
    stable key across refreshes would silently stop updating after the
    first write.

    ``generation`` (default ``None``, meaning "not tracking scanner-
    compatibility generations") must match whatever
    ``update-main-baseline.yml``'s own ``baseline-generation`` input was set
    to for the run whose key you're reproducing -- see
    docs/reference/publish-baseline.md's "Cache key contract" for the full
    consumer recipe. Passing an already-folded ``key_prefix`` (e.g.
    ``"abicheck-baseline-main-g3"``) with ``generation`` left ``None`` still
    works (this function treats ``key_prefix`` as opaque either way), but
    the explicit parameter is the documented, discoverable way to compute
    the identical key without a consumer having to know to pre-fold it
    themselves.
    """
    key_prefix = _fold_generation(key_prefix, generation)
    return f"{key_prefix}-{profile_id}-{head_sha}"


def accepted_main_cache_restore_prefix(
    key_prefix: str, profile_id: str, *, generation: int | None = None
) -> str:
    """The ``restore-keys`` prefix (ADR-047 section 10) ``resolve-baseline``
    -- or, before that primitive runs, ``update-main-baseline.yml``'s own
    freshness check -- uses to find the *latest* entry
    :func:`accepted_main_cache_key` wrote, regardless of which ``head_sha``
    it was written under. Same ``generation`` parameter/meaning as
    :func:`accepted_main_cache_key`."""
    key_prefix = _fold_generation(key_prefix, generation)
    return f"{key_prefix}-{profile_id}-"
