# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Public contract evidence for changed enumerators owned by dependencies."""

from __future__ import annotations

from typing import TYPE_CHECKING

from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.ownership_rules import is_dependency_owner

from ..contract_relevance_types import ContractAssurance, ContractRelevance

if TYPE_CHECKING:
    from abicheck.model.change import Change


DEPENDENCY_ENUM_PATH_UNRESOLVED = "dependency_enum_public_path_unresolved"


def dependency_enum_public_decision(
    change: Change,
) -> tuple[ContractRelevance, str, ContractAssurance] | None:
    """Decide public relevance from evidence of exposure of the value.

    None means this rule does not apply. A dependency header's presence in
    the include closure does not establish public exposure on its own.
    """
    if change.kind not in (
        ChangeKind.ENUM_MEMBER_VALUE_CHANGED,
        ChangeKind.ENUM_LAST_MEMBER_VALUE_CHANGED,
    ):
        return None
    sides = (change.review_evidence or {}).get("enum_declarations")
    if not isinstance(sides, dict):
        return None
    declarations = [
        decl for side in ("old", "new") if isinstance(decl := sides.get(side), dict)
    ]
    if not any(is_dependency_owner(decl.get("owner")) for decl in declarations):
        return None
    # Both kinds modify an existing value, so ADR-049 D4 requires old-side
    # evidence even when a new public path appears in the candidate.
    old = sides.get("old")
    if isinstance(old, dict) and (
        old.get("public_target") is True
        or old.get("public_field_paths")
        or old.get("public_inline_arguments")
        or old.get("public_exported_signatures")
    ):
        return (
            ContractRelevance.IN_CONTRACT,
            "public_root_membership",
            ContractAssurance.COMPLETE,
        )
    return (
        ContractRelevance.UNKNOWN_UNRESOLVED,
        DEPENDENCY_ENUM_PATH_UNRESOLVED,
        ContractAssurance.PARTIAL,
    )
