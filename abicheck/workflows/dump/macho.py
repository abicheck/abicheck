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

"""Mach-O primary extraction: the ``macho`` binary-format adapter's body.

Moved verbatim from ``service_dump_native_pe._dump_macho`` (lane B, stage
B1b). :class:`abicheck.workflows.dump.formats.MachoAdapter` calls
:func:`extract_macho`.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ...errors import SnapshotError
from ...extract.surface_fact_producers import export_table_surface_facts
from ...model import AbiSnapshot, Function, Visibility
from ..snapshot_factory import new_snapshot
from .scoped import try_header_scoped_dump

if TYPE_CHECKING:
    from ...compile_context import CompileContext


def extract_macho(
    path: Path,
    version: str,
    *,
    headers: list[Path] | None = None,
    includes: list[Path] | None = None,
    lang: str = "c++",
    lang_explicit: bool = False,
    header_backend: str = "auto",
    compile: CompileContext | None = None,
    public_headers: list[Path] | None = None,
    public_header_dirs: list[Path] | None = None,
    include_labels: dict[Path, str] | None = None,
) -> AbiSnapshot:
    """Dump a Mach-O binary (macOS dylib) to an ABI snapshot.

    When *headers* are supplied the ABI surface is scoped to declarations in
    those public headers via castxml; otherwise the full export table is used.
    """
    from ...macho_metadata import parse_macho_metadata

    try:
        macho_meta = parse_macho_metadata(path)
    except (RuntimeError, OSError, ValueError) as exc:
        raise SnapshotError(f"Failed to parse Mach-O '{path}': {exc}") from exc

    if (
        not macho_meta.exports
        and not macho_meta.install_name
        and not macho_meta.dependent_libs
    ):
        raise SnapshotError(
            f"Mach-O file '{path}' has no exports or load-command metadata. "
            "Verify the file is a valid dynamic library."
        )

    scope_fallback: str | None = None
    if headers:
        scoped, scope_fallback = try_header_scoped_dump(
            "macho",
            path,
            headers,
            includes or [],
            version,
            lang,
            lang_explicit=lang_explicit,
            header_backend=header_backend,
            compile=compile,
            public_headers=public_headers,
            public_header_dirs=public_header_dirs,
            include_labels=include_labels,
        )
        if scoped is not None:
            return scoped

    funcs = [
        Function(
            name=exp.name,
            mangled=exp.name,
            return_type="?",
            visibility=Visibility.PUBLIC,
            # An export-table entry: (c) confirmed, (a)/(b) never looked at
            # on this path -- see model/surface_facts.py.
            **export_table_surface_facts(),
            is_extern_c=not exp.name.startswith("_Z"),
        )
        for exp in macho_meta.exports
        if exp.name
    ]
    return new_snapshot(
        library=path.name,
        version=version,
        functions=funcs,
        macho=macho_meta,
        platform="macho",
        scope_fallback=scope_fallback,
    )
