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

"""Removal decisions for a template instantiation the new header no longer
enumerates.

castxml records an instantiation only when some header translation unit
instantiates it, or an ``extern template`` names it. When the new header stops
using a template, the new snapshot has no record for that instantiation, and
the header-depth removal path reads the absence as a removal. But the primary
template (for a member template or a class-template member, the template that
owns it) is still declared in the new header graph, so the declaration did not
go. What can have changed is only whether the library still exports the
instantiation, and the new export table answers that question.

For an old instantiation whose primary template is declared exactly once in the
new header graph:

* the new binary still exports the same symbol: no finding, since the binary
  still provides what consumers bind to;
* the new export table was read and lacks the symbol, and the old binary
  exported it: ``func_visibility_changed``, the export-level change that
  occurred, at the same BREAKING severity as every other export loss;
* the old binary never exported it: no finding, since there was no symbol for a
  consumer to bind to and the declaration remains.

The rule never emits a declaration-removal kind for such an instantiation, and
it never demotes an export loss: a WEAK loss keeps its binding and its verdict
(``case217``/``case218`` pin that contract). It returns ``None`` whenever it
makes no claim -- not a template instance, no declared primary or an ambiguous
one, an unknown export table, or an ELF-only old record -- and the caller falls
back to the existing removal path.

Known limit: a class-template instantiation's implicit constructors, destructors
and operators carry no mangled name a header spelling can be matched against,
and ``type_removed`` for the specialization is raised by the type detector.
Those rows keep their existing kinds (see ``docs/contribute/known-gaps.md``).
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

from ..diff_helpers import make_change
from ..elf_symbol_filter import FUNCTION_SYMBOL_TYPES
from ..model import Function
from ..model.change import Change
from ..model.change_catalog.kinds import ChangeKind
from ..model.edge_coverage import EdgeAnswer
from ..model.export_entity_name import entity_name_components, has_synthetic_component
from ..model.graph_identity import decl_node_identity
from ..model.surface_facts import (
    is_binary_exported,
    is_export_confirmed_absent,
    is_export_table_only_record,
    surface_fact_summary,
    visibility_label,
)
from .public_surface import observed_exports

if TYPE_CHECKING:
    from ..model import AbiSnapshot

__all__ = ["TemplateInstanceAbsence", "primary_stem"]

#: Node-id prefix of a declaration in the header graph (``decl://<qname>#sha``).


def primary_stem(mangled: str) -> str | None:
    """The header spelling of the template *mangled* instantiates.

    ``_ZN2ns3BoxIdE3setEd`` -> ``ns::Box::set``. ``None`` unless *mangled* is an
    Itanium name with at least one template-argument list and every scope
    component a plain identifier: a constructor, operator or substitution has no
    header spelling this module can match, so it makes no claim.
    """
    parsed = entity_name_components(mangled)
    if parsed is None or not parsed.template_positions:
        return None
    if has_synthetic_component(parsed):
        return None
    return "::".join(parsed.components)


class TemplateInstanceAbsence:
    """The new side's facts a template-absence decision reads, for one comparison.

    The export table is the new snapshot's function table as an I4 answer, so a
    name is only "absent" when the table was actually read. The header graph's
    declarations are read lazily, once, and only if a removal candidate needs
    them.
    """

    def __init__(self, new: AbiSnapshot) -> None:
        self._new = new
        self._new_exports = observed_exports(new, FUNCTION_SYMBOL_TYPES)
        self._declared: Counter[str] | None = None

    def _declared_primaries(self) -> Counter[str]:
        if self._declared is None:
            graph = getattr(self._new, "surface_graph", None)
            node_ids = {node.id for node in graph.nodes} if graph is not None else set()
            identities = (decl_node_identity(node_id) for node_id in node_ids)
            self._declared = Counter(i for i in identities if i is not None)
        return self._declared

    def changes_for(
        self, mangled: str, f_old: Function, elf_only_mode: bool
    ) -> list[Change] | None:
        """The findings for *f_old*, an old function the new declaration map does
        not match, or ``None`` when this rule makes no claim about it.

        An export-table-only old record (``elf_only_mode``) keeps the existing
        ELF-only removal path, as ``_check_removed_function`` already does.
        """
        if elf_only_mode and is_export_table_only_record(f_old):
            return None
        stem = primary_stem(mangled)
        if stem is None or self._declared_primaries().get(stem, 0) != 1:
            return None
        answer = self._new_exports.answer(mangled)
        if answer is EdgeAnswer.PRESENT:
            return []
        if answer is not EdgeAnswer.PROVEN_ABSENT:
            return None
        if is_binary_exported(f_old):
            return [_export_lost(mangled, f_old)]
        if is_export_confirmed_absent(f_old):
            return []
        return None


def _export_lost(mangled: str, f_old: Function) -> Change:
    """``func_visibility_changed`` for an export the new binary dropped while
    its declaration remained. The facts are the old side's, with the new side's
    export fact replaced by the confirmed absence the table proved."""
    return make_change(
        ChangeKind.FUNC_VISIBILITY_CHANGED,
        symbol=mangled,
        name=f_old.name,
        old_value=visibility_label(f_old),
        new_value="hidden",
        symbol_binding=f_old.elf_binding.value if f_old.elf_binding else None,
        entity_id=f_old.entity_id,
        surface_facts={**surface_fact_summary(f_old), "binary_exported": "false"},
    )
