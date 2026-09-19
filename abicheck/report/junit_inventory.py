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

"""JUnit's boundary onto :class:`~abicheck.model.symbol_inventory.SymbolInventory`.

The projection itself is ``model``-owned (see that module for why). What is
here is only the rendering boundary's own rule: ``to_junit_xml(result,
old_snapshot)`` is public API that third-party callers and the single-pair
path invoke with a real ``AbiSnapshot``, so the parameter keeps accepting
one and is projected on the way in, while the release fan-out passes an
already-built inventory and never materialises the snapshot at all.
"""

from __future__ import annotations

from ..model.symbol_inventory import SymbolInventory, build_symbol_inventory

__all__ = ["SymbolInventory", "coerce_junit_inventory"]


def coerce_junit_inventory(old: object) -> SymbolInventory | None:
    """Accept either shape at the public JUnit boundary.

    ``to_junit_xml(result, old_snapshot)`` is public API and is called with
    a real :class:`~abicheck.model.AbiSnapshot` by single-pair callers and
    by third-party code, so the parameter keeps accepting one and is
    projected here. The release fan-out passes an already-built
    :class:`SymbolInventory` and never materialises the snapshot at
    all.
    """
    if old is None:
        return None
    if isinstance(old, SymbolInventory):
        return old
    return build_symbol_inventory(old)  # type: ignore[arg-type]
