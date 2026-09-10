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

"""CodeRabbit review, round 8: ``pack_application()``'s provenance must not
mislabel a project-config-contributed ``ChangeKind`` override as
pack-sourced.

Split out of ``tests/test_pack_application.py`` (already at its own
``architecture/debt.yaml`` ``no_growth`` line-count ceiling) rather than
trimmed to fit there -- the same "move responsibility, don't shrink the
file" rule the root ``AGENTS.md`` states for every debt-tracked module
applies identically to a debt-tracked test file.
"""

from __future__ import annotations

from pathlib import Path

from abicheck.compatibility_evaluation_frontend import (
    ExplicitCompatibilityInputs,
    FrontEnd,
    ProjectCompatibilityInputs,
    resolve_compatibility_evaluation_config,
)
from abicheck.pack_application import pack_application


def _pack(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def test_a_project_contributed_kind_does_not_leak_into_pack_overrides(
    tmp_path: Path,
) -> None:
    """A project-config (``.abicheck.yml``) ``policy.overrides`` kind that
    neither an explicit ``--policy`` file nor a selected pack claims must
    not be misreported as pack-sourced in ``PackApplication.
    policy_overrides`` -- it used to be, because ``pack_application()``
    derived "pack-contributed" from the fully merged ``overrides`` (which
    also contains the project's own contribution) minus only the explicit
    file's kinds. Scoring stayed correct either way (both are real
    overrides), but a "pack-sourced" label on a project-sourced kind is
    wrong provenance -- exactly the bug this pins directly. See
    ``pack_application()``'s own docstring (and its ``pack_overrides``
    field's own docstring on ``CompatibilityPolicyConfig``) for the fix.
    """
    pack = _pack(
        tmp_path,
        "ignore-removals.yml",
        "id: relax_removals\nversion: 1\nkind: policy\n"
        "assignments:\n  func_removed: ignore\n",
    )
    config = resolve_compatibility_evaluation_config(
        front_end=FrontEnd.CLI,
        explicit=ExplicitCompatibilityInputs(pack_paths=(str(pack),)),
        project=ProjectCompatibilityInputs(policy_overrides={"var_removed": "ignore"}),
    )
    application = pack_application(config, policy_file=None)
    contributed = {k.value for k in application.policy_overrides}
    # The pack's own kind is genuinely pack-sourced -- must still be
    # present (this is not a test that packs stop contributing).
    assert "func_removed" in contributed
    # The project's kind is not this pack's (or any pack's) -- must not be
    # misreported as pack-sourced.
    assert "var_removed" not in contributed
