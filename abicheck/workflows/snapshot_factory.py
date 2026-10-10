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

"""The one place a fresh :class:`~abicheck.model.AbiSnapshot` is built.

Design-hardening Phase 2 (F2, ``docs/contribute/plans/design-hardening-
from-defect-families.md``). Snapshots used to be constructed at about two
dozen sites -- each dump format, the DWARF-only and symbols-only fallbacks,
the header-only parse, BTF/CTF, kABI, the source-only path, several empty
stand-ins -- and the three finishing passes (declaration provenance,
dependency scoping, ownership) were each re-applied by hand wherever the
author remembered them. A route that forgot one produced a snapshot that
classified differently from the identical input on another route, which is
the F2 family: one semantic request, two answers.

Now:

* :func:`new_snapshot` constructs. Every production ``AbiSnapshot(...)``
  call outside the storage decoders goes through it; the ``repo_scan`` gate
  in ``tests/test_snapshot_factory_gate.py`` rejects any other.
* :func:`finish_snapshot` applies the finishing passes in their one fixed
  order -- provenance, then dependency scoping, then ownership -- each only
  when its inputs are given. Ownership reads origin, and scoping reads the
  header roots provenance resolved, so the order is not a style choice.
  :func:`new_snapshot` accepts the same :class:`SnapshotFinish`, so a
  producer that has its inputs at construction time finishes in one call.
* :func:`absent_baseline` is the stand-in for "no OLD side" (a
  ``--no-baseline`` audit, the first entry of a history): a snapshot that
  declares, exports and knows nothing. It is built here, never by an inner
  layer, which is why the post-processing pipeline receives it rather than
  making one.

Implementation of each pass stays with its owner
(:func:`abicheck.provenance.apply_provenance`,
:func:`abicheck.dumper_scoping.resolve_dependency_scope`,
:func:`abicheck.workflows.ownership_request.classify_extracted`); this
module only decides that they run, and in what order. Their imports are
function-local so this module stays a leaf that every producer, including
``dumper.py``, can import without joining an import cycle.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..model import AbiSnapshot

if TYPE_CHECKING:
    from ..model.ownership_rules import OwnershipRequest

__all__ = [
    "DependencyScopeInputs",
    "OwnershipInputs",
    "ProvenanceInputs",
    "SnapshotFinish",
    "absent_baseline",
    "finish_dependency_scope",
    "finish_ownership",
    "finish_provenance",
    "finish_snapshot",
    "new_snapshot",
]


@dataclass(frozen=True)
class ProvenanceInputs:
    """The public-header set declaration origin is classified against."""

    public_headers: Sequence[Path] | None = None
    public_header_dirs: Sequence[Path] | None = None
    include_search_dirs: Sequence[Path] | None = None


@dataclass(frozen=True)
class DependencyScopeInputs:
    """Whether toolchain/system declarations are kept, and the roots that
    mark the library's own headers (so an installed library under a
    system-like prefix is not mistaken for a dependency)."""

    include_dependencies: bool
    header_roots: Sequence[Path] | None = None


@dataclass(frozen=True)
class OwnershipInputs:
    """ADR-075 ownership classification inputs; ``request=None`` uses the
    enclosing run's project rules."""

    request: OwnershipRequest | None = None
    headers: Sequence[Path] | None = None
    public_header_dirs: Sequence[Path] | None = None


@dataclass(frozen=True)
class SnapshotFinish:
    """Which finishing passes a snapshot receives. ``None`` skips a pass."""

    provenance: ProvenanceInputs | None = None
    dependency_scope: DependencyScopeInputs | None = None
    ownership: OwnershipInputs | None = None


