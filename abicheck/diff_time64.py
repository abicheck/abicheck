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

from .checker_policy import ChangeKind
from .checker_types import Change
from .detector_registry import registry
from .diff_helpers import make_change
from .diff_integer_model import _int_width_bucket
from .model import AbiSnapshot

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


@registry.detector("time64_abi")
def _diff_time64_abi(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect a time64/LFS ABI flip from resized time/offset-family typedefs.

    Fires when at least one public typedef in the ``time_t``/``off_t`` family
    changed its underlying width between the snapshots. One grouped finding is
    emitted; the per-symbol layout findings remain separate (they share this
    root cause).
    """
    # A glibc story: skip PE/Mach-O snapshots outright.
    if "pe" in (old.platform, new.platform) or "macho" in (old.platform, new.platform):
        return []

    old_32 = _is_32bit_elf(old)
    new_32 = _is_32bit_elf(new)

    flipped: list[str] = []
    up = down = 0
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
