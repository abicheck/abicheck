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

"""ADR-068 §3 #23: ``.abicheck.yml``'s ``policy.overrides`` block -- the
documented replacement route for the retired ``--crosscheck KEY=LEVEL``
flag.

A sibling of ``policy_file.py``/``compatibility_evaluation_wiring.py``
rather than an addition to either: both carry a ``no_growth``
architecture-debt baseline (``architecture/debt.yaml``), and per this
repo's own convention ("the way to shrink an entry is to move
responsibility out to a properly-owned module, never to trim the file to
fit" -- root ``AGENTS.md``), a genuinely new parsing/folding
responsibility gets its own leaf module instead of growing a capped one --
mirrors ``policy_file_versioning.py``'s identical split for the
``versioning:`` namespace.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from .change_registry_types import Verdict
from .checker_policy import ChangeKind


def resolve_project_config_policy_overrides(
    project_cfg: Any, project_path: Path | None = None
) -> dict[ChangeKind, Verdict]:
    """Parse a discovered ``.abicheck.yml``'s ``policy.overrides`` block into
    a ``ChangeKind -> Verdict`` mapping.

    Reuses ``policy_file._parse_overrides`` -- the identical slug/severity
    validation an explicit ``--policy <file>``'s own ``overrides:`` block
    goes through, so a real ``ChangeKind`` slug and a real severity spelling
    (``break``/``warn``/``risk``/``ignore``) are the only accepted shape
    either way, and an unrecognized slug or spelling is the identical hard
    :class:`~abicheck.errors.PolicyError`, not a silently-dropped entry.

    Returns an empty mapping for a *None* / non-``BuildConfig`` / stated-
    nothing *project_cfg* -- callers fold this in as the weakest-precedence
    source (see :func:`merge_project_config_policy_overrides`), so "nothing
    stated" must be indistinguishable from "no project config at all".
    """
    from .policy_file import _parse_overrides

    raw = getattr(project_cfg, "policy_overrides", None)
    if not raw:
        return {}
    return _parse_overrides(raw, project_path or Path(".abicheck.yml"))


def merge_project_config_policy_overrides(
    policy_file: Any,
    *,
    base_policy: str,
    project_cfg: Any,
    project_path: Path | None = None,
) -> Any:
    """Fold ``.abicheck.yml``'s ``policy.overrides`` into *policy_file*,
    at the ``PROJECT_CONFIG`` precedence tier: an explicitly loaded
    ``--policy <file>``'s own ``overrides:`` entry for a given
    ``ChangeKind`` always wins over the project-config-stated one for the
    same kind (ADR-049 D7's "explicit_cli/api_request > ... > project_config
    > built_in_default" tier order) -- only a kind the explicit policy file
    left unstated is filled in from the project config.

    *policy_file* is a ``PolicyFile | None`` (typed ``Any`` here purely to
    avoid a module-level import of ``policy_file.py``, which this module's
    own docstring explains the split from -- importing it only inside the
    function body avoids growing that capped module's own import surface
    for no behavioral reason).

    Returns *policy_file* unchanged when the project config states no
    overrides at all (the common case), so every existing invocation with
    no ``policy:`` block in its ``.abicheck.yml`` is bit-for-bit unaffected.
    Returns a freshly-constructed :class:`~abicheck.policy_file.PolicyFile`
    (``base_policy=base_policy``) when no ``--policy <file>`` was given at
    all but the project config states overrides -- the same "policy applies
    even with no explicit ``--policy`` file" contract an explicit
    ``--policy <file>`` already has.
    """
    project_overrides = resolve_project_config_policy_overrides(
        project_cfg, project_path
    )
    if not project_overrides:
        return policy_file
    from .policy_file import PolicyFile

    if policy_file is None:
        return PolicyFile(base_policy=base_policy, overrides=dict(project_overrides))
    merged = dict(project_overrides)
    merged.update(policy_file.overrides)  # explicit --policy wins per kind
    return replace(policy_file, overrides=merged)
