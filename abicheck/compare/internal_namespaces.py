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

"""The internal-namespace naming convention (``detail::``, ``impl::``, ...).

One matcher for every consumer. It used to exist twice -- in
``internal_leak`` and as a "leaf-local duplicate" in
``policy/public_surface_closure`` -- because ``policy/`` may not import the
unclassified ``internal_leak``; both now import it from here (``policy`` may
import ``compare``). The spelling alone never decides a finding: each
consumer registers its own use with ``model.name_heuristics`` together with
the structural fact that confirms or vetoes it (design-hardening Phase 5).
"""

from __future__ import annotations

import re
from collections.abc import Iterable

__all__ = [
    "DEFAULT_INTERNAL_NAMESPACES",
    "TEMPLATE_ARG_RE",
    "is_internal_type",
    "name_segments",
    "strip_template_args",
]

# Namespace segments that mark a type as "internal" by convention.
# Matched as a name segment (between ``::``) — substring matches inside an
# identifier like ``DetailView`` are intentionally not flagged.
DEFAULT_INTERNAL_NAMESPACES: tuple[str, ...] = (
    "detail",
    "impl",
    "internal",
    "__detail",
    "_impl",
)

# Splits a qualified C++ name into namespace segments, ignoring template
# argument lists. ``acme::lib::detail::pimpl<X>`` →
# ``["acme", "lib", "detail", "pimpl"]``.
TEMPLATE_ARG_RE = re.compile(r"<[^<>]*>")


def strip_template_args(name: str) -> str:
    """Collapse balanced ``<...>`` template arg lists out of *name*.

    Handles nesting iteratively. Used for splitting the name into
    ``::``-separated segments, not for canonicalisation.
    """
    prev = None
    cur = name
    # Iteratively strip innermost <...> until stable (handles nesting).
    while cur != prev:
        prev = cur
        cur = TEMPLATE_ARG_RE.sub("", cur)
    return cur


def name_segments(name: str) -> list[str]:
    """Return ``::``-separated identifier segments of *name*.

    Template arguments are stripped first so that
    ``acme::lib::detail::pimpl<Foo<int>>`` yields
    ``["acme", "lib", "detail", "pimpl"]``.
    """
    if not name:
        return []
    stripped = strip_template_args(name)
    return [seg.strip() for seg in stripped.split("::") if seg.strip()]


def is_internal_type(
    name: str,
    internal_namespaces: Iterable[str] = DEFAULT_INTERNAL_NAMESPACES,
) -> bool:
    """Return True if *name* lives in one of the *internal_namespaces*.

    The check is segment-based: a segment matches exactly (case-sensitive)
    one of *internal_namespaces*. Template arguments are stripped first.

    Examples (with default namespaces)::

        is_internal_type("acme::lib::detail::impl") -> True
        is_internal_type("acme::lib::detail::pimpl<X>") -> True
        is_internal_type("std::__detail::node") -> True
        is_internal_type("MyClass") -> False
        is_internal_type("Details") -> False   # not a segment match
    """
    needles = set(internal_namespaces)
    if not needles:
        return False
    return any(seg in needles for seg in name_segments(name))
