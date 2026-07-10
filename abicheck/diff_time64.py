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

"""time64 / large-file-support (LFS) ABI-flip detection.

On 32-bit targets glibc ≥ 2.34 lets a build opt into 64-bit ``time_t``
(``_TIME_BITS=64``) and 64-bit ``off_t`` (``_FILE_OFFSET_BITS=64``). Flipping
either macro between two builds of the same library resizes every public
parameter/field that carries a time/offset-family typedef — a mass break with
a single root cause (a glibc feature-macro or distro/toolchain default change,
not a source edit). This mirrors the LP64↔ILP64 collapse detector
(``diff_integer_model``): the per-symbol breaking findings still fire; this
emits ONE grouped diagnostic naming the root cause.

Requires typedef evidence (L1 DWARF / L2 headers): an L0 symbol-only snapshot
carries no ``typedefs`` map, so the detector is naturally silent there.
"""
from __future__ import annotations

import re

from .checker_policy import ChangeKind
from .checker_types import Change
from .detector_registry import registry
from .diff_helpers import make_change
from .diff_integer_model import _int_width_bucket
from .model import AbiSnapshot, Visibility

#: Typedefs resized by glibc's ``_TIME_BITS=64`` (time64) option.
_TIME64_TYPEDEFS = frozenset({"time_t", "suseconds_t"})

#: Typedefs resized by glibc's ``_FILE_OFFSET_BITS=64`` (LFS) option.
_LFS_TYPEDEFS = frozenset({
    "off_t", "ino_t", "blkcnt_t", "fsblkcnt_t", "fsfilcnt_t", "rlim_t",
})

_FAMILY = _TIME64_TYPEDEFS | _LFS_TYPEDEFS


def _is_32bit_elf(snap: AbiSnapshot) -> bool:
    """True when the snapshot targets a 32-bit ELF image.

    The time64/LFS flip only exists on 32-bit targets (on 64-bit,
    ``time_t``/``off_t`` are already 64-bit), and the ``long`` family must be
    resolved as 32-bit there — the LP64 assumption baked into
    ``_int_width_bucket``'s default would read ``long → long long`` as 64→64.
    """
    elf = getattr(snap, "elf", None)
    if elf is None:
        return False
    if getattr(elf, "elf_class", 64) == 32:
        return True
    return getattr(elf, "pointer_size", 8) == 4


def _bucket(type_str: str, bits32: bool) -> str | None:
    """Width bucket for *type_str*, resolving the ``long`` family per target.

    DWARF/AST producers spell the long family many ways (``long``,
    ``long int``, ``unsigned long int``, ``long unsigned int``, …), so the
    spelling is normalized to its core words before classification — the
    delegated :func:`_int_width_bucket` only knows a fixed subset of
    spellings and would silently drop e.g. an ``ino_t`` LFS flip written as
    ``unsigned long int`` → ``unsigned long long int`` (Codex review #510).
    """
    if not isinstance(type_str, str):
        return None
    t = " ".join(type_str.split())
    core = " ".join(w for w in t.split() if w not in ("unsigned", "signed", "int"))
    if core == "long long":
        return "64"
    if core == "long":
        return "32" if bits32 else "64"
    return _int_width_bucket(t, is_llp64=False)


#: ABI-visible function/variable visibilities — mirrors
#: ``diff_symbols._PUBLIC_VIS`` (kept local so this leaf detector does not
#: import the heavy symbol-diff module). ELF_ONLY covers the DWARF/binary
#: path without headers, where exported signatures are just as much public
#: ABI as header-proven ones (Codex review #510).
_ABI_VISIBLE = (Visibility.PUBLIC, Visibility.ELF_ONLY)

# Qualified identifiers are captured whole ("ns::Event" is one token, not
# {ns, Event}) so a namespaced record/alias key matches its public spelling
# exactly, without a basename fallback that would also match an *unrelated*
# private type of the same basename in another scope (Codex review #510,
# rounds 6-7).
_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:::[A-Za-z_][A-Za-z0-9_]*)*")


