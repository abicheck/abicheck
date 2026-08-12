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

"""Behavioral tests for publish-baseline.yml's "Upload release asset" step
specifically -- split out of test_publish_baseline_workflows.py (which
covers the rest of that workflow plus update-main-baseline.yml) once this
file's own TestUploadReleaseAsset* class count pushed the combined file
past the AI-readiness 2000-line hard cap (CLAUDE.md "Files that are large
-- edit carefully"). Each class here executes the real step's embedded
``run:`` script verbatim (extracted via the small helper set below, a
deliberate near-duplicate of test_publish_baseline_workflows.py's own
``_load``/``_steps``/``_bash_executable``/``_WINDOWS_PYTHON3_SKIP`` --
kept independent rather than imported cross-module, so each test file
stays independently collectible) against a stubbed ``gh``, covering the
safe-retry/immutability logic's several distinct rejection shapes: a
cross-profile collision, a missing profile, an unsupported schema
(manifest_version and snapshot_schema), a mismatched project_ref, a
leading-dash asset name, corrupted existing content, and genuinely
different content.

NOTE (CodeRabbit nitpick, deliberately not done): the classes below each
carry a near-identical ``_upload_step_script()`` + gh-stub + tempfile-run
harness. Consolidating them into one parametrized module-level helper was
considered and deliberately deferred -- each class stubs ``gh`` with a
DIFFERENT shape (a plain safe-retry echo, a cross-profile-collision
reject, a missing-profile reject, an unsupported-schema reject, a
mismatched-project_ref reject, a leading-dash-name upload, a
corrupted-existing-content reject, a genuinely-different-content reject),
and the harnesses already diverge enough in what they assert and set up
(real ``build_manifest.py`` module loading, different manifest fixtures,
different expected stdout/exit codes) that forcing them through one
shared parametrized shape risks obscuring exactly the behavioral
distinction each test exists to pin, for a readability win that is
marginal at best. Revisit only if the duplication becomes a real
maintenance cost, not preemptively.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

WORKFLOWS_DIR = Path(__file__).resolve().parents[1] / ".github" / "workflows"
PUBLISH_BASELINE = WORKFLOWS_DIR / "publish-baseline.yml"


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


def _load(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    return job["steps"]


#: Shared by every ``TestUploadReleaseAsset*`` class below -- each executes
#: the real "Upload release asset" step's embedded script via
#: ``_bash_executable()``, which calls ``python3`` directly; Git Bash on
#: Windows typically only resolves ``python``, not ``python3`` (mirrors
#: ``test_reusable_workflows.py``'s identical convention).
_WINDOWS_PYTHON3_SKIP = pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "This step's embedded script invokes `python3` directly -- Git "
        "Bash on Windows typically only resolves `python`, not "
        "`python3`, so this test exercises real POSIX behavior a "
        "Windows test runner cannot provide (mirrors "
        "test_reusable_workflows.py's identical convention)."
    ),
)

#: The extracted step pipes `gh release view --json assets` output through a
#: real `jq` invocation (CodeRabbit nitpick) -- GitHub-hosted runners ship
#: `jq`, but a local/self-hosted run without it would otherwise fail every
#: class here with an unrelated "command not found" instead of skipping.
_JQ_REQUIRED = pytest.mark.skipif(
    shutil.which("jq") is None,
    reason="the extracted step pipes `gh release view --json assets` output through real `jq`",
)


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

    @_WINDOWS_PYTHON3_SKIP
    @_JQ_REQUIRED
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
        compute_snapshot_content_hash = build_manifest.compute_snapshot_content_hash

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

        # A real snapshot dict, not just a declared digest string --
        # recompute_content_digest_from_disk() now reads the ACTUAL
        # extracted snapshot bytes for the existing-asset comparison
        # (Codex review), so the manifest's declared sha256 must genuinely
        # match what compute_snapshot_content_hash() computes from this
        # exact snapshot content, the same way build_manifest.py's own
        # main() derives it from a real dump.
        snapshot = {"schema_version": 9, "functions": [], "types": []}
        snapshot_hash = compute_snapshot_content_hash(snapshot)

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
                    "sha256": snapshot_hash,
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
        (asset_src / "libfoo.abicheck.json").write_text(
            json.dumps(snapshot), encoding="utf-8"
        )
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

    @_WINDOWS_PYTHON3_SKIP
    @_JQ_REQUIRED
    def test_same_content_different_profile_is_rejected(self, tmp_path: Path) -> None:
        import importlib.util
        import json
        import shutil
        import subprocess
        import tarfile
        import tempfile

        src_root = (
            Path(__file__).resolve().parents[1]
            / "actions"
            / "baseline"
            / "build_manifest.py"
        )
        module_dir = tmp_path / ".publish-baseline-src" / "actions" / "baseline"
        module_dir.mkdir(parents=True)
        shutil.copy(src_root, module_dir / "build_manifest.py")

        module_spec = importlib.util.spec_from_file_location(
            "test_build_manifest_module_2", src_root
        )
        assert module_spec is not None and module_spec.loader is not None
        build_manifest = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(build_manifest)

        # A real snapshot dict, not just a declared digest string --
        # recompute_content_digest_from_disk() now reads the ACTUAL
        # extracted snapshot bytes for the existing-asset comparison
        # (Codex review), so this fixture needs a real, hash-consistent
        # snapshot file for that recomputation to succeed at all (the
        # profile-mismatch rejection this test is about happens AFTER
        # that recomputation in the real script).
        snapshot = {"schema_version": 9, "functions": [], "types": []}
        snapshot_hash = build_manifest.compute_snapshot_content_hash(snapshot)

        # Two manifests with IDENTICAL artifact lists (so
        # compute_content_digest() agrees) but different `profile` --
        # exactly the collision an asset-name-template without {profile}
        # can cause across a multi-profile matrix.
        artifacts = [
            {
                "library": "libfoo",
                "artifact": "build/libfoo.so",
                "snapshot": "libfoo.abicheck.json",
                "sha256": snapshot_hash,
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
        new_content_digest = build_manifest.compute_content_digest(new_manifest)

        asset_src = tmp_path / "asset-src"
        asset_src.mkdir()
        (asset_src / "manifest.json").write_text(
            json.dumps(existing_manifest), encoding="utf-8"
        )
        (asset_src / "libfoo.abicheck.json").write_text(
            json.dumps(snapshot), encoding="utf-8"
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
        assert "DIFFERENT (or missing) profile" in (result.stdout + result.stderr)
        # Must fail on the profile mismatch, never reach (or pass) the
        # content-digest comparison -- the whole point is that identical
        # content must NOT be treated as a safe retry across profiles.
        assert "safe re-run" not in result.stdout


class TestUploadReleaseAssetRejectsMissingProfile:
    """Regression (Codex review, P2): the profile-identity guard above only
    fired when the existing manifest's ``profile`` was NONEMPTY and
    different -- an existing manifest with an empty/absent ``profile``
    field (a real possibility, since ``actions/baseline``'s own ``profile``
    input is optional) skipped the check entirely, letting matching library
    digests alone classify an otherwise-unverifiable asset as a safe retry.
    The check now requires the existing profile to be nonempty AND equal.
    """

    def _upload_step_script(self) -> str:
        data = _load(PUBLISH_BASELINE)
        steps = _steps(data["jobs"]["publish"])
        step = next(
            s for s in steps if s.get("name", "").startswith("Upload release asset")
        )
        return step["run"]

    @_WINDOWS_PYTHON3_SKIP
    @_JQ_REQUIRED
    def test_existing_manifest_with_no_profile_is_rejected(
        self, tmp_path: Path
    ) -> None:
        import importlib.util
        import json
        import shutil
        import subprocess
        import tarfile
        import tempfile

        src_root = (
            Path(__file__).resolve().parents[1]
            / "actions"
            / "baseline"
            / "build_manifest.py"
        )
        module_dir = tmp_path / ".publish-baseline-src" / "actions" / "baseline"
        module_dir.mkdir(parents=True)
        shutil.copy(src_root, module_dir / "build_manifest.py")

        module_spec = importlib.util.spec_from_file_location(
            "test_build_manifest_module_missing_profile", src_root
        )
        assert module_spec is not None and module_spec.loader is not None
        build_manifest = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(build_manifest)

        snapshot = {"schema_version": 9, "functions": [], "types": []}
        snapshot_hash = build_manifest.compute_snapshot_content_hash(snapshot)
        artifacts = [
            {
                "library": "libfoo",
                "artifact": "build/libfoo.so",
                "snapshot": "libfoo.abicheck.json",
                "sha256": snapshot_hash,
            }
        ]
        # No "profile" key at all -- the legacy/malformed case this
        # regression targets.
        existing_manifest = {
            "manifest_version": 1,
            "project_ref": "v1.0.0",
            "snapshot_schema": None,
            "fact_set": None,
            "artifacts": artifacts,
        }
        new_manifest = {**existing_manifest, "profile": "linux-x86_64-gcc13-release"}
        new_content_digest = build_manifest.compute_content_digest(new_manifest)

        asset_src = tmp_path / "asset-src"
        asset_src.mkdir()
        (asset_src / "manifest.json").write_text(
            json.dumps(existing_manifest), encoding="utf-8"
        )
        (asset_src / "libfoo.abicheck.json").write_text(
            json.dumps(snapshot), encoding="utf-8"
        )
        asset_name = "abicheck-baseline-no-profile.tar"
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
        assert "DIFFERENT (or missing) profile" in (result.stdout + result.stderr)
        assert "safe re-run" not in result.stdout


class TestUploadReleaseAssetRejectsUnsupportedManifestVersion:
    """Regression (Codex review, P2): compute_content_digest()/
    recompute_content_digest_from_disk() hash artifacts[] content alone --
    neither reads manifest_version. An existing asset with an absent or
    unsupported manifest_version, but the SAME profile and library
    content, could therefore still reach a matching content digest and
    take this step's safe-retry no-op path -- leaving an asset published
    that a real consumer (resolve_target()/resolve_bundle() in
    abicheck/buildsource/baseline_set.py) would reject outright as
    stale_schema. The step now validates the existing manifest's
    manifest_version against the same SUPPORTED_MANIFEST_VERSIONS a real
    consumer uses, before ever reaching the content-digest comparison.
    """

    def _upload_step_script(self) -> str:
        data = _load(PUBLISH_BASELINE)
        steps = _steps(data["jobs"]["publish"])
        step = next(
            s for s in steps if s.get("name", "").startswith("Upload release asset")
        )
        return step["run"]

    @_WINDOWS_PYTHON3_SKIP
    @_JQ_REQUIRED
    def test_unsupported_manifest_version_is_rejected_even_with_matching_content(
        self, tmp_path: Path
    ) -> None:
        import importlib.util
        import json
        import shutil
        import subprocess
        import tarfile
        import tempfile

        src_root = (
            Path(__file__).resolve().parents[1]
            / "actions"
            / "baseline"
            / "build_manifest.py"
        )
        module_dir = tmp_path / ".publish-baseline-src" / "actions" / "baseline"
        module_dir.mkdir(parents=True)
        shutil.copy(src_root, module_dir / "build_manifest.py")

        module_spec = importlib.util.spec_from_file_location(
            "test_build_manifest_module_bad_schema", src_root
        )
        assert module_spec is not None and module_spec.loader is not None
        build_manifest = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(build_manifest)

        snapshot = {"schema_version": 9, "functions": [], "types": []}
        snapshot_hash = build_manifest.compute_snapshot_content_hash(snapshot)
        artifacts = [
            {
                "library": "libfoo",
                "artifact": "build/libfoo.so",
                "snapshot": "libfoo.abicheck.json",
                "sha256": snapshot_hash,
            }
        ]
        # manifest_version 999 -- not in SUPPORTED_MANIFEST_VERSIONS ({1}).
        # Same profile and content as the fresh run, so the profile guard
        # and the content-digest comparison alone would both classify this
        # as a safe retry.
        existing_manifest = {
            "manifest_version": 999,
            "project_ref": "v1.0.0",
            "profile": "linux-x86_64-gcc13-release",
            "snapshot_schema": None,
            "fact_set": None,
            "artifacts": artifacts,
        }
        new_manifest = {**existing_manifest, "manifest_version": 1}
        new_content_digest = build_manifest.compute_content_digest(new_manifest)

        asset_src = tmp_path / "asset-src"
        asset_src.mkdir()
        (asset_src / "manifest.json").write_text(
            json.dumps(existing_manifest), encoding="utf-8"
        )
        (asset_src / "libfoo.abicheck.json").write_text(
            json.dumps(snapshot), encoding="utf-8"
        )
        asset_name = "abicheck-baseline-bad-schema.tar"
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
        assert "stale_schema" in (result.stdout + result.stderr)
        assert "safe re-run" not in result.stdout


class TestUploadReleaseAssetRejectsUnsupportedSnapshotSchema:
    """Regression (Codex review, P2, second round): manifest_version and
    snapshot_schema are two SEPARATE stale_schema checks
    ``resolve_target()``/``resolve_bundle()`` apply
    (``abicheck/buildsource/baseline_set.py``) -- the first round's fix
    validated only manifest_version. An existing asset with a valid
    manifest_version but a snapshot_schema NEWER than this checkout's
    installed reader (``serialization.SCHEMA_VERSION``) could still reach
    a matching content digest and take the safe-retry no-op, leaving an
    asset published that a real consumer would reject outright.
    """

    def _upload_step_script(self) -> str:
        data = _load(PUBLISH_BASELINE)
        steps = _steps(data["jobs"]["publish"])
        step = next(
            s for s in steps if s.get("name", "").startswith("Upload release asset")
        )
        return step["run"]

    @_WINDOWS_PYTHON3_SKIP
    @_JQ_REQUIRED
    def test_snapshot_schema_newer_than_installed_reader_is_rejected(
        self, tmp_path: Path
    ) -> None:
        import importlib.util
        import json
        import shutil
        import subprocess
        import tarfile
        import tempfile

        from abicheck import serialization

        src_root = (
            Path(__file__).resolve().parents[1]
            / "actions"
            / "baseline"
            / "build_manifest.py"
        )
        module_dir = tmp_path / ".publish-baseline-src" / "actions" / "baseline"
        module_dir.mkdir(parents=True)
        shutil.copy(src_root, module_dir / "build_manifest.py")

        module_spec = importlib.util.spec_from_file_location(
            "test_build_manifest_module_bad_snapshot_schema", src_root
        )
        assert module_spec is not None and module_spec.loader is not None
        build_manifest = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(build_manifest)

        snapshot = {"schema_version": 9, "functions": [], "types": []}
        snapshot_hash = build_manifest.compute_snapshot_content_hash(snapshot)
        artifacts = [
            {
                "library": "libfoo",
                "artifact": "build/libfoo.so",
                "snapshot": "libfoo.abicheck.json",
                "sha256": snapshot_hash,
            }
        ]
        # A valid manifest_version, but snapshot_schema newer than this
        # checkout's installed reader supports. Same profile and content
        # as the fresh run, so the manifest_version guard and the
        # content-digest comparison alone would both classify this as a
        # safe retry.
        existing_manifest = {
            "manifest_version": 1,
            "project_ref": "v1.0.0",
            "profile": "linux-x86_64-gcc13-release",
            "snapshot_schema": serialization.SCHEMA_VERSION + 1,
            "fact_set": None,
            "artifacts": artifacts,
        }
        new_manifest = {
            **existing_manifest,
            "snapshot_schema": serialization.SCHEMA_VERSION,
        }
        new_content_digest = build_manifest.compute_content_digest(new_manifest)

        asset_src = tmp_path / "asset-src"
        asset_src.mkdir()
        (asset_src / "manifest.json").write_text(
            json.dumps(existing_manifest), encoding="utf-8"
        )
        (asset_src / "libfoo.abicheck.json").write_text(
            json.dumps(snapshot), encoding="utf-8"
        )
        asset_name = "abicheck-baseline-bad-snapshot-schema.tar"
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
        assert "stale_schema" in (result.stdout + result.stderr)
        assert "safe re-run" not in result.stdout

    @_WINDOWS_PYTHON3_SKIP
    @_JQ_REQUIRED
    def test_per_snapshot_schema_version_newer_than_installed_reader_is_rejected(
        self, tmp_path: Path
    ) -> None:
        """Regression (Codex review, third round): the manifest-level
        ``snapshot_schema`` check alone is not the whole story -- the real
        resolver (``_snapshot_digest_issue()`` in
        ``abicheck/buildsource/baseline_set.py``) ALSO reads each
        individual snapshot file's OWN ``schema_version``, since an
        older/hand-authored manifest with no aggregate ``snapshot_schema``
        has nothing for that first check to compare, while the snapshot
        file itself always carries one. This existing asset omits
        ``snapshot_schema`` entirely but its one snapshot file's own
        ``schema_version`` is newer than this checkout's installed
        reader -- matching profile and content, so every other guard
        would classify it as a safe retry.
        """
        import importlib.util
        import json
        import shutil
        import subprocess
        import tarfile
        import tempfile

        from abicheck import serialization

        src_root = (
            Path(__file__).resolve().parents[1]
            / "actions"
            / "baseline"
            / "build_manifest.py"
        )
        module_dir = tmp_path / ".publish-baseline-src" / "actions" / "baseline"
        module_dir.mkdir(parents=True)
        shutil.copy(src_root, module_dir / "build_manifest.py")

        module_spec = importlib.util.spec_from_file_location(
            "test_build_manifest_module_bad_per_snapshot_schema", src_root
        )
        assert module_spec is not None and module_spec.loader is not None
        build_manifest = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(build_manifest)

        # The snapshot file's own schema_version is newer than this
        # checkout supports -- content hash is computed from the SAME
        # dict, so the fresh run's manifest (using an otherwise-identical
        # snapshot at the CURRENT schema) still reaches a matching digest.
        bad_snapshot = {
            "schema_version": serialization.SCHEMA_VERSION + 1,
            "functions": [],
            "types": [],
        }
        good_snapshot = {
            "schema_version": serialization.SCHEMA_VERSION,
            "functions": [],
            "types": [],
        }
        bad_hash = build_manifest.compute_snapshot_content_hash(bad_snapshot)
        good_hash = build_manifest.compute_snapshot_content_hash(good_snapshot)

        existing_manifest = {
            "manifest_version": 1,
            "project_ref": "v1.0.0",
            "profile": "linux-x86_64-gcc13-release",
            "snapshot_schema": None,  # legacy/absent -- nothing to compare here
            "fact_set": None,
            "artifacts": [
                {
                    "library": "libfoo",
                    "artifact": "build/libfoo.so",
                    "snapshot": "libfoo.abicheck.json",
                    "sha256": bad_hash,
                }
            ],
        }
        new_manifest = {
            **existing_manifest,
            "artifacts": [
                {
                    "library": "libfoo",
                    "artifact": "build/libfoo.so",
                    "snapshot": "libfoo.abicheck.json",
                    "sha256": good_hash,
                }
            ],
        }
        new_content_digest = build_manifest.compute_content_digest(new_manifest)

        asset_src = tmp_path / "asset-src"
        asset_src.mkdir()
        (asset_src / "manifest.json").write_text(
            json.dumps(existing_manifest), encoding="utf-8"
        )
        (asset_src / "libfoo.abicheck.json").write_text(
            json.dumps(bad_snapshot), encoding="utf-8"
        )
        asset_name = "abicheck-baseline-bad-per-snapshot-schema.tar"
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
        assert "stale_schema" in (result.stdout + result.stderr)
        assert "safe re-run" not in result.stdout

    @_WINDOWS_PYTHON3_SKIP
    @_JQ_REQUIRED
    def test_non_integer_snapshot_schema_is_rejected_not_a_crash(
        self, tmp_path: Path
    ) -> None:
        """Regression (Codex review, third round): a malformed, non-integer
        ``snapshot_schema`` (e.g. a string) used to reach a bare ``>``
        comparison, raising ``TypeError`` -- which, under this script's own
        ``set -euo pipefail``, aborted the whole step with a raw Python
        traceback instead of this check's own actionable ``::error::``
        message. Explicit ``isinstance`` validation now makes this a clean
        rejection instead of a crash.
        """
        import importlib.util
        import json
        import shutil
        import subprocess
        import tarfile
        import tempfile

        src_root = (
            Path(__file__).resolve().parents[1]
            / "actions"
            / "baseline"
            / "build_manifest.py"
        )
        module_dir = tmp_path / ".publish-baseline-src" / "actions" / "baseline"
        module_dir.mkdir(parents=True)
        shutil.copy(src_root, module_dir / "build_manifest.py")

        module_spec = importlib.util.spec_from_file_location(
            "test_build_manifest_module_non_int_snapshot_schema", src_root
        )
        assert module_spec is not None and module_spec.loader is not None
        build_manifest = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(build_manifest)

        snapshot = {"schema_version": 9, "functions": [], "types": []}
        snapshot_hash = build_manifest.compute_snapshot_content_hash(snapshot)
        artifacts = [
            {
                "library": "libfoo",
                "artifact": "build/libfoo.so",
                "snapshot": "libfoo.abicheck.json",
                "sha256": snapshot_hash,
            }
        ]
        existing_manifest = {
            "manifest_version": 1,
            "project_ref": "v1.0.0",
            "profile": "linux-x86_64-gcc13-release",
            "snapshot_schema": "not-a-number",
            "fact_set": None,
            "artifacts": artifacts,
        }
        new_manifest = {**existing_manifest, "snapshot_schema": None}
        new_content_digest = build_manifest.compute_content_digest(new_manifest)

        asset_src = tmp_path / "asset-src"
        asset_src.mkdir()
        (asset_src / "manifest.json").write_text(
            json.dumps(existing_manifest), encoding="utf-8"
        )
        (asset_src / "libfoo.abicheck.json").write_text(
            json.dumps(snapshot), encoding="utf-8"
        )
        asset_name = "abicheck-baseline-non-int-snapshot-schema.tar"
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
        assert "Traceback" not in (result.stdout + result.stderr)
        assert "stale_schema" in (result.stdout + result.stderr)
        assert "safe re-run" not in result.stdout


class TestUploadReleaseAssetRejectsMismatchedProjectRef:
    """Regression (Codex review, P2): compute_content_digest()/
    recompute_content_digest_from_disk() hash artifacts[] content alone --
    neither reads project_ref. An existing asset published under a
    DIFFERENT (or missing) project_ref than this run's own $RELEASE_TAG,
    but the SAME profile and library content, could therefore still reach
    a matching content digest and take this step's safe-retry no-op path.
    A real consumer supplying $RELEASE_TAG as resolve-baseline's own
    expected-project-ref would reject that asset outright as
    wrong_project_ref -- the step now checks project_ref against
    $RELEASE_TAG before ever reaching the content-digest comparison, the
    same "identity field first" pattern the profile check already uses.
    """

    def _upload_step_script(self) -> str:
        data = _load(PUBLISH_BASELINE)
        steps = _steps(data["jobs"]["publish"])
        step = next(
            s for s in steps if s.get("name", "").startswith("Upload release asset")
        )
        return step["run"]

    @_WINDOWS_PYTHON3_SKIP
    @_JQ_REQUIRED
    def test_different_project_ref_is_rejected_even_with_matching_content(
        self, tmp_path: Path
    ) -> None:
        import importlib.util
        import json
        import shutil
        import subprocess
        import tarfile
        import tempfile

        src_root = (
            Path(__file__).resolve().parents[1]
            / "actions"
            / "baseline"
            / "build_manifest.py"
        )
        module_dir = tmp_path / ".publish-baseline-src" / "actions" / "baseline"
        module_dir.mkdir(parents=True)
        shutil.copy(src_root, module_dir / "build_manifest.py")

        module_spec = importlib.util.spec_from_file_location(
            "test_build_manifest_module_bad_project_ref", src_root
        )
        assert module_spec is not None and module_spec.loader is not None
        build_manifest = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(build_manifest)

        snapshot = {"schema_version": 9, "functions": [], "types": []}
        snapshot_hash = build_manifest.compute_snapshot_content_hash(snapshot)
        artifacts = [
            {
                "library": "libfoo",
                "artifact": "build/libfoo.so",
                "snapshot": "libfoo.abicheck.json",
                "sha256": snapshot_hash,
            }
        ]
        # A stale project_ref ("v0.9.0") from an earlier release run, while
        # this run publishes to "v1.0.0" -- same profile and content as
        # the fresh run, so the profile/schema guards and the
        # content-digest comparison alone would all classify this as a
        # safe retry.
        existing_manifest = {
            "manifest_version": 1,
            "project_ref": "v0.9.0",
            "profile": "linux-x86_64-gcc13-release",
            "snapshot_schema": None,
            "fact_set": None,
            "artifacts": artifacts,
        }
        new_manifest = {**existing_manifest, "project_ref": "v1.0.0"}
        new_content_digest = build_manifest.compute_content_digest(new_manifest)

        asset_src = tmp_path / "asset-src"
        asset_src.mkdir()
        (asset_src / "manifest.json").write_text(
            json.dumps(existing_manifest), encoding="utf-8"
        )
        (asset_src / "libfoo.abicheck.json").write_text(
            json.dumps(snapshot), encoding="utf-8"
        )
        asset_name = "abicheck-baseline-bad-project-ref.tar"
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
        assert "wrong_project_ref" in (result.stdout + result.stderr)
        assert "safe re-run" not in result.stdout

    @_WINDOWS_PYTHON3_SKIP
    @_JQ_REQUIRED
    def test_missing_project_ref_is_rejected_even_with_matching_content(
        self, tmp_path: Path
    ) -> None:
        import importlib.util
        import json
        import shutil
        import subprocess
        import tarfile
        import tempfile

        src_root = (
            Path(__file__).resolve().parents[1]
            / "actions"
            / "baseline"
            / "build_manifest.py"
        )
        module_dir = tmp_path / ".publish-baseline-src" / "actions" / "baseline"
        module_dir.mkdir(parents=True)
        shutil.copy(src_root, module_dir / "build_manifest.py")

        module_spec = importlib.util.spec_from_file_location(
            "test_build_manifest_module_missing_project_ref", src_root
        )
        assert module_spec is not None and module_spec.loader is not None
        build_manifest = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(build_manifest)

        snapshot = {"schema_version": 9, "functions": [], "types": []}
        snapshot_hash = build_manifest.compute_snapshot_content_hash(snapshot)
        artifacts = [
            {
                "library": "libfoo",
                "artifact": "build/libfoo.so",
                "snapshot": "libfoo.abicheck.json",
                "sha256": snapshot_hash,
            }
        ]
        # No "project_ref" key at all -- a legacy/malformed manifest, the
        # same class of gap the missing-profile regression covers for that
        # field.
        existing_manifest = {
            "manifest_version": 1,
            "profile": "linux-x86_64-gcc13-release",
            "snapshot_schema": None,
            "fact_set": None,
            "artifacts": artifacts,
        }
        new_manifest = {**existing_manifest, "project_ref": "v1.0.0"}
        new_content_digest = build_manifest.compute_content_digest(new_manifest)

        asset_src = tmp_path / "asset-src"
        asset_src.mkdir()
        (asset_src / "manifest.json").write_text(
            json.dumps(existing_manifest), encoding="utf-8"
        )
        (asset_src / "libfoo.abicheck.json").write_text(
            json.dumps(snapshot), encoding="utf-8"
        )
        asset_name = "abicheck-baseline-no-project-ref.tar"
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
        assert "wrong_project_ref" in (result.stdout + result.stderr)
        assert "safe re-run" not in result.stdout


class TestUploadReleaseAssetHandlesLeadingDashAssetName:
    """Regression (Codex review, P2): `gh release upload`'s own arg parser
    treats a leading '-' in a file argument as a FLAG, not part of the
    filename (confirmed against real gh 2.96.0:
    `gh release upload dummy --nightly.tar.gz` exits "unknown flag" rather
    than uploading a file by that name). A custom asset-name-template
    resolving to a name starting with '-' (e.g. "--nightly.tar.gz")
    packages and stages just fine in actions/stage-baseline -- '-' is a
    perfectly legal leading filename character on every real filesystem --
    but this step's first-time-publish `gh release upload` call would
    otherwise always fail. The file argument is now "./$ASSET_NAME", not a
    bare "$ASSET_NAME".
    """

    def _upload_step_script(self) -> str:
        data = _load(PUBLISH_BASELINE)
        steps = _steps(data["jobs"]["publish"])
        step = next(
            s for s in steps if s.get("name", "").startswith("Upload release asset")
        )
        return step["run"]

    @_WINDOWS_PYTHON3_SKIP
    @_JQ_REQUIRED
    def test_leading_dash_asset_name_is_uploaded_via_a_relative_path(
        self, tmp_path: Path
    ) -> None:
        import shlex
        import subprocess
        import tempfile

        asset_name = "--nightly.tar.gz"
        captured = tmp_path / "captured-upload-args.txt"
        # Mirrors real gh's own leading-'-' flag-parsing failure for the
        # file argument (the second positional after "release upload
        # <tag>"), rather than merely recording what was passed.
        gh_stub = f"""
