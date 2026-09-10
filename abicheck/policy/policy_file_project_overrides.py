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

ADR-061 task routing ("decide relevance, suppression, classification,
severity, or gating" -> ``policy/``): this module implements exactly that
decision -- which precedence tier a project-config-stated severity
override lands at -- so it belongs under ``abicheck/policy/`` rather than
the flat legacy root, unlike ``policy_file.py``/``policy_file_versioning.py``/
``policy_file_acknowledgment.py`` (parsing helpers grandfathered into
``architecture/modules.yaml``'s ``legacy_paths`` allowlist for the
``policy_file.py`` family as a whole). Originally landed at
``abicheck/policy_file_project_overrides.py`` and grandfathered the same
way; moved here once a second review round found its *placement* was itself
one of the findings, not just its precedence bug (see
:func:`apply_lower_precedence_overrides`'s own docstring for that bug).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from ..change_registry_types import Verdict
from ..checker_policy import ChangeKind


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
    source (see :func:`apply_lower_precedence_overrides`), so "nothing
    stated" must be indistinguishable from "no project config at all".
    """
    from ..policy_file import _parse_overrides

    raw = getattr(project_cfg, "policy_overrides", None)
    if not raw:
        return {}
    return _parse_overrides(raw, project_path or Path(".abicheck.yml"))


def apply_lower_precedence_overrides(
    policy_file: Any,
    overrides: dict[ChangeKind, Verdict],
    *,
    base_policy: str,
) -> Any:
    """Fold *overrides* into *policy_file* at a precedence tier strictly
    *below* whatever *policy_file* already carries: a ``ChangeKind`` the
    file already states (an explicit ``--policy <file>`` entry, or one an
    already-applied ``--pack`` contributed) always wins; only a kind
    neither already claimed is filled in from *overrides*.

    This is the general primitive both the project-config route
    (:func:`merge_project_config_policy_overrides`) and, since a second
    review round, the directory/package release fan-out build on: the
    original (single-caller) version of this function merged project-config
    values into *policy_file* **before** ``--pack`` folding ever ran
    (``pack_application.policy_file_with_packs``/``pack_application()``),
    which reads a value already present in ``policy_file.overrides`` as
    "explicitly stated by ``--policy <file>``" and therefore refuses to let
    a pack override it for the same kind (ADR-049 D8's "explicit-last, never
    overwrites a kind the file states" rule). That made a project-config
    ``ignore`` silently outrank an explicit ``--pack``'s ``break`` for the
    same kind -- backwards from D7's precedence order
    (``explicit_cli/api_request > legacy_alias > run_recipe > run_profile >
    project_config > built_in_default``), where ``project_config`` is
    *weaker* than an explicit selection. The fix is call-site ordering, not
    a change to this merge rule itself: apply this **after** every other
    fold that should outrank it has already run, so "whatever's already in
    ``policy_file.overrides``" always means "everything with real
    precedence over project config", not "just the file".

    *policy_file* is a ``PolicyFile | None`` (typed ``Any`` here purely to
    avoid a module-level import of ``policy_file.py``, which this module's
    own docstring explains the split from -- importing it only inside the
    function body avoids growing that capped module's own import surface
    for no behavioral reason).

    Returns *policy_file* unchanged when *overrides* is empty (the common
    case), so every existing invocation with nothing to fold in is
    bit-for-bit unaffected. Returns a freshly-constructed
    :class:`~abicheck.policy_file.PolicyFile` (``base_policy=base_policy``)
    when *policy_file* is ``None`` but *overrides* is non-empty -- the same
    "policy applies even with no explicit ``--policy`` file" contract an
    explicit ``--policy <file>`` already has.
    """
    if not overrides:
        return policy_file
    from ..policy_file import PolicyFile

    if policy_file is None:
        return PolicyFile(base_policy=base_policy, overrides=dict(overrides))
    merged = dict(overrides)
    merged.update(policy_file.overrides)  # whatever's already stated wins
    return replace(policy_file, overrides=merged)


def merge_project_config_policy_overrides(
    policy_file: Any,
    *,
    base_policy: str,
    project_cfg: Any,
    project_path: Path | None = None,
) -> Any:
    """Fold ``.abicheck.yml``'s ``policy.overrides`` into *policy_file*, at
    the ``PROJECT_CONFIG`` precedence tier (ADR-049 D7).

    **Caller contract (this is the part a second review round found had
    been violated at the single call site this function had):** call this
    only after every fold with real precedence over project config --
    an explicit ``--policy <file>`` load, and any ``--pack`` fold on top of
    it -- has already happened. See :func:`apply_lower_precedence_overrides`
    (this function's implementation) for the full account of what goes
    wrong when a caller merges project config in first.

    Returns *policy_file* unchanged when the project config states no
    overrides at all, so every existing invocation with no ``policy:``
    block in its ``.abicheck.yml`` is bit-for-bit unaffected.
    """
    return apply_lower_precedence_overrides(
        policy_file,
        resolve_project_config_policy_overrides(project_cfg, project_path),
        base_policy=base_policy,
    )