def _public_surface_tokens(snap: AbiSnapshot) -> set[str]:
    """Identifier tokens of the snapshot's *reachable* public surface.

    Seeded from ABI-visible function returns/parameters and variables, then
    expanded through record types transitively reachable by name from that
    seed (a struct is in the surface only when an ABI-visible signature — or
    an already-reachable record's field — spells its name). A private struct
    the public API never references stays out, so its ``time_t`` field cannot
    drive the roll-up (Codex review #510).

    Token-set based (not text search) so the fixpoint stays near-linear in
    the number of spellings — this runs inside every ``compare`` and must not
    regress the scaling benchmarks.
    """
    tokens: set[str] = set()

    def _add(spelling: object) -> None:
        if isinstance(spelling, str) and spelling:
            tokens.update(_IDENTIFIER_RE.findall(spelling))

    for fn in snap.functions:
        if fn.visibility not in _ABI_VISIBLE:
            continue
        _add(fn.return_type)
        for p in fn.params:
            _add(getattr(p, "type", ""))
    for var in snap.variables:
        if getattr(var, "visibility", Visibility.PUBLIC) not in _ABI_VISIBLE:
            continue
        _add(var.type)

    # Expand through name-reachable typedef aliases AND records to a fixpoint;
    # each is folded in at most once. Typedefs participate because a public
    # signature often reaches a record only through an alias (`typedef struct
    # stat Stat;` + `f(Stat *)` puts "Stat" in the tokens while the record map
    # is keyed "stat") — without resolving the alias the record's fields would
    # never be visited (Codex review #510, round 5).
    def _reachable(name: str) -> bool:
        # Exact-name matching only: the tokenizer keeps qualified spellings
        # whole, so `ns::Event` in a public signature reaches the record keyed
        # `ns::Event` — and an unrelated private `other::Event` does NOT ride
        # along on the shared basename (Codex review #510, round 7). The
        # accepted limitation: a dumper that spells a type unqualified while
        # keying the record qualified will miss the roll-up, but the ordinary
        # per-typedef/per-field findings still report that change.
        return name in tokens

    remaining_aliases = dict(snap.typedefs)
    remaining_records = {rec.name: rec for rec in snap.types if rec.name}
    changed = True
    while changed and (remaining_aliases or remaining_records):
        changed = False
        for alias in list(remaining_aliases):
            if _reachable(alias):
                _add(remaining_aliases.pop(alias))
                changed = True
        for name in list(remaining_records):
            if _reachable(name):
                rec = remaining_records.pop(name)
                for fld in rec.fields:
                    _add(getattr(fld, "type", ""))
                # Inherited layout is public layout: fold base-class names in
                # so a base carrying time_t/off_t is visited too (Codex
                # review #510, round 6).
                for base in list(rec.bases) + list(rec.virtual_bases):
                    _add(base)
                changed = True
    return tokens


@registry.detector("time64_abi")
def _diff_time64_abi(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect a time64/LFS ABI flip from resized time/offset-family typedefs.

    Fires when at least one typedef in the ``time_t``/``off_t`` family changed
    its underlying width between the snapshots AND is referenced by the public
    surface (a function signature, public variable, or record field). A
    resized system typedef nothing public carries is not this library's ABI
    story and must not drive a BREAKING verdict (Codex review #510). One
    grouped finding is emitted; the per-symbol layout findings remain separate
    (they share this root cause).
    """
    # A glibc story: skip PE/Mach-O snapshots outright.
    if "pe" in (old.platform, new.platform) or "macho" in (old.platform, new.platform):
        return []

    old_32 = _is_32bit_elf(old)
    new_32 = _is_32bit_elf(new)

    # Cheap pass first: candidate family-typedef flips. The vast majority of
    # comparisons have none, and the surface scan below must not run for them
    # (it walks every signature/record and would tax the scaling benchmarks).
    candidates: list[tuple[str, str, str, str]] = []
    for name, old_under in old.typedefs.items():
        if name not in _FAMILY:
            continue
        new_under = new.typedefs.get(name)
        if new_under is None:
            continue
        ob = _bucket(old_under, old_32)
        nb = _bucket(new_under, new_32)
        if ob is None or nb is None or ob == nb:
            continue
        candidates.append((name, old_under, new_under, nb))

    if not candidates:
        return []

    surface_tokens = _public_surface_tokens(old) | _public_surface_tokens(new)

    flipped: list[str] = []
    up = down = 0
    for name, old_under, new_under, nb in candidates:
        if name not in surface_tokens:
            # Present-but-unused system typedef — not part of this library's
            # public ABI, so its resize must not roll up to a break.
            continue
        flipped.append(f"{name} ({old_under} → {new_under})")
        if nb == "64":
            up += 1
        else:
            down += 1

    if not flipped:
        return []

    macros = []
    if any(f.split(" ", 1)[0] in _TIME64_TYPEDEFS for f in flipped):
        macros.append("_TIME_BITS=64")
    if any(f.split(" ", 1)[0] in _LFS_TYPEDEFS for f in flipped):
        macros.append("_FILE_OFFSET_BITS=64")
    direction = (
        "32-bit → 64-bit (time64/LFS enabled)"
        if up >= down
        else "64-bit → 32-bit (time64/LFS disabled)"
    )
    detail = (
        f"{', '.join(sorted(flipped))}; {direction} — "
        f"likely {' / '.join(macros)} drift"
    )
    return [
        make_change(
            ChangeKind.TIME64_ABI_CHANGED,
            symbol="__time64_abi",
            detail=detail,
            old_value=direction,
        )
    ]
