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

"""G23 Phase D2 — ``long double`` ABI-transition detector.

A migration of ``long double`` between representations — ppc64 IBM
double-double ↔ IEEE binary128, or 80-bit x87 ↔ ``__float128`` — keeps the
source signature identical but changes the floating-point format a function
passes and returns. On the platforms where the change also changes the Itanium
mangling (``e`` long double, ``g`` ``__float128``, ``u9__ieee128`` IEEE128), a
symbol is *removed* under its old encoding and *added* under the new one, which
a plain symbol diff reports as an unrelated remove+add.

This detector re-pairs such removed↔added symbols by their **demangled** type
spelling: two symbols that demangle to the same function except that a
long-double-family parameter/return type was swapped for another
long-double-family type are one ``long_double_abi_changed`` finding, not a
removal and an addition. Comparing demangled type *spellings* (``long double``,
``__float128``, ``__ieee128``, …) avoids the ambiguity of the bare ``e``/``g``
type codes, which also occur inside ordinary length-prefixed identifiers.

The same-mangling case (``-mlong-double-64``/``-mabi=ibmlongdouble`` keeps the
``e`` encoding while changing the width) leaves no removed/added pair. When
DWARF is present on both sides this detector picks it up from the ``long
double`` base-type byte size (L1): a persisting exported symbol whose demangled
signature mentions ``long double`` is reported when that size differs between
the two snapshots. Without DWARF the same-mangling flip stays invisible (an L3
build-flag flip would be needed).

Accepted limitation — ppc64 IBM double-double ↔ IEEE binary128 at EQUAL width:
both representations are 16 bytes, so a return-only function (whose return type
is not mangled, keeping the symbol identical) exhibits *no* observable signal at
L0 or L1 — no removed/added pair, no DWARF base-type size delta, and the base
type is spelled ``long double`` on both sides. Distinguishing the two 16-byte
formats requires the L3 build flag (``-mabi=ibmlongdouble`` vs
``-mabi=ieeelongdouble``); it is out of reach for the artifact/DWARF tiers this
detector operates on. The unequal-width ppc64 modes and the x87↔__float128
transitions (which DO change size or mangling) are covered above.
"""
from __future__ import annotations

from .checker_policy import ChangeKind
from .checker_types import Change
from .demangle import demangle
from .detector_registry import registry
from .diff_helpers import make_change
from .elf_symbol_filter import is_abi_relevant_elf_symbol
from .model import AbiSnapshot, stdlib_namespaces_excluded

# Human spellings of the long-double family, longest-first so a longer spelling
# is normalized before a substring of it. ``long double`` is normalized as a
# whole, so a plain ``double`` parameter is left untouched.
_LD_SPELLINGS: tuple[str, ...] = (
    "__ieee128",
    "__float128",
    "__ibm128",
    "__float80",
    "long double",
)
_LD_SENTINEL = "\x01LD\x01"


def _has_ld(dem: str) -> bool:
    return any(s in dem for s in _LD_SPELLINGS)


def _normalize_ld(dem: str) -> str:
    for s in _LD_SPELLINGS:
        dem = dem.replace(s, _LD_SENTINEL)
    return dem


def _exported(
    snap: AbiSnapshot, *, filter_runtime: bool = True, cxx_only: bool = True
) -> set[str]:
    elf = snap.elf
    if elf is None:
        return set()
    # When the inspected DSO *is* the C++ runtime (libstdc++/libc++), its own
    # ``std::…(long double)`` exports are the ABI surface under test, not a
    # transitive dependency leak — so keep them (filter_runtime=False), else a
    # runtime release that flips one of its public std:: symbols from the `e`
    # encoding to `g`/`u9__ieee128` would have both sides filtered out and the
    # long_double_abi_changed break would go unreported.
    #
    # ``cxx_only`` keeps only Itanium ``_Z…`` names (the removed↔added mangling
    # pairing path can only reason about demangled C++ signatures). The
    # same-mangling DWARF-size path passes ``cxx_only=False`` so C / ``extern
    # "C"`` exports (e.g. ``long double f(void)`` → symbol ``f``) are kept and
    # checked against their recorded function types.
    return {
        s.name
        for s in elf.symbols
        if (s.name.startswith("_Z") or not cxx_only)
        and is_abi_relevant_elf_symbol(
            s.name, filter_transitive_runtime_symbols=filter_runtime
        )
    }


