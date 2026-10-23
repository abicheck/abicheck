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

"""A static archive as the ``compare --no-baseline`` operand (ADR-086).

``compare --no-baseline libfoo.a -H include/`` audits declared-vs-defined:
the public headers are parsed exactly as a header-only dump parses them
(:func:`abicheck.header_only_dump.build_header_only_snapshot`), and the
export table is the archive's own linker-written symbol index
(:func:`abicheck.extract.ar_archive.read_archive_exports`). Nothing else is
read from the archive: it has no dynamic section, so SONAME, symbol
versioning and visibility stay unobserved, and the snapshot says so through
:attr:`~abicheck.model.AbiSnapshot.static_archive`.

Every other command keeps rejecting an archive (``resolve_input``'s own
error); only the no-baseline audit opts in, through ``static_archive=True``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..errors import ValidationError

if TYPE_CHECKING:
    from pathlib import Path

    from ..compile_context import CompileContext
    from ..model import AbiSnapshot

__all__ = ["STATIC_ARCHIVE_NOTICE", "dump_static_archive_snapshot"]

#: The run-level notice (``AbiSnapshot.load_notices`` ->
#: ``DiffResult.coverage_warnings``) naming what an archive cannot show.
STATIC_ARCHIVE_NOTICE = (
    "static archive '{name}': exports read from the archive's symbol index "
    "(global and weak definitions of its members); SONAME, DT_NEEDED, symbol "
    "versioning and symbol visibility are not collected for an archive"
)


def dump_static_archive_snapshot(
    path: Path,
    headers: list[Path],
    includes: list[Path],
    version: str,
    lang: str,
    *,
    lang_explicit: bool = False,
    public_headers: list[Path] | None = None,
    public_header_dirs: list[Path] | None = None,
    header_backend: str = "auto",
    compile: CompileContext | None = None,
    include_dependencies: bool = False,
) -> AbiSnapshot:
    """Build the audit snapshot for the static archive at *path*.

    Raises :class:`ValidationError` -- never returns an empty export table --
    when the archive cannot answer "what does it define": no symbol index
    (the message says to run ``ranlib``), a thin archive, or members that
    are not all ELF objects of one target. Headers are required: without a
    declared surface there is nothing for the audit to compare.
    """
    from ..dry_run_estimate import expand_header_inputs
    from ..extract.ar_archive import (
        ARCHIVE_EXPORTS_READ,
        archive_elf_metadata,
        read_archive_exports,
    )
    from ..extract.ar_archive_format import ArchiveFormatError
    from ..header_only_dump import build_header_only_snapshot
    from .snapshot_factory import (
        DependencyScopeInputs,
        ProvenanceInputs,
        SnapshotFinish,
        finish_snapshot,
    )

    try:
        read = read_archive_exports(path)
    except (OSError, ArchiveFormatError) as exc:
        raise ValidationError(f"cannot read static archive '{path}': {exc}") from exc
    if read.status != ARCHIVE_EXPORTS_READ:
        raise ValidationError(f"static archive '{path}': {read.reason}.")
    resolved_headers = expand_header_inputs(headers)
    if not resolved_headers:
        raise ValidationError(
            f"static archive '{path}' needs public headers (-H/--header): the "
            "archive audit compares the declared surface against the archive's "
            "symbol index, and there is no declared surface without headers."
        )

    snap = build_header_only_snapshot(
        library_hint=path,
        version=version,
        headers=resolved_headers,
        extra_includes=list(includes),
        dump_manifest=None,
        backend=header_backend,
        compile=compile,
        lang=lang,
        lang_explicit=lang_explicit,
        public_headers=list(public_headers or []),
        public_header_dirs=list(public_header_dirs or []),
    )
    snap = finish_snapshot(
        snap,
        SnapshotFinish(
            provenance=ProvenanceInputs(
                [*(public_headers or []), *resolved_headers],
                list(public_header_dirs or []),
                include_search_dirs=list(includes),
            ),
            dependency_scope=DependencyScopeInputs(
                include_dependencies, header_roots=resolved_headers
            ),
        ),
    )
    # Header facts above, archive export facts here: from this point the
    # snapshot is a binary-backed (export-table-carrying) ELF snapshot whose
    # dynamic section was never observable.
    snap.elf = archive_elf_metadata(read)
    snap.platform = "elf"
    snap.header_only = False
    snap.static_archive = True
    snap.library = path.name
    snap.source_path = str(path.resolve())
    snap.load_notices = (STATIC_ARCHIVE_NOTICE.format(name=path.name),)
    return snap
