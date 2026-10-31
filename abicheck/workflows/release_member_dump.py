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

"""Dump one release member for a ``BundleFacts`` capture.

The one place a release member is dumped *on its own* (not as one side of a
member comparison): ``compare --bundle-facts-out`` uses it for every OLD
member no successful pair captured, and ``dump --bundle-facts-out`` uses it
for every member of the release it captures. Both therefore persist a member
the same way: the ``DumpRequest -> resolve_dump_request ->
execute_dump_request`` pipeline ``dump`` itself runs (ADR-063 D1), under the
extraction context the release was given -- including the canonical
``--exclude-header`` rules -- and, when that dump fails, an ELF-only
snapshot that *carries* its failure (ADR-065 D8: persisted as a degraded
member), never an abort and never a silently impoverished member.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from .release_scope import StrandedLibraryResolution

if TYPE_CHECKING:
    from ..compile_context import CompileContext
    from ..model import AbiSnapshot


@dataclass(frozen=True)
class ReleaseMemberDumpContext:
    """The extraction context every member of one release side is dumped
    under -- the fields a member comparison's own OLD-side dump reads."""

    headers: Sequence[Path] = ()
    includes: Sequence[Path] = ()
    version: str = ""
    lang: str = "c++"
    lang_explicit: bool = False
    depth: str | None = None
    compile_context: CompileContext | None = None
    include_dependencies: bool = False
    public_header_dirs: Sequence[Path] | None = None
    exclude_headers: Sequence[str] = field(default_factory=tuple)
    #: A debug package's extracted tree (``--debug-info old=PKG``), searched
    #: per member; ``None`` when the side has none.
    debug_dir: Path | None = None


def dump_release_member(
    path: Path,
    ctx: ReleaseMemberDumpContext,
    *,
    notice: Callable[[str], None],
) -> StrandedLibraryResolution:
    """Dump the member at *path* under *ctx* (see this module's docstring)."""
    from . import extraction
    from .dump.request import execute_dump_request, resolve_dump_request
    from .request_inputs import InputSpec
    from .service_requests import DumpRequest

    debug = (
        extraction.resolve_package_debug_info(path, ctx.debug_dir)
        if ctx.debug_dir
        else None
    )
    try:
        dump_request = DumpRequest(
            input=InputSpec.of(
                path,
                headers=list(ctx.headers),
                includes=list(ctx.includes),
                version=ctx.version,
                pdb=debug,
                compile=ctx.compile_context,
                include_system_declarations=ctx.include_dependencies,
                public_header_dirs=ctx.public_header_dirs,
                exclude_headers=ctx.exclude_headers,
            ),
            lang=ctx.lang,
            lang_explicit=ctx.lang_explicit,
            depth=ctx.depth,
        )
        resolved = resolve_dump_request(dump_request)
        return StrandedLibraryResolution(execute_dump_request(resolved).snapshot)
    except Exception as exc:
        notice(f"{path.name}: ELF-only ({exc})")
        snapshot, failure = _elf_only_capture(path, exc)
        return StrandedLibraryResolution(snapshot, failure=failure)


def _elf_only_capture(path: Path, exc: Exception) -> tuple[AbiSnapshot, str]:
    from ..elf_metadata import ElfMetadata
    from ..extract.parse_failures import recording_export_read_failures
    from . import extraction
    from .snapshot_factory import new_snapshot

    with recording_export_read_failures() as export_failures:
        elf = extraction.parse_elf_metadata(path)
    failure = f"ELF-only degraded capture: {exc}"
    if export_failures:
        # The same rule the dump applies: a table that was not read is not
        # persisted as a partial one. The member keeps an unread export
        # table, which every reader already treats as FAILED.
        elf = ElfMetadata()
        reasons = "; ".join(dict.fromkeys(export_failures))
        failure += f"; export table not read: {reasons}"
    snapshot = new_snapshot(
        library=path.name,
        version="",
        elf=elf,
        # ADR-065 D8: no header/DWARF evidence at all -- every detector that
        # special-cases elf_only_mode must see the reduced tier.
        elf_only_mode=True,
    )
    return snapshot, failure
