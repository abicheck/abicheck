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

"""Status-vocabulary tests for the real-integration L2 profiles.

Split from `test_l2_real_profiles.py` (which owns the profile *definitions*,
validation, and prepare-script rendering) because these classes answer a
different question: not "is this profile honestly described" but "can a status
claim completed work that never happened". They grew as one body when the
readiness-vs-measurement defect was fixed, and keeping them here keeps each
module under the architecture gate's test-size ceiling by giving them an owner
rather than by trimming either file.
"""

from __future__ import annotations

import dataclasses
import shutil
from pathlib import Path

import pytest
from _l2_real_profiles_support import materialize_operands, profiles


class TestReadinessIsNotAMeasurement:
    """An empty directory once reported ``MEASURED``.

    ``resolve_status`` checked that ``prepared_root`` was a directory and then
    returned "measured" -- with no library on either side, no headers on either
    side, and no comparison ever run. The existing test *enforced* that
    behaviour: it passed an empty ``tmp_path``, mocked tool availability, and
    asserted ``MEASURED`` under a name (``..._is_measurable``) that described
    readiness while the assertion described completed execution.

    The class is **a status asserting completed work derived from a container's
    existence rather than from its contents or from the work itself**, so these
    tests state it as an invariant over every shipped profile and over generated
    partial trees, rather than re-asserting the one empty-directory input.
    """

    @pytest.fixture(autouse=True)
    def _tools_present(self, monkeypatch):
        monkeypatch.setattr(profiles.shutil, "which", lambda tool: f"/usr/bin/{tool}")
        monkeypatch.setattr(profiles, "toolchain_identity", lambda p: {})

    @pytest.mark.parametrize("profile_id", sorted(profiles.PROFILES))
    def test_an_empty_prepared_tree_is_blocked_not_measured(self, profile_id, tmp_path):
        status = profiles.resolve_status(
            profiles.PROFILES[profile_id], prepared_root=tmp_path, requested=True
        )
        assert status.status == "BLOCKED"
        assert status.reason
        assert status.missing_inputs

    @pytest.mark.parametrize("profile_id", sorted(profiles.PROFILES))
    def test_resolve_status_can_never_return_measured(self, profile_id, tmp_path):
        # The invariant, not the instance: no input to the readiness resolver
        # produces a completed-measurement claim. Swept over every profile and
        # every prepared-tree shape below.
        profile = profiles.PROFILES[profile_id]
        roots = [None, tmp_path / "absent", tmp_path / "empty"]
        (tmp_path / "empty").mkdir()
        complete = tmp_path / "complete"
        materialize_operands(profile, complete)
        roots.append(complete)
        for root in roots:
            status = profiles.resolve_status(
                profile, prepared_root=root, requested=True
            )
            assert status.status != "MEASURED", (root, status.reason)

    @pytest.mark.parametrize("profile_id", sorted(profiles.PROFILES))
    def test_every_single_missing_operand_blocks(self, profile_id, tmp_path):
        """Exhaustive over the profile's own operand set, not one example.

        Each declared operand is removed in turn from an otherwise complete
        tree. A readiness check that consulted only *some* of them -- or only
        the candidate side -- would pass for the ones it skipped.
        """
        profile = profiles.PROFILES[profile_id]
        complete = tmp_path / "complete"
        materialize_operands(profile, complete)
        operands = sorted(
            profile.side_root(complete, side) / path
            for side in ("old", "new")
            for lib in profile.l2_libraries
            for path in (lib.artifact, *lib.public_headers)
        )
        assert operands, profile_id
        assert (
            profiles.resolve_status(
                profile, prepared_root=complete, requested=True
            ).status
            == "READY"
        ), "vacuity guard: the complete tree must be ready to begin with"
        for index, operand in enumerate(operands):
            root = tmp_path / f"minus_{index}_{operand.name}"
            shutil.copytree(complete, root, symlinks=True)
            victim = root / operand.relative_to(complete)
            if victim.is_dir():
                shutil.rmtree(victim)
            else:
                victim.unlink()
            status = profiles.resolve_status(
                profile, prepared_root=root, requested=True
            )
            assert status.status == "BLOCKED", (operand, status.reason)
            assert status.missing_inputs, operand

    @pytest.mark.parametrize("profile_id", sorted(profiles.PROFILES))
    def test_a_complete_historical_side_alone_is_not_enough(self, profile_id, tmp_path):
        # A comparison needs both operands; a missing historical side is exactly
        # as disqualifying as a missing candidate one.
        profile = profiles.PROFILES[profile_id]
        materialize_operands(profile, tmp_path)
        shutil.rmtree(profile.side_root(tmp_path, "new"))
        status = profiles.resolve_status(
            profile, prepared_root=tmp_path, requested=True
        )
        assert status.status == "BLOCKED"
        assert any("new" in entry for entry in status.missing_inputs)


