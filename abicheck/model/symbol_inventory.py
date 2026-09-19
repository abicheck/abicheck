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

"""The compact OLD-side snapshot projection JUnit's renderer needs.

A release fan-out held every matched member's **full** ``AbiSnapshot`` for
the whole run whenever JUnit was requested, because
``junit_report._collect_all_symbols`` wanted a symbol-name -> classname map
and the snapshot was the only thing on hand that could produce one. That is
the entire OLD-side JUnit contract -- audited rather than assumed: the four
loops in ``_collect_all_symbols`` are the *only* places anywhere in
``junit_report.py`` that read an attribute off ``old_snapshot`` at all
(``grep -n "old_snapshot\\."``; every other mention is a parameter or a
pass-through). Nothing in it reads declaration locations, types' members,
the semantic IR, the surface graph or the build-source pack, all of which
the retained snapshot dragged along with it.

:class:`SymbolInventory` is that map and nothing else. It is built once,
where the member's comparison finishes, and the snapshot is then free --
instead of the snapshot being pinned until the release-level JUnit fold runs
at the very end, multiplied by the member count.

What is deliberately preserved:

* **The pass-rate denominator.** Every symbol the snapshot contributed is
  still contributed, so the unchanged-symbol ``<testcase>`` set -- and
  therefore the denominator every JUnit consumer computes a pass rate from
  -- is identical.
* **Category and ordering.** The four categories keep their own names
  (``functions``/``variables``/``types``/``enums``) and are emitted in the
  snapshot's own iteration order, folded in the same category order the
  four loops used, so the rendered document is byte-identical. A name that
  appears in two categories resolves to the same one it did before (first
  writer wins, because ``_collect_all_symbols`` only ever assigned into a
  fresh key).
* **Policy, suppression and disposition.** None of it is decided here --
  this is a projection of the OLD *inventory*, not of any finding.

``build_symbol_inventory`` is the one projection; ``as_symbol_map`` is what
the renderer consumes. It lives in ``model/`` rather than beside its
consumer because both of the layers that touch it -- ``workflows`` (which
projects it when a member's comparison finishes) and ``report`` (which
renders from it) -- may depend on ``model`` and not on each other; a
``report``-owned shape would make the release fan-out's ``workflows ->
report`` import a dependency-direction violation, which is exactly what
``scripts/check_architecture.py`` reported when it first lived there. The
JUnit-specific half -- accepting either shape at the public rendering
boundary -- stays in ``report/junit_inventory.py``, where its one consumer
is.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .snapshot import AbiSnapshot

__all__ = ["SymbolInventory", "build_symbol_inventory"]


@dataclass(frozen=True)
class SymbolInventory:
    """One member's OLD-side symbol names, by JUnit classname.

    Four tuples of ``str`` -- no declaration objects, no snapshot
    reference. Frozen, and every field is an immutable tuple of immutable
    strings, so one member's inventory cannot be mutated into another's.
    """

    functions: tuple[str, ...] = ()
    variables: tuple[str, ...] = ()
    types: tuple[str, ...] = ()
    enums: tuple[str, ...] = ()

    def as_symbol_map(self) -> dict[str, str]:
        """``symbol name -> JUnit classname``, in the original fold order.

        Same construction ``_collect_all_symbols`` performed inline:
        functions, then variables, then types, then enums, each assigning
        only into a name it has not already claimed.
        """
        out: dict[str, str] = {}
        for classname, names in (
            ("functions", self.functions),
            ("variables", self.variables),
            ("types", self.types),
            ("enums", self.enums),
        ):
            for name in names:
                out.setdefault(name, classname)
        return out

    def __len__(self) -> int:
        return len(self.as_symbol_map())


def build_symbol_inventory(snapshot: AbiSnapshot) -> SymbolInventory:
    """Project *snapshot* down to what the JUnit renderer reads off it.

    Deliberately reads the same four attributes, in the same order, as the
    loops it replaces -- ``functions``/``variables`` by ``mangled``,
    ``types``/``enums`` by ``name``.
    """
    return SymbolInventory(
        functions=tuple(f.mangled for f in snapshot.functions),
        variables=tuple(v.mangled for v in snapshot.variables),
        types=tuple(t.name for t in snapshot.types),
        enums=tuple(e.name for e in snapshot.enums),
    )
