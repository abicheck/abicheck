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

"""Per-member header operand for a directory/package ``compare``.

A release hands the fan-out one ``-H`` set for all members. Parsing that
union for every member made a type declared in library A's header a finding
of library B too, and made the release's header work grow with
``members x headers`` (``docs/contribute/archive/known-gaps-closed.md``, "A
directory ``compare`` still feeds the union ``-H`` set to every member",
step 2).

This module narrows the union to what each member *claims*: a header file
claims a member when one of the member's own exported names appears in it
as an identifier token. Each header is tokenised once and each member's
export table read once, so the assignment is linear in their sum.

Every rule errs towards the union, never towards silence:

* a header no member claims (a types-only ``common.h``) goes to every member;
* a member that claims no header at all, or whose export table cannot be
  read (a non-ELF binary, a parse failure), keeps the whole union;
* a ``-H`` *directory* is expanded to its files for the assignment, and the
  directory itself is kept as that member's include root and public-header
  directory, so header provenance and include resolution are unchanged.

Over-claiming (a name mentioned only in a comment) costs one extra header;
it can never drop a declaration the member owns.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .release_member_request import ReleaseMemberCompareRequest

__all__ = [
    "ReleaseHeaderOwnership",
    "SideHeaderOwnership",
    "assign_side_headers",
    "member_header_operand",
    "resolve_release_header_ownership",
]

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_EXPORT_SYMBOL_TYPES = frozenset({"func", "object", "tls", "ifunc", "common"})


@dataclass(frozen=True)
class SideHeaderOwnership:
    """One side's assignment: the union and each narrowed member's files."""

    #: The ``-H`` entries as the release resolved them (the union operand).
    union: tuple[Path, ...] = ()
    #: Members whose operand is narrower than the union -> their files.
    member_files: Mapping[str, tuple[Path, ...]] = field(default_factory=dict)
    #: ``-H`` directories expanded for the assignment.
    expanded_dirs: tuple[Path, ...] = ()
    #: Each member's claimed files (what it owns; used for attribution).
    claimed: Mapping[str, frozenset[Path]] = field(default_factory=dict)

    def headers_for(self, key: str) -> list[Path] | None:
        """*key*'s narrowed operand, or ``None`` to inherit the union."""
        files = self.member_files.get(key)
        return list(files) if files is not None else None


@dataclass(frozen=True)
class ReleaseHeaderOwnership:
    """Both sides' assignments for one release fan-out."""

    old: SideHeaderOwnership = field(default_factory=SideHeaderOwnership)
    new: SideHeaderOwnership = field(default_factory=SideHeaderOwnership)


def _header_tokens(path: Path) -> frozenset[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return frozenset()
    return frozenset(_IDENT_RE.findall(text))


def _export_tokens(binary: Path) -> frozenset[str] | None:
    """Identifier tokens naming *binary*'s exports; ``None`` when unknown."""
    from ..demangle import demangle
    from ..elf_symbol_filter import exported_symbol_names
    from .extraction import parse_elf_metadata

    try:
        elf = parse_elf_metadata(binary)
    except Exception:  # noqa: BLE001 - unknown ownership falls back to the union
        return None
    names = exported_symbol_names(elf, _EXPORT_SYMBOL_TYPES, abi_relevant_only=True)
    if not names:
        return None
    tokens: set[str] = set()
    for name in names:
        bare = name.split("@", 1)[0]
        if bare.startswith("_Z"):
            demangled = demangle(bare) or ""
            head = demangled.split("(", 1)[0]
            idents = _IDENT_RE.findall(head)
            if idents:
                tokens.add(idents[-1])
            continue
        tokens.add(bare)
    return frozenset(tokens)


def _expand(entries: Sequence[Path]) -> tuple[list[Path], list[Path]]:
    from ..buildsource.build_query import PRUNED_HEADER_DIR_SEGMENTS
    from ..header_utils import iter_directory_headers

    files: list[Path] = []
    dirs: list[Path] = []
    for entry in entries:
        if entry.is_dir():
            dirs.append(entry)
            files.extend(iter_directory_headers(entry, PRUNED_HEADER_DIR_SEGMENTS))
        else:
            files.append(entry)
    return list(dict.fromkeys(files)), dirs


def assign_side_headers(
    headers: Sequence[Path],
    member_exports: Mapping[str, frozenset[str] | None],
) -> SideHeaderOwnership:
    """Assign one side's *headers* to the members in *member_exports*.

    *member_exports* maps a member key to its export tokens (``None`` when
    they could not be read). Pure apart from reading the header files.
    """
    union = tuple(headers)
    if not union or len(member_exports) < 2:
        return SideHeaderOwnership(union=union)
    files, dirs = _expand(union)
    tokens = {f: _header_tokens(f) for f in files}
    claimed: dict[str, frozenset[Path]] = {}
    for key, exports in member_exports.items():
        if exports:
            claimed[key] = frozenset(f for f in files if tokens[f] & exports)
    owned = frozenset().union(*claimed.values()) if claimed else frozenset()
    member_files: dict[str, tuple[Path, ...]] = {}
    for key, mine in claimed.items():
        if not mine:
            continue  # claims nothing: keep the union, never fewer headers
        operand = tuple(f for f in files if f in mine or f not in owned)
        if len(operand) < len(files):
            member_files[key] = operand
    return SideHeaderOwnership(
        union=union,
        member_files=member_files,
        expanded_dirs=tuple(dirs),
        claimed=claimed,
    )


def resolve_release_header_ownership(
    keys: Sequence[str],
    old_map: Mapping[str, Path],
    new_map: Mapping[str, Path],
    old_headers: Sequence[Path],
    new_headers: Sequence[Path],
) -> ReleaseHeaderOwnership:
    """Both sides' assignments for the matched *keys* of one release."""
    if len(keys) < 2:
        return ReleaseHeaderOwnership(
            SideHeaderOwnership(union=tuple(old_headers)),
            SideHeaderOwnership(union=tuple(new_headers)),
        )

    def side(paths: Mapping[str, Path], headers: Sequence[Path]) -> SideHeaderOwnership:
        if not headers:
            return SideHeaderOwnership()
        exports = {k: _export_tokens(paths[k]) for k in keys if k in paths}
        return assign_side_headers(headers, exports)

    return ReleaseHeaderOwnership(
        old=side(old_map, old_headers), new=side(new_map, new_headers)
    )


def _merged(base: Iterable[Path] | None, extra: Iterable[Path]) -> list[Path]:
    return list(dict.fromkeys([*(base or ()), *extra]))


def member_header_operand(
    ownership: ReleaseHeaderOwnership | None,
    key: str,
    parent: ReleaseMemberCompareRequest,
) -> dict[str, list[Path]]:
    """The ``MemberDelta`` header fields for *key* (empty: inherit all).

    A narrowed side whose union named a directory keeps that directory as an
    include root and public-header directory, which is what the directory
    entry itself contributed to the union run.
    """
    if ownership is None:
        return {}
    out: dict[str, list[Path]] = {}
    dirs: list[Path] = []
    for name, side, includes in (
        ("old", ownership.old, parent.old_includes),
        ("new", ownership.new, parent.new_includes),
    ):
        narrowed = side.headers_for(key)
        if narrowed is None:
            continue
        out[f"{name}_headers"] = narrowed
        if side.expanded_dirs:
            out[f"{name}_includes"] = _merged(includes, side.expanded_dirs)
            dirs.extend(side.expanded_dirs)
    if dirs:
        out["public_header_dirs"] = _merged(parent.public_header_dirs, dirs)
    return out