def finish_snapshot(snapshot: AbiSnapshot, finish: SnapshotFinish) -> AbiSnapshot:
    """Apply *finish*'s passes to *snapshot* in the canonical order.

    Returns the finished snapshot. Provenance and ownership stamp in place;
    dependency scoping may return a new object, so callers must use the
    return value.
    """
    if finish.provenance is not None:
        from ..provenance import apply_provenance

        p = finish.provenance
        apply_provenance(
            snapshot,
            list(p.public_headers) if p.public_headers is not None else None,
            list(p.public_header_dirs) if p.public_header_dirs is not None else None,
            include_search_dirs=(
                list(p.include_search_dirs)
                if p.include_search_dirs is not None
                else None
            ),
        )
    if finish.dependency_scope is not None:
        from .dump.dependency_scope import resolve_dependency_scope

        d = finish.dependency_scope
        snapshot = resolve_dependency_scope(
            snapshot, d.include_dependencies, d.header_roots
        )
    if finish.ownership is not None:
        from .ownership_request import classify_extracted

        o = finish.ownership
        classify_extracted(snapshot, o.request, o.headers, o.public_header_dirs)
    return snapshot


def finish_provenance(
    snapshot: AbiSnapshot,
    public_headers: Sequence[Path] | None,
    public_header_dirs: Sequence[Path] | None,
    *,
    include_search_dirs: Sequence[Path] | None = None,
) -> AbiSnapshot:
    """:func:`finish_snapshot` with only the provenance pass -- the common case
    for a binary dump, whose scoping and ownership are applied later by the
    run-level wrappers."""
    return finish_snapshot(
        snapshot,
        SnapshotFinish(
            provenance=ProvenanceInputs(
                public_headers, public_header_dirs, include_search_dirs
            )
        ),
    )


def finish_binary_dump(
    snapshot: AbiSnapshot,
    public_headers: Sequence[Path] | None,
    public_header_dirs: Sequence[Path] | None,
    *,
    include_search_dirs: Sequence[Path] | None = None,
) -> AbiSnapshot:
    """``dumper.dump``'s shared tail for every binary format: record the
    image's own build mode (ELF ``DW_AT_producer``/``DW_AT_language``/
    ``.comment``, :mod:`abicheck.extract.build_mode_capture`), then
    :func:`finish_provenance`."""
    from ..extract.build_mode_capture import capture_elf_build_mode

    capture_elf_build_mode(snapshot)
    return finish_provenance(
        snapshot,
        public_headers,
        public_header_dirs,
        include_search_dirs=include_search_dirs,
    )


def finish_dependency_scope(
    snapshot: AbiSnapshot,
    include_dependencies: bool,
    header_roots: Sequence[Path] | None = None,
) -> AbiSnapshot:
    """:func:`finish_snapshot` with only the dependency-scoping pass."""
    return finish_snapshot(
        snapshot,
        SnapshotFinish(
            dependency_scope=DependencyScopeInputs(include_dependencies, header_roots)
        ),
    )


def finish_ownership(
    snapshot: AbiSnapshot,
    request: OwnershipRequest | None,
    headers: Sequence[Path] | None,
    public_header_dirs: Sequence[Path] | None,
) -> AbiSnapshot:
    """:func:`finish_snapshot` with only the ownership pass (ADR-075)."""
    return finish_snapshot(
        snapshot,
        SnapshotFinish(ownership=OwnershipInputs(request, headers, public_header_dirs)),
    )


def new_snapshot(
    *,
    library: str,
    version: str,
    finish: SnapshotFinish | None = None,
    **fields: Any,
) -> AbiSnapshot:
    """Construct an :class:`AbiSnapshot` from *fields* and, when *finish* is
    given, apply its passes. The only production constructor outside the
    storage decoders."""
    snapshot = AbiSnapshot(library=library, version=version, **fields)
    if finish is None:
        return snapshot
    return finish_snapshot(snapshot, finish)


def absent_baseline(library: str) -> AbiSnapshot:
    """The OLD-side stand-in for a run with no baseline.

    Declares, exports and knows nothing: a union with it is the other side,
    and a membership test against it answers "not there before". Never a
    copy of the candidate, which would answer "unchanged" about a history
    nobody observed.
    """
    return new_snapshot(library=library, version="")
