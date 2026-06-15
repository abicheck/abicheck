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

"""ABI-tag change detection (Itanium [abi:cxx11] / [[gnu::abi_tag]]).

The Itanium C++ ABI encodes ABI tags in the mangled name as a sequence of
``B<source-name>`` components attached to a name, e.g. the ``[abi:cxx11]`` tag
appears as ``B5cxx11``. Two symbols that demangle to the same name but carry a
different *set* of ABI tags are distinct symbols at the binary level: the
mangled names differ, so an old binary referencing one will not resolve the
other.

This is the per-symbol analogue of the libstdc++ dual-ABI mass-flip diagnostic
(``glibcxx_dual_abi_flip_detected``). When that mass flip already fired we stay
quiet to avoid duplicate noise.
"""
from __future__ import annotations

import re

from .checker_policy import ChangeKind
from .checker_types import Change
from .detector_registry import registry
from .diff_helpers import make_change
from .model import AbiSnapshot, Function, Visibility

# Itanium ABI tag component: 'B' followed by a <source-name> = <length><chars>.
# e.g. 'B5cxx11' -> tag 'cxx11'. Tags may repeat (a name can carry several).
_ABI_TAG_RE = re.compile(r"B(\d+)([A-Za-z0-9_]+)")

# Markers used by the glibcxx dual-ABI mass-flip detector. We mirror its
# trigger conditions so we can suppress per-symbol noise when that fired.
_CXX11_ABI_MARKERS = ("__cxx11", "cxx11")


def _extract_abi_tags(mangled: str) -> tuple[frozenset[str], str]:
    """Return (tag_set, base_mangled_with_tags_removed) for a mangled name.

    The base form lets us pair a tagged symbol with its untagged (or
    differently-tagged) counterpart: same base, different tag set.
    """
    tag_list: list[str] = []

    def _take(m: re.Match[str]) -> str:
        length = int(m.group(1))
        rest = m.group(2)
        # The numeric length prefixes the tag chars; honour it so we do not
        # swallow following mangled components.
        tag = rest[:length]
        if len(tag) == length:
            tag_list.append(tag)
            return rest[length:]
        # Length/chars mismatch: not actually a tag component — leave intact.
        return m.group(0)

    base = _ABI_TAG_RE.sub(_take, mangled)
    return frozenset(tag_list), base


def _glibcxx_flip_active(removed: set[str], added: set[str]) -> bool:
    """True if the glibcxx dual-ABI mass-flip diagnostic would fire.

    Mirrors the threshold logic in ``diff_platform._diff_glibcxx_dual_abi`` so
    that per-symbol abi_tag findings are suppressed under a detected mass flip.
    """
    if len(removed) < 5 or len(added) < 5:
        return False
    removed_marker = sum(1 for s in removed if any(m in s for m in _CXX11_ABI_MARKERS))
    added_marker = sum(1 for s in added if any(m in s for m in _CXX11_ABI_MARKERS))
    total = len(removed) + len(added)
    marker = removed_marker + added_marker
    return marker > 0 and marker >= total * 0.3


@registry.detector("abi_tag")
def _diff_abi_tags(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect per-symbol Itanium ABI-tag set changes (e.g. gained/lost cxx11)."""
    changes: list[Change] = []

    old_map: dict[str, Function] = {
        f.mangled: f
        for f in old.functions
        if f.visibility == Visibility.PUBLIC and isinstance(f.mangled, str) and f.mangled
    }
    new_map: dict[str, Function] = {
        f.mangled: f
        for f in new.functions
        if f.visibility == Visibility.PUBLIC and isinstance(f.mangled, str) and f.mangled
    }

    removed = set(old_map) - set(new_map)
    added = set(new_map) - set(old_map)
    if not removed or not added:
        return changes

    # Suppress when the mass dual-ABI flip diagnostic already covers this churn.
    if _glibcxx_flip_active(removed, added):
        return changes

    # Pair removed<->added by their tag-stripped base mangled name.
    added_by_base: dict[str, list[str]] = {}
    for a in added:
        _, base = _extract_abi_tags(a)
        added_by_base.setdefault(base, []).append(a)

    used: set[str] = set()
    for r in sorted(removed):
        old_tags, base = _extract_abi_tags(r)
        candidates = [a for a in added_by_base.get(base, []) if a not in used]
        if not candidates:
            continue
        a = candidates[0]
        new_tags, _ = _extract_abi_tags(a)
        if old_tags == new_tags:
            continue  # base matched but tags identical — not a tag change
        used.add(a)
        gained = sorted(new_tags - old_tags)
        lost = sorted(old_tags - new_tags)
        parts = []
        if gained:
            parts.append("gained " + ", ".join(f"[abi:{t}]" for t in gained))
        if lost:
            parts.append("lost " + ", ".join(f"[abi:{t}]" for t in lost))
        changes.append(make_change(
            ChangeKind.ABI_TAG_CHANGED,
            symbol=old_map[r].name,
            name=old_map[r].name,
            detail="; ".join(parts),
            old=r,
            new=a,
            old_value=", ".join(sorted(old_tags)) or "(none)",
            new_value=", ".join(sorted(new_tags)) or "(none)",
        ))

    return changes
