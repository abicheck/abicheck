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

"""``compare``'s fold of ``debug.info``/``deps.*`` into the flags they back.

The four keys are the config homes of ``--debug-info``, ``--follow-deps``,
``--search-path`` and ``--ld-library-path`` (ADR-091 D4, 2026-10-31
amendment). They are folded into the command's own keyword arguments once,
after the side-aware options were normalized and before ``run_compare``
dispatches, so the single-pair and directory/package routes read a configured
value exactly as they read the flag. The values come from
:mod:`abicheck.workflows.config_run_inputs`, which the typed request reads
too.
"""

from __future__ import annotations

from typing import Any

import click

from ...workflows.config_run_inputs import resolve_debug_info, resolve_deps_settings
from .options.evidence_roles import (
    reject_unsupported_detached_debug,
    split_debug_evidence,
)

__all__ = ["fold_config_run_inputs"]

#: The normalized ``--debug-info`` destinations (``split_debug_evidence``).
_DEBUG_DESTS: tuple[str, ...] = (
    "debug_roots",
    "debug_roots_old",
    "debug_roots_new",
    "debug_info1",
    "debug_info2",
)


def fold_config_run_inputs(kwargs: dict[str, Any]) -> None:
    """Fill the dependency-walk and debug-info kwargs from the project config.

    Each stated flag wins and is never merged with the configured value; an
    unstated one takes ``--set``, else the file. Updates *kwargs* in place.
    """
    from .project_config import resolve_project_compare_config

    cfg_path, project_cfg, _, _ = resolve_project_compare_config(
        config=kwargs.get("config"),
        severity_preset=None,
        config_overrides=kwargs.get("config_overrides"),
    )
    if project_cfg is None:
        return
    deps = resolve_deps_settings(
        project_cfg,
        cfg_path,
        follow=bool(kwargs.get("follow_deps")),
        search_paths=kwargs.get("search_paths") or (),
        ld_library_path=kwargs.get("ld_library_path") or "",
    )
    missing = [str(p) for p in deps.search_paths if not p.exists()]
    if missing:
        raise click.UsageError(
            f"deps.search_paths: {', '.join(missing)} does not exist "
            "(the same check --search-path applies)"
        )
    kwargs["follow_deps"] = deps.follow
    kwargs["search_paths"] = deps.search_paths
    kwargs["ld_library_path"] = deps.ld_library_path
    if any(kwargs.get(dest) for dest in _DEBUG_DESTS):
        return
    configured = resolve_debug_info((), project_cfg, cfg_path)
    if not configured:
        return
    reject_unsupported_detached_debug(path for _side, path in configured)
    kwargs.update(split_debug_evidence(configured))
