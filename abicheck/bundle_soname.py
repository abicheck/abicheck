# SPDX-License-Identifier: Apache-2.0
# Copyright The abicheck Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
"""Soname matching for the bundle layer's system-provider allow-list.

Split out of ``bundle.py`` (a leaf module, no other imports needed) purely to
keep that file under the AI-readiness file-size hard cap — see
``bundle.py``'s own ``_detect_intra_dep_removed``/
``_detect_unresolved_intra_dependency``, which are this module's only
callers.
"""

from __future__ import annotations

import re

__all__ = ["soname_matches_providers", "soname_stem"]

# Strips a trailing ``.so`` + version suffix (``libfoo.so.1.2.3`` -> ``libfoo``).
_SONAME_VERSION_SUFFIX_RE = re.compile(r"\.so(?:\.\d+)*$")


def soname_stem(soname: str) -> str:
    """Stem, so ``libmkl_core`` matches the real ``libmkl_core.so.2``."""
    return _SONAME_VERSION_SUFFIX_RE.sub("", soname)


def soname_matches_providers(soname: str, providers: set[str]) -> bool:
    """True when soname (a real DT_NEEDED entry) is covered by providers
    (built-in + --bundle-system-providers): exact, then stem match."""
    if soname in providers:
        return True
    stem = soname_stem(soname)
    return any(stem == p or stem == soname_stem(p) for p in providers)
