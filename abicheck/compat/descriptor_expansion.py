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

"""Expanding an ABICC descriptor's own declarations into run inputs.

Three closely-related jobs, all of them "read what the descriptor says and
turn it into something the engine takes": expanding a ``<headers>`` or
``<libs>`` *directory* operand into the files it names, and translating the
descriptor's ``<skip_namespaces>``/``<skip_symbols>``/``<skip_types>``/
``<skip_constants>`` rules into a real suppression.

Split out of ``compat/_helpers.py``, which is a
``legacy_generic_modules`` grab-bag already at the 800-line production
maximum. Giving this its own owner is the response ``architecture/``'s rules
ask for; trimming the grab-bag to fit is the one they forbid.
"""

from __future__ import annotations

import re as _re
from pathlib import Path
from typing import TYPE_CHECKING

from ..buildsource.build_query import PRUNED_HEADER_DIR_SEGMENTS
from ..errors import ValidationError
from ..header_utils import dedup_paths_preserve_order, iter_directory_headers
from ..suppression import SuppressionList

if TYPE_CHECKING:
    from collections.abc import Sequence


def expand_descriptor_headers(paths: Sequence[Path]) -> list[Path]:
    """*paths* with every directory entry expanded to the headers beneath it.

    ABICC's ``<headers>`` element takes a **directory** at least as often as
    it takes a file -- that is its documented, ordinary usage, and it is what
    real descriptors (Intel MKL's among them) contain. Before this expansion
    a directory value was passed through untouched and handed to the header
    parser as though it were a file, which reached castxml as
    ``#include "<some/dir>"`` and failed for a reason that named neither the
    descriptor nor the directory.

    Deliberately reuses ``header_utils.iter_directory_headers`` -- the same
    walk, suffix vocabulary and pruned-segment set that ``-H <dir>`` already
    expands through on the native ``dump``/``compare`` path -- rather than
    growing a second definition of "what counts as a header" for the compat
    front end. That set is ``HEADER_SUFFIXES``, the conservative
    standalone-TU one, not the ``.inl``/``.tcc``-inclusive cache superset: a
    template body is not a translation unit and must not be compiled as one.

    A directory with no headers in it raises rather than contributing
    nothing: silently expanding to an empty list would let a descriptor that
    points at the wrong tree produce a confident "no changes" verdict off an
    empty surface, which is exactly the "weaker evidence narrows
    conclusions" rule in reverse.
    """
    out: list[Path] = []
    for p in paths:
        if p.is_dir():
            found = iter_directory_headers(p, PRUNED_HEADER_DIR_SEGMENTS)
            if not found:
                raise ValidationError(
                    f"Descriptor <headers> directory contains no supported "
                    f"header files: {p}"
                )
            out.extend(found)
        else:
            out.append(p)
    return dedup_paths_preserve_order(out)


def expand_descriptor_libs(paths: Sequence[Path]) -> list[Path]:
    """*paths* with every directory entry expanded to the shared objects in it.

    The ``<libs>`` counterpart of :func:`expand_descriptor_headers`, and the
    same ABICC-compatibility gap: a directory value previously reached the
    binary parser directly and failed with "Unrecognised binary format".

    Discovery is by **file magic**, across all three container formats
    (``binary_utils.detect_binary_format``), not by suffix and not
    ELF-only. The first version delegated to
    ``package.discover_shared_libraries``, which recognises ELF alone -- so a
    ``<libs>`` directory expanded to nothing on macOS and Windows and this
    function then hard-errored, making the whole capability silently
    Linux-only (caught by the macOS integration lane). A descriptor naming a
    directory of ``.dylib``/``.dll`` files is exactly as legitimate as one
    naming ``.so`` files.

    Executables are excluded as far as magic allows: an ELF entry is checked
    against ``package._is_elf_shared_object`` (ET_DYN, no ``PT_INTERP``, no
    ``DF_1_PIE``), which is the one format where that distinction is cheap
    and already implemented here. For PE and Mach-O the magic alone does not
    separate a library from a program, so the filename is used as the
    secondary signal -- a deliberate, documented weakening rather than
    silently returning nothing, which is what the ELF-only version did.

    A directory holding no shared library at all still raises: expanding to
    an empty list would let a descriptor pointing at the wrong tree produce
    a confident verdict off an empty surface.
    """
    from ..binary_utils import detect_binary_format
    from ..package import _is_elf_shared_object

    def _is_library(path: Path) -> bool:
        fmt = detect_binary_format(path)
        if fmt is None:
            return False
        if fmt == "elf":
            return _is_elf_shared_object(path)
        # PE/Mach-O: magic cannot tell a library from an executable, so the
        # name carries the distinction (`.dylib`, `.so`, `.dll`, and the
        # versioned `libfoo.1.dylib`/`libfoo.so.1` spellings).
        name = path.name.lower()
        return (
            ".dylib" in name
            or ".so" in name
            or name.endswith(".dll")
            or ".dll." in name
        )

    out: list[Path] = []
    for p in paths:
        if p.is_dir():
            found = sorted(
                entry
                for entry in p.rglob("*")
                if entry.is_file() and not entry.is_symlink() and _is_library(entry)
            )
            if not found:
                raise ValidationError(
                    f"Descriptor <libs> directory contains no shared libraries: {p}"
                )
            out.extend(found)
        else:
            out.append(p)
    return dedup_paths_preserve_order(out)