gh() {{
  if [[ "$1" == "release" && "$2" == "view" ]]; then
    echo '{{"assets": []}}'
    return 0
  fi
  if [[ "$1" == "release" && "$2" == "upload" ]]; then
    shift 2
    printf '%s\\n' "$@" > {shlex.quote(str(captured))}
    shift  # drop the release tag
    file_arg="$1"
    case "$file_arg" in
      -*)
        echo "unknown flag: $file_arg" >&2
        return 1
        ;;
    esac
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
                "NEW_CONTENT_DIGEST": "deadbeef",
                "NEW_PROFILE": "linux-x86_64-gcc13-release",
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
        assert captured.is_file(), "gh release upload was never invoked"
        args = captured.read_text(encoding="utf-8").splitlines()
        assert args[0] == "v1.0.0"
        assert args[1] == f"./{asset_name}"


class TestUploadReleaseAssetRejectsSymlinkContainingExistingAsset:
    """Regression (Codex review, P2): ``TarExtractor``'s own member
    validation rejects a symlink escaping the extraction root, but not an
    IN-ROOT symlink (one whose target stays inside the extracted
    directory) -- so a real symlink can land here and be silently
    followed by the digest computation, letting an existing asset with
    identical content reach the safe-retry success path.
    ``actions/resolve-baseline/run.sh``'s own extraction path rejects ANY
    symlink at all (a baseline-set has no legitimate reason to contain
    one), so an asset that passes this step's check but would be
    rejected by the canonical resolver could be reported as successfully
    published while remaining unusable to a real consumer. This step now
    applies the identical no-symlink structural check before accepting
    the existing asset as a safe retry.
    """

    def _upload_step_script(self) -> str:
        data = _load(PUBLISH_BASELINE)
        steps = _steps(data["jobs"]["publish"])
        step = next(
            s for s in steps if s.get("name", "").startswith("Upload release asset")
        )
        return step["run"]

    @_WINDOWS_PYTHON3_SKIP
    @_JQ_REQUIRED
    def test_in_root_symlink_is_rejected_even_with_matching_content(
        self, tmp_path: Path
    ) -> None:
        import importlib.util
        import json
        import shutil
        import subprocess
        import tarfile
        import tempfile

        src_root = (
            Path(__file__).resolve().parents[1]
            / "actions"
            / "baseline"
            / "build_manifest.py"
        )
        module_dir = tmp_path / ".publish-baseline-src" / "actions" / "baseline"
        module_dir.mkdir(parents=True)
        shutil.copy(src_root, module_dir / "build_manifest.py")

        module_spec = importlib.util.spec_from_file_location(
            "test_build_manifest_module_symlink", src_root
        )
        assert module_spec is not None and module_spec.loader is not None
        build_manifest = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(build_manifest)

        snapshot = {"schema_version": 9, "functions": [], "types": []}
        snapshot_hash = build_manifest.compute_snapshot_content_hash(snapshot)
        artifacts = [
            {
                "library": "libfoo",
                "artifact": "build/libfoo.so",
                "snapshot": "libfoo.abicheck.json",
                "sha256": snapshot_hash,
            }
        ]
        manifest = {
            "manifest_version": 1,
            "project_ref": "v1.0.0",
            "profile": "linux-x86_64-gcc13-release",
            "snapshot_schema": None,
            "fact_set": None,
            "artifacts": artifacts,
        }
        new_content_digest = build_manifest.compute_content_digest(manifest)

        asset_src = tmp_path / "asset-src"
        asset_src.mkdir()
        (asset_src / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (asset_src / "libfoo.abicheck.json").write_text(
            json.dumps(snapshot), encoding="utf-8"
        )

        asset_name = "abicheck-baseline-symlink.tar"
        archive_path = tmp_path / asset_name
        with tarfile.open(archive_path, "w") as tf:
            tf.add(asset_src, arcname=".")
            # An in-root symlink -- its target stays inside the archive
            # (never escapes), so TarExtractor's own path-traversal guard
            # doesn't reject it.
            link_info = tarfile.TarInfo(name="./libfoo.abicheck.json.link")
            link_info.type = tarfile.SYMTYPE
            link_info.linkname = "libfoo.abicheck.json"
            tf.addfile(link_info)

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

        assert result.returncode == 1, result.stdout + result.stderr
        assert "symlink" in (result.stdout + result.stderr)
        assert "safe re-run" not in result.stdout


class TestUploadReleaseAssetRejectsCorruptedExistingContent:
    """Regression (Codex review, P2): the safe-retry digest comparison used
    to trust the existing asset's manifest.json declared sha256/
    binary_sha256 fields alone -- an existing asset whose manifest.json was
    hand-edited (or otherwise corrupted after upload) so its DECLARED
    digest matched a fresh run's real digest would pass as a safe retry
    even though its ACTUAL extracted snapshot bytes differ from what the
    manifest claims. The digest is now recomputed from the real extracted
    bytes (``recompute_content_digest_from_disk``), so a declared-vs-actual
    mismatch is now caught as a genuine content difference.
    """

    def _upload_step_script(self) -> str:
        data = _load(PUBLISH_BASELINE)
        steps = _steps(data["jobs"]["publish"])
        step = next(
            s for s in steps if s.get("name", "").startswith("Upload release asset")
        )
        return step["run"]

    @_WINDOWS_PYTHON3_SKIP
    @_JQ_REQUIRED
    def test_declared_digest_matching_but_real_bytes_differing_is_rejected(
        self, tmp_path: Path
    ) -> None:
        import importlib.util
        import json
        import shutil
        import subprocess
        import tarfile
        import tempfile

        src_root = (
            Path(__file__).resolve().parents[1]
            / "actions"
            / "baseline"
            / "build_manifest.py"
        )
        module_dir = tmp_path / ".publish-baseline-src" / "actions" / "baseline"
        module_dir.mkdir(parents=True)
        shutil.copy(src_root, module_dir / "build_manifest.py")

        module_spec = importlib.util.spec_from_file_location(
            "test_build_manifest_module_corrupted", src_root
        )
        assert module_spec is not None and module_spec.loader is not None
        build_manifest = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(build_manifest)

        # The manifest's DECLARED sha256 is computed from `real_snapshot`,
        # matching what a fresh run of the SAME content would compute --
        # but the actual bytes written into the archive are
        # `corrupted_snapshot`, simulating a manually-edited or corrupted
        # existing asset whose manifest.json lies about its own content.
        real_snapshot = {"schema_version": 9, "functions": [], "types": []}
        corrupted_snapshot = {
            "schema_version": 9,
            "functions": [{"name": "injected_function"}],
            "types": [],
        }
        declared_hash = build_manifest.compute_snapshot_content_hash(real_snapshot)

        artifacts = [
            {
                "library": "libfoo",
                "artifact": "build/libfoo.so",
                "snapshot": "libfoo.abicheck.json",
                "sha256": declared_hash,
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
        # The "new" run's own real, self-consistent digest (as if this
        # release had never been tampered with).
        new_content_digest = build_manifest.compute_content_digest(existing_manifest)

        asset_src = tmp_path / "asset-src"
        asset_src.mkdir()
        (asset_src / "manifest.json").write_text(
            json.dumps(existing_manifest), encoding="utf-8"
        )
        # The corrupted bytes, not the ones the declared hash was computed
        # from.
        (asset_src / "libfoo.abicheck.json").write_text(
            json.dumps(corrupted_snapshot), encoding="utf-8"
        )
        asset_name = "abicheck-baseline-corrupted.tar"
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
                "NEW_PROFILE": existing_manifest["profile"],
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
        # Second-round regression (Codex review): the declared-vs-actual
        # validation added to recompute_content_digest_from_disk() itself
        # now catches this exact scenario even earlier/more precisely
        # (a self-inconsistent manifest.json), rather than falling through
        # to the generic "DIFFERENT content" digest-mismatch error --
        # still a hard failure either way, never a silent "safe retry".
        assert "self-inconsistent" in (result.stdout + result.stderr)
        assert "safe re-run" not in result.stdout


class TestUploadReleaseAssetRejectsGenuinelyDifferentContent:
    """The genuinely-different-content path -- both the existing manifest's
    declared digest AND its recomputed real digest agree with EACH OTHER
    (a self-consistent, uncorrupted asset), but that content is genuinely
    different from the fresh run's own content. Must still reach the
    original "DIFFERENT content" immutability error, not the newer
    self-inconsistent-manifest check (which is about declared-vs-actual
    disagreement WITHIN the existing asset, a different failure mode).
    """

    def _upload_step_script(self) -> str:
        data = _load(PUBLISH_BASELINE)
        steps = _steps(data["jobs"]["publish"])
        step = next(
            s for s in steps if s.get("name", "").startswith("Upload release asset")
        )
        return step["run"]

    @_WINDOWS_PYTHON3_SKIP
    @_JQ_REQUIRED
    def test_self_consistent_but_genuinely_different_content_is_rejected(
        self, tmp_path: Path
    ) -> None:
        import importlib.util
        import json
        import shutil
        import subprocess
        import tarfile
        import tempfile

        src_root = (
            Path(__file__).resolve().parents[1]
            / "actions"
            / "baseline"
            / "build_manifest.py"
        )
        module_dir = tmp_path / ".publish-baseline-src" / "actions" / "baseline"
        module_dir.mkdir(parents=True)
        shutil.copy(src_root, module_dir / "build_manifest.py")

        module_spec = importlib.util.spec_from_file_location(
            "test_build_manifest_module_genuinely_different", src_root
        )
        assert module_spec is not None and module_spec.loader is not None
        build_manifest = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(build_manifest)

        # The EXISTING asset's declared sha256 correctly matches its own
        # real bytes (self-consistent) -- but that content genuinely
        # differs from what the "new" run produced.
        existing_snapshot = {"schema_version": 9, "functions": [], "types": []}
        existing_hash = build_manifest.compute_snapshot_content_hash(existing_snapshot)
        existing_manifest = {
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
                    "sha256": existing_hash,
                }
            ],
        }

        new_snapshot = {
            "schema_version": 9,
            "functions": [{"name": "new_function"}],
            "types": [],
        }
        new_hash = build_manifest.compute_snapshot_content_hash(new_snapshot)
        new_manifest = {
            **existing_manifest,
            "artifacts": [{**existing_manifest["artifacts"][0], "sha256": new_hash}],
        }
        new_content_digest = build_manifest.compute_content_digest(new_manifest)

        asset_src = tmp_path / "asset-src"
        asset_src.mkdir()
        (asset_src / "manifest.json").write_text(
            json.dumps(existing_manifest), encoding="utf-8"
        )
        (asset_src / "libfoo.abicheck.json").write_text(
            json.dumps(existing_snapshot), encoding="utf-8"
        )
        asset_name = "abicheck-baseline-genuinely-different.tar"
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
                "NEW_PROFILE": existing_manifest["profile"],
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
        assert "DIFFERENT content" in (result.stdout + result.stderr)
        assert "self-inconsistent" not in (result.stdout + result.stderr)
        assert "safe re-run" not in result.stdout