class TestMeasuredRequiresACompletedMeasurement:
    """``MEASURED`` is reachable only through a validated, timed result."""

    def _ready(self, monkeypatch, tmp_path, profile):
        monkeypatch.setattr(profiles.shutil, "which", lambda tool: f"/usr/bin/{tool}")
        monkeypatch.setattr(profiles, "toolchain_identity", lambda p: {})
        materialize_operands(profile, tmp_path)
        status = profiles.resolve_status(
            profile, prepared_root=tmp_path, requested=True
        )
        assert status.status == "READY", status.reason
        return status

    def test_a_validated_result_promotes(self, monkeypatch, tmp_path):
        ready = self._ready(monkeypatch, tmp_path, profiles.SVS)
        report = tmp_path / "report.json"
        report.write_text("{}")
        measured = profiles.promote_to_measured(
            ready,
            profiles.MeasurementResult(
                "svs", ("svs_runtime",), wall_seconds=3.5, output_paths=(report,)
            ),
        )
        assert measured.status == "MEASURED"
        assert measured.measurement["libraries"] == ["svs_runtime"]
        assert measured.measurement["wall_seconds"] == 3.5
        assert measured.measurement["promoted_from"] == "READY"

    @pytest.mark.parametrize(
        "result_kwargs, expected",
        [
            ({"libraries": ()}, "measured nothing"),
            ({"wall_seconds": 0.0}, "not a timed window"),
            ({"wall_seconds": -1.0}, "not a timed window"),
            ({"output_paths": (Path("/nonexistent/report.json"),)}, "do not exist"),
            ({"libraries": ("not_a_library",)}, "unmeasurable"),
            ({"profile_id": "pvxs"}, "cannot promote the status"),
        ],
    )
    def test_an_unvalidated_result_cannot_promote(
        self, monkeypatch, tmp_path, result_kwargs, expected
    ):
        ready = self._ready(monkeypatch, tmp_path, profiles.SVS)
        base = {
            "profile_id": "svs",
            "libraries": ("svs_runtime",),
            "wall_seconds": 2.0,
            "output_paths": (),
        }
        base.update(result_kwargs)
        with pytest.raises(ValueError, match=expected):
            profiles.promote_to_measured(ready, profiles.MeasurementResult(**base))

    @pytest.mark.parametrize("status_name", ["BLOCKED", "NOT_RUN", "MEASURED"])
    def test_a_status_that_was_never_ready_cannot_promote(self, status_name):
        with pytest.raises(ValueError, match="only a ready profile"):
            profiles.promote_to_measured(
                profiles.ProfileStatus("svs", status_name, reason="r"),
                profiles.MeasurementResult("svs", ("svs_runtime",), 1.0),
            )

    def test_a_partial_status_promotes_only_over_its_measurable_libraries(
        self, monkeypatch, tmp_path
    ):
        # Promoting a PARTIAL status over a blocked library would republish the
        # coverage gap PARTIAL exists to expose.
        monkeypatch.setattr(
            profiles.shutil,
            "which",
            lambda tool: None if tool == "icpx" else f"/usr/bin/{tool}",
        )
        monkeypatch.setattr(profiles, "toolchain_identity", lambda p: {})
        materialize_operands(profiles.ONEDAL, tmp_path)
        partial = profiles.resolve_status(
            profiles.ONEDAL, prepared_root=tmp_path, requested=True
        )
        assert partial.status == "PARTIAL"
        promoted = profiles.promote_to_measured(
            partial,
            profiles.MeasurementResult("onedal", ("onedal_core",), 9.0),
        )
        assert promoted.status == "MEASURED"
        assert promoted.measurement["promoted_from"] == "PARTIAL"
        assert promoted.blocked_libraries
        with pytest.raises(ValueError, match="unmeasurable"):
            profiles.promote_to_measured(
                partial,
                profiles.MeasurementResult("onedal", ("onedal_dpc",), 9.0),
            )


class TestSideAcquisitionIsDeclared:
    @pytest.mark.parametrize("profile_id", sorted(profiles.PROFILES))
    def test_every_side_names_how_it_is_obtained(self, profile_id):
        profile = profiles.PROFILES[profile_id]
        for side in ("old", "new"):
            assert profile.source_for_side(side) in profiles.SIDE_SOURCES

    @pytest.mark.parametrize("profile_id", sorted(profiles.PROFILES))
    def test_every_built_side_has_commands_that_build_it(self, profile_id):
        profile = profiles.PROFILES[profile_id]
        for side in ("old", "new"):
            if profile.source_for_side(side) == "build_from_revision":
                assert profile.commands_for_side(side), (profile_id, side)

    def test_a_prebuilt_side_carrying_build_commands_is_rejected(self):
        bad = dataclasses.replace(
            profiles.SVS,
            side_commands={},
            side_sources={"old": "prebuilt_distribution"},
        )
        assert any(
            "measures the rebuild" in problem
            for problem in profiles.validate_profile(bad)
        )

    def test_a_built_side_with_no_commands_is_rejected(self):
        bad = dataclasses.replace(profiles.PVXS, per_side_commands=())
        problems = profiles.validate_profile(bad)
        assert any("can never exist" in problem for problem in problems)

    def test_the_script_refuses_to_silently_skip_a_supplied_side(self):
        # A prebuilt side produces no build commands, so the script must say so
        # and fail loudly if the distribution is absent -- an absent operand tree
        # is the exact condition that used to read as a completed measurement.
        script = profiles.prepare_script(profiles.SVS)
        assert "PREBUILT DISTRIBUTION" in script
        assert 'if [ ! -d "$ROOT"/svs_old ]' in script
        assert "exit 1" in script

    def test_a_built_side_installs_into_its_own_operand_root(self):
        # {root} is the operand tree `missing_inputs` and the comparison read;
        # {src_root} is the checkout. A build that checked out over the operand
        # tree would leave the two indistinguishable.
        script = profiles.prepare_script(profiles.SVS_PR_BASE)
        assert "svs_pr_base_old_src" in script
        assert '-DCMAKE_INSTALL_PREFIX="$ROOT"/svs_pr_base_old ' in script
