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

"""Structural tests for the ``publish-baseline.yml``/``update-main-
baseline.yml`` reusable workflows (G30 P1.6, ADR-047 §6/§10).

Mirrors ``tests/test_reusable_workflows.py``'s own precedent: these
workflows' step orchestration (self-checkout, nested composite-Action
``uses:``, cache key rotation) needs a real GitHub Actions runner to
exercise end-to-end, so these tests assert structure over the parsed YAML
instead -- plus a pure-Python cross-check that the cache-key template each
file embeds literally matches
:mod:`abicheck.buildsource.baseline_publish`'s own key-format functions, the
same "structural-plus-semantic pairing" ``test_action_check_target.py``'s
``TestReplaySourcesForwardedWithDefault`` established for exactly this kind
of "an embedded expression string must not silently drift from its
pure-Python mirror" risk.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from abicheck.buildsource.baseline_publish import (
    accepted_main_cache_key,
    accepted_main_cache_restore_prefix,
)


def _bash_executable() -> str:
    if os.name != "nt":
        return "bash"
    for candidate in (
        os.environ.get("GIT_BASH_PATH"),
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
    ):
        if candidate and Path(candidate).is_file():
            return candidate
    return "bash"


WORKFLOWS_DIR = Path(__file__).resolve().parents[1] / ".github" / "workflows"
PUBLISH_BASELINE = WORKFLOWS_DIR / "publish-baseline.yml"
UPDATE_MAIN_BASELINE = WORKFLOWS_DIR / "update-main-baseline.yml"
TEST_BASELINE_ROTATION = WORKFLOWS_DIR / "test-baseline-rotation.yml"

#: The full commit SHA every elevated-permission workflow in this repository
#: pins actions/checkout@v6/actions/setup-python@v6/actions/
#: download-artifact@v8 to (publish.yml, security.yml) -- these two new
#: workflows must reuse the SAME commits, not independently re-resolve each
#: tag's current HEAD (see both files' own header comment for why).
_CHECKOUT_V6_SHA = "df4cb1c069e1874edd31b4311f1884172cec0e10"
_SETUP_PYTHON_V6_SHA = "ece7cb06caefa5fff74198d8649806c4678c61a1"
_DOWNLOAD_ARTIFACT_V8_SHA = "3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c"


def _load(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    return job["steps"]


def _step_names(job: dict[str, Any]) -> list[str]:
    return [s.get("name") for s in _steps(job)]


def _all_uses(data: dict[str, Any]) -> list[str]:
    uses: list[str] = []
    for job in data["jobs"].values():
        for step in job.get("steps", []):
            if "uses" in step:
                uses.append(step["uses"])
    return uses


class TestBothFilesParseAsValidWorkflowYaml:
    def test_publish_baseline_parses(self) -> None:
        data = _load(PUBLISH_BASELINE)
        assert set(data["jobs"]) == {"discover", "no-profiles", "publish"}

    def test_update_main_baseline_parses(self) -> None:
        data = _load(UPDATE_MAIN_BASELINE)
        assert set(data["jobs"]) == {"discover", "no-profiles", "refresh"}


class TestNeverPullRequestTriggered:
    """ADR-047 §12: baseline-publishing workflows are push/
    workflow_dispatch-triggered only, never pull_request/pull_request_target
    -- a fork PR must never be able to mint or refresh a baseline. Both
    files are ``workflow_call``-only (the calling repository's own workflow
    decides the real trigger), so the only thing to assert here is that
    neither file itself declares a `pull_request`/`pull_request_target`
    trigger alongside `workflow_call`."""

    def test_publish_baseline_is_workflow_call_only(self) -> None:
        data = _load(PUBLISH_BASELINE)
        triggers = data[True]
        assert set(triggers) == {"workflow_call"}

    def test_update_main_baseline_is_workflow_call_only(self) -> None:
        data = _load(UPDATE_MAIN_BASELINE)
        triggers = data[True]
        assert set(triggers) == {"workflow_call"}


class TestElevatedPermissionActionsArePinnedToASha:
    """AGENTS.md's "Action pinning is deliberately partial" note: a
    workflow that writes releases/caches gets the same SHA-pinning bar as
    security.yml/publish.yml (ADR-047 §12) -- a re-pointed tag here is a
    real supply-chain risk, unlike check-target/resolve-baseline's
    tag-pinning (no elevated permissions)."""

    def test_publish_baseline_every_uses_is_sha_pinned(self) -> None:
        data = _load(PUBLISH_BASELINE)
        for uses in _all_uses(data):
            if uses.startswith("./"):
                continue  # local nested Action -- pinning doesn't apply.
            assert "@" in uses, uses
            ref = uses.rsplit("@", 1)[1]
            assert len(ref) == 40 and all(c in "0123456789abcdef" for c in ref), uses

    def test_update_main_baseline_every_uses_is_sha_pinned(self) -> None:
        data = _load(UPDATE_MAIN_BASELINE)
        for uses in _all_uses(data):
            if uses.startswith("./"):
                continue
            assert "@" in uses, uses
            ref = uses.rsplit("@", 1)[1]
            assert len(ref) == 40 and all(c in "0123456789abcdef" for c in ref), uses

    def test_pins_match_this_repositorys_existing_publish_security_pins(self) -> None:
        # Not independently re-resolved from each tag's current HEAD -- a
        # floating major tag (e.g. actions/checkout@v6) can move to a newer
        # commit over time, so every elevated-permission workflow in this
        # repository must point at the SAME already-reviewed commit.
        for path in (PUBLISH_BASELINE, UPDATE_MAIN_BASELINE):
            data = _load(path)
            uses = _all_uses(data)
            checkout_uses = [u for u in uses if u.startswith("actions/checkout@")]
            assert checkout_uses and all(
                u == f"actions/checkout@{_CHECKOUT_V6_SHA}" for u in checkout_uses
            )
            setup_python_uses = [
                u for u in uses if u.startswith("actions/setup-python@")
            ]
            assert setup_python_uses and all(
                u == f"actions/setup-python@{_SETUP_PYTHON_V6_SHA}"
                for u in setup_python_uses
            )
            download_artifact_uses = [
                u for u in uses if u.startswith("actions/download-artifact@")
            ]
            assert download_artifact_uses and all(
                u == f"actions/download-artifact@{_DOWNLOAD_ARTIFACT_V8_SHA}"
                for u in download_artifact_uses
            )


class TestPublishBaselineSelfCheckout:
    """Same self-checkout rationale as check-single.yml/check-project.yml's
    own identical pattern -- a relative `uses: ./x` step inside a reusable
    workflow's own steps resolves against the CALLER's checkout, never this
    repository's, so the Action files this workflow nests
    (`actions/baseline`) must be fetched into a side directory first."""

    def test_identity_captured_before_the_nested_checkout(self) -> None:
        data = _load(PUBLISH_BASELINE)
        names = _step_names(data["jobs"]["publish"])
        assert "Capture this reusable workflow's identity" in names
        assert (
            "Checkout abicheck (for installing the CLI and nested Action composition)"
            in names
        )
        identity_idx = names.index("Capture this reusable workflow's identity")
        checkout_idx = names.index(
            "Checkout abicheck (for installing the CLI and nested Action composition)"
        )
        assert identity_idx < checkout_idx

    def test_identity_step_uses_job_context_not_github_context(self) -> None:
        # job.workflow_ref/job.workflow_sha -- NOT github.workflow_ref/
        # github.workflow_sha, which GitHub's own docs document as
        # caller-associated inside a called reusable workflow (see
        # check-project.yml's own identity step for the full writeup this
        # repository already fought through, twice, to get right).
        data = _load(PUBLISH_BASELINE)
        steps = _steps(data["jobs"]["publish"])
        identity_step = next(
            s
            for s in steps
            if s.get("name") == "Capture this reusable workflow's identity"
        )
        assert identity_step["env"]["WORKFLOW_REF"] == "${{ job.workflow_ref }}"
        assert identity_step["env"]["WORKFLOW_SHA"] == "${{ job.workflow_sha }}"

    def test_nested_baseline_action_uses_the_checked_out_copy(self) -> None:
        data = _load(PUBLISH_BASELINE)
        steps = _steps(data["jobs"]["publish"])
        dump_step = next(s for s in steps if s.get("name") == "Dump baseline-set")
        assert dump_step["uses"] == "./.publish-baseline-src/actions/baseline"


class TestUpdateMainBaselineSelfCheckout:
    def test_identity_captured_before_the_nested_checkout(self) -> None:
        data = _load(UPDATE_MAIN_BASELINE)
        names = _step_names(data["jobs"]["refresh"])
        assert "Capture this reusable workflow's identity" in names
        assert (
            "Checkout abicheck (for installing the CLI and nested Action composition)"
            in names
        )

    def test_nested_baseline_action_uses_the_checked_out_copy(self) -> None:
        data = _load(UPDATE_MAIN_BASELINE)
        steps = _steps(data["jobs"]["refresh"])
        dump_step = next(s for s in steps if s.get("name") == "Dump baseline-set")
        assert dump_step["uses"] == "./.update-main-baseline-src/actions/baseline"


class TestNoProfilesGuard:
    """Same "a skipped job reports success" failure mode check-project.yml's
    own no-checks guard exists to close (ADR-047 §4's required sub-task) --
    a zero-profile run must fail loud, not silently report success having
    published/refreshed nothing."""

    def test_publish_baseline_no_profiles_job_fails(self) -> None:
        data = _load(PUBLISH_BASELINE)
        job = data["jobs"]["no-profiles"]
        assert job["if"] == "needs.discover.outputs.has-profiles != 'true'"
        run = job["steps"][0]["run"]
        assert "exit 1" in run

    def test_update_main_baseline_no_profiles_job_fails(self) -> None:
        data = _load(UPDATE_MAIN_BASELINE)
        job = data["jobs"]["no-profiles"]
        assert job["if"] == "needs.discover.outputs.has-profiles != 'true'"
        run = job["steps"][0]["run"]
        assert "exit 1" in run

    def test_publish_job_gated_on_has_profiles(self) -> None:
        data = _load(PUBLISH_BASELINE)
        assert (
            data["jobs"]["publish"]["if"]
            == "needs.discover.outputs.has-profiles == 'true'"
        )

    def test_refresh_job_gated_on_has_profiles(self) -> None:
        data = _load(UPDATE_MAIN_BASELINE)
        assert (
            data["jobs"]["refresh"]["if"]
            == "needs.discover.outputs.has-profiles == 'true'"
        )


class TestPublishBaselinePermissions:
    def test_publish_job_declares_contents_write(self) -> None:
        data = _load(PUBLISH_BASELINE)
        assert data["jobs"]["publish"]["permissions"] == {"contents": "write"}

    def test_discover_job_declares_no_elevated_permissions(self) -> None:
        # The profile-discovery job only downloads artifacts -- it must not
        # carry contents: write, keeping the elevated scope confined to the
        # one job that actually publishes a release asset.
        data = _load(PUBLISH_BASELINE)
        assert "permissions" not in data["jobs"]["discover"]


class TestAcceptedMainCacheKeyRotation:
    """ADR-047 §10's explicit warning: a workflow that reused one stable key
    across refreshes would silently stop updating after the first write --
    the cache action reports that as a hit, not an error. Both the literal
    key template embedded in update-main-baseline.yml AND the pure-Python
    accepted_main_cache_key()/accepted_main_cache_restore_prefix() mirror it
    calls out to must agree, checked two ways: structurally (the template
    string itself), and semantically (feeding representative values through
    both and comparing)."""

    def test_compute_cache_key_step_uses_the_documented_template(self) -> None:
        data = _load(UPDATE_MAIN_BASELINE)
        steps = _steps(data["jobs"]["refresh"])
        step = next(s for s in steps if s.get("name") == "Compute cache key")
        run = step["run"]
        assert "${KEY_PREFIX}-${PROFILE_ID}-${HEAD_SHA}" in run
        assert "${KEY_PREFIX}-${PROFILE_ID}-" in run

    def test_template_semantics_match_the_pure_python_mirror(self) -> None:
        key_prefix, profile_id, head_sha = (
            "abicheck-baseline-main",
            "linux-x86_64-gcc",
            "deadbeef",
        )
        # Mirrors the bash template's own string interpolation exactly --
        # if either drifts from the other, this assertion (not a live
        # GitHub Actions run) is what catches it.
        bash_equivalent_key = f"{key_prefix}-{profile_id}-{head_sha}"
        bash_equivalent_prefix = f"{key_prefix}-{profile_id}-"
        assert bash_equivalent_key == accepted_main_cache_key(
            key_prefix, profile_id, head_sha
        )
        assert bash_equivalent_prefix == accepted_main_cache_restore_prefix(
            key_prefix, profile_id
        )

    def test_restore_step_key_is_this_runs_own_unique_key(self) -> None:
        # `key:` must be THIS run's own (always-unique-by-head-sha) key, not
        # the stable prefix -- so the exact-match lookup always misses and
        # falls through to restore-keys, resolving the newest previously
        # written entry (see the workflow's own inline comment for the full
        # semantics).
        data = _load(UPDATE_MAIN_BASELINE)
        steps = _steps(data["jobs"]["refresh"])
        restore_step = next(
            s
            for s in steps
            if s.get("name") == "Restore previous accepted-main baseline-set"
        )
        assert restore_step["with"]["key"] == "${{ steps.cache-key.outputs.cache-key }}"
        assert (
            "${{ steps.cache-key.outputs.restore-prefix }}"
            in restore_step["with"]["restore-keys"]
        )

    def test_save_step_writes_the_new_unique_key_not_the_prefix(self) -> None:
        data = _load(UPDATE_MAIN_BASELINE)
        steps = _steps(data["jobs"]["refresh"])
        save_step = next(
            s
            for s in steps
            if s.get("name") == "Save accepted-main baseline-set to cache"
        )
        assert save_step["with"]["key"] == "${{ steps.cache-key.outputs.cache-key }}"

    def test_save_step_is_not_continue_on_error(self) -> None:
        # A failed "Dump baseline-set" step above must fail this job before
        # the save step is reached -- no continue-on-error anywhere in the
        # chain, or a broken baseline-set could still get cached.
        data = _load(UPDATE_MAIN_BASELINE)
        steps = _steps(data["jobs"]["refresh"])
        dump_step = next(s for s in steps if s.get("name") == "Dump baseline-set")
        save_step = next(
            s
            for s in steps
            if s.get("name") == "Save accepted-main baseline-set to cache"
        )
        assert "continue-on-error" not in dump_step
        assert "continue-on-error" not in save_step


class TestBaselineLibrariesDerivation:
    """Both workflows derive actions/baseline's `libraries` input from
    build-output.json via the same library function call -- never a separate
    .abicheck.yml read (abicheck.buildsource.baseline_publish's own module
    docstring). Not a CLI command (ADR-054): `build-output baseline-libraries`
    was a wire-format adapter for exactly this call site, not general
    project-integration CLI surface, so both workflows call
    `derive_baseline_libraries()` directly via isolated `python3 -I -c`."""

    def test_publish_baseline_calls_the_library_function(self) -> None:
        data = _load(PUBLISH_BASELINE)
        steps = _steps(data["jobs"]["publish"])
        step = next(
            s
            for s in steps
            if s.get("name") == "Derive baseline libraries from build-output.json"
        )
        assert "derive_baseline_libraries" in step["run"]
        assert "abicheck build-output" not in step["run"]

    def test_update_main_baseline_calls_the_library_function(self) -> None:
        data = _load(UPDATE_MAIN_BASELINE)
        steps = _steps(data["jobs"]["refresh"])
        step = next(
            s
            for s in steps
            if s.get("name") == "Derive baseline libraries from build-output.json"
        )
        assert "derive_baseline_libraries" in step["run"]
        assert "abicheck build-output" not in step["run"]

    def test_library_imports_use_isolated_python(self) -> None:
        """The caller checkout must not shadow the installed abicheck package."""
        for path, job_name in (
            (PUBLISH_BASELINE, "publish"),
            (UPDATE_MAIN_BASELINE, "refresh"),
        ):
            data = _load(path)
            steps = _steps(data["jobs"][job_name])
            step = next(
                s
                for s in steps
                if s.get("name") == "Derive baseline libraries from build-output.json"
            )
            assert step["run"].count('python3 -I -c "') == 2
            assert 'python3 -c "' not in step["run"]

    def test_libraries_output_feeds_the_baseline_action_input(self) -> None:
        for path, job_name in (
            (PUBLISH_BASELINE, "publish"),
            (UPDATE_MAIN_BASELINE, "refresh"),
        ):
            data = _load(path)
            steps = _steps(data["jobs"][job_name])
            dump_step = next(s for s in steps if s.get("name") == "Dump baseline-set")
            assert (
                dump_step["with"]["libraries"]
                == "${{ steps.libraries.outputs.libraries }}"
            )

    def test_snapshot_compression_input_forwarded_to_baseline_action(self) -> None:
        """ADR-059: each workflow declares its own ``snapshot-compression``
        ``workflow_call`` input (default ``none``, unchanged behavior) and
        forwards it verbatim to the nested ``actions/baseline`` call -- the
        same declare-then-forward shape ``validation``/``depth`` already use
        above, so a caller can opt a published or accepted-main baseline-set
        into per-snapshot compression without either reusable workflow
        hardcoding a choice."""
        for path, job_name in (
            (PUBLISH_BASELINE, "publish"),
            (UPDATE_MAIN_BASELINE, "refresh"),
        ):
            data = _load(path)
            inputs = data[True]["workflow_call"]["inputs"]
            assert inputs["snapshot-compression"]["default"] == "none"
            assert inputs["snapshot-compression"]["type"] == "string"

            steps = _steps(data["jobs"][job_name])
            dump_step = next(s for s in steps if s.get("name") == "Dump baseline-set")
            assert (
                dump_step["with"]["snapshot-compression"]
                == "${{ inputs.snapshot-compression }}"
            )


class TestBaselineRotationFixture:
    """The plan's own P1.6 item requires a fixture asserting two consecutive
    update-main-baseline.yml runs produce two distinct, resolve-baseline-
    resolvable baselines -- something only a real GitHub Actions run can
    exercise (TestAcceptedMainCacheKeyRotation above only proves the
    key-FORMAT contract in pure Python). test-baseline-rotation.yml is that
    live fixture; these tests assert its structure so the wiring can't
    silently drift (e.g. the two reusable-workflow calls sharing a
    key-prefix, or the needs: chain enforcing run #1 completes before run
    #2 starts) without a real run being needed to catch it."""

    def test_both_runs_call_the_real_reusable_workflow(self) -> None:
        data = _load(TEST_BASELINE_ROTATION)
        for job_name in ("run-1", "run-2"):
            assert (
                data["jobs"][job_name]["uses"]
                == "./.github/workflows/update-main-baseline.yml"
            )

    def test_run_2_depends_on_run_1_completing_first(self) -> None:
        # Sequential, not parallel -- run #2 must only start after run #1's
        # cache write has actually landed, or this proves nothing about
        # rotation ordering.
        data = _load(TEST_BASELINE_ROTATION)
        assert "run-1" in data["jobs"]["run-2"]["needs"]

    def test_verify_depends_on_both_runs(self) -> None:
        data = _load(TEST_BASELINE_ROTATION)
        needs = data["jobs"]["verify"]["needs"]
        assert "run-1" in needs
        assert "run-2" in needs

    def test_both_runs_share_the_same_key_prefix_and_differ_only_by_head_sha(
        self,
    ) -> None:
        data = _load(TEST_BASELINE_ROTATION)
        run_1_with = data["jobs"]["run-1"]["with"]
        run_2_with = data["jobs"]["run-2"]["with"]
        assert run_1_with["key-prefix"] == run_2_with["key-prefix"]
        assert run_1_with["head-sha"] != run_2_with["head-sha"]

    def test_runs_use_distinct_build_output_artifact_prefixes(self) -> None:
        # Each run must resolve its own, distinct library build -- sharing
        # one artifact prefix would make the two dumps identical and the
        # fixture would prove nothing about distinctness.
        data = _load(TEST_BASELINE_ROTATION)
        run_1_prefix = data["jobs"]["run-1"]["with"]["build-output-artifact-prefix"]
        run_2_prefix = data["jobs"]["run-2"]["with"]["build-output-artifact-prefix"]
        assert run_1_prefix != run_2_prefix

    def test_verify_job_checks_cache_entry_existence_via_the_list_api(self) -> None:
        # This fixture's own first two live runs established that
        # actions/cache/restore is not reliably usable cross-job within the
        # same workflow run on this platform (see the workflow file's own
        # header comment for the evidence) -- verify checks existence via
        # the List Actions Caches REST API instead.
        data = _load(TEST_BASELINE_ROTATION)
        steps = _steps(data["jobs"]["verify"])
        names = [s.get("name") for s in steps]
        assert (
            "Confirm both rotation-test cache entries exist as separate GitHub Actions cache entries"
            in names
        )
        step = next(
            s
            for s in steps
            if s.get("name")
            == "Confirm both rotation-test cache entries exist as separate GitHub Actions cache entries"
        )
        assert "actions/caches" in step["run"]

    def test_verify_job_declares_actions_read_permission(self) -> None:
        # Required for the List Actions Caches API call -- the top-level
        # `permissions: contents: read` alone can't call it.
        data = _load(TEST_BASELINE_ROTATION)
        assert data["jobs"]["verify"]["permissions"]["actions"] == "read"

    def test_verify_asserts_the_two_cache_entries_have_distinct_ids(self) -> None:
        # The actual rotation assertion: two DIFFERENT cache entry ids, not
        # just two key strings that differ by construction.
        data = _load(TEST_BASELINE_ROTATION)
        steps = _steps(data["jobs"]["verify"])
        step = next(
            s
            for s in steps
            if s.get("name")
            == "Confirm both rotation-test cache entries exist as separate GitHub Actions cache entries"
        )
        assert "c1['id'] == c2['id']" in step["run"]

    def test_cleanup_job_always_runs_and_needs_every_other_job(self) -> None:
        # A failed run-1/run-2/verify still wrote real cache entries under
        # this run's own run_id that deserve cleanup -- if: always() must
        # not be gated on the other jobs' outcomes.
        data = _load(TEST_BASELINE_ROTATION)
        cleanup = data["jobs"]["cleanup"]
        assert cleanup["if"] == "always()"
        for job_name in ("build-fixture", "run-1", "run-2", "verify"):
            assert job_name in cleanup["needs"]

    def test_cleanup_job_declares_actions_write_permission(self) -> None:
        # Deleting a cache entry via the REST API needs actions: write, not
        # just the actions: read the verify job's List API call needs.
        data = _load(TEST_BASELINE_ROTATION)
        assert data["jobs"]["cleanup"]["permissions"]["actions"] == "write"

    def test_cleanup_deletes_both_keys_via_the_delete_caches_api(self) -> None:
        data = _load(TEST_BASELINE_ROTATION)
        steps = _steps(data["jobs"]["cleanup"])
        step = next(
            s
            for s in steps
            if s.get("name") == "Delete rotation-test cache entries for this run"
        )
        assert "DELETE" in step["run"]
        assert "actions/caches" in step["run"]
        assert "$KEY_1" in step["run"]
        assert "$KEY_2" in step["run"]


class TestUploadReleaseAssetRetryDispatchesBySuffix:
    """Regression (Codex review): the "Upload release asset" step's safe-
    retry comparison used to always download the existing asset to a
    hardcoded ``.tar.zst`` filename and extract it with
    ``TarExtractor._safe_extract_zst_tar`` directly, regardless of
    ``$ASSET_NAME``'s real extension. A non-default
    ``asset-name-template`` resolving to ``.tar.gz``/``.tgz``/``.tar`` (the
    "Package baseline-set" step's own sibling fix, same review round, now
    supports all four) would fail this comparison outright on every retry
    -- the download would still land in a ``.tar.zst``-named file, and
    zstd-frame decoding a gzip/plain-tar archive raises, never reaching the
    content-digest comparison at all.

    Extracts the real step verbatim and executes it against a real
    ``.tar.gz`` existing asset with a stubbed ``gh``, asserting the safe
    no-op path (identical content -> exit 0, "safe re-run" notice) is
    actually reachable for a non-zstd asset name -- not just that the step
    *parses*.
    """

    def _upload_step_script(self) -> str:
        data = _load(PUBLISH_BASELINE)
        steps = _steps(data["jobs"]["publish"])
        step = next(
            s for s in steps if s.get("name", "").startswith("Upload release asset")
        )
        return step["run"]

    def test_identical_tar_gz_content_is_a_safe_retry(self, tmp_path: Path) -> None:
        import importlib.util
        import json
        import shutil
        import subprocess
        import tarfile
        import tempfile

        module_spec = importlib.util.spec_from_file_location(
            "test_build_manifest_module",
            Path(__file__).resolve().parents[1]
            / "actions"
            / "baseline"
            / "build_manifest.py",
        )
        assert module_spec is not None and module_spec.loader is not None
        build_manifest = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(build_manifest)
        compute_content_digest = build_manifest.compute_content_digest

        # Real build_manifest.py at the exact relative path the script
        # loads it from (cwd-relative ".publish-baseline-src/...", matching
        # the "Self-checkout" step's own layout).
        src_root = (
            Path(__file__).resolve().parents[1]
            / "actions"
            / "baseline"
            / "build_manifest.py"
        )
        module_dir = tmp_path / ".publish-baseline-src" / "actions" / "baseline"
        module_dir.mkdir(parents=True)
        shutil.copy(src_root, module_dir / "build_manifest.py")

        manifest = {
            "manifest_version": 1,
            "project_ref": "v1.0.0",
            "profile": "linux-x86_64-gcc13-release",
            "snapshot_schema": None,
            "fact_set": None,
            "artifacts": [
                {
                    "library": "libfoo",
                    "artifact": "build/libfoo.so",
                    "snapshot": "libfoo.abicheck.json",
                    "sha256": "a" * 64,
                }
            ],
        }
        content_digest = compute_content_digest(manifest)

        # Build the "existing" asset as a real .tar.gz -- not .tar.zst --
        # so this test actually exercises the suffix-dispatch fix rather
        # than the always-worked default case.
        asset_src = tmp_path / "asset-src"
        asset_src.mkdir()
        (asset_src / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        asset_name = "abicheck-baseline-linux-x86_64-gcc13-release.tar.gz"
        archive_path = tmp_path / asset_name
        with tarfile.open(archive_path, "w:gz") as tf:
            tf.add(asset_src, arcname=".")

        gh_stub = f"""
gh() {{
  if [[ "$1" == "release" && "$2" == "view" ]]; then
    echo '{{"assets": [{{"name": "{asset_name}", "apiUrl": "https://api.example/asset"}}]}}'
    return 0
  fi
  if [[ "$1" == "api" ]]; then
    cat "{archive_path.as_posix()}"
    return 0
  fi
  echo "unexpected gh invocation: $*" >&2
  return 1
}}
"""
        script = gh_stub + "\n" + self._upload_step_script()

        env = os.environ.copy()
        env.update(
            {
                "GH_TOKEN": "fake-token",
                "RELEASE_TAG": "v1.0.0",
                "ASSET_NAME": asset_name,
                "REPO": "abicheck/abicheck",
                "NEW_CONTENT_DIGEST": content_digest,
                "NEW_PROFILE": manifest["profile"],
                "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
            }
        )

        fd, path = tempfile.mkstemp(suffix=".sh")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(script)
            result = subprocess.run(
                [_bash_executable(), path],
                capture_output=True,
                text=True,
                env=env,
                cwd=tmp_path,
                check=False,
            )
        finally:
            os.unlink(path)

        assert result.returncode == 0, result.stdout + result.stderr
        assert "safe re-run" in result.stdout


class TestUploadReleaseAssetRejectsCrossProfileCollision:
    """Regression (Codex review, P1): ``compute_content_digest()``
    deliberately excludes ``profile`` -- it hashes library/snapshot/binary
    content, not build-profile identity. When a caller's
    ``asset-name-template`` omits ``{profile}`` for a multi-profile matrix,
    every profile's job targets the SAME asset name; if two profiles happen
    to produce identical content digests, the old logic would treat a
    later profile's upload as a safe retry of an earlier, DIFFERENT
    profile's already-published asset -- silently discarding that
    profile's own baseline. The profile identity is now checked
    independently, before the content-digest comparison, and never treated
    as a safe retry regardless of what the digests say.
    """

    def _upload_step_script(self) -> str:
        data = _load(PUBLISH_BASELINE)
        steps = _steps(data["jobs"]["publish"])
        step = next(
            s for s in steps if s.get("name", "").startswith("Upload release asset")
        )
        return step["run"]

    def test_same_content_different_profile_is_rejected(self, tmp_path: Path) -> None:
        import json
        import shutil
        import subprocess
        import tarfile
        import tempfile

        # Two manifests with IDENTICAL artifact lists (so
        # compute_content_digest() agrees) but different `profile` --
        # exactly the collision an asset-name-template without {profile}
        # can cause across a multi-profile matrix.
        artifacts = [
            {
                "library": "libfoo",
                "artifact": "build/libfoo.so",
                "snapshot": "libfoo.abicheck.json",
                "sha256": "a" * 64,
            }
        ]
        existing_manifest = {
            "manifest_version": 1,
            "project_ref": "v1.0.0",
            "profile": "linux-x86_64-gcc13-release",
            "snapshot_schema": None,
            "fact_set": None,
            "artifacts": artifacts,
        }
        new_manifest = {**existing_manifest, "profile": "linux-aarch64-gcc13-release"}

        src_root = (
            Path(__file__).resolve().parents[1]
            / "actions"
            / "baseline"
            / "build_manifest.py"
        )
        module_dir = tmp_path / ".publish-baseline-src" / "actions" / "baseline"
        module_dir.mkdir(parents=True)
        shutil.copy(src_root, module_dir / "build_manifest.py")

        import importlib.util

        module_spec = importlib.util.spec_from_file_location(
            "test_build_manifest_module_2", src_root
        )
        assert module_spec is not None and module_spec.loader is not None
        build_manifest = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(build_manifest)
        new_content_digest = build_manifest.compute_content_digest(new_manifest)

        asset_src = tmp_path / "asset-src"
        asset_src.mkdir()
        (asset_src / "manifest.json").write_text(
            json.dumps(existing_manifest), encoding="utf-8"
        )
        asset_name = "abicheck-baseline-shared-name.tar"
        archive_path = tmp_path / asset_name
        with tarfile.open(archive_path, "w") as tf:
            tf.add(asset_src, arcname=".")

        gh_stub = f"""
gh() {{
  if [[ "$1" == "release" && "$2" == "view" ]]; then
    echo '{{"assets": [{{"name": "{asset_name}", "apiUrl": "https://api.example/asset"}}]}}'
    return 0
  fi
  if [[ "$1" == "api" ]]; then
    cat "{archive_path.as_posix()}"
    return 0
  fi
  echo "unexpected gh invocation: $*" >&2
  return 1
}}
"""
        script = gh_stub + "\n" + self._upload_step_script()

        env = os.environ.copy()
        env.update(
            {
                "GH_TOKEN": "fake-token",
                "RELEASE_TAG": "v1.0.0",
                "ASSET_NAME": asset_name,
                "REPO": "abicheck/abicheck",
                "NEW_CONTENT_DIGEST": new_content_digest,
                "NEW_PROFILE": new_manifest["profile"],
                "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
            }
        )

        fd, path = tempfile.mkstemp(suffix=".sh")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(script)
            result = subprocess.run(
                [_bash_executable(), path],
                capture_output=True,
                text=True,
                env=env,
                cwd=tmp_path,
                check=False,
            )
        finally:
            os.unlink(path)

        assert result.returncode == 1, result.stdout + result.stderr
        assert "DIFFERENT profile" in (result.stdout + result.stderr)
        # Must fail on the profile mismatch, never reach (or pass) the
        # content-digest comparison -- the whole point is that identical
        # content must NOT be treated as a safe retry across profiles.
        assert "safe re-run" not in result.stdout
