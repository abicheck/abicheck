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

This is the per-symbol analogue of the libstdc++ dual-ABI flip diagnostic
(``glibcxx_dual_abi_flip_detected``). A tag change that is exactly one such
flipped function (see :func:`compare.glibcxx_dual_abi_pairs.is_flip_pair`) is
reported by that diagnostic instead, so it is not repeated here. Every other tag
change is reported, whatever the churn around it.
"""

from __future__ import annotations

import re

from .compare.glibcxx_dual_abi_pairs import is_flip_pair
from .compare.template_surface import reconciled_public_functions
from .demangle import demangle_batch
from .detector_registry import registry
from .diff_helpers import make_change
from .model import AbiSnapshot, Function
from .model.change import Change
from .model.change_catalog.kinds import ChangeKind

# Itanium ABI tag component: 'B' followed by a <source-name> = <length><chars>.
# e.g. 'B5cxx11' -> tag 'cxx11'. Tags may repeat (a name can carry several).
_ABI_TAG_RE = re.compile(r"B(\d+)([A-Za-z0-9_]+)")


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


def _demangle_churn(removed: set[str], added: set[str]) -> dict[str, str]:
    """The demangled spellings of the churned names, in one deterministic batch."""
    return demangle_batch(sorted(removed) + sorted(added))


@registry.detector("abi_tag")
def _diff_abi_tags(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect per-symbol Itanium ABI-tag set changes (e.g. gained/lost cxx11)."""
    changes: list[Change] = []

    # The shared evidence-gap-reconciled surface, not a raw
    # `in_public_surface` pair: an ABI-tag change *is* a mangled-name change
    # (`_Z3fooB3barv` -> `_Z3foov`), so under an asymmetry the pair never met
    # and the observed tag change was reported as a bare removal plus
    # addition instead (Codex review, P2). See compare/template_surface.py --
    # its alias tier pairs exactly this shape, since the declared name is
    # what survives a tag change.
    reconciled_old, reconciled_new = reconciled_public_functions(old, new)
    old_map: dict[str, Function] = {
        f.mangled: f for f in reconciled_old if isinstance(f.mangled, str) and f.mangled
    }
    new_map: dict[str, Function] = {
        f.mangled: f for f in reconciled_new if isinstance(f.mangled, str) and f.mangled
    }

    removed = set(old_map) - set(new_map)
    added = set(new_map) - set(old_map)
    if not removed or not added:
        return changes

    # Pair removed<->added by their tag-stripped base mangled name.
    added_by_base: dict[str, list[str]] = {}
    for a in added:
        _, base = _extract_abi_tags(a)
        added_by_base.setdefault(base, []).append(a)

    # Demangled spellings, read only if a tag change needs the flip check.
    demangled: dict[str, str] | None = None

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
        if demangled is None and ("cxx11" in r or "cxx11" in a):
            demangled = _demangle_churn(removed, added)
        if demangled is not None and is_flip_pair(
            demangled.get(r, ""), demangled.get(a, "")
        ):
            continue  # one flipped function: the dual-ABI finding reports it
        used.add(a)
        gained = sorted(new_tags - old_tags)
        lost = sorted(old_tags - new_tags)
        parts = []
        if gained:
            parts.append("gained " + ", ".join(f"[abi:{t}]" for t in gained))
        if lost:
            parts.append("lost " + ", ".join(f"[abi:{t}]" for t in lost))
        changes.append(
            make_change(
                ChangeKind.ABI_TAG_CHANGED,
                symbol=old_map[r].name,
                name=old_map[r].name,
                detail="; ".join(parts),
                old=r,
                new=a,
                old_value=", ".join(sorted(old_tags)) or "(none)",
                new_value=", ".join(sorted(new_tags)) or "(none)",
            )
        )

    return changes
