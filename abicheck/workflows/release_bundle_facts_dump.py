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

"""``abicheck dump RELEASE --bundle-facts-out PATH``: capture one release's
``BundleFacts`` document without comparing it to anything.

Capturing a release's per-library snapshots is a dump: it reads one release
and writes facts about it. ``compare --bundle-facts-out`` captures the OLD
side as a by-product of a comparison; this is the same capture with no NEW
side. Nothing here is a second implementation of it -- it composes the
owners the comparison uses:

* **Members** come from :func:`~abicheck.workflows.no_baseline_set.
  resolve_no_baseline_set_plan`, the one-sided release resolver (directory,
  package archive, or stored ``ProjectSnapshot`` package; ``--dso-only``
  classification; the operand's own header tree folded into the side's
  headers and include roots).
* **Each member** is dumped by :func:`~abicheck.workflows.release_member_dump.
  dump_release_member` -- the function ``compare --bundle-facts-out`` dumps
  every unpaired OLD member with.
* **The public surface** is :func:`~abicheck.workflows.release_public_surface.
  reconcile_release_public_surface`'s acquisition of the same headers.
* **The document** is written by :func:`~abicheck.workflows.release_bundle.
  write_bundle_facts_out` -- the comparison's own producer, given no member
  pairs, so every member reaches it through the member-dump resolver.

Framework-free: no Click. A front end translates the typed
:class:`~abicheck.errors.ReleaseOperandError` family into usage errors.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..compile_context import CompileContext


@dataclass(frozen=True)
class ReleaseBundleFactsDumpRequest:
    """One ``dump --bundle-facts-out`` capture."""

    operand: Path
    output: Path
    headers: tuple[Path, ...] = ()
    includes: tuple[Path, ...] = ()
    version: str = ""
    lang: str = "c++"
    lang_explicit: bool = False
    depth: str | None = None
    compile_context: CompileContext | None = None
    include_dependencies: bool = False
    public_header_dirs: Sequence[Path] | None = None
    exclude_headers: Sequence[str] = field(default_factory=tuple)
    #: A stored ``ProjectSnapshot`` package's variant, when it has several.
    variant: str | None = None


@dataclass(frozen=True)
class ReleaseBundleFactsDumpResult:
    """What the capture wrote: every member key, and those persisted
    degraded (a failed dump or a stored package's own marker) with why."""

    members: tuple[str, ...]
    degraded: dict[str, str]
    warnings: tuple[str, ...] = ()


def dump_release_bundle_facts(
    request: ReleaseBundleFactsDumpRequest,
    *,
    notice: Callable[[str], None] = lambda _text: None,
) -> ReleaseBundleFactsDumpResult:
    """Capture *request*'s release into a ``BundleFacts`` document.

    Raises :class:`~abicheck.errors.ReleaseOperandContentError` for an
    operand holding no library, and the release resolution's other typed
    errors unchanged.
    """
    from ..errors import ReleaseOperandContentError
    from .extraction import ast_acquisition_scope
    from .no_baseline_set import (
        cleanup_no_baseline_set_plan,
        resolve_no_baseline_set_plan,
    )
    from .release_bundle import resolve_bundle_manifest, write_bundle_facts_out
    from .release_member_dump import ReleaseMemberDumpContext, dump_release_member
    from .release_public_surface import reconcile_release_public_surface
    from .release_scope import StrandedLibraryResolution
    from .release_stored_inventory import stored_side_degraded_members

    plan = resolve_no_baseline_set_plan(
        request.operand,
        operand_kind="release",
        variant=request.variant,
        side_label="old",
        headers=request.headers,
        includes=request.includes,
    )
    try:
        side = plan.side
        members = dict(side.members)
        if not members:
            raise ReleaseOperandContentError(
                f"{request.operand}: no library found to capture"
            )
        for warning in plan.warnings:
            notice(warning)
        ctx = ReleaseMemberDumpContext(
            headers=tuple(side.headers),
            includes=tuple(side.includes),
            version=request.version,
            lang=request.lang,
            lang_explicit=request.lang_explicit,
            depth=request.depth,
            compile_context=request.compile_context,
            include_dependencies=request.include_dependencies,
            public_header_dirs=request.public_header_dirs,
            exclude_headers=request.exclude_headers,
            debug_dir=side.debug_dir,
        )
        inherited = stored_side_degraded_members(
            request.operand, variant_id=request.variant
        )
        degraded = {k: v for k, v in inherited.items() if k in members}
        key_of = {path: key for key, path in members.items()}

        def _dump_member(path: Path) -> StrandedLibraryResolution:
            resolved = dump_release_member(path, ctx, notice=notice)
            if resolved.failure is not None:
                degraded.setdefault(key_of[path], resolved.failure)
            return resolved

        with ast_acquisition_scope():
            # The release's public contract from its own headers: the one
            # acquisition the comparison makes, with this side on both of its
            # inputs (the identity keys them alike, so it parses once).
            surface = reconcile_release_public_surface(
                [],
                expected_members=[path.name for path in members.values()],
                old_headers=side.headers,
                new_headers=side.headers,
                old_includes=side.includes,
                new_includes=side.includes,
                lang=request.lang,
                lang_explicit=request.lang_explicit,
                exclude_headers=request.exclude_headers,
                public_header_dirs=request.public_header_dirs,
                compile_context=request.compile_context,
                depth=request.depth,
                include_dependencies=request.include_dependencies,
            )
            write_bundle_facts_out(
                request.output,
                [],
                None,
                members,
                resolve_stranded_library=_dump_member,
                inherited_degraded=inherited,
                inventory_complete=not side.unclassified,
                public_surface=surface.old_surface,
                resolved_manifest=resolve_bundle_manifest(
                    None,
                    request.operand,
                    None,
                    members,
                    {},
                    old_variant=request.variant,
                    old_side_only=True,
                ),
            )
    finally:
        cleanup_no_baseline_set_plan(plan)
    return ReleaseBundleFactsDumpResult(
        members=tuple(sorted(members)),
        degraded=degraded,
        warnings=plan.warnings,
    )
