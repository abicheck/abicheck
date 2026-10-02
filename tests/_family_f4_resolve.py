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

"""Resolve an H4 inventory site to the runtime-registered name heuristic
whose spelling check it is (design-hardening Phase 5).

A site belongs to heuristic *h* when it is

* a ``vocab`` constant *h* names in ``vocabularies``;
* a module-level compiled pattern equal to one of *h*'s ``patterns``, in
  *h*'s owner module or the module of its matcher/helpers; or
* inside *h*'s matcher or one of its helpers (nested functions included).

Derived from the live registry, never a hand-kept list: moving a spelling
check out of its registered matcher makes the site unresolved, and the
completeness test then fails.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from _family_f4_inventory import _short

from abicheck.policy.name_heuristics import name_heuristic_registry


def _qual(fn: Callable[..., object]) -> tuple[str, str]:
    return fn.__module__, fn.__qualname__.replace(".<locals>", "")


def resolve_site(site: str, registry: Mapping[str, Any] | None = None) -> str | None:
    reg = name_heuristic_registry() if registry is None else registry
    module, qual, rest = site.split("::", 2)
    kind, _, pattern = rest.partition(":")
    for hid, h in sorted(reg.items()):
        funcs = [_qual(f) for f in (h.matcher, *h.helpers)]
        if kind == "vocab":
            for ref in h.vocabularies:
                vm, _, vn = ref.rpartition(":")
                if (vm or h.owner, vn) == (module, pattern):
                    return hid
            continue
        if qual == "<module>" and kind == "re":
            modules = {h.owner, *(m for m, _ in funcs)}
            if module in modules and any(
                _short(repr(p.pattern)) == pattern for p in h.patterns
            ):
                return hid
            continue
        for fm, fq in funcs:
            if fm == module and (qual == fq or qual.startswith(fq + ".")):
                return hid
    return None
