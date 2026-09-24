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

"""``dump --dry-run``'s ownership preview (Phase 1 of
``docs/contribute/plans/target-ownership-and-extraction-scope.md``).

Reads the project's ownership rules and classifies the headers the run was
given, without parsing anything: the rules a real dump would apply and what
they say about each named header. Per-declaration counts need a parse and
arrive with Phase 2, where each declaration records its owner.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from ..extract.ownership import (
    DeclarationSite,
    OwnershipDecision,
    OwnershipRuleError,
    classify,
    resolve_ownership_rules,
)
from ..model.ownership_rules import CONTRACT_PUBLIC, OWNER_TARGET, OwnershipRules

__all__ = ["OwnershipPreview", "is_unexpected", "ownership_preview"]


@dataclass(frozen=True)
class OwnershipPreview:
    project_root: Path
    rules: OwnershipRules
    #: One (header, decision) per ``-H`` *file*, in the order given.
    headers: tuple[tuple[Path, OwnershipDecision], ...] = ()
    #: Set instead of ``headers`` when the rules contradict each other.
    error: str | None = None


def ownership_preview(
    config_path: Path | None, headers: Sequence[Path]
) -> OwnershipPreview | None:
    """The preview, or ``None`` when no ownership key is configured (the
    preview is then silent, as every optional dry-run section is).

    Roots are relative to the directory holding the config file. A ``-H``
    directory joins the target roots, exactly as it joins
    ``public_header_dirs`` for the real run; a ``-H`` file is what gets
    classified.
    """
    if config_path is None:
        return None
    from .extraction import load_build_config

    try:
        cfg = load_build_config(config_path)
    except ValueError:
        return None
    rules: OwnershipRules = cfg.ownership
    if not rules.is_configured():
        return None
    project_root = config_path.resolve().parent
    header_dirs = [str(h.resolve()) for h in headers if h.is_dir()]
    rules = replace(rules, target_roots=(*cfg.public_header_dirs, *header_dirs))
    try:
        resolved = resolve_ownership_rules(rules, project_root)
    except OwnershipRuleError as exc:
        return OwnershipPreview(project_root, rules, error=str(exc))
    rows = tuple(
        (h, classify(DeclarationSite(str(h.resolve())), resolved))
        for h in headers
        if not h.is_dir()
    )
    return OwnershipPreview(project_root, rules, rows)


def is_unexpected(decision: OwnershipDecision) -> bool:
    """A header named with ``-H`` is meant as public target API; anything
    else deserves a warning line in the preview."""
    return decision.owner != OWNER_TARGET or decision.contract != CONTRACT_PUBLIC
