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

"""A snapshot's confirmed-exported functions and variables by mangled name,
shared across one comparison.

Three mangling detectors (the libstdc++ dual-ABI churn and selection
detectors, the inline-namespace move) each read the same map from each
snapshot. Confirmed exports only, not the exported-or-promised union: they
diagnose a *mangling* change across two export tables, so a promised but
unexported declaration has no mangled symbol to have churned.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING

from ..model.comparison_memo import comparison_memoized
from ..model.surface_facts import is_binary_exported

if TYPE_CHECKING:
    from ..model import AbiSnapshot, Function, Variable

__all__ = ["exported_functions_by_mangled", "exported_variables_by_mangled"]


def exported_functions_by_mangled(snap: AbiSnapshot) -> Mapping[str, Function]:
    """``{mangled: function}`` for every function the binary exports
    (read-only; memoized per snapshot inside a comparison scope)."""

    def build() -> Mapping[str, Function]:
        return MappingProxyType(
            {f.mangled: f for f in snap.declarations.functions if is_binary_exported(f)}
        )

    return comparison_memoized("exported_functions_by_mangled", snap, build)


def exported_variables_by_mangled(snap: AbiSnapshot) -> Mapping[str, Variable]:
    """``{mangled: variable}`` for every variable the binary exports
    (read-only; memoized per snapshot inside a comparison scope)."""

    def build() -> Mapping[str, Variable]:
        return MappingProxyType(
            {
                v.mangled: v
                for v in snap.declarations.variables
                if v.mangled and is_binary_exported(v)
            }
        )

    return comparison_memoized("exported_variables_by_mangled", snap, build)
