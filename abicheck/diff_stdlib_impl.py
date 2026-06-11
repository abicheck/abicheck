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

"""Cross-implementation standard-library compatibility diff (D-stdlib).

ABI compatibility has a third axis beyond backward/forward: compatibility
*between different standard-library implementations*. The C++ standard never
guarantees it. A class that embeds a ``std::`` container/string **by value**
gets a different layout under libstdc++ vs libc++ vs the MSVC STL (different
``sizeof``, different member offsets), so the same source linked against a
mismatched runtime is silently ABI-incompatible — exactly the
``class A { std::vector<T> v; };`` trap.

This detector compares the normalized :class:`~abicheck.build_mode.BuildMode`
captured on each snapshot and emits a RISK finding when the standard-library
implementation (or the libc++ ABI version) differs. It is deliberately
conservative:

* It works on real snapshots, **not just captured build-mode.** The normalized
  ``build_mode`` field is not populated by every dump path, so the detector
  falls back to recovering the stdlib family from the mangled symbol names
  (which are always present and serialized) — see :func:`_effective_build_mode`.
* It is **quiet when evidence is missing.** When neither a captured build-mode
  nor any mangled symbol reveals the stdlib family (it stays ``UNKNOWN``), it
  emits nothing — it does not guess and it does not escalate. The absence of
  evidence is a reason to stay silent, not to raise an alarm.
* It defaults to **RISK, never BREAKING.** When a public type embeds a stdlib
  type *by value* and its layout actually differs, that owner type is itself a
  non-``std::`` type (e.g. ``class A``) and is therefore never filtered, so the
  type diff emits its ``TYPE_SIZE_CHANGED``/offset BREAKING finding through the
  ordinary path; this kind explains and localizes the root cause. We do **not**
  globally un-filter standalone ``std::`` records in the cross-implementation
  case — across implementations they differ wholesale and would flood BREAKING
  noise for toolchain-owned internals (see
  :func:`abicheck.model.stdlib_namespaces_excluded`). Fine-grained, per-owner
  attribution of the specific embedded ``std::`` field is deferred to the
  layout-closure work.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .build_mode import StdlibFamily, build_mode_from_signals
from .checker_policy import ChangeKind
from .checker_types import Change
from .detector_registry import registry

if TYPE_CHECKING:
    from .build_mode import BuildMode
    from .model import AbiSnapshot

#: libc++'s ``std::__1`` / ``std::__2`` inline namespace, as it appears mangled
#: (``St3__1`` / ``St3__2``). Unlike the shared prefix-anchored detector, this is
#: searched as a *substring* so it is recognized inside ordinary user-API
#: manglings too — e.g. ``void api(std::vector<int>)`` mangles as
#: ``_Z3apiNSt3__16vector...`` under libc++, where the marker is not at the start
#: of the symbol (Codex review on #345).
_LIBCXX_INLINE_NS_SUBSTR = re.compile(r"St\d+__([12])")

#: Marker symbol used for the synthetic build-mode findings (they are not tied
#: to a single exported symbol). Mirrors ``__glibcxx_dual_abi`` in diff_platform.
_STDLIB_IMPL_MARKER = "__stdlib_implementation"

#: Human-readable label per stdlib family for finding descriptions.
_STDLIB_LABEL: dict[StdlibFamily, str] = {
    StdlibFamily.LIBSTDCXX: "libstdc++ (GNU)",
    StdlibFamily.LIBCXX: "libc++ (LLVM)",
    StdlibFamily.MSVC_STL: "MSVC STL",
}


def _public_type_embeds_stdlib_by_value(snap: AbiSnapshot) -> bool:
    """Return True if any record type embeds a ``std::`` field by value.

    A by-value field whose type names a standard-library namespace is what
    makes a public type's layout implementation-dependent. Pointers/references
    to stdlib types are layout-neutral (just a ``void*``), so this only looks
    at the field's spelled type — pointer/reference spellings are skipped.
    """
    from .model import is_non_abi_surface_type

    for rec in snap.types:
        for fld in rec.fields:
            tname = (fld.type or "").strip()
            # Skip only when the *field itself* is a pointer or reference (a
            # top-level ``*``/``&`` at the end of the spelling): those are
            # layout-neutral. A ``*`` inside template arguments — e.g.
            # ``std::vector<int*>`` held by value — still embeds the container
            # by value, so it must NOT be skipped (CodeRabbit review on #345).
            if tname.endswith("*") or tname.endswith("&"):
                continue
            # is_non_abi_surface_type() is True for std::/__gnu_cxx:: etc. We
            # reuse it as the single source of truth for "is a stdlib type".
            if is_non_abi_surface_type(tname.replace("const ", "").strip()):
                return True
    return False


def _layout_evidence_present(snap: AbiSnapshot) -> bool:
    """Return True if the snapshot carries type-layout evidence (DWARF/headers).

    When absent, we cannot verify whether an embedded stdlib type's layout
    actually diverged; the finding then notes the gap calmly instead of
    claiming a clean bill of health.
    """
    return any(rec.size_bits is not None for rec in snap.types)


def _effective_build_mode(snap: AbiSnapshot) -> BuildMode | None:
    """Return the snapshot's :class:`BuildMode`, deriving it on the fly when the
    captured field is absent.

    The normalized ``build_mode`` field is not populated by every dump path, and
    serialized snapshots predating schema v5 lack it entirely — but the
    standard-library family (and libc++ ABI version) this detector keys on is
    encoded directly in the mangled symbol names (``_ZNSt3__1`` ⇒ libc++ v1,
    ``B5cxx11`` ⇒ libstdc++ C++11 ABI). Those names are always present on a real
    snapshot and are serialized to JSON, so we can recover the stdlib signal
    from them whenever the field itself is missing. Returns ``None`` only when
    there are no mangled symbols at all to reason from (then the detector stays
    silent rather than guessing).
    """
    if snap.build_mode is not None:
        return snap.build_mode
    mangled = [f.mangled for f in snap.functions if getattr(f, "mangled", None)]
    mangled += [v.mangled for v in snap.variables if getattr(v, "mangled", None)]
    if not mangled:
        return None
    bm = build_mode_from_signals(mangled_symbols=mangled)
    if bm.stdlib is StdlibFamily.UNKNOWN:
        # The shared detector only recognizes the libc++ inline namespace as a
        # symbol *prefix* (anchored match), so it misses it inside ordinary
        # user-API manglings such as ``void api(std::vector<int>)`` →
        # ``_Z3apiNSt3__16vector...``. Recover it as a substring (Codex #345).
        # libstdc++'s C++11 dual-ABI tag is already substring-detected upstream.
        for sym in mangled:
            m = _LIBCXX_INLINE_NS_SUBSTR.search(sym)
            if m:
                bm.stdlib = StdlibFamily.LIBCXX
                if bm.libcpp_abi_version is None:
                    bm.libcpp_abi_version = int(m.group(1))
                break
    if bm.stdlib is StdlibFamily.UNKNOWN and any(
        # MSVC STL: COFF-decorated C++ symbols (``?...@@``) are non-Itanium, so
        # the shared ``_Z``-only detector skips them entirely. MSVC encodes the
        # ``std`` namespace as ``std@@`` in the mangled name (Codex review #345).
        sym.startswith("?") and "std@@" in sym for sym in mangled
    ):
        bm.stdlib = StdlibFamily.MSVC_STL
    if bm.stdlib is StdlibFamily.UNKNOWN:
        # Untagged libstdc++: a parameter type like ``std::vector<int>`` mangles
        # with the Itanium ``St`` substitution but carries no ``__cxx11`` tag and
        # no libc++ ``__1`` inline namespace, so a substring heuristic cannot
        # separate it from an identifier that merely contains "St" (e.g. a user
        # type ``St3Db``). Demangling parses the substitution correctly —
        # libstdc++ → ``std::``; libc++ → ``std::__1`` (already handled above);
        # a user type → no ``std::`` — and is already used across the diff core.
        # It degrades to ``None`` (→ stay quiet) when no demangler is available
        # (Codex review #345).
        from .demangle import demangle
        for sym in mangled:
            d = demangle(sym)
            if d and "std::" in d and "std::__1" not in d and "std::__2" not in d:
                bm.stdlib = StdlibFamily.LIBSTDCXX
                break
    return bm


def _describe(old_bm: BuildMode, new_bm: BuildMode) -> str:
    """Render a human-readable ``old → new`` stdlib-implementation label."""
    old_lbl = _STDLIB_LABEL.get(old_bm.stdlib, old_bm.stdlib.value)
    new_lbl = _STDLIB_LABEL.get(new_bm.stdlib, new_bm.stdlib.value)
    return f"{old_lbl} → {new_lbl}"


@registry.detector("stdlib_impl")
def _diff_stdlib_implementation(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect a change of C++ standard-library implementation between snapshots.

    Emits at most one ``STDLIB_IMPLEMENTATION_CHANGED`` and/or one
    ``LIBCPP_ABI_VERSION_CHANGED`` finding (both RISK). Stays silent when
    build-mode evidence is missing or inconclusive.
    """
    changes: list[Change] = []
    old_bm = _effective_build_mode(old)
    new_bm = _effective_build_mode(new)

    # Quiet when evidence is absent: no build-mode and no mangled symbols on a
    # side means we have no basis to claim an implementation change. Do not
    # guess, do not escalate.
    if old_bm is None or new_bm is None:
        return changes

    # ── Standard-library implementation changed (libstdc++ ↔ libc++ ↔ MSVC) ──
    both_known = (
        old_bm.stdlib is not StdlibFamily.UNKNOWN
        and new_bm.stdlib is not StdlibFamily.UNKNOWN
    )
    if both_known and old_bm.stdlib != new_bm.stdlib:
        embeds = _public_type_embeds_stdlib_by_value(new) or (
            _public_type_embeds_stdlib_by_value(old)
        )
        have_layout = _layout_evidence_present(old) and _layout_evidence_present(new)
        desc = (
            "C++ standard-library implementation changed "
            f"({_describe(old_bm, new_bm)}). The standard does not guarantee ABI "
            "compatibility across implementations: any public type embedding a "
            "std:: container/string by value is laid out differently, and inline "
            "std:: code can ODR-conflict."
        )
        if embeds and have_layout:
            desc += (
                " A public type embeds a std:: type by value; the type diff "
                "reports the concrete layout change separately."
            )
        elif embeds and not have_layout:
            # Calm, non-escalating note that we could not fully verify layout.
            desc += (
                " A public type embeds a std:: type by value, but no layout "
                "evidence (debug info/headers) is available to confirm the exact "
                "divergence — pin the implementation or rebuild against the "
                "matching runtime to be safe."
            )
        changes.append(Change(
            kind=ChangeKind.STDLIB_IMPLEMENTATION_CHANGED,
            symbol=_STDLIB_IMPL_MARKER,
            description=desc,
            old_value=old_bm.stdlib.value,
            new_value=new_bm.stdlib.value,
        ))

    # ── libc++ ABI version changed (_LIBCPP_ABI_VERSION 1 ↔ 2) ───────────────
    old_v = old_bm.libcpp_abi_version
    new_v = new_bm.libcpp_abi_version
    if old_v is not None and new_v is not None and old_v != new_v:
        changes.append(Change(
            kind=ChangeKind.LIBCPP_ABI_VERSION_CHANGED,
            symbol=_STDLIB_IMPL_MARKER,
            description=(
                f"libc++ ABI version changed ({old_v} → {new_v}). libc++ selects "
                "incompatible internal layouts for std:: types via an inline "
                f"namespace (std::__{old_v} vs std::__{new_v}); types embedding "
                "them by value are laid out differently. Rebuild consumers against "
                "the matching libc++ ABI version."
            ),
            old_value=str(old_v),
            new_value=str(new_v),
        ))

    return changes
