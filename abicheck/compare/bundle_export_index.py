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

"""Which bundle member exports a given symbol -- the release side's export index.

A multi-library product's public contract is satisfied by the *union* of its
members' exports, not by any single member: a ScaLAPACK declaration in a
shared Intel MKL header is provided by one MKL library, and demanding it
from each of the other 27 is the Cartesian-product defect
``model.release_surface`` describes. This module builds the one index that
makes the union answerable, and answers it *with attribution* -- which
member(s) actually provide a symbol, so an undocumented export names its
real exporter instead of an invented owner.

"Exported" means exactly what it already means for a single-artifact
comparison: ``model.export_index.default_versioned_names`` over
``build_raw_export_index``'s raw platform read -- default/unversioned ELF
exports only (a symbol existing solely as a non-default version alias does
not satisfy an unversioned consumer link, here or there), every named PE
export, and Mach-O's already-normalized spellings. Sharing that one
projection is what keeps "satisfied by a sibling" and "satisfied by this
member itself" the same notion of satisfied;
``buildsource.cross_source_checks_base._exported_symbol_names`` and
``bundle.artifact_set_member_exports`` are its other two call sites.

Accepts a full :class:`~abicheck.model.AbiSnapshot` *or* the compact
per-member stand-in the release fan-out keeps instead
(:class:`~abicheck.bundle_models.BundleSignatureEvidence`, which carries the
same ``elf`` metadata), so building the index never forces the fan-out to
retain every member's full snapshot.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import cast

from ..model.export_index import (
    build_raw_export_index_from_elf,
    build_raw_export_index_from_macho,
    build_raw_export_index_from_pe,
    default_versioned_names,
)

#: Sentinel distinguishing "this object has no such attribute" from an
#: attribute whose value is a legitimate `None` ("observed, no export
#: table") -- the two must not collapse, since only the first may fall
#: through to the container chain.
_UNSET = object()


def member_export_platform(member: object) -> str | None:
    """Which export table *member*'s names come from (``elf``/``pe``/
    ``macho``), or ``None`` with none -- the same precedence
    :func:`member_export_names` reads them in."""
    projected = getattr(member, "export_names", _UNSET)
    if projected is not _UNSET:
        platform = getattr(member, "export_platform", None)
        return str(platform) if projected is not None and platform else None
    for platform in ("elf", "pe", "macho"):
        if getattr(member, platform, None) is not None:
            return platform
    return None


def member_export_names(member: object) -> frozenset[str] | None:
    """*member*'s default-export names, or ``None`` with no export table.

    ``None`` is load-bearing: a member whose container metadata never
    reached this side (a failed or symbol-less acquisition) proves nothing
    about what the product exports, and must not be read as a member that
    exports nothing -- which would leave every obligation it provides
    looking unsatisfied. :attr:`BundleExportIndex.members_without_exports`
    records it and :attr:`BundleExportIndex.complete` goes False.
    """
    # The compact per-member evidence the release fan-out keeps carries an
    # already-projected, platform-agnostic name set; a full `AbiSnapshot`
    # does not, and falls through to the container chain below. Checked
    # first precisely because the compact form's own `elf` is ELF-only, so
    # reading that instead would report "no export table" for every PE and
    # Mach-O member.
    projected = getattr(member, "export_names", _UNSET)
    if projected is not _UNSET:
        if projected is None:
            return None
        return frozenset(cast("Iterable[str]", projected))
    elf = getattr(member, "elf", None)
    if elf is not None:
        return default_versioned_names(build_raw_export_index_from_elf(elf))
    pe = getattr(member, "pe", None)
    if pe is not None:
        return default_versioned_names(build_raw_export_index_from_pe(pe))
    macho = getattr(member, "macho", None)
    if macho is not None:
        return default_versioned_names(build_raw_export_index_from_macho(macho))
    return None


@dataclass(frozen=True)
class BundleExportIndex:
    """``exported symbol -> exporting member(s)`` for one release side.

    :attr:`complete` is the coverage half, and the reason this type exists
    rather than a bare ``dict``: an obligation no member provides is a real
    finding only when *every* expected member was actually observed. With a
    member missing (acquisition failed, or its container carried no export
    table) an absent symbol may simply live in the member nobody read, so
    the reconciliation must narrow its conclusion rather than report a
    missing export that rests on unread evidence.
    """

    side: str
    #: Sorted provider tuples, so a symbol several members export names all
    #: of them in a deterministic order.
    providers_by_symbol: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    #: Every member this index observed, in sorted order.
    members: tuple[str, ...] = ()
    #: Members observed but carrying no export table at all.
    members_without_exports: tuple[str, ...] = ()
    #: ``{member: reason}`` for a member expected on this side whose
    #: acquisition failed outright, so it contributed no exports.
    failed_members: Mapping[str, str] = field(default_factory=dict)
    #: The export table kind every observed member's names came from
    #: (``elf``/``pe``/``macho``); ``"mixed"`` if members disagree, ``""``
    #: with none. Keys the ``provided_by`` relation onto the Phase 2
    #: ``binary_symbol://<platform>/<spelling>`` node ids.
    platform: str = ""

    def providers(self, symbol: str) -> tuple[str, ...]:
        """Which members export *symbol* (empty when none does)."""
        return self.providers_by_symbol.get(symbol, ())

    @property
    def symbols(self) -> frozenset[str]:
        return frozenset(self.providers_by_symbol)

    @property
    def complete(self) -> bool:
        """Whether every expected member contributed real export evidence."""
        return not self.failed_members and not self.members_without_exports

    def incompleteness_reason(self) -> str | None:
        """One sentence naming why coverage is incomplete, or ``None``."""
        if self.complete:
            return None
        parts: list[str] = []
        if self.failed_members:
            # Each member *with its own reason*: `failed_members` keeps the
            # reason verbatim precisely so a report can explain the gap
            # rather than merely flag it, and this string is what the
            # Markdown coverage warning is built from -- naming only the
            # library left a human reader with "coverage is incomplete" and
            # no way to tell a failed dump from an unreadable file.
            named = "; ".join(
                f"{member}: {reason}"
                for member, reason in sorted(self.failed_members.items())
            )
            parts.append(
                f"{len(self.failed_members)} member(s) failed acquisition ({named})"
            )
        if self.members_without_exports:
            named = ", ".join(self.members_without_exports)
            parts.append(
                f"{len(self.members_without_exports)} member(s) carried no export table ({named})"
            )
        return "; ".join(parts)

    def to_dict(self) -> dict[str, object]:
        """Deterministic summary (counts and coverage, never every symbol)."""
        out: dict[str, object] = {
            "side": self.side,
            "members": list(self.members),
            "exported_symbols": len(self.providers_by_symbol),
            "complete": self.complete,
        }
        if self.members_without_exports:
            out["members_without_exports"] = list(self.members_without_exports)
        if self.failed_members:
            out["failed_members"] = dict(sorted(self.failed_members.items()))
        return out


def build_bundle_export_index(
    side: str,
    members: Mapping[str, object],
    *,
    failed_members: Mapping[str, str] | None = None,
) -> BundleExportIndex:
    """Index *members*' exports for one release side.

    *members* maps a member's display name to its snapshot or compact
    export evidence; *failed_members* names the members expected on this
    side that never produced any (their reason is kept verbatim so the
    report can explain the coverage gap rather than merely flag it).
    """
    providers: dict[str, list[str]] = {}
    without: list[str] = []
    platforms: set[str] = set()
    for name in sorted(members):
        exports = member_export_names(members[name])
        if exports is None:
            without.append(name)
            continue
        platform = member_export_platform(members[name])
        if platform:
            platforms.add(platform)
        for sym in exports:
            providers.setdefault(sym, []).append(name)
    return BundleExportIndex(
        side=side,
        providers_by_symbol={
            sym: tuple(sorted(libs)) for sym, libs in sorted(providers.items())
        },
        members=tuple(sorted(members)),
        members_without_exports=tuple(without),
        failed_members=dict(sorted((failed_members or {}).items())),
        platform=(
            next(iter(platforms))
            if len(platforms) == 1
            else "mixed"
            if platforms
            else ""
        ),
    )
