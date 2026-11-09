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

"""The new side of a virtual that became pure.

A virtual that becomes ``= 0`` loses its definition and so its exported
symbol, which takes it off the new side's export-narrowed public surface. Its
only record there is the declaration: the header's, or since extractor
generation 44 DWARF's in-class declaration DIE (``Function.is_pure_virtual``).
The method-qualifier detector pairs the old virtual with that declaration to
report ``func_virtual_became_pure`` instead of reading a bare removal.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..model import AbiSnapshot, Function

__all__ = ["PureVirtualPeers"]


class PureVirtualPeers:
    """Look up, by mangled name, the pure virtual declaration a side records
    for an old virtual. The side's full function map is read on first use
    only: most comparisons never ask."""

    def __init__(self, snapshot: AbiSnapshot) -> None:
        self._snapshot = snapshot
        self._functions: dict[str, Function] | None = None

    def of(self, old: Function) -> Function | None:
        """*old*'s pure virtual peer on this side, or ``None``."""
        if not old.is_virtual:
            return None
        if self._functions is None:
            self._functions = self._snapshot.function_map
        peer = self._functions.get(old.mangled)
        return peer if peer is not None and peer.is_pure_virtual else None
