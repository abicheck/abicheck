# Copyright 2026 Nikolay Petrov
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

"""Which operand combinations a dump or compare must refuse -- decided once.

A run can be stated in a combination its operands cannot honour: a forced ELF
debug format against a PE or Mach-O side, a ``--dump-manifest`` on a non-ELF
side, or a manifest alongside that side's own header list. Each rule is decided
here and carried back as a typed :class:`~abicheck.errors.ValidationError`
subclass that names the offending side. A frontend renders its own wording
(``compare`` names its ``.abicheck.yml`` key or CLI flag, the typed API names
the request field) and picks its own exit code. Nothing here prints or exits.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ..errors import ValidationError

__all__ = [
    "DebugFormatNotElfError",
    "ManifestOperandError",
    "reject_debug_format_for_binaries",
    "reject_debug_format_for_non_elf",
    "reject_manifest_header_conflicts",
    "reject_manifest_non_elf",
]


class DebugFormatNotElfError(ValidationError):
    """A forced ELF debug format met a PE or Mach-O side.

    The PE and Mach-O dump paths take no debug-format argument, so the forced
    ``dwarf``/``btf``/``ctf`` would be silently dropped there. Refused up front
    instead. ``debug_format`` is the value exactly as the caller passed it, so
    a frontend can quote it in its own spelling.
    """

    def __init__(self, debug_format: str, side: str, binary_fmt: str) -> None:
        super().__init__(
            f"debug_format={debug_format!r} is only supported for ELF "
            f"binaries, but the {side} input is {binary_fmt.upper()}."
        )
        self.debug_format = debug_format
        self.side = side
        self.binary_fmt = binary_fmt


class ManifestOperandError(ValidationError):
    """A ``--dump-manifest`` side that the run cannot honour as stated.

    *rule* is ``"header_conflict"`` (the side also names headers, which the
    manifest already declares) or ``"non_elf"`` (manifest extraction is wired
    for ELF only, ADR-050 D3). ``str(exc)`` is the ``compare`` wording, which
    is the only frontend that states manifests.
    """

    def __init__(self, rule: str, side: str, binary_fmt: str | None) -> None:
        if rule == "header_conflict":
            message = (
                f"--dump-manifest {side}=... and a header for the {side} side "
                "(-H/--header) are mutually exclusive -- declare the "
                f"{side} side's public surface in the manifest's own base "
                "profile instead."
            )
        else:
            message = (
                f"--dump-manifest {side}=... requires the {side} input to be an "
                f"ELF binary; got {binary_fmt or 'a non-binary input'}."
            )
        super().__init__(message)
        self.rule = rule
        self.side = side
        self.binary_fmt = binary_fmt


def reject_debug_format_for_binaries(
    debug_format: str | None, sides: Iterable[tuple[str, str | None]]
) -> None:
    """Raise :class:`DebugFormatNotElfError` if a forced format meets a PE/Mach-O input.

    *sides* is ``(label, detected format)`` pairs, so the error names the input
    that cannot honour the request -- ``compare`` passes ``old``/``new``,
    ``dump`` its single ``input``.
    """
    if debug_format is None:
        return
    for side, binary_fmt in sides:
        if binary_fmt in ("pe", "macho"):
            raise DebugFormatNotElfError(debug_format, side, binary_fmt)


def reject_debug_format_for_non_elf(
    debug_format: str | None, old_fmt: str | None, new_fmt: str | None
) -> None:
    """Both sides of a comparison; see :func:`reject_debug_format_for_binaries`.

    A JSON-snapshot or dump input has ``fmt is None`` and is unaffected.
    """
    reject_debug_format_for_binaries(debug_format, (("old", old_fmt), ("new", new_fmt)))


def reject_manifest_header_conflicts(
    old_manifest: Any,
    new_manifest: Any,
    old_headers: Any,
    new_headers: Any,
) -> None:
    """Refuse a manifest side that also names headers for that same side."""
    for manifest, side_headers, side in (
        (old_manifest, old_headers, "old"),
        (new_manifest, new_headers, "new"),
    ):
        if manifest is not None and side_headers:
            raise ManifestOperandError("header_conflict", side, None)


def reject_manifest_non_elf(
    old_manifest: Any,
    new_manifest: Any,
    old_fmt: str | None,
    new_fmt: str | None,
) -> None:
    """Refuse a manifest side whose input is not an ELF binary (ADR-050 D3)."""
    for manifest, fmt, side in (
        (old_manifest, old_fmt, "old"),
        (new_manifest, new_fmt, "new"),
    ):
        if manifest is not None and fmt != "elf":
            raise ManifestOperandError("non_elf", side, fmt)
