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

"""The compared public function *list* the template detectors select, and
its evidence-gap reconciliation.

``diff_templates`` selects a list straight off ``AbiSnapshot.functions``
rather than the mangled-keyed map ``diff_symbols`` builds, so it needs the
list-shaped side of :mod:`~abicheck.compare.surface_reconcile`. Owning both
the selector and the join here keeps that pairing in one place -- and in
``compare``, whose job matching two sides' entities is (ADR-061) -- instead
of in a detector module already at its debt baseline.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

from ..elf_symbol_filter import FUNCTION_SYMBOL_TYPES, exported_symbol_names
from ..model.surface_facts import in_public_surface
from .surface_reconcile import reconcile_declaration_lists

if TYPE_CHECKING:
    from ..model import AbiSnapshot, Function

__all__ = [
    "public_functions",
    "reconciled_public_function_maps",
    "reconciled_public_functions",
]


def public_functions(snap: AbiSnapshot) -> list[Function]:
    """Return the subset of public functions in *snap*."""
    return [f for f in snap.functions if in_public_surface(f)]


def reconciled_public_functions(
    old: AbiSnapshot, new: AbiSnapshot
) -> tuple[list[Function], list[Function]]:
    """Both sides' public function lists, evidence-gap-reconciled.

    :func:`public_functions` builds each side's list from that side's own
    facts, and one of them -- ``in_public_contract`` -- is only *established*
    when that run's producer was given a public-header set. So two captures
    of an unchanged library, one with that set and one without, disagree
    about every promised-but-unexported instantiation, and
    ``diff_templates.detect_internal_template_leaks`` reads the disagreement
    as an OLD instantiation NEW no longer emits: a false breaking
    ``INTERNAL_TEMPLATE_LEAKS_VIA_PUBLIC_API`` (Codex review, P1).

    Routed through the shared list-shaped join rather than a local guard, so
    no second copy of the rule exists -- see
    :mod:`abicheck.compare.surface_reconcile` for why the repair belongs to
    the surface and not to each disposition site.
    """
    return reconcile_declaration_lists(
        public_functions(old),
        public_functions(new),
        old_all=old.functions,
        new_all=new.functions,
        key=lambda f: f.mangled or f.name,
        old_exported=exported_symbol_names(
            getattr(old, "elf", None), FUNCTION_SYMBOL_TYPES
        ),
        new_exported=exported_symbol_names(
            getattr(new, "elf", None), FUNCTION_SYMBOL_TYPES
        ),
    )


def reconciled_public_function_maps(
    old: AbiSnapshot,
    new: AbiSnapshot,
    *,
    key: Callable[[Function], str] = lambda f: f.mangled,
) -> tuple[dict[str, Function], dict[str, Function]]:
    """:func:`reconciled_public_functions`, as the mangled-keyed maps the
    type-spelling and integer-model detectors join on.

    They select with the same ``in_public_surface`` predicate as the template
    detectors, then key the result themselves -- so the evidence asymmetry
    costs them the same pair, and with it a real finding: a
    ``char *`` -> ``char8_t *`` return change on a promised-but-unexported
    function reported ``CHAR8T_MIGRATION`` only when both sides happened to
    carry contract evidence, and an ``int`` -> ``long`` group likewise lost
    ``INTEGER_MODEL_CHANGED`` (Codex review, P2).

    *key* is used for the reconciliation join as well as for the returned
    maps, so the two cannot disagree about what counts as the same
    declaration. Later wins on a duplicate key, matching the dict
    comprehensions this replaces.
    """
    reconciled_old, reconciled_new = reconcile_declaration_lists(
        public_functions(old),
        public_functions(new),
        old_all=old.functions,
        new_all=new.functions,
        key=key,
        old_exported=exported_symbol_names(
            getattr(old, "elf", None), FUNCTION_SYMBOL_TYPES
        ),
        new_exported=exported_symbol_names(
            getattr(new, "elf", None), FUNCTION_SYMBOL_TYPES
        ),
    )
    return (
        {key(f): f for f in reconciled_old},
        {key(f): f for f in reconciled_new},
    )
