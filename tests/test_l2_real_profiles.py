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

"""Tests for the pinned real-integration L2 profiles.

These profiles can never run in this test lane -- oneDAL alone is ~120
build-minutes and 25GB -- so what is testable here is the part that keeps their
*reporting* honest: that an unavailable profile is BLOCKED with a concrete
reason rather than silently substituted, that a library with no public API is a
recorded non-case rather than an inflated one, and that a "historical" baseline
really comes from its own revision.

That list is not incidental. Each item is a way a real-integration number can be
published while being about something other than what its name says, which is
worth more guarding than the arithmetic is.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

_spec = importlib.util.spec_from_file_location(
    "l2_real_profiles", _SCRIPTS / "l2_real_profiles.py"
)
assert _spec and _spec.loader
profiles = importlib.util.module_from_spec(_spec)
sys.modules["l2_real_profiles"] = profiles
_spec.loader.exec_module(profiles)


class TestProfileDefinitions:
    def test_all_three_integrations_are_defined(self):
        assert set(profiles.PROFILES) == {"onedal", "svs", "pvxs"}

    @pytest.mark.parametrize("profile_id", ["onedal", "svs", "pvxs"])
    def test_every_profile_is_structurally_valid(self, profile_id):
        assert profiles.validate_profile(profiles.PROFILES[profile_id]) == []

    @pytest.mark.parametrize("profile_id", ["onedal", "svs", "pvxs"])
    def test_every_profile_names_its_upstream_reference(self, profile_id):
        # A measurement must be traceable to the change it was taken for.
        assert profiles.PROFILES[profile_id].reference.startswith("https://")

    def test_onedal_has_five_l2_libraries_not_six(self):
        # libonedal_thread has no public API of its own. Counting it would
        # manufacture a sixth L2 case rather than measure one.
        assert len(profiles.ONEDAL.l2_libraries) == 5

    def test_the_thread_library_is_a_recorded_non_case_with_a_reason(self):
        thread = next(
            lib for lib in profiles.ONEDAL.libraries if lib.name == "onedal_thread"
        )
        assert thread.in_l2_scope is False
        assert thread.out_of_scope_reason
        assert thread.public_headers == ()

    def test_onedal_carries_two_distinct_header_contexts(self):
        # The shared-vs-differing-context axis the profile exists to cover: the
        # same headers under a host and a DPC++ compile context are two
        # different L2 inputs, not a duplicate.
        assert profiles.ONEDAL.header_contexts == 2

    def test_pvxs_carries_two_libraries_with_different_header_sets(self):
        core, ioc = profiles.PVXS.libraries
        assert core.public_headers != ioc.public_headers
        assert core.context != ioc.context

    def test_svs_scopes_to_published_runtime_headers_not_the_whole_tree(self):
        # Scoping L2 at the whole include/ tree would measure a largely
        # header-only source tree instead of the runtime library's contract.
        headers = profiles.SVS.l2_libraries[0].public_headers
        assert headers and all(h.endswith(".h") for h in headers)

    def test_svs_keeps_temporal_and_equivalence_as_separate_scenarios(self):
        # One number cannot mean both "did this change break the ABI" and "is
        # the analysis stable across two builds of one revision".
        assert set(profiles.SVS.scenarios) == {"temporal", "equivalence"}


class TestValidationCatchesDishonestDefinitions:
    """Each case is a definition that would publish a misleading number."""

    def _profile(self, **overrides) -> profiles.RealProfile:
        base = {
            "id": "t",
            "project": "p",
            "reference": "https://example.invalid/pr/1",
            "repository": "https://example.invalid/p.git",
            "old_revision": "aaa",
            "new_revision": "bbb",
            "libraries": (profiles.LibraryTarget("l", "lib/l.so", ("inc/l.h",)),),
            "required_tools": ("git",),
        }
        base.update(overrides)
        return profiles.RealProfile(**base)

    def test_a_valid_minimal_profile_passes(self):
        assert profiles.validate_profile(self._profile()) == []

    def test_an_l2_library_with_no_headers_is_rejected(self):
        # That is a binary-only case mislabelled as an L2 one -- it would be
        # faster and would appear as L2 coverage.
        bad = self._profile(
            libraries=(profiles.LibraryTarget("l", "lib/l.so", public_headers=()),)
        )
        problems = profiles.validate_profile(bad)
        assert any("binary-only case mislabelled" in p for p in problems)

    def test_an_unexplained_exclusion_is_rejected(self):
        bad = self._profile(
            libraries=(
                profiles.LibraryTarget("l", "lib/l.so", ("inc/l.h",)),
                profiles.LibraryTarget("x", "lib/x.so", in_l2_scope=False),
            )
        )
        problems = profiles.validate_profile(bad)
        assert any("without a stated reason" in p for p in problems)

    def test_an_excluded_library_may_not_also_claim_headers(self):
        bad = self._profile(
            libraries=(
                profiles.LibraryTarget("l", "lib/l.so", ("inc/l.h",)),
                profiles.LibraryTarget(
                    "x",
                    "lib/x.so",
                    ("inc/x.h",),
                    in_l2_scope=False,
                    out_of_scope_reason="r",
                ),
            )
        )
        assert profiles.validate_profile(bad)

    def test_a_temporal_scenario_with_one_revision_is_rejected(self):
        bad = self._profile(old_revision="same", new_revision="same")
        problems = profiles.validate_profile(bad)
        assert any("two different revisions" in p for p in problems)

    def test_a_profile_with_no_l2_library_is_rejected(self):
        bad = self._profile(
            libraries=(
                profiles.LibraryTarget(
                    "x", "lib/x.so", in_l2_scope=False, out_of_scope_reason="r"
                ),
            )
        )
        assert any(
            "no library is in L2 scope" in p for p in profiles.validate_profile(bad)
        )

    def test_a_profile_stating_no_required_tools_is_rejected(self):
        # Without required tools, availability cannot be checked, so the profile
        # could never report BLOCKED -- it would silently read as runnable.
        bad = self._profile(required_tools=())
        assert any(
            "could never report BLOCKED" in p for p in profiles.validate_profile(bad)
        )


class TestHistoricalBaselineIsReallyHistorical:
    def test_two_distinct_header_roots_are_accepted(self, tmp_path):
        old = tmp_path / "old"
        new = tmp_path / "new"
        old.mkdir()
        new.mkdir()
        assert (
            profiles.validate_side_headers(old_header_root=old, new_header_root=new)
            == []
        )

    def test_one_shared_header_root_is_rejected(self, tmp_path):
        # The easy accidental substitution: check out the new revision, build
        # both binaries, point both --header sets at the working tree. It runs,
        # it is faster, and it is not a temporal L2 comparison.
        shared = tmp_path / "inc"
        shared.mkdir()
        problems = profiles.validate_side_headers(
            old_header_root=shared, new_header_root=shared
        )
        assert any("not a temporal L2 comparison" in p for p in problems)

    def test_a_symlink_to_the_same_tree_is_also_rejected(self, tmp_path):
        # Resolved, not compared as strings: two different paths naming one tree
        # is the same substitution wearing a different spelling.
        real = tmp_path / "inc"
        real.mkdir()
        link = tmp_path / "alias"
        link.symlink_to(real)
        assert profiles.validate_side_headers(
            old_header_root=real, new_header_root=link
        )


class TestStatusReporting:
    def test_a_missing_tool_blocks_with_a_concrete_reason(self, monkeypatch):
        monkeypatch.setattr(profiles.shutil, "which", lambda tool: None)
        status = profiles.resolve_status(
            profiles.PVXS, prepared_root=None, requested=True
        )
        assert status.status == "BLOCKED"
        assert status.missing_tools
        # "Concrete" means a reader can act on it: which tools, and what the
        # profile would cost if they had them.
        assert "not on PATH" in status.reason
        assert "build-minutes" in status.reason

    def test_an_unprepared_tree_blocks_with_a_different_reason(self, monkeypatch):
        monkeypatch.setattr(profiles.shutil, "which", lambda tool: f"/usr/bin/{tool}")
        monkeypatch.setattr(profiles, "toolchain_identity", lambda p: {})
        status = profiles.resolve_status(
            profiles.PVXS, prepared_root=None, requested=True
        )
        assert status.status == "BLOCKED"
        assert "no prepared build tree" in status.reason

    def test_not_requested_is_distinct_from_blocked(self, monkeypatch):
        # Collapsing the two would let a lane that skipped a profile for
        # convenience look like one that could not run it.
        status = profiles.resolve_status(
            profiles.SVS, prepared_root=None, requested=False
        )
        assert status.status == "NOT_RUN"
        assert "not selected" in status.reason

    def test_a_prepared_tree_with_tools_is_measurable(self, monkeypatch, tmp_path):
        monkeypatch.setattr(profiles.shutil, "which", lambda tool: f"/usr/bin/{tool}")
        monkeypatch.setattr(profiles, "toolchain_identity", lambda p: {"git": "git 2"})
        status = profiles.resolve_status(
            profiles.SVS, prepared_root=tmp_path, requested=True
        )
        assert status.status == "MEASURED"

    def test_every_status_is_from_the_declared_vocabulary(self, monkeypatch, tmp_path):
        monkeypatch.setattr(profiles.shutil, "which", lambda tool: None)
        for profile in profiles.PROFILES.values():
            for requested in (True, False):
                status = profiles.resolve_status(
                    profile, prepared_root=tmp_path, requested=requested
                )
                assert status.status in profiles.STATUSES

    def test_a_negative_status_always_carries_a_reason(self, monkeypatch, tmp_path):
        # A profile reported as merely "skipped" tells a reader nothing about
        # whether the gap is environmental or a decision.
        monkeypatch.setattr(profiles.shutil, "which", lambda tool: None)
        for profile in profiles.PROFILES.values():
            status = profiles.resolve_status(
                profile, prepared_root=tmp_path, requested=True
            )
            assert status.status != "MEASURED"
            assert status.reason


class TestDigestTree:
    def test_identical_trees_digest_identically(self, tmp_path):
        for name in ("a", "b"):
            root = tmp_path / name
            (root / "pvxs").mkdir(parents=True)
            (root / "pvxs" / "data.h").write_text("struct X {};")
        assert profiles.digest_tree(tmp_path / "a") == profiles.digest_tree(
            tmp_path / "b"
        )

    def test_a_changed_header_changes_the_digest(self, tmp_path):
        root = tmp_path / "a"
        root.mkdir()
        header = root / "data.h"
        header.write_text("struct X {};")
        before = profiles.digest_tree(root)
        header.write_text("struct X { int y; };")
        assert profiles.digest_tree(root) != before

    def test_a_renamed_header_changes_the_digest(self, tmp_path):
        # A stale or partially-applied checkout is exactly as dangerous as
        # changed content: both publish a number against the wrong revision.
        root = tmp_path / "a"
        root.mkdir()
        (root / "one.h").write_text("x")
        before = profiles.digest_tree(root)
        (root / "one.h").rename(root / "two.h")
        assert profiles.digest_tree(root) != before

    def test_the_digest_is_labelled_with_its_algorithm(self, tmp_path):
        root = tmp_path / "a"
        root.mkdir()
        assert profiles.digest_tree(root).startswith("sha256:")


class TestRevisionsArePinned:
    """A placeholder revision is unrunnable, and must not read as valid.

    Rendered into ``git checkout <pinned-base-sha>``, the shell reads ``<`` as
    input redirection and fails before git runs -- while ``validate_profile()``
    previously called the profile structurally valid. Both halves are now closed:
    validation flags it, and the status is BLOCKED naming the real blocker.
    """

    @pytest.mark.parametrize(
        "revision",
        [
            "<pinned-base-sha>",
            "<pinned-head-sha>",
            "pinned-base-sha",
            "TODO",
            "FIXME-later",
            "",
            "   ",
        ],
    )
    def test_a_placeholder_is_recognised(self, revision):
        assert profiles.is_placeholder_revision(revision)

    @pytest.mark.parametrize(
        "revision",
        [
            "9371e12391794a66520fc5c4aba87c26a6c6b628",
            "b8a557d",
            "a689f87d2f37873078598dfdbf069ee45de2c76e",
        ],
    )
    def test_a_real_revision_is_not_a_placeholder(self, revision):
        assert not profiles.is_placeholder_revision(revision)

    @pytest.mark.parametrize("profile_id", ["onedal", "svs", "pvxs"])
    def test_every_shipped_profile_is_pinned_to_real_revisions(self, profile_id):
        profile = profiles.PROFILES[profile_id]
        assert not profiles.is_placeholder_revision(profile.old_revision)
        assert not profiles.is_placeholder_revision(profile.new_revision)

    def test_a_placeholder_profile_fails_validation(self):
        bad = profiles.RealProfile(
            id="t",
            project="p",
            reference="https://example.invalid/pr/1",
            repository="https://example.invalid/p.git",
            old_revision="<pinned-base-sha>",
            new_revision="bbb",
            libraries=(profiles.LibraryTarget("l", "lib/l.so", ("inc/l.h",)),),
            required_tools=("git",),
        )
        assert any("placeholder" in p for p in profiles.validate_profile(bad))

    def test_a_placeholder_profile_blocks_naming_the_real_blocker(
        self, monkeypatch, tmp_path
    ):
        # Checked before the tool probe: reporting "missing icpx" for a profile
        # that has no revisions to build would name the wrong blocker.
        monkeypatch.setattr(profiles.shutil, "which", lambda tool: None)
        bad = profiles.RealProfile(
            id="t",
            project="p",
            reference="https://example.invalid/pr/1",
            repository="https://example.invalid/p.git",
            old_revision="<pinned-base-sha>",
            new_revision="<pinned-head-sha>",
            libraries=(profiles.LibraryTarget("l", "lib/l.so", ("inc/l.h",)),),
            required_tools=("git", "icpx"),
        )
        status = profiles.resolve_status(bad, prepared_root=tmp_path, requested=True)
        assert status.status == "BLOCKED"
        assert "not pinned" in status.reason
        assert "icpx" not in status.reason

    def test_rendering_a_placeholder_profile_raises_instead_of_emitting_bad_shell(self):
        bad = profiles.RealProfile(
            id="t",
            project="p",
            reference="https://example.invalid/pr/1",
            repository="https://example.invalid/p.git",
            old_revision="<pinned-base-sha>",
            new_revision="bbb",
            libraries=(profiles.LibraryTarget("l", "lib/l.so", ("inc/l.h",)),),
            required_tools=("git",),
        )
        with pytest.raises(ValueError):
            profiles.prepare_script(bad)


class TestPrepareScriptBuildsBothSides:
    """A temporal profile needs two artifacts, so its script must build two.

    Every profile's preparation previously checked out only ``old_revision`` and
    nothing referenced ``new_revision`` at all, so no generated script could
    produce the old-vs-new pair it advertised.
    """

    @pytest.mark.parametrize("profile_id", ["onedal", "svs", "pvxs"])
    def test_both_revisions_appear_in_the_script(self, profile_id):
        profile = profiles.PROFILES[profile_id]
        script = profiles.prepare_script(profile)
        assert profile.old_revision in script
        assert profile.new_revision in script

    @pytest.mark.parametrize("profile_id", ["onedal", "svs", "pvxs"])
    def test_each_side_gets_its_own_tree(self, profile_id):
        script = profiles.prepare_script(profiles.PROFILES[profile_id])
        assert f"{profile_id}_old" in script
        assert f"{profile_id}_new" in script

    @pytest.mark.parametrize("profile_id", ["onedal", "svs", "pvxs"])
    def test_every_profile_declares_per_side_commands(self, profile_id):
        # A profile with an empty list here can only ever build one side, which is
        # the defect this guards against reappearing.
        assert profiles.PROFILES[profile_id].per_side_commands

    @pytest.mark.parametrize("profile_id", ["onedal", "svs", "pvxs"])
    def test_the_build_step_runs_once_per_side(self, profile_id):
        profile = profiles.PROFILES[profile_id]
        script = profiles.prepare_script(profile)
        # The worktree creation is per side by construction, so count it: one per
        # side and no more.
        assert script.count("worktree add") == 2


class TestPrepareScriptIsolatesEachCommand:
    """Each command must start from the same root.

    Appending ``cd svs && git checkout`` then ``cd svs && cmake`` into one shell
    leaves the process inside ``svs``, so the second looks for ``svs/svs``. Every
    profile used that repeated-``cd`` shape, so no script could complete even its
    old-side build.
    """

    @pytest.mark.parametrize("profile_id", ["onedal", "svs", "pvxs"])
    def test_the_script_captures_a_root(self, profile_id):
        assert 'ROOT="$(pwd)"' in profiles.prepare_script(profiles.PROFILES[profile_id])

    @pytest.mark.parametrize("profile_id", ["onedal", "svs", "pvxs"])
    def test_every_command_line_is_a_rooted_subshell(self, profile_id):
        script = profiles.prepare_script(profiles.PROFILES[profile_id])
        commands = [
            line
            for line in script.splitlines()
            if line and not line.startswith(("#", "set -", "ROOT=", "#!"))
        ]
        assert commands, "a profile with no commands would pass this vacuously"
        for line in commands:
            assert line.startswith('( cd "$ROOT" && '), line
            assert line.endswith(" )"), line

    def test_a_cd_inside_one_command_cannot_leak_into_the_next(self):
        # The property, stated directly: two consecutive `cd X` commands both
        # resolve X against the root, not against each other.
        script = profiles.prepare_script(profiles.SVS)
        cd_lines = [ln for ln in script.splitlines() if "svs_old" in ln]
        assert len(cd_lines) >= 2
        for line in cd_lines:
            assert line.startswith('( cd "$ROOT" && ')

    @pytest.mark.parametrize("profile_id", ["onedal", "svs", "pvxs"])
    def test_the_rendered_script_is_valid_shell(self, profile_id):
        # Parse-checked rather than eyeballed: a script nobody can run is not
        # reproduction instructions. `bash -n` needs no network and builds nothing.
        script = profiles.prepare_script(profiles.PROFILES[profile_id])
        proc = subprocess.run(
            ["bash", "-n"], input=script, capture_output=True, text=True, check=False
        )
        assert proc.returncode == 0, proc.stderr


class TestPrepareScript:
    @pytest.mark.parametrize("profile_id", ["onedal", "svs", "pvxs"])
    def test_it_is_a_runnable_reproducible_script(self, profile_id):
        script = profiles.prepare_script(profiles.PROFILES[profile_id])
        assert script.startswith("#!/usr/bin/env bash")
        assert "set -euo pipefail" in script
        # No unsubstituted placeholders: a script a reader cannot run verbatim
        # is documentation pretending to be reproduction instructions.
        assert "{repository}" not in script
        assert "{old_revision}" not in script

    @pytest.mark.parametrize("profile_id", ["onedal", "svs", "pvxs"])
    def test_it_states_that_preparation_is_excluded_from_measurement(self, profile_id):
        script = profiles.prepare_script(profiles.PROFILES[profile_id])
        assert "SETUP" in script
        assert "excluded from every measured window" in script

    @pytest.mark.parametrize("profile_id", ["onedal", "svs", "pvxs"])
    def test_it_states_the_resource_cost_up_front(self, profile_id):
        # So a lane can decline before spending an hour finding out.
        script = profiles.prepare_script(profiles.PROFILES[profile_id])
        assert "build-minutes" in script

    def test_pvxs_preparation_builds_epics_base_first(self):
        script = profiles.prepare_script(profiles.PVXS)
        # EPICS base must be built before either pvxs side, since pvxs's own
        # makefiles include base's rules. Anchored on the per-side build rather
        # than a `cd pvxs && make` spelling, which the subshell rewrite changed;
        # the side root is $ROOT-anchored since the relative-worktree fix.
        base = script.index("epics-base && make")
        assert base < script.index('make -C "$ROOT"/pvxs_old')
        assert base < script.index('make -C "$ROOT"/pvxs_new')

    def test_no_profile_prepares_with_a_depth_source_run(self):
        # A --depth source run is an L4/L5 measurement; its numbers do not
        # belong in an L2 profile, and the PVXS project's own script does
        # exactly that.
        for profile in profiles.PROFILES.values():
            script = profiles.prepare_script(profile)
            assert "--depth source" not in script


class TestPrepareScriptUsesAbsoluteWorktreePaths:
    """``git worktree add`` with a relative path does not land where you think.

    ``git -C <repo> worktree add <path>`` resolves a *relative* path against the
    repository directory, not the caller's cwd. Reproduced with the installed
    git: ``git -C repo.git worktree add --detach mytree HEAD`` creates
    ``repo.git/mytree``, so every following command looking for ``$ROOT/mytree``
    fails. Asserted as a property over every rendered command rather than against
    the one profile that was read first.
    """

    @pytest.mark.parametrize("profile_id", ["onedal", "svs", "pvxs"])
    def test_every_worktree_operand_is_root_anchored(self, profile_id):
        script = profiles.prepare_script(profiles.PROFILES[profile_id])
        worktree_lines = [ln for ln in script.splitlines() if "worktree add" in ln]
        assert worktree_lines, "every profile prepares a per-side worktree"
        for line in worktree_lines:
            # The operand after the revision/flags must begin at $ROOT.
            assert '"$ROOT"/' in line.split("worktree add", 1)[1], line

    @pytest.mark.parametrize("profile_id", ["onedal", "svs", "pvxs"])
    def test_no_rendered_path_operand_is_bare_relative(self, profile_id):
        # The general invariant behind the one bug: the side root never appears
        # in the script as a bare relative name, in any command, not only the
        # worktree one (a later consumer would resolve it against a different cwd).
        profile = profiles.PROFILES[profile_id]
        script = profiles.prepare_script(profile)
        for side in ("old", "new"):
            bare = f"{profile.id}_{side}"
            for line in script.splitlines():
                if bare not in line:
                    continue
                assert f'"$ROOT"/{bare}' in line, line

    def test_a_relative_worktree_path_really_lands_in_the_repo(self, tmp_path):
        # The oracle is real git, not the formula this module uses: without it
        # "relative resolves against the repo" is an assumption, and the fix
        # would be unverified.
        import shutil as _shutil

        git = _shutil.which("git")
        if git is None:  # pragma: no cover - git is present in every dev env
            pytest.skip("git unavailable")
        src = tmp_path / "src"
        src.mkdir()
        (src / "f.txt").write_text("x", encoding="utf-8")
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@e",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@e",
        }
        subprocess.run([git, "init", "-q"], cwd=src, check=True, env=env)
        subprocess.run([git, "add", "."], cwd=src, check=True, env=env)
        subprocess.run([git, "commit", "-qm", "c"], cwd=src, check=True, env=env)
        caller = tmp_path / "caller"
        caller.mkdir()
        proc = subprocess.run(
            [git, "-C", str(src), "worktree", "add", "--detach", "mytree", "HEAD"],
            cwd=caller,
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr
        assert (src / "mytree").is_dir(), "git resolved it against the repo dir"
        assert not (caller / "mytree").exists(), "and not against the caller's cwd"


class TestPartialCoverageSurvivesAMissingContextTool:
    """A tool only one header/compile context needs must not block the others.

    oneDAL's two DPC++ libraries need `icpx`; its three host libraries do not.
    Listing `icpx` among the profile-wide `required_tools` made a host-only
    machine report the *whole* profile `BLOCKED` — which contradicted the
    profile's own documented behaviour (measure the host libraries, report only
    the DPC++ ones blocked) and hid usable coverage from the availability
    artifact. The status vocabulary exists to tell "this host cannot" from
    "nobody asked"; collapsing "this host can do part of it" into the first is
    the same class of dishonesty as publishing a synthetic substitute.
    """

    def test_icpx_is_not_a_profile_wide_requirement(self):
        assert "icpx" not in profiles.ONEDAL.required_tools
        assert profiles.ONEDAL.context_tools.get("dpcpp") == ("icpx",)

    def test_a_missing_context_tool_blocks_only_its_own_libraries(self, monkeypatch):
        monkeypatch.setattr(
            profiles.shutil,
            "which",
            lambda tool: None if tool == "icpx" else "/usr/bin",
        )
        blocked = profiles.blocked_contexts(profiles.ONEDAL)
        assert blocked == {"dpcpp": ["icpx"]}
        measurable = [
            lib.name for lib in profiles.measurable_libraries(profiles.ONEDAL)
        ]
        assert measurable == ["onedal_core", "onedal", "onedal_parameters"]

    def test_the_status_is_partial_not_blocked(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            profiles.shutil,
            "which",
            lambda tool: None if tool == "icpx" else "/usr/bin",
        )
        status = profiles.resolve_status(
            profiles.ONEDAL, prepared_root=tmp_path, requested=True
        )
        assert status.status == "PARTIAL", status.reason
        # The split is in the receipt, not only in prose: a reader must be able to
        # see which libraries the number covers.
        assert len(status.measurable_libraries) == 3
        assert set(status.blocked_libraries) == {
            "onedal_dpc",
            "onedal_parameters_dpc",
        }
        assert status.missing_tools == ["icpx"]

    def test_every_context_missing_a_tool_is_blocked_not_partial(
        self, monkeypatch, tmp_path
    ):
        # "Partially measurable" must not absorb the case where nothing is.
        # `RealProfile` is frozen, so this builds a real variant rather than
        # mutating the shipped one -- which also keeps the shipped profile honest
        # for every other test in this module.
        variant = dataclasses.replace(
            profiles.ONEDAL,
            context_tools={"host": ("nonexistent-host-cc",), "dpcpp": ("icpx",)},
        )
        monkeypatch.setattr(
            profiles.shutil,
            "which",
            lambda tool: (
                None if tool in ("icpx", "nonexistent-host-cc") else "/usr/bin"
            ),
        )
        status = profiles.resolve_status(
            variant, prepared_root=tmp_path, requested=True
        )
        assert status.status == "BLOCKED", status.reason
        assert "no part of this profile can be measured" in status.reason

    def test_a_complete_toolchain_still_reports_measured(self, monkeypatch, tmp_path):
        # Vacuity guard: PARTIAL must not be the answer whenever context_tools is
        # non-empty.
        monkeypatch.setattr(profiles.shutil, "which", lambda tool: "/usr/bin/" + tool)
        status = profiles.resolve_status(
            profiles.ONEDAL, prepared_root=tmp_path, requested=True
        )
        assert status.status == "MEASURED", status.reason
        assert status.measurable_libraries == []

    def test_a_missing_profile_wide_tool_still_blocks_everything(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(
            profiles.shutil, "which", lambda tool: None if tool == "git" else "/usr/bin"
        )
        status = profiles.resolve_status(
            profiles.ONEDAL, prepared_root=tmp_path, requested=True
        )
        assert status.status == "BLOCKED"
        assert status.missing_tools == ["git"]

    def test_the_prepared_tree_blocker_is_named_before_the_context_split(
        self, monkeypatch
    ):
        # Same rule the placeholder check follows before the tool probe: name the
        # blocker the caller actually hits first. A PARTIAL result for a host with
        # no prepared tree describes a coverage split nothing could act on.
        monkeypatch.setattr(
            profiles.shutil,
            "which",
            lambda tool: None if tool == "icpx" else "/usr/bin",
        )
        status = profiles.resolve_status(
            profiles.ONEDAL, prepared_root=None, requested=True
        )
        assert status.status == "BLOCKED"
        assert "no prepared build tree" in status.reason

    @pytest.mark.parametrize("profile_id", ["onedal", "svs", "pvxs"])
    def test_every_context_tool_belongs_to_a_real_context(self, profile_id):
        # A context_tools key naming no library's context gates nothing, which
        # would be an inert declaration reading as a real restriction.
        profile = profiles.PROFILES[profile_id]
        contexts = {lib.context for lib in profile.l2_libraries}
        assert set(profile.context_tools) <= contexts, profile.context_tools


class TestTheStatusVocabularyCoversWhatResolveStatusReturns:
    """`PARTIAL` was missing from `STATUSES` while `resolve_status` returned it.

    A caller validating or enumerating results through the declared constant
    would have rejected or dropped a valid partial result. The pre-existing
    vocabulary test missed it because its all-tools-missing setup reaches
    `BLOCKED` before the per-context branch runs at all — so the test below
    drives the `PARTIAL` branch specifically, and the one after it derives the
    expectation from the code rather than restating a list.
    """

    def test_partial_is_declared(self):
        assert "PARTIAL" in profiles.STATUSES

    def test_the_partial_branch_really_produces_a_declared_status(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(
            profiles.shutil,
            "which",
            lambda tool: None if tool == "icpx" else "/usr/bin",
        )
        status = profiles.resolve_status(
            profiles.ONEDAL, prepared_root=tmp_path, requested=True
        )
        assert status.status == "PARTIAL"
        assert status.status in profiles.STATUSES

    def test_every_status_resolve_status_can_return_is_declared(self):
        # Derived from the source rather than hand-listed: a fifth status added
        # without extending the vocabulary is the defect this class exists for,
        # and a hand-listed expectation would not notice it.
        source = (_SCRIPTS / "l2_real_profiles.py").read_text(encoding="utf-8")
        returned = set(re.findall(r'ProfileStatus\(\s*[^,]+,\s*"([A-Z_]+)"', source))
        returned |= set(re.findall(r'"(MEASURED|PARTIAL|BLOCKED|NOT_RUN)"', source))
        undeclared = sorted(returned - set(profiles.STATUSES))
        assert undeclared == [], undeclared


class TestContextToolsAreInTheMeasurementIdentity:
    """A context-specific compiler is as identity-bearing as a profile-wide one.

    Recording only `required_tools` omitted `icpx`, the compiler oneDAL's DPC++
    libraries are built with, so two measurements taken with different DPC++
    compilers recorded identical toolchains — defeating the one thing the field
    exists for.
    """

    def test_icpx_is_in_onedals_identity_tools(self):
        assert "icpx" in profiles.identity_tools(profiles.ONEDAL)

    def test_identity_tools_is_the_union_of_both_sources(self):
        for profile in profiles.PROFILES.values():
            tools = set(profiles.identity_tools(profile))
            assert set(profile.required_tools) <= tools, profile.id
            for context_tools in profile.context_tools.values():
                assert set(context_tools) <= tools, profile.id

    def test_identity_tools_deduplicates_and_keeps_order(self):
        variant = dataclasses.replace(
            profiles.SVS, context_tools={"runtime": ("g++", "ninja")}
        )
        tools = profiles.identity_tools(variant)
        assert tools.count("g++") == 1, tools
        assert tools[: len(variant.required_tools)] == variant.required_tools
        assert "ninja" in tools

    def test_the_recorded_toolchain_covers_every_identity_tool(self, monkeypatch):
        monkeypatch.setattr(profiles.shutil, "which", lambda tool: None)
        recorded = profiles.toolchain_identity(profiles.ONEDAL)
        assert set(recorded) == set(profiles.identity_tools(profiles.ONEDAL))
        # Absent tools are recorded as None rather than omitted, so a reader can
        # tell "not installed" from "not part of this profile's identity".
        assert all(value is None for value in recorded.values())

    def test_a_profile_with_no_context_tools_is_unchanged(self):
        assert profiles.identity_tools(profiles.PVXS) == profiles.PVXS.required_tools
