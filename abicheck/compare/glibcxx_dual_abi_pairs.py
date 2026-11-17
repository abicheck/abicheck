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

"""Pair the exports a libstdc++ dual-ABI flip re-mangles, per symbol.

A flip (``_GLIBCXX_USE_CXX11_ABI`` 0 <-> 1) re-mangles every exported function
that takes or returns ``std::string`` or ``std::list``: the same function is
removed under one spelling and added under the other. A removed and an added
export pair when their demangled identities agree once the dual-ABI spellings
exactly one of them spells the ``std::__cxx11`` namespace. A bare ``[abi:cxx11]``
tag is not enough: a tag-only change is an ABI-tag finding, not a flip. One
flipped function is a flip; a library without ``std::string`` has no pair.

A symbol the demangler cannot read has no identity, so it is not paired. For
those alone the raw-marker mass heuristic remains (at least five removed and
five added, marker churn of at least 30%): with no demangled identity it is the
only evidence of a flip. Pure: no snapshot, no ``Change`` is built here.
"""

from __future__ import annotations

from dataclasses import dataclass

from .glibcxx_dual_abi import normalize_dual_abi_spelling

__all__ = ["DualAbiPairing", "pair_dual_abi_flips"]

#: libstdc++'s inline namespace; a user namespace named ``__cxx11`` is not one.
_STD_CXX11_NS = "std::__cxx11::"


@dataclass(frozen=True)
class DualAbiPairing:
    """The result of pairing one comparison's churned exports."""

    paired: int  #: removed/added pairs that are one flipped function
    rem_tagged_paired: int  #: paired removes that carried the cxx11 tag
    add_tagged_paired: int  #: paired adds that carried the cxx11 tag
    direction: str  #: the flip's direction, from the paired counts
    un_removed: int  #: removed exports the demangler could not read
    un_added: int  #: added exports the demangler could not read
    un_rem_tag: int  #: unread removed exports carrying a cxx11 marker
    un_add_tag: int  #: unread added exports carrying a cxx11 marker
    un_direction: str  #: the direction of the raw-marker fallback
    mass: bool  #: whether the raw-marker fallback fires


def _has_cxx11_tag(demangled: str) -> bool:
    """Whether the spelling is libstdc++'s new-ABI ``std::string`` (the
    ``std::__cxx11`` namespace), not merely an ``[abi:cxx11]`` tag."""
    return _STD_CXX11_NS in demangled


def _cxx11_identity(demangled: str) -> str:
    """The demangled name with the dual-ABI spellings removed (the shared
    :func:`normalize_dual_abi_spelling`), so the old and new spellings of one
    function compare equal. The inline-namespace scope is enforced by
    :func:`_has_cxx11_tag`, not here."""
    return normalize_dual_abi_spelling(demangled)


def pair_dual_abi_flips(
    removed: list[str],
    added: list[str],
    removed_demangled: dict[str, str],
    added_demangled: dict[str, str],
) -> DualAbiPairing:
    """Pair *removed* and *added* mangled exports into dual-ABI flips.

    *removed_demangled* and *added_demangled* map a mangled name to its
    demangled spelling; a name absent from them (or mapped to a falsy value) is
    unreadable and falls back to the raw-marker heuristic.
    """
    # Per identity: [removed tagged, removed plain, added tagged, added plain].
    counts: dict[str, list[int]] = {}
    for name in removed:
        dm = removed_demangled.get(name)
        if dm:
            slot = counts.setdefault(_cxx11_identity(dm), [0, 0, 0, 0])
            slot[0 if _has_cxx11_tag(dm) else 1] += 1
    for name in added:
        dm = added_demangled.get(name)
        if dm:
            slot = counts.setdefault(_cxx11_identity(dm), [0, 0, 0, 0])
            slot[2 if _has_cxx11_tag(dm) else 3] += 1

    rem_tagged = add_tagged = 0
    for rem_t, rem_p, add_t, add_p in counts.values():
        rem_tagged += min(rem_t, add_p)  # a tagged remove paired with a plain add
        add_tagged += min(rem_p, add_t)  # a plain remove paired with a tagged add
    paired = rem_tagged + add_tagged

    un_removed = [n for n in removed if not removed_demangled.get(n)]
    un_added = [n for n in added if not added_demangled.get(n)]
    un_rem_tag = sum(1 for n in un_removed if _STD_CXX11_NS in n or "cxx11" in n)
    un_add_tag = sum(1 for n in un_added if _STD_CXX11_NS in n or "cxx11" in n)
    mass = (
        len(un_removed) >= 5
        and len(un_added) >= 5
        and (un_rem_tag + un_add_tag) > 0
        and (un_rem_tag + un_add_tag) >= (len(un_removed) + len(un_added)) * 0.3
    )
    return DualAbiPairing(
        paired=paired,
        rem_tagged_paired=rem_tagged,
        add_tagged_paired=add_tagged,
        direction=(
            "CXX11 ABI → legacy ABI"
            if rem_tagged >= add_tagged
            else "legacy ABI → CXX11 ABI"
        ),
        un_removed=len(un_removed),
        un_added=len(un_added),
        un_rem_tag=un_rem_tag,
        un_add_tag=un_add_tag,
        un_direction=(
            "CXX11 ABI → legacy ABI"
            if un_rem_tag >= un_add_tag
            else "legacy ABI → CXX11 ABI"
        ),
        mass=mass,
    )
