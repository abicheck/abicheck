# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Per-candidate reference oracle for diff_filtering._opaque_usage_index.

These are the original one-type-at-a-time predicates the index replaced,
kept as an independent second derivation for the equivalence test in
test_cov95_diff_filtering.py. Test-only: no product path calls them.
"""

from __future__ import annotations

import re

from abicheck.diff_filtering import _type_used_by_value
from abicheck.model import AbiSnapshot
from abicheck.model.surface_facts import is_abi_visible


def _public_function_uses_type_by_value(
    snap: AbiSnapshot, bare_re: re.Pattern[str]
) -> bool:
    """True if any PUBLIC function uses the type (matched by *bare_re*) by value."""
    for f in snap.declarations.functions:
        if not is_abi_visible(f):
            continue
        if _type_used_by_value(f.return_type, bare_re):
            return True
        for p in f.params:
            if _type_used_by_value(p.type, bare_re):
                return True
    return False


def _public_variable_uses_type_by_value(
    snap: AbiSnapshot, bare_re: re.Pattern[str]
) -> bool:
    """True if any PUBLIC variable uses the type (matched by *bare_re*) by value."""
    for v in snap.declarations.variables:
        if not is_abi_visible(v):
            continue
        if _type_used_by_value(v.type, bare_re):
            return True
    return False


def _is_pointer_only_type(
    type_name: str,
    snap: AbiSnapshot,
    _re_cache: dict[str, re.Pattern[str]] | None = None,
) -> bool:
    """Return True if all PUBLIC API functions/variables use this type via pointer only.

    A type is pointer-only (opaque-handle pattern) when every function param/return
    that references it uses a raw pointer (`T*`) — never a bare by-value or reference
    (`T`, `T&`) occurrence.  References are treated as non-opaque usage because a
    caller could still hold the referent by value.

    Uses pre-compiled word-boundary regex to avoid substring false-positives.
    *_re_cache* can supply a shared regex cache to avoid recompilation across calls.
    """
    if _re_cache is not None and type_name in _re_cache:
        bare_re = _re_cache[type_name]
    else:
        bare_re = re.compile(r"\b" + re.escape(type_name) + r"\b")
        if _re_cache is not None:
            _re_cache[type_name] = bare_re

    if _public_function_uses_type_by_value(
        snap, bare_re
    ) or _public_variable_uses_type_by_value(snap, bare_re):
        return False
    return True


def _has_public_pointer_factory(
    type_name: str,
    snap: AbiSnapshot,
    _factory_re_cache: dict[str, re.Pattern[str]] | None = None,
) -> bool:
    """True if snapshot has at least one PUBLIC function returning exactly ``type_name*``.

    Uses word-boundary regex to avoid substring false-positives such as
    ``type_name="Context"`` matching ``SSLContext*``.
    """
    # Match: optional const/volatile, then word-boundary type name, then `*`
    if _factory_re_cache is not None and type_name in _factory_re_cache:
        factory_re = _factory_re_cache[type_name]
    else:
        factory_re = re.compile(r"\b" + re.escape(type_name) + r"\s*\*")
        if _factory_re_cache is not None:
            _factory_re_cache[type_name] = factory_re
    for f in snap.declarations.functions:
        if not is_abi_visible(f):
            continue
        rt = f.return_type or ""
        if factory_re.search(rt) and "&" not in rt:
            return True
    return False
