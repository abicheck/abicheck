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

"""When ``--contract public`` may say "searched completely, no commitment".

ADR-063's 2026-10-01 amendment, implementing the public-contract-default
plan's §4.2 closed-world rule for one case: the appearance or disappearance
of an export only the export table knew.

``classify_change_surface`` keeps such a finding in-surface on purpose:
without a stated contract, an undeclared export can still have users
(catalog case182). Under ``--contract public`` the question is narrower --
do the declared public headers commit to this symbol -- and it is closed only
when all of the following hold on the side the finding is judged by:

1. the header search is complete, by the same predicate the evidence ledger
   records as that provider's ``completeness``, so the decision and the
   receipt cannot disagree;
2. the raw-text identifier index (``AbiSnapshot.public_header_identifiers``)
   was captured -- ``None`` means unknown, never empty;
3. the symbol's leaf identifier (:func:`model.symbol_leaf.
   symbol_leaf_identifier`, no host demangler) is absent from that index,
   so a declaration inside an inactive ``#if`` branch (catalog case97)
   keeps the finding unresolved.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..contract_relevance_types import ContractAssurance, ContractRelevance
from ..model.symbol_leaf import symbol_leaf_identifier

if TYPE_CHECKING:
    from ..checker_types import Change
    from .public_surface import PublicSurface


def header_domain_closes_without_commitment(
    change: Change,
    surf_old: PublicSurface,
    surf_new: PublicSurface,
    authoritative: PublicSurface,
) -> bool:
    """True when the ``public`` domain provably holds no commitment to the
    undeclared export *change* is about (see the module docstring)."""
    from ..contract_evidence_collect import public_header_search_is_complete
    from ..surface import _is_undeclared_export_existence_change

    if not _is_undeclared_export_existence_change(change, surf_old, surf_new):
        return False
    if not public_header_search_is_complete(authoritative):
        return False
    spelled = authoritative.header_identifiers
    leaf = symbol_leaf_identifier(change.symbol or "")
    return spelled is not None and leaf is not None and leaf not in spelled


def closed_domain_decision(
    change: Change, surf_old: PublicSurface, surf_new: PublicSurface
) -> tuple[ContractRelevance, str, ContractAssurance] | None:
    """``(UNKNOWN_UNPROVEN, "closed_domain_no_commitment", COMPLETE)`` when
    :func:`header_domain_closes_without_commitment` holds, else ``None``.
    The only place that relevance is produced.

    The side the finding is judged by (ADR-049 D4) follows the same rule
    ``_is_undeclared_export_existence_change`` applies: a disappearance is
    judged on OLD, an appearance on NEW.
    """
    from ..surface import _UNDECLARED_EXPORT_KEEP_KINDS

    removal = change.kind.value in _UNDECLARED_EXPORT_KEEP_KINDS
    authoritative = surf_old if removal else surf_new
    if not header_domain_closes_without_commitment(
        change, surf_old, surf_new, authoritative
    ):
        return None
    return (
        ContractRelevance.UNKNOWN_UNPROVEN,
        "closed_domain_no_commitment",
        ContractAssurance.COMPLETE,
    )


#: Surface-metric findings (``diff_surface_metrics``): counts over the whole
#: public surface, spelled with the pseudo-symbol ``<surface>``. Not about any
#: one contract entity, so ``NOT_APPLICABLE`` in every mode -- never
#: ``UNKNOWN_UNRESOLVED``, which would now floor the exit to 1.
SURFACE_METRIC_KIND_SLUGS: frozenset[str] = frozenset(
    {
        "public_surface_grew",
        "public_surface_shrank",
        "undocumented_export_ratio_increased",
    }
)


__all__ = [
    "SURFACE_METRIC_KIND_SLUGS",
    "closed_domain_decision",
    "header_domain_closes_without_commitment",
]
