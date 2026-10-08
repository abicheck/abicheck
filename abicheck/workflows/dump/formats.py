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

"""Binary-format seam for the native dump (lane B, stage B1b).

``workflows.dump.native._run_dump_uncached`` builds one
:class:`NativeExtractRequest` and looks the detected format up in the
registry ``workflows.dump.native.FORMAT_ADAPTERS``. Each adapter performs only
the primary extraction; the shared post-extraction tail (metadata attach,
header graph, clang layout, closure renumbering) stays with the caller, so a
substituted adapter sees exactly the request the real one would and its
result runs through the same tail.

To substitute an extractor (a test fake, an embedder's own format), replace
the registry entry; ``tests/_dump_format_fakes.py`` does that for one
``with`` block.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Callable

    from ...compile_context import CompileContext
    from ...dump_manifest import DumpManifest
    from ...model import AbiSnapshot


# The parent facade's logger name, kept from before the move.
_logger = logging.getLogger("abicheck.service")


def emit_notice(notify: Callable[[str], None] | None, message: str) -> None:
    """Send a user-facing progress note to *notify*, or the logger if unset."""
    if notify is not None:
        notify(message)
    else:
        _logger.warning(message)


@dataclass(frozen=True)
class NativeExtractRequest:
    """Everything a format adapter may read for one primary extraction.

    Field names match the keyword arguments of the extractors in
    :mod:`.elf`/:mod:`.pe`/:mod:`.macho`. ``header_backend`` is the already
    resolved backend (an explicit ``compile.frontend`` wins over the bare
    argument). ``public_include_search_dirs`` is the caller's own explicit
    ``-I`` list, already defaulted to ``includes`` when the caller gave none.
    Fields a format does not use (``pdb_path`` on ELF, ``dwarf_only`` on PE)
    are ignored by that format's adapter.
    """

    path: Path
    version: str
    headers: list[Path]
    includes: list[Path]
    lang: str
    lang_explicit: bool = False
    header_backend: str = "auto"
    compile: CompileContext | None = None
    public_headers: list[Path] | None = None
    public_header_dirs: list[Path] | None = None
    public_include_search_dirs: list[Path] | None = None
    include_labels: dict[Path, str] | None = None
    pdb_path: Path | None = None
    dwarf_only: bool = False
    debug_roots: list[Path] | None = None
    enable_debuginfod: bool = False
    debuginfod_url: str | None = None
    debug_format: str | None = None
    symbols_only: bool = False
    debug_presence_only: bool = False
    dump_manifest: DumpManifest | None = None
    notify: Callable[[str], None] | None = None


@runtime_checkable
class BinaryFormatAdapter(Protocol):
    """Primary extraction for one binary format (``elf``/``pe``/``macho``)."""

    format: str

    def extract(self, request: NativeExtractRequest) -> AbiSnapshot:
        """Extract the format's own surface for ``request.path``.

        Raises :class:`~abicheck.errors.SnapshotError` when the binary cannot
        be parsed and :class:`~abicheck.errors.ValidationError` for invalid
        arguments, exactly as the extractor it wraps does.
        """
        ...


class PeAdapter:
    """PE: :func:`.pe.extract_pe` (export table, PDB, header scoping)."""

    format = "pe"

    def extract(self, request: NativeExtractRequest) -> AbiSnapshot:
        from .pe import extract_pe

        r = request
        return extract_pe(
            r.path,
            r.version,
            headers=r.headers,
            includes=r.includes,
            lang=r.lang,
            lang_explicit=r.lang_explicit,
            pdb_path=r.pdb_path,
            header_backend=r.header_backend,
            compile=r.compile,
            public_headers=r.public_headers,
            public_header_dirs=r.public_header_dirs,
            include_labels=r.include_labels,
        )


class MachoAdapter:
    """Mach-O: :func:`.macho.extract_macho` (export trie, header scoping)."""

    format = "macho"

    def extract(self, request: NativeExtractRequest) -> AbiSnapshot:
        from .macho import extract_macho

        r = request
        return extract_macho(
            r.path,
            r.version,
            headers=r.headers,
            includes=r.includes,
            header_backend=r.header_backend,
            lang=r.lang,
            lang_explicit=r.lang_explicit,
            compile=r.compile,
            public_headers=r.public_headers,
            public_header_dirs=r.public_header_dirs,
            include_labels=r.include_labels,
        )


#: PE and Mach-O. The ELF adapter lives with its extractor in
#: ``workflows.dump.native``, which composes the full registry
#: (``workflows.dump.native.FORMAT_ADAPTERS``).
DEFAULT_ADAPTERS: Mapping[str, BinaryFormatAdapter] = {
    "pe": PeAdapter(),
    "macho": MachoAdapter(),
}