def build_descriptor_suppression(
    descriptors: Sequence[object],
) -> SuppressionList | None:
    """Suppression rules from the ``<skip_*>`` elements of *descriptors*.

    ``<skip_namespaces>``, ``<skip_symbols>``, ``<skip_types>`` and
    ``<skip_constants>`` are narrowing rules a descriptor *declares*, and
    until they were parsed they had no effect on anything -- the run still
    produced a confident verdict, computed over the surface the descriptor
    had asked to narrow. Intel MKL's descriptors carry 16 such rules.

    Both sides' rules are unioned rather than intersected, and the union is
    applied to the whole comparison: a suppression is a statement about what
    the *project* considers internal, and a symbol either side calls
    internal is not something the other side's silence should re-expose.

    ``<skip_namespaces>`` maps to the ``namespace`` selector -- entity
    identity only, deliberately not ``cause_namespace`` (ADR-044 D3): "do
    not report changes *in* ``detail::``" is a much narrower claim than "do
    not report a public break *caused by* something in ``detail::``", and
    silently granting the second is how an internal-namespace rule hides a
    real public regression.
    """
    from ..suppression import Suppression, SuppressionList  # noqa: PLC0415

    rules: list[Suppression] = []
    seen: set[tuple[str, str]] = set()

    def _add(kind: str, value: str, rule: Suppression) -> None:
        if (kind, value) in seen:
            return
        seen.add((kind, value))
        rules.append(rule)

    for desc in descriptors:
        for ns in getattr(desc, "skip_namespaces", []):
            # The bare namespace, **not** a `ns*` glob. The selector already
            # has ancestor semantics -- `detail` matches `detail::T` and
            # `detail::impl::T` -- so the trailing wildcard buys nothing and
            # costs correctness: `detail*` also matches the unrelated
            # segments `details::T` and `detail_public::T`, silently
            # suppressing real breaks in public namespaces the descriptor
            # never named (Codex review). Verified against the selector
            # rather than assumed; see
            # `test_namespace_rule_does_not_leak_into_sibling_segments`.
            _add(
                "namespace",
                ns,
                Suppression(
                    namespace=ns,
                    reason=f"descriptor <skip_namespaces>: {ns}",
                ),
            )
        for name in getattr(desc, "skip_symbols", []):
            _add(
                "symbol",
                name,
                Suppression(symbol=name, reason=f"descriptor <skip_symbols>: {name}"),
            )
        for name in getattr(desc, "skip_constants", []):
            # Constants are reported under their own name in `change.symbol`,
            # so they select the same way an exact symbol does.
            _add(
                "symbol",
                name,
                Suppression(symbol=name, reason=f"descriptor <skip_constants>: {name}"),
            )
        for name in getattr(desc, "skip_types", []):
            _add(
                "type",
                name,
                Suppression(
                    type_pattern=_re.escape(name),
                    reason=f"descriptor <skip_types>: {name}",
                ),
            )
    return SuppressionList(suppressions=rules) if rules else None