def _ld_base_size(snap: AbiSnapshot) -> int | None:
    """DWARF byte size of the ``long double`` base type, or None if unknown."""
    dw = snap.dwarf
    if dw is None or not dw.has_dwarf:
        return None
    return dw.base_types.get("long double")


def _diff_same_mangling(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Catch a ``long double`` width change that keeps the mangling (L1).

    ``-mlong-double-64`` (and ppc64's IBM↔IEEE toggle at equal width is handled
    by the mangling path) leaves the symbol name identical, so only the DWARF
    ``long double`` byte size reveals the ABI break. Fires once per persisting
    exported symbol (C++ *or* C / ``extern "C"``) whose demangled signature or
    recorded function types mention ``long double``.
    """
    old_size, new_size = _ld_base_size(old), _ld_base_size(new)
    if old_size is None or new_size is None or old_size == new_size:
        return []
    filter_runtime = stdlib_namespaces_excluded(old, new)
    # Include C / extern "C" exports (cxx_only=False): their symbol name (`f`)
    # encodes neither params nor return type, so a `long double` width flip is
    # only visible through the recorded function types below.
    persisting = _exported(
        old, filter_runtime=filter_runtime, cxx_only=False
    ) & _exported(new, filter_runtime=filter_runtime, cxx_only=False)
    detail = f"long double byte size {old_size} → {new_size}"
    # The mangled name is an unreliable signal here: Itanium omits the return
    # type (`long double f()` stays `_Z1fv`) and a C export encodes no types at
    # all. So consult the recorded function types — a `long double` in any
    # parameter OR the return type — as the authoritative signal, which covers
    # C++ return-only breaks and C/extern-"C" exports alike.
    ld_by_mangled = {
        f.mangled
        for f in old.functions
        if (f.return_type and "long double" in f.return_type)
        or any("long double" in p.type for p in f.params)
    }
    changes: list[Change] = []
    for sym in sorted(persisting):
        dem = demangle(sym)
        param_hit = dem is not None and "long double" in dem
        if not (param_hit or sym in ld_by_mangled):
            continue
        changes.append(
            make_change(
                ChangeKind.LONG_DOUBLE_ABI_CHANGED,
                symbol=sym,
                name=dem or sym,
                old=sym,
                new=sym,
                detail=detail,
            )
        )
    return changes


@registry.detector(
    "long_double",
    requires_support=lambda o, n: (
        o.elf is not None and n.elf is not None,
        "missing ELF metadata on one side",
    ),
)
def _diff_long_double(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect long-double ABI transitions via demangled removed↔added pairing (D2)."""
    filter_runtime = stdlib_namespaces_excluded(old, new)
    old_syms = _exported(old, filter_runtime=filter_runtime)
    new_syms = _exported(new, filter_runtime=filter_runtime)
    removed = old_syms - new_syms
    added = new_syms - old_syms
    # Persisting symbols (present on both sides) can only reveal a width change
    # through the DWARF base-type size; this is disjoint from the removed↔added
    # pairing below, so it always runs.
    changes: list[Change] = _diff_same_mangling(old, new)
    if not removed or not added:
        return changes

    # Index added symbols that carry a long-double type by their LD-normalized
    # demangled form, so a removed LD symbol can find its renamed counterpart.
    added_by_key: dict[str, list[str]] = {}
    added_dem: dict[str, str] = {}
    for a in added:
        dem = demangle(a)
        if not dem or not _has_ld(dem):
            continue
        added_dem[a] = dem
        added_by_key.setdefault(_normalize_ld(dem), []).append(a)

    if not added_by_key:
        return changes
    used_added: set[str] = set()
    for r in sorted(removed):
        r_dem = demangle(r)
        if not r_dem or not _has_ld(r_dem):
            continue
        key = _normalize_ld(r_dem)
        candidates = [a for a in added_by_key.get(key, []) if a not in used_added]
        if not candidates:
            continue
        a = min(candidates)
        used_added.add(a)
        a_dem = added_dem[a]
        if a_dem == r_dem:
            continue  # identical demangling → not an LD-token change
        changes.append(
            make_change(
                ChangeKind.LONG_DOUBLE_ABI_CHANGED,
                symbol=r,
                name=r_dem,
                # old_value/new_value carry the mangled symbols so
                # SuppressRenamedPairs collapses the redundant func_removed(r) +
                # func_added(a) into this single finding.
                old=r,
                new=a,
                detail=f"{r_dem} → {a_dem}",
            )
        )
    return changes
