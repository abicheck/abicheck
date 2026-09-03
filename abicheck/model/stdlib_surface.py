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

"""Whether a comparison should treat ``std::`` records as leaked dependencies.

One snapshot-aware predicate, kept apart from the pure name classifiers in
``abicheck.name_classification`` because it answers a question about the pair
of libraries under test rather than about a spelling.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..name_classification import is_cxx_runtime_library

if TYPE_CHECKING:
    from .snapshot import AbiSnapshot


def stdlib_namespaces_excluded(old: AbiSnapshot, new: AbiSnapshot) -> bool:
    """Return True when ``std::``/runtime namespaces should be filtered out of
    type diffing as leaked dependencies.

    False only when *either* side IS the C++ runtime (libstdc++ / libc++), where
    those types are the surface under test.  Single source of truth so every
    registered detector that consumes ``snapshot.types`` agrees on whether to
    keep std:: records (validation/REPORT.md FP-1; Codex reviews on PR #273).

    Note (cross-implementation comparisons): when two snapshots are built
    against *different* stdlib implementations (libstdc++ ↔ libc++), standalone
    ``std::`` records in debug info differ wholesale even when the public ABI
    does not embed them — so this filter stays ON to avoid flooding BREAKING
    findings for toolchain-owned internals. The cross-implementation hazard is
    surfaced instead by the build-mode diff (``diff_stdlib_impl.py``) as a RISK
    finding, and a public owner type that *does* embed a ``std::`` type by value
    is caught through its own (non-``std::``, never-filtered) layout change.
    Per-owner un-filtering of the specific embedded records is deferred to the
    layout-closure work.
    """
    old_elf = getattr(old, "elf", None)
    new_elf = getattr(new, "elf", None)
    return not (
        is_cxx_runtime_library(old.library)
        or is_cxx_runtime_library(new.library)
        or is_cxx_runtime_library(getattr(old_elf, "soname", ""))
        or is_cxx_runtime_library(getattr(new_elf, "soname", ""))
    )
