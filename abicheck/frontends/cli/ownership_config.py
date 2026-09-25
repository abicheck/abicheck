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

"""The project config's ownership rules, for the CLI's ``InputSpec``s.

ADR-075 has no CLI spelling for these keys (ownership is a property of the
project, stated once in ``.abicheck.yml``). ``dump`` and ``compare`` each
already select one project config; this turns that selection into the
``InputSpec.ownership`` value both of a comparison's sides share.
"""

from __future__ import annotations

from pathlib import Path

import click

from ...model.ownership_rules import OwnershipRequest
from ...workflows.ownership_request import ownership_request_from_config

__all__ = ["dump_ownership_request", "project_ownership_request"]


def dump_ownership_request(cfg_path: Path | None) -> OwnershipRequest | None:
    """Load *cfg_path* and return its request; ``None`` with no config.

    A config that fails to load was already reported by the command's own
    config resolution, which runs first; a load error here is re-raised as
    a usage error rather than silently classifying under no rules.
    """
    if cfg_path is None:
        return None
    from ...workflows.extraction import load_build_config

    try:
        cfg = load_build_config(cfg_path)
    except ValueError as exc:
        raise click.UsageError(f"{cfg_path}: {exc}") from exc
    return project_ownership_request(cfg, cfg_path)


def project_ownership_request(
    project_cfg: object, cfg_path: Path | None
) -> OwnershipRequest | None:
    """The request of an already-loaded project config (``compare``'s case).

    ``None`` with no config. Relative roots resolve against the project root
    the config belongs to, which for ``.github/.abicheck.yml`` is not the
    config file's own directory (``config_paths.project_root_for_config``).
    """
    if project_cfg is None or cfg_path is None:
        return None
    from ...config_paths import project_root_for_config

    return ownership_request_from_config(
        project_cfg, project_root_for_config(cfg_path.resolve())
    )
