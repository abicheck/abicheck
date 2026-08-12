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

"""Behavioral tests for publish-baseline.yml's "Upload release asset" step's
fact_set (evidence-producer identity) safe-retry guard -- split out of
test_publish_baseline_upload_step.py once this file's own class count
pushed the combined file past the AI-readiness 2000-line hard cap
(CLAUDE.md "Files that are large -- edit carefully"). Executes the real
step's embedded ``run:`` script verbatim (extracted via the small helper
set below, a deliberate near-duplicate of
test_publish_baseline_upload_step.py's own ``_load``/``_steps``/
``_bash_executable``/``_WINDOWS_PYTHON3_SKIP``/``_JQ_REQUIRED`` -- kept
independent rather than imported cross-module, so each test file stays
independently collectible) against a stubbed ``gh``.

compute_content_digest()/recompute_content_digest_from_disk() hash
artifacts[] content alone -- neither reads fact_set (the evidence-producer
identity a real consumer's incompatible-evidence check,
abicheck.buildsource.baseline_set._evidence_incompatibility(), depends on
independently of snapshot/binary content). This file's test confirms the
step validates fact_set by plain equality against the LOCAL manifest this
same job just built, before ever reaching the content-digest comparison
-- an existing asset with a DIFFERENT fact_set is never a safe retry
regardless of what the content digest says.
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


#: Mirrors test_publish_baseline_upload_step.py's identical convention.
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
#: real `jq` invocation -- GitHub-hosted runners ship `jq`, but a local/
#: self-hosted run without it would otherwise fail with an unrelated
#: "command not found" instead of skipping.
_JQ_REQUIRED = pytest.mark.skipif(
    shutil.which("jq") is None,
    reason="the extracted step pipes `gh release view --json assets` output through real `jq`",
)


class TestUploadReleaseAssetRejectsMismatchedFactSet:
    """Regression (Codex review, P2): compute_content_digest()/
    recompute_content_digest_from_disk() hash artifacts[] content alone --
    neither reads fact_set (actions/baseline/build_manifest.py's own
    compute_content_digest() docstring: "deliberately excludes every other
    manifest field"). An existing asset published with a DIFFERENT (or
    edited/corrupted) fact_set than this run's own freshly-built manifest,
    but the SAME profile, project_ref, and library content, could
    therefore still reach a matching content digest and take the
    safe-retry no-op -- even though a real consumer's incompatible-evidence
    check (abicheck.buildsource.baseline_set._evidence_incompatibility())
    depends on fact_set.producer independently of snapshot/binary content.
    The step now validates fact_set by plain equality against the LOCAL
    manifest this same job just built, before ever reaching the
    content-digest comparison.
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
    def test_different_fact_set_is_rejected_even_with_matching_content(
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
            "test_build_manifest_module_mismatched_fact_set", src_root
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
        # Same profile, project_ref, and library content as the fresh run
        # -- every OTHER guard would classify this as a safe retry -- but
        # a DIFFERENT fact_set.producer (as if the existing asset's
        # manifest.json was hand-edited, or published from a differently-
        # configured evidence collector).
        existing_manifest = {
            "manifest_version": 1,
            "project_ref": "v1.0.0",
            "profile": "linux-x86_64-gcc13-release",
            "snapshot_schema": None,
            "fact_set": {"producer": "abicheck-cc-clang-extractor"},
            "artifacts": artifacts,
        }
        new_manifest = {
            **existing_manifest,
            "fact_set": {"producer": "abicheck-clang-plugin"},
        }
        new_content_digest = build_manifest.compute_content_digest(new_manifest)
        new_manifest_path = tmp_path / "new-manifest.json"
        new_manifest_path.write_text(json.dumps(new_manifest), encoding="utf-8")

        asset_src = tmp_path / "asset-src"
        asset_src.mkdir()
        (asset_src / "manifest.json").write_text(
            json.dumps(existing_manifest), encoding="utf-8"
        )
        (asset_src / "libfoo.abicheck.json").write_text(
            json.dumps(snapshot), encoding="utf-8"
        )
        asset_name = "abicheck-baseline-mismatched-fact-set.tar"
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
                "NEW_MANIFEST_PATH": str(new_manifest_path),
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
        assert "DIFFERENT fact_set" in (result.stdout + result.stderr)
        assert "safe re-run" not in result.stdout
