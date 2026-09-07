# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""ADR-068 plan §5 P4: the config-sourced half of ``compare``'s public/
internal header boundary.

``compare``'s live-binary dumping already tags declaration provenance
(``ScopeOrigin.PUBLIC_HEADER``/``PRIVATE_HEADER``/... via
``provenance.apply_provenance``) from a ``-H``/``--header`` *directory*
argument -- see ``workflows.cross_source_evolution``'s own module docstring
for the full account of why that boundary is what lets
``exported_not_public``/``public_not_exported``/``rtti_for_internal_type``/
``public_to_internal_dependency`` evidence-gate correctly rather than
fabricate a finding. This module adds the second named source plan §5 P4
asks for: a project's ``.abicheck.yml`` ``scope.public_header_dirs`` list
(``buildsource.build_config.BuildConfig.public_header_dirs``) -- a *new*
config key, deliberately distinct from the pre-existing ``scope.public``
boolean (a public-surface FP-scoping toggle unrelated to declaration
provenance, despite the naming collision the plan doc's own shorthand
risks suggesting).

:func:`project_config_public_header_dirs` is intentionally the only thing
here: a config value is always a directory (never a file), so it can never
weaken the file-vs-directory asymmetry ``scan --public-header-dir``
implements via ``workflows.scan_config.public_provenance_set`` -- there is
no boundary-derivation *logic* to share, only a value to project out of an
already-loaded config. ``cli_resolve._resolve_compare_snapshots`` folds the
result into both sides' ``InputSpec.public_header_dirs`` -- the same field
a ``-H`` directory argument already populates -- so this is purely an
additional input to the existing ``apply_provenance`` machinery, not a
second boundary-tagging mechanism.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def project_config_public_header_dirs(project_cfg: Any) -> list[Path]:
    """The project's ``.abicheck.yml`` ``scope.public_header_dirs`` list, as
    ``Path`` objects.

    *project_cfg* is loosely typed (``BuildConfig | None``, read via
    ``getattr`` rather than an import) so callers across both the
    ``frontends`` and ``workflows`` layers can use this without pulling in
    ``buildsource.build_config`` just for a type annotation. No discovered
    config, or a discovered config that never set the key, both resolve to
    an empty list -- the caller then folds in nothing and behavior is
    exactly what it was before this key existed.
    """
    dirs = getattr(project_cfg, "public_header_dirs", None)
    return [Path(d) for d in dirs] if dirs else []
