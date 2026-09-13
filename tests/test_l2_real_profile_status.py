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
import subprocess
from pathlib import Path

import pytest
from _l2_real_profiles_support import (
    materialize_operands,
    measurement_output,
    profiles,
)


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
            ({"output_paths": ()}, "names no output"),
            (
                {"output_paths": (Path("/nonexistent/report.json"),)},
                "not existing files",
            ),
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
            # A valid baseline in every field, so each parametrized case is the
            # ONLY thing wrong with the result it builds.
            "output_paths": (measurement_output(tmp_path),),
        }
        base.update(result_kwargs)
        with pytest.raises(ValueError, match=expected):
            profiles.promote_to_measured(ready, profiles.MeasurementResult(**base))

    @pytest.mark.parametrize("status_name", ["BLOCKED", "NOT_RUN", "MEASURED"])
    def test_a_status_that_was_never_ready_cannot_promote(self, status_name):
        with pytest.raises(ValueError, match="only a ready profile"):
            profiles.promote_to_measured(
                profiles.ProfileStatus("svs", status_name, reason="r"),
                profiles.MeasurementResult(
                    "svs", ("svs_runtime",), 1.0, output_paths=(Path(__file__),)
                ),
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
            profiles.MeasurementResult(
                "onedal",
                ("onedal_core", "onedal", "onedal_parameters"),
                9.0,
                output_paths=(measurement_output(tmp_path),),
            ),
        )
        assert promoted.status == "MEASURED"
        assert promoted.measurement["promoted_from"] == "PARTIAL"
        assert promoted.blocked_libraries
        with pytest.raises(ValueError, match="unmeasurable"):
            profiles.promote_to_measured(
                partial,
                profiles.MeasurementResult(
                    "onedal",
                    ("onedal_dpc",),
                    9.0,
                    output_paths=(measurement_output(tmp_path),),
                ),
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

    @staticmethod
    def _run_supplied_side_guard(tmp_path: Path) -> subprocess.CompletedProcess:
        """Actually execute SVS's supplied-side guard against *tmp_path*.

        Executed, not text-asserted. This repository has the scar for that
        distinction (#705 asserted YAML text and #758 had to add the test that
        ran the attack), and it matters here for the same reason: the first
        version of this guard was `[ -d svs_old ]`, which *contains* every
        string a text assertion would look for while passing for an empty
        directory.
        """
        script = profiles.prepare_script(profiles.SVS)
        guard = [
            line
            for line in script.splitlines()
            if line.startswith("if [ ") or line.startswith("ROOT=")
        ]
        assert guard, "no guard was generated for the supplied side"
        return subprocess.run(
            ["bash", "-c", "set -u\n" + "\n".join(guard)],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_the_guard_rejects_an_absent_supplied_side(self, tmp_path):
        result = self._run_supplied_side_guard(tmp_path)
        assert result.returncode == 1, result.stdout
        assert "missing old side" in result.stderr

    def test_the_guard_rejects_an_empty_supplied_side_directory(self, tmp_path):
        # The case `[ -d svs_old ]` accepted: the directory exists and holds
        # nothing, so the script would build the candidate and exit 0 having
        # never been given the baseline it claims to prepare.
        (tmp_path / "svs_old").mkdir()
        result = self._run_supplied_side_guard(tmp_path)
        assert result.returncode == 1, result.stdout
        assert "missing old side" in result.stderr

    def test_the_guard_rejects_a_header_root_holding_no_header(self, tmp_path):
        root = tmp_path / "svs_old"
        (root / "lib").mkdir(parents=True)
        (root / "lib/libsvs_runtime.so").write_bytes(b"\x7fELF")
        (root / "include/svs/runtime").mkdir(parents=True)
        (root / "include/svs/runtime/README.md").write_text("not a header")
        result = self._run_supplied_side_guard(tmp_path)
        assert result.returncode == 1, result.stdout
        assert "header evidence" in result.stderr

    def test_the_guard_accepts_a_complete_supplied_side(self, tmp_path):
        # Vacuity guard: the guard must be satisfiable, or the three rejections
        # above would pass against a guard that always fails.
        root = tmp_path / "svs_old"
        (root / "lib").mkdir(parents=True)
        (root / "lib/libsvs_runtime.so").write_bytes(b"\x7fELF")
        (root / "include/svs/runtime").mkdir(parents=True)
        (root / "include/svs/runtime/api_defs.h").write_text("int x;")
        result = self._run_supplied_side_guard(tmp_path)
        assert result.returncode == 0, result.stderr

    def test_the_script_states_that_the_side_is_supplied(self):
        script = profiles.prepare_script(profiles.SVS)
        assert "PREBUILT DISTRIBUTION" in script

    def test_a_built_side_installs_into_its_own_operand_root(self):
        # {root} is the operand tree `missing_inputs` and the comparison read;
        # {src_root} is the checkout. A build that checked out over the operand
        # tree would leave the two indistinguishable.
        script = profiles.prepare_script(profiles.SVS_PR_BASE)
        assert "svs_pr_base_old_src" in script
        assert '-DCMAKE_INSTALL_PREFIX="$ROOT"/svs_pr_base_old ' in script


class TestOperandChecksRespectTheContextSplit:
    """A context this host cannot build does not also owe its operands.

    The first version of the operand check ran over every declared library
    unconditionally, before the context split was resolved. oneDAL on a host
    without ``icpx`` then reported ``BLOCKED`` for the two DPC++ artifacts a
    host-only build correctly never produces -- making ``PARTIAL``, the whole
    point of ``context_tools``, reachable only when the already-unmeasurable
    operands happened to be present anyway (Codex review).

    The class is **a precondition check that demands evidence for work it has
    already ruled out**, so these tests build genuinely partial trees (only the
    buildable contexts' operands) rather than the complete ones the shared
    fixture makes -- which is exactly what hid the regression: a fixture that
    materializes everything cannot tell a context-aware check from a blind one.
    """

    @staticmethod
    def _materialize_contexts(profile, prepared_root: Path, contexts: set[str]):
        """Only the operands belonging to *contexts* -- a real partial tree."""
        prepared_root.mkdir(parents=True, exist_ok=True)
        for side in ("old", "new"):
            root = profile.side_root(prepared_root, side)
            # The side tree exists even when this host can build nothing in it,
            # so a test of the context split is not answered by the earlier
            # "no prepared build tree" blocker instead.
            root.mkdir(parents=True, exist_ok=True)
            for lib in profile.l2_libraries:
                if lib.context not in contexts:
                    continue
                artifact = root / lib.artifact
                artifact.parent.mkdir(parents=True, exist_ok=True)
                artifact.write_bytes(b"\x7fELF")
                for header in (*lib.public_headers, *lib.include_roots):
                    target = root / header
                    if target.suffix in profiles.HEADER_SUFFIXES:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_text("int x;")
                    else:
                        target.mkdir(parents=True, exist_ok=True)
                        (target / "api.h").write_text("int x;")

    def test_a_host_only_tree_is_partial_not_blocked(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            profiles.shutil,
            "which",
            lambda tool: None if tool == "icpx" else f"/usr/bin/{tool}",
        )
        monkeypatch.setattr(profiles, "toolchain_identity", lambda p: {})
        self._materialize_contexts(profiles.ONEDAL, tmp_path, {"host"})
        status = profiles.resolve_status(
            profiles.ONEDAL, prepared_root=tmp_path, requested=True
        )
        assert status.status == "PARTIAL", status.reason
        assert status.measurable_libraries == [
            "onedal_core",
            "onedal",
            "onedal_parameters",
        ]
        assert set(status.blocked_libraries) == {
            "onedal_dpc",
            "onedal_parameters_dpc",
        }

    @pytest.mark.parametrize("profile_id", sorted(profiles.PROFILES))
    def test_no_unbuildable_context_is_ever_demanded(
        self, monkeypatch, tmp_path, profile_id
    ):
        """The invariant, over every profile and every one of its contexts.

        Generalized rather than asserted for oneDAL's `dpcpp` alone: each
        profile is given a synthetic per-context tool for every context it
        declares, then each context in turn is made unbuildable by withholding
        exactly that tool while only the *other* contexts' operands exist on
        disk. The resolver must never report the withheld context's operands as
        a blocker, whatever the profile's shape -- the defect was in the shared
        resolver, not in one profile.
        """
        profile = profiles.PROFILES[profile_id]
        contexts = sorted({lib.context for lib in profile.l2_libraries})
        assert contexts, profile_id
        monkeypatch.setattr(profiles, "toolchain_identity", lambda p: {})
        for withheld in contexts:
            variant = dataclasses.replace(
                profile,
                context_tools={
                    context: (f"fake-{context}-cc",) for context in contexts
                },
            )
            absent_tool = f"fake-{withheld}-cc"
            monkeypatch.setattr(
                profiles.shutil,
                "which",
                lambda tool, _absent=absent_tool: (
                    None if tool == _absent else f"/usr/bin/{tool}"
                ),
            )
            root = tmp_path / f"without_{withheld}"
            buildable = set(contexts) - {withheld}
            self._materialize_contexts(variant, root, buildable)
            status = profiles.resolve_status(
                variant, prepared_root=root, requested=True
            )
            withheld_libraries = [
                lib.name for lib in variant.l2_libraries if lib.context == withheld
            ]
            if not buildable:
                # A single-context profile with its only context withheld is
                # BLOCKED on the context, which is the honest answer -- and it
                # must name the tool, not the operands it would have needed.
                assert status.status == "BLOCKED", (withheld, status.reason)
                assert status.missing_inputs == []
                assert set(status.blocked_libraries) == set(withheld_libraries)
                continue
            assert status.status == "PARTIAL", (withheld, status.reason)
            assert status.missing_inputs == []
            for name in withheld_libraries:
                assert name not in status.measurable_libraries, (withheld, name)


class TestHeaderEvidenceMustBeEvidence:
    """An empty header root is not header evidence.

    ``missing_inputs`` first accepted any declared public header whose path
    merely ``exists()``. Both SVS profiles declare a header *root*
    (``include/svs/runtime``), so a partial extraction, an interrupted install,
    or a distribution whose layout moved left an empty directory that satisfied
    the check -- and a run with no header evidence at all could proceed toward
    appearing to have completed an L2 comparison (Codex review). That is this
    PR's own bug class one level down: a container's existence taken for its
    contents.
    """

    @pytest.fixture(autouse=True)
    def _tools_present(self, monkeypatch):
        monkeypatch.setattr(profiles.shutil, "which", lambda tool: f"/usr/bin/{tool}")
        monkeypatch.setattr(profiles, "toolchain_identity", lambda p: {})

    def test_an_empty_header_root_is_blocked(self, tmp_path):
        materialize_operands(profiles.SVS, tmp_path)
        for side in ("old", "new"):
            root = profiles.SVS.side_root(tmp_path, side) / "include/svs/runtime"
            for child in root.iterdir():
                child.unlink()
        status = profiles.resolve_status(
            profiles.SVS, prepared_root=tmp_path, requested=True
        )
        assert status.status == "BLOCKED"
        assert all(
            "contains no header file" in entry for entry in status.missing_inputs
        )

    def test_a_root_holding_only_non_headers_is_blocked(self, tmp_path):
        # The near-miss an `exists()` check and a naive `any(iterdir())` check
        # both accept: a directory that is populated, but with nothing that is
        # a header.
        materialize_operands(profiles.SVS, tmp_path)
        for side in ("old", "new"):
            root = profiles.SVS.side_root(tmp_path, side) / "include/svs/runtime"
            for child in root.iterdir():
                child.unlink()
            (root / "README.md").write_text("not a header")
            (root / "CMakeLists.txt").write_text("not a header")
        status = profiles.resolve_status(
            profiles.SVS, prepared_root=tmp_path, requested=True
        )
        assert status.status == "BLOCKED"

    @pytest.mark.parametrize("suffix", sorted(profiles.HEADER_SUFFIXES))
    def test_every_declared_header_suffix_counts_as_evidence(self, tmp_path, suffix):
        # Exhaustive over the vocabulary rather than the one suffix SVS ships:
        # a suffix silently absent from the check would make a real header root
        # read as empty.
        root = tmp_path / "inc"
        root.mkdir()
        (root / f"api{suffix}").write_text("int x;")
        assert profiles.header_evidence_missing(root) is None

    def test_a_nested_header_counts(self, tmp_path):
        root = tmp_path / "inc"
        (root / "detail").mkdir(parents=True)
        (root / "detail" / "impl.h").write_text("int x;")
        assert profiles.header_evidence_missing(root) is None

    def test_an_absent_path_and_an_empty_root_are_distinguished(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        assert profiles.header_evidence_missing(tmp_path / "nope") == "absent"
        assert "no header file" in profiles.header_evidence_missing(empty)


class TestMeasurementCoversTheMeasurableSet:
    """A subset result may not be promoted as the profile's whole scope.

    ``promote_to_measured`` first checked only that a result named no *unknown*
    library, so a five-library oneDAL profile could be promoted by a result
    naming one of them -- and the promoted status then republished that subset
    as the profile's measurable set, so "we measured oneDAL" stood for a fifth
    of it with nothing anywhere recording the other four (Codex review).
    """

    def _ready(self, monkeypatch, tmp_path, profile):
        monkeypatch.setattr(profiles.shutil, "which", lambda tool: f"/usr/bin/{tool}")
        monkeypatch.setattr(profiles, "toolchain_identity", lambda p: {})
        materialize_operands(profile, tmp_path)
        status = profiles.resolve_status(
            profile, prepared_root=tmp_path, requested=True
        )
        assert status.status == "READY", status.reason
        return status

    @pytest.mark.parametrize("profile_id", sorted(profiles.PROFILES))
    def test_every_proper_subset_is_refused(self, monkeypatch, tmp_path, profile_id):
        """Exhaustive over every proper subset of each profile's libraries.

        Not "oneDAL minus four": every way a result can cover less than the
        measurable set, for every profile, including the one-library profiles
        where the only proper subset is the empty one.
        """
        import itertools

        profile = profiles.PROFILES[profile_id]
        ready = self._ready(monkeypatch, tmp_path, profile)
        names = tuple(ready.measurable_libraries)
        assert names, profile_id
        subsets = [
            combo
            for size in range(len(names))
            for combo in itertools.combinations(names, size)
        ]
        assert subsets, profile_id
        for subset in subsets:
            with pytest.raises(ValueError):
                profiles.promote_to_measured(
                    ready,
                    profiles.MeasurementResult(
                        profile.id,
                        subset,
                        5.0,
                        output_paths=(measurement_output(tmp_path),),
                    ),
                )
        # Vacuity guard: the complete set really does promote, so the sweep
        # above is not passing because promotion is broken outright.
        promoted = profiles.promote_to_measured(
            ready,
            profiles.MeasurementResult(
                profile.id, names, 5.0, output_paths=(measurement_output(tmp_path),)
            ),
        )
        assert promoted.status == "MEASURED"

    def test_a_stated_omission_is_accepted_and_stays_in_the_receipt(
        self, monkeypatch, tmp_path
    ):
        # Omission is allowed -- it just has to be stated, with a reason, and
        # remain visible. Hiding it is the defect; declining to run a library
        # is not.
        ready = self._ready(monkeypatch, tmp_path, profiles.ONEDAL)
        promoted = profiles.promote_to_measured(
            ready,
            profiles.MeasurementResult(
                "onedal",
                ("onedal_core", "onedal", "onedal_parameters"),
                12.0,
                output_paths=(measurement_output(tmp_path),),
                omitted_libraries={
                    "onedal_dpc": "DPC++ build ran out of disk",
                    "onedal_parameters_dpc": "DPC++ build ran out of disk",
                },
            ),
        )
        assert promoted.status == "MEASURED"
        assert set(promoted.measurement["omitted_libraries"]) == {
            "onedal_dpc",
            "onedal_parameters_dpc",
        }

    def test_an_unexplained_omission_is_refused(self, monkeypatch, tmp_path):
        ready = self._ready(monkeypatch, tmp_path, profiles.ONEDAL)
        with pytest.raises(ValueError, match="state no reason"):
            profiles.promote_to_measured(
                ready,
                profiles.MeasurementResult(
                    "onedal",
                    ("onedal_core", "onedal", "onedal_parameters"),
                    12.0,
                    omitted_libraries={"onedal_dpc": "", "onedal_parameters_dpc": " "},
                ),
            )

    def test_a_library_cannot_be_both_measured_and_omitted(self, monkeypatch, tmp_path):
        ready = self._ready(monkeypatch, tmp_path, profiles.SVS)
        with pytest.raises(ValueError, match="both"):
            profiles.promote_to_measured(
                ready,
                profiles.MeasurementResult(
                    "svs",
                    ("svs_runtime",),
                    3.0,
                    omitted_libraries={"svs_runtime": "also skipped"},
                ),
            )

    def test_an_unknown_omission_is_refused(self, monkeypatch, tmp_path):
        ready = self._ready(monkeypatch, tmp_path, profiles.SVS)
        with pytest.raises(ValueError, match="unmeasurable"):
            profiles.promote_to_measured(
                ready,
                profiles.MeasurementResult(
                    "svs",
                    ("svs_runtime",),
                    3.0,
                    omitted_libraries={"not_a_library": "whatever"},
                ),
            )


class TestIncludeRootsAreRequiredInputs:
    """A declared include root is an operand, not decoration.

    `LibraryTarget.include_roots` names the directories the declared headers
    need in order to parse. A prepared PVXS tree holding its binaries and its
    public headers but missing `../epics-base/include` reported READY while
    being unparseable (Codex review) -- readiness a first parse would
    immediately refute, which is the same "declared but unchecked input" family
    as the empty header root.
    """

    @pytest.fixture(autouse=True)
    def _tools_present(self, monkeypatch):
        monkeypatch.setattr(profiles.shutil, "which", lambda tool: f"/usr/bin/{tool}")
        monkeypatch.setattr(profiles, "toolchain_identity", lambda p: {})

    @pytest.mark.parametrize("profile_id", sorted(profiles.PROFILES))
    def test_every_declared_include_root_is_checked(self, tmp_path, profile_id):
        """Exhaustive over each profile's own include roots, one at a time.

        Removing each in turn from an otherwise complete tree: a resolver that
        consulted only *some* of them would pass for the ones it skipped, which
        is how `../epics-base/include` went unchecked while `src` looked
        covered.
        """
        profile = profiles.PROFILES[profile_id]
        complete = tmp_path / "complete"
        materialize_operands(profile, complete)
        assert (
            profiles.resolve_status(
                profile, prepared_root=complete, requested=True
            ).status
            == "READY"
        ), "vacuity guard: the complete tree must be ready to begin with"
        roots = sorted(
            {root for lib in profile.l2_libraries for root in lib.include_roots}
        )
        assert roots, profile_id
        for index, include_root in enumerate(roots):
            tree = tmp_path / f"minus_root_{index}"
            shutil.copytree(complete, tree, symlinks=True)
            victim = profile.side_root(tree, "old") / include_root
            shutil.rmtree(victim.resolve())
            status = profiles.resolve_status(
                profile, prepared_root=tree, requested=True
            )
            assert status.status == "BLOCKED", (include_root, status.reason)
            assert any(
                "include root" in entry or "public header" in entry
                for entry in status.missing_inputs
            ), (include_root, status.missing_inputs)

    def test_a_relative_include_root_resolves_against_its_own_side(self, tmp_path):
        # PVXS's `../epics-base/include` deliberately escapes the side tree --
        # that is where the generated prepare script puts EPICS base. Checking
        # it against the wrong base would either always pass or always fail.
        materialize_operands(profiles.PVXS, tmp_path)
        assert (tmp_path / "epics-base" / "include").is_dir()
        assert (
            profiles.resolve_status(
                profiles.PVXS, prepared_root=tmp_path, requested=True
            ).status
            == "READY"
        )


class TestScenariosMustBeAnswerableByTheirOperands:
    """A profile may not declare a scenario its own two sides cannot answer.

    `svs_pr_base` declared `rebuild_equivalence` while building a merge base
    against a PR head. A difference it found could be the source change or the
    build, and that scenario's expectation ("investigate against the recorded
    compiler, flags, dependencies") presumes the build is the only variable --
    so the label made two causes indistinguishable (Codex review).
    """

    def test_the_pr_base_profile_declares_only_its_temporal_scenario(self):
        assert profiles.SVS_PR_BASE.scenarios == ("temporal_pr_base",)

    @pytest.mark.parametrize("profile_id", sorted(profiles.PROFILES))
    def test_no_shipped_profile_mixes_the_two_families(self, profile_id):
        profile = profiles.PROFILES[profile_id]
        temporal = [s for s in profile.scenarios if s in profiles.TEMPORAL_SCENARIOS]
        same = [s for s in profile.scenarios if s in profiles.SAME_REVISION_SCENARIOS]
        assert not (temporal and same), profile.scenarios

    @pytest.mark.parametrize("scenario", sorted(profiles.SAME_REVISION_SCENARIOS))
    def test_a_same_revision_scenario_on_differing_sides_is_rejected(self, scenario):
        # Swept over the whole family rather than the one scenario that had the
        # defect: each of these compares two acquisitions of one revision, and
        # none of them can be answered by two different ones.
        bad = dataclasses.replace(profiles.SVS_PR_BASE, scenarios=(scenario,))
        problems = profiles.validate_profile(bad)
        assert any("one revision" in problem for problem in problems), problems

    @pytest.mark.parametrize("scenario", sorted(profiles.SAME_REVISION_SCENARIOS))
    def test_a_same_revision_scenario_on_one_revision_is_accepted(self, scenario):
        # Vacuity guard: the rule rejects the mismatch, not the whole family.
        good = dataclasses.replace(
            profiles.SVS_PR_BASE,
            old_revision="7058e9605a54180aa64fbb7a81a82aa47f07eeff",
            new_revision="7058e9605a54180aa64fbb7a81a82aa47f07eeff",
            scenarios=(scenario,),
        )
        assert not any(
            "one revision" in problem for problem in profiles.validate_profile(good)
        )

    @pytest.mark.parametrize("scenario", sorted(profiles.TEMPORAL_SCENARIOS))
    def test_a_temporal_scenario_still_needs_two_revisions(self, scenario):
        bad = dataclasses.replace(
            profiles.SVS_PR_BASE,
            old_revision="aaa",
            new_revision="aaa",
            scenarios=(scenario,),
        )
        assert any(
            "two different revisions" in problem
            for problem in profiles.validate_profile(bad)
        )
