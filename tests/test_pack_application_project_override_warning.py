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

"""Round 9/10 finding (Codex review, fresh evidence):
``policy/policy_file_project_overrides.py``'s project-config policy fold
(reached via ``pack_application.resolve_bundle_policy_file``,
``cli_compare_helpers``'s scalar-compare fold, ``cli_scan``'s ``scan
--against`` fold, and ``compare_bundle_facts.py``'s stored-bundle
dispatcher fold) never ran the shared ``validate_overrides()``/
``HIGH RISK`` warning check -- every CLI path warns on a risky *explicit*
``--policy <file>`` downgrade (``_load_suppression_and_policy``), but a
project-config-sourced downgrade of the identical shape
(``.abicheck.yml``'s ``policy.overrides.func_removed: ignore``) took
effect completely silently, since that warning check ran *before* the
project-config fold that introduces this specific override.

Covers the general mechanism directly through
``pack_application.resolve_bundle_policy_file`` -- the shared resolver
every non-scalar-compare route above now funnels through -- rather than
threading a full compiled-binary CLI invocation through each of the four
call sites individually; the fold itself (not which command reached it)
is the bug class.
"""

from __future__ import annotations

from pathlib import Path

from abicheck.change_registry_types import Verdict
from abicheck.checker_policy import ChangeKind
from abicheck.pack_application import resolve_bundle_policy_file


class TestProjectConfigOverrideWarning:
    def test_high_risk_project_override_is_warned(self, tmp_path: Path, capsys) -> None:
        # Already-resolved `ChangeKind -> Verdict` mapping -- the shape
        # `resolve_bundle_policy_file`'s `project_policy_overrides` takes
        # (the raw-YAML-slug parsing already happened upstream, mirroring
        # every other caller of this function).
        pf = resolve_bundle_policy_file(
            None,
            "strict_abi",
            None,
            None,
            project_policy_overrides={ChangeKind.FUNC_REMOVED: Verdict.COMPATIBLE},
        )
        assert pf is not None
        assert pf.overrides
        err = capsys.readouterr().err
        assert "HIGH RISK" in err
        assert "func_removed" in err

    def test_safe_project_override_does_not_warn(self, tmp_path: Path, capsys) -> None:
        # Not a downgrade of a critical breaking kind -- negative control
        # proving the warning is conditional on real risk, not merely "any
        # project override at all fired the check".
        pf = resolve_bundle_policy_file(
            None,
            "strict_abi",
            None,
            None,
            project_policy_overrides={
                ChangeKind.FUNC_ADDED: Verdict.COMPATIBLE_WITH_RISK
            },
        )
        assert pf is not None
        err = capsys.readouterr().err
        assert "HIGH RISK" not in err

    def test_no_project_overrides_is_a_silent_no_op(
        self, tmp_path: Path, capsys
    ) -> None:
        pf = resolve_bundle_policy_file(
            None, "strict_abi", None, None, project_policy_overrides=None
        )
        assert pf is None
        assert capsys.readouterr().err == ""
