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

"""Behavioral tests for ``actions/resolve-baseline/run.sh`` (G30 P1.2,
ADR-047 §4/§6).

Covers the bash orchestration layer: input validation, archive extraction,
and each of ADR-047 §6's typed outcomes end-to-end through the real script
(``resolve_baseline.py`` + ``abicheck.buildsource.baseline_set``, unit-tested
in isolation in ``tests/test_baseline_set.py``).
"""

from __future__ import annotations

import json
import os
import subprocess
import tarfile
from pathlib import Path

import pytest

ACTION_DIR = Path(__file__).resolve().parents[1] / "actions" / "resolve-baseline"
RUN_SH = ACTION_DIR / "run.sh"

PROFILE = "linux-x86_64-gcc13-release"


def _bash_executable() -> str:
    """Resolve a real bash, bypassing Windows' WSL-launcher stub.

    See ``test_action_run_sh_helpers._bash_executable`` for the full
    rationale.
    """
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


def _run_action(
    env_extra: dict[str, str], cwd: Path
) -> tuple[subprocess.CompletedProcess[str], dict[str, str]]:
    """Invoke the real script end-to-end with a GITHUB_OUTPUT file."""
    github_output = cwd / "github_output"
    github_output.write_text("")
    # Strip any inherited INPUT_* from the host/CI environment before
    # overlaying env_extra: the input-validation negative tests (e.g.
    # test_missing_baseline_path_fails) assert failure purely on an input's
    # *absence*, and a leaked INPUT_* value would make those tests pass for
    # the wrong reason (or stop failing) instead of exercising the real
    # guard (CodeRabbit review).
    base_env = {k: v for k, v in os.environ.items() if not k.startswith("INPUT_")}
    env = {
        **base_env,
        "GITHUB_OUTPUT": str(github_output),
        "ACTION_PATH": str(ACTION_DIR),
        **env_extra,
    }
    result = subprocess.run(
        [_bash_executable(), str(RUN_SH)],
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
        check=False,
    )
    outputs = _parse_kv_file(github_output)
    return result, outputs


def _parse_kv_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k] = v
    return out


def _write_manifest(
    baseline_dir: Path,
    *,
    manifest_version: int | None = 1,
    profile: str = PROFILE,
    fact_set: dict | None = None,
    artifacts: list[dict] | None = None,
) -> None:
    baseline_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "manifest_version": manifest_version,
        "project_ref": "v1.0.0",
        "profile": profile,
        "snapshot_schema": 9,
        "fact_set": fact_set,
        "artifacts": artifacts if artifacts is not None else [],
    }
    (baseline_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )


def _target_artifact(name: str) -> dict:
    # No real "sha256" -- these shell-level tests aren't about digest
    # verification (that's covered at the unit level in
    # tests/test_baseline_set.py), and an empty/absent recorded digest makes
    # resolve_target()'s digest check a no-op rather than a false mismatch
    # against whatever placeholder snapshot content each test happens to
    # write.
    return {
        "library": name,
        "artifact": f"build/{name}.so",
        "snapshot": f"{name}.abicheck.json",
        "sha256": "",
    }


@pytest.mark.skipif(
    not RUN_SH.is_file(), reason="actions/resolve-baseline/run.sh not found"
)
class TestInputValidation:
    def test_missing_baseline_path_fails(self, tmp_path: Path) -> None:
        result, _ = _run_action(
            {"INPUT_CHANNEL": "accepted-main", "INPUT_PROFILE": PROFILE}, tmp_path
        )
        assert result.returncode != 0

    def test_missing_channel_fails(self, tmp_path: Path) -> None:
        result, _ = _run_action(
            {"INPUT_BASELINE_PATH": str(tmp_path), "INPUT_PROFILE": PROFILE}, tmp_path
        )
        assert result.returncode != 0

    def test_unknown_kind_fails(self, tmp_path: Path) -> None:
        result, _ = _run_action(
            {
                "INPUT_BASELINE_PATH": str(tmp_path),
                "INPUT_CHANNEL": "accepted-main",
                "INPUT_PROFILE": PROFILE,
                "INPUT_KIND": "bogus",
            },
            tmp_path,
        )
        assert result.returncode == 1
        assert "not recognized" in result.stdout

    def test_bundle_kind_without_bundle_name_fails(self, tmp_path: Path) -> None:
        result, _ = _run_action(
            {
                "INPUT_BASELINE_PATH": str(tmp_path),
                "INPUT_CHANNEL": "accepted-main",
                "INPUT_PROFILE": PROFILE,
                "INPUT_KIND": "bundle",
            },
            tmp_path,
        )
        assert result.returncode == 1
        assert "bundle input is required" in result.stdout

    def test_channel_with_newline_fails(self, tmp_path: Path) -> None:
        result, _ = _run_action(
            {
                "INPUT_BASELINE_PATH": str(tmp_path),
                "INPUT_CHANNEL": "accepted-main\nmalicious-key=evil",
                "INPUT_PROFILE": PROFILE,
                "INPUT_TARGET": "libpvxs",
            },
            tmp_path,
        )
        assert result.returncode == 1
        assert "must not contain a newline" in result.stdout

    def test_baseline_path_with_newline_fails(self, tmp_path: Path) -> None:
        result, _ = _run_action(
            {
                "INPUT_BASELINE_PATH": f"{tmp_path}\nmalicious-key=evil",
                "INPUT_CHANNEL": "accepted-main",
                "INPUT_PROFILE": PROFILE,
                "INPUT_TARGET": "libpvxs",
            },
            tmp_path,
        )
        assert result.returncode == 1
        assert "must not contain a newline" in result.stdout


@pytest.mark.skipif(
    not RUN_SH.is_file(), reason="actions/resolve-baseline/run.sh not found"
)
class TestFailureTaxonomy:
    """One test per ADR-047 §6 failure-taxonomy row, driven end-to-end."""

    def test_not_found_required_hard_fails(self, tmp_path: Path) -> None:
        missing_dir = tmp_path / "no-such-baseline"
        result, outputs = _run_action(
            {
                "INPUT_BASELINE_PATH": str(missing_dir),
                "INPUT_CHANNEL": "release-contract",
                "INPUT_TARGET": "libpvxs",
                "INPUT_PROFILE": PROFILE,
                "INPUT_REQUIRED": "true",
            },
            tmp_path,
        )
        assert result.returncode == 1
        assert outputs.get("outcome") == "not_found"
        assert outputs.get("bootstrap") == "false"

    def test_not_found_bootstrap_is_non_fatal(self, tmp_path: Path) -> None:
        missing_dir = tmp_path / "no-such-baseline"
        result, outputs = _run_action(
            {
                "INPUT_BASELINE_PATH": str(missing_dir),
                "INPUT_CHANNEL": "release-contract",
                "INPUT_TARGET": "libpvxs",
                "INPUT_PROFILE": PROFILE,
                "INPUT_REQUIRED": "false",
            },
            tmp_path,
        )
        assert result.returncode == 0
        assert outputs.get("outcome") == "not_found"
        assert outputs.get("bootstrap") == "true"

    def test_ambiguous_target_missing_from_set(self, tmp_path: Path) -> None:
        baseline_dir = tmp_path / "baseline"
        _write_manifest(baseline_dir, artifacts=[_target_artifact("libpvxsIoc")])
        result, outputs = _run_action(
            {
                "INPUT_BASELINE_PATH": str(baseline_dir),
                "INPUT_CHANNEL": "accepted-main",
                "INPUT_TARGET": "libpvxs",
                "INPUT_PROFILE": PROFILE,
            },
            tmp_path,
        )
        assert result.returncode == 1
        assert outputs.get("outcome") == "ambiguous"

    def test_wrong_profile(self, tmp_path: Path) -> None:
        baseline_dir = tmp_path / "baseline"
        _write_manifest(
            baseline_dir,
            profile="windows-x86_64-msvc-release",
            artifacts=[_target_artifact("libpvxs")],
        )
        result, outputs = _run_action(
            {
                "INPUT_BASELINE_PATH": str(baseline_dir),
                "INPUT_CHANNEL": "accepted-main",
                "INPUT_TARGET": "libpvxs",
                "INPUT_PROFILE": PROFILE,
            },
            tmp_path,
        )
        assert result.returncode == 1
        assert outputs.get("outcome") == "wrong_profile"

    def test_stale_schema(self, tmp_path: Path) -> None:
        baseline_dir = tmp_path / "baseline"
        _write_manifest(
            baseline_dir, manifest_version=999, artifacts=[_target_artifact("libpvxs")]
        )
        result, outputs = _run_action(
            {
                "INPUT_BASELINE_PATH": str(baseline_dir),
                "INPUT_CHANNEL": "accepted-main",
                "INPUT_TARGET": "libpvxs",
                "INPUT_PROFILE": PROFILE,
            },
            tmp_path,
        )
        assert result.returncode == 1
        assert outputs.get("outcome") == "stale_schema"

    def test_corrupt_manifest_is_a_typed_outcome_not_a_traceback(
        self, tmp_path: Path
    ) -> None:
        # A manifest.json that exists but isn't valid JSON (partial
        # download, hand edit) must still produce the Action's typed
        # outcome/message contract, not let a Python ValueError escape
        # resolve_baseline.py unhandled (Codex review).
        baseline_dir = tmp_path / "baseline"
        baseline_dir.mkdir(parents=True)
        (baseline_dir / "manifest.json").write_text("{not valid json", encoding="utf-8")
        result, outputs = _run_action(
            {
                "INPUT_BASELINE_PATH": str(baseline_dir),
                "INPUT_CHANNEL": "accepted-main",
                "INPUT_TARGET": "libpvxs",
                "INPUT_PROFILE": PROFILE,
            },
            tmp_path,
        )
        assert result.returncode == 1
        assert outputs.get("outcome") == "stale_schema"
        assert outputs.get("message")

    def test_incompatible_evidence(self, tmp_path: Path) -> None:
        baseline_dir = tmp_path / "baseline"
        _write_manifest(
            baseline_dir,
            fact_set={
                "name": "pvxs",
                "version": 3,
                "producer": "wrapper",
                "producer_version": "0.5.0",
            },
            artifacts=[_target_artifact("libpvxs")],
        )
        (baseline_dir / "libpvxs.abicheck.json").write_text("{}", encoding="utf-8")
        build_output = tmp_path / "build-output.json"
        build_output.write_text(
            json.dumps(
                {
                    "evidence_producer": {
                        "kind": "replay",
                        "tool": "abicheck",
                        "version": "0.5.0",
                    }
                }
            ),
            encoding="utf-8",
        )
        result, outputs = _run_action(
            {
                "INPUT_BASELINE_PATH": str(baseline_dir),
                "INPUT_CHANNEL": "accepted-main",
                "INPUT_TARGET": "libpvxs",
                "INPUT_PROFILE": PROFILE,
                "INPUT_CANDIDATE_BUILD_OUTPUT": str(build_output),
            },
            tmp_path,
        )
        assert result.returncode == 1
        assert outputs.get("outcome") == "incompatible_evidence"

    def test_resolved(self, tmp_path: Path) -> None:
        baseline_dir = tmp_path / "baseline"
        _write_manifest(baseline_dir, artifacts=[_target_artifact("libpvxs")])
        (baseline_dir / "libpvxs.abicheck.json").write_text("{}", encoding="utf-8")
        result, outputs = _run_action(
            {
                "INPUT_BASELINE_PATH": str(baseline_dir),
                "INPUT_CHANNEL": "accepted-main",
                "INPUT_TARGET": "libpvxs",
                "INPUT_PROFILE": PROFILE,
            },
            tmp_path,
        )
        assert result.returncode == 0
        assert outputs.get("outcome") == "resolved"
        assert outputs.get("channel") == "accepted-main"
        assert outputs.get("snapshot-path") == str(
            baseline_dir / "libpvxs.abicheck.json"
        )


@pytest.mark.skipif(
    not RUN_SH.is_file(), reason="actions/resolve-baseline/run.sh not found"
)
class TestBundleResolution:
    def test_bundle_returns_binaries_not_snapshots(self, tmp_path: Path) -> None:
        baseline_dir = tmp_path / "baseline"
        _write_manifest(
            baseline_dir,
            artifacts=[
                {**_target_artifact("libpvxs"), "binary": "binaries/libpvxs.so.1.5"},
                {
                    **_target_artifact("libpvxsIoc"),
                    "binary": "binaries/libpvxsIoc.so.1.5",
                },
            ],
        )
        binaries_dir = baseline_dir / "binaries"
        binaries_dir.mkdir()
        (binaries_dir / "libpvxs.so.1.5").write_bytes(b"\x7fELF-fake")
        (binaries_dir / "libpvxsIoc.so.1.5").write_bytes(b"\x7fELF-fake")

        result, outputs = _run_action(
            {
                "INPUT_BASELINE_PATH": str(baseline_dir),
                "INPUT_CHANNEL": "accepted-main",
                "INPUT_KIND": "bundle",
                "INPUT_BUNDLE": "pvxs-release",
                "INPUT_BUNDLE_MEMBERS": json.dumps(["libpvxs", "libpvxsIoc"]),
                "INPUT_PROFILE": PROFILE,
            },
            tmp_path,
        )
        assert result.returncode == 0
        assert outputs.get("outcome") == "resolved"
        assert outputs.get("snapshot-path") == ""
        assert outputs.get("binaries-dir") == str(binaries_dir)
        binary_paths = json.loads(outputs["binary-paths"])
        assert binary_paths == {
            "libpvxs": str(binaries_dir / "libpvxs.so.1.5"),
            "libpvxsIoc": str(binaries_dir / "libpvxsIoc.so.1.5"),
        }

    def test_bundle_ambiguous_when_member_binary_not_staged(
        self, tmp_path: Path
    ) -> None:
        baseline_dir = tmp_path / "baseline"
        # Legacy (pre-P1.6) manifest -- snapshots only, no staged binaries.
        _write_manifest(
            baseline_dir,
            artifacts=[_target_artifact("libpvxs"), _target_artifact("libpvxsIoc")],
        )
        result, outputs = _run_action(
            {
                "INPUT_BASELINE_PATH": str(baseline_dir),
                "INPUT_CHANNEL": "accepted-main",
                "INPUT_KIND": "bundle",
                "INPUT_BUNDLE": "pvxs-release",
                "INPUT_BUNDLE_MEMBERS": json.dumps(["libpvxs", "libpvxsIoc"]),
                "INPUT_PROFILE": PROFILE,
            },
            tmp_path,
        )
        assert result.returncode == 1
        assert outputs.get("outcome") == "ambiguous"


@pytest.mark.skipif(
    not RUN_SH.is_file(), reason="actions/resolve-baseline/run.sh not found"
)
class TestArchiveExtraction:
    def test_tar_gz_archive_is_extracted_and_resolved(self, tmp_path: Path) -> None:
        baseline_dir = tmp_path / "baseline-src"
        _write_manifest(baseline_dir, artifacts=[_target_artifact("libpvxs")])
        (baseline_dir / "libpvxs.abicheck.json").write_text("{}", encoding="utf-8")

        archive_path = tmp_path / "baseline.tar.gz"
        with tarfile.open(archive_path, "w:gz") as tf:
            tf.add(baseline_dir, arcname=".")

        result, outputs = _run_action(
            {
                "INPUT_BASELINE_PATH": str(archive_path),
                "INPUT_CHANNEL": "release-contract",
                "INPUT_TARGET": "libpvxs",
                "INPUT_PROFILE": PROFILE,
            },
            tmp_path,
        )
        assert result.returncode == 0
        assert outputs.get("outcome") == "resolved"
        assert Path(outputs["snapshot-path"]).is_file()

    def test_tar_gz_archive_with_nested_directory_is_descended_into(
        self, tmp_path: Path
    ) -> None:
        # An archive built by tarring the profile-named directory itself
        # (rather than its contents) nests manifest.json one level down --
        # run.sh must find it there, not just at the extraction root.
        baseline_dir = tmp_path / "abicheck-baseline-linux-x86_64-gcc13-release"
        _write_manifest(baseline_dir, artifacts=[_target_artifact("libpvxs")])
        (baseline_dir / "libpvxs.abicheck.json").write_text("{}", encoding="utf-8")

        archive_path = tmp_path / "baseline-nested.tar.gz"
        with tarfile.open(archive_path, "w:gz") as tf:
            tf.add(baseline_dir, arcname=baseline_dir.name)

        result, outputs = _run_action(
            {
                "INPUT_BASELINE_PATH": str(archive_path),
                "INPUT_CHANNEL": "release-contract",
                "INPUT_TARGET": "libpvxs",
                "INPUT_PROFILE": PROFILE,
            },
            tmp_path,
        )
        assert result.returncode == 0
        assert outputs.get("outcome") == "resolved"

    def test_unrecognized_archive_extension_fails(self, tmp_path: Path) -> None:
        bogus = tmp_path / "baseline.zip"
        bogus.write_bytes(b"PK\x03\x04not-really-a-zip")
        result, _ = _run_action(
            {
                "INPUT_BASELINE_PATH": str(bogus),
                "INPUT_CHANNEL": "release-contract",
                "INPUT_TARGET": "libpvxs",
                "INPUT_PROFILE": PROFILE,
            },
            tmp_path,
        )
        assert result.returncode == 1
        assert "not a recognized archive" in result.stdout

    def test_archive_with_no_manifest_anywhere_hard_fails_even_when_not_required(
        self, tmp_path: Path
    ) -> None:
        # A malformed archive (no manifest.json at its root or in a single
        # subdirectory) must never be treated as an ordinary "no baseline
        # published yet" bootstrap, even under required: false -- the
        # archive WAS present, just unusable, which is a real extraction
        # failure distinct from nothing having been staged at all (Codex
        # review).
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        (empty_dir / "readme.txt").write_text("not a baseline set", encoding="utf-8")
        archive_path = tmp_path / "malformed.tar.gz"
        with tarfile.open(archive_path, "w:gz") as tf:
            tf.add(empty_dir, arcname=".")

        result, outputs = _run_action(
            {
                "INPUT_BASELINE_PATH": str(archive_path),
                "INPUT_CHANNEL": "release-contract",
                "INPUT_TARGET": "libpvxs",
                "INPUT_PROFILE": PROFILE,
                "INPUT_REQUIRED": "false",
            },
            tmp_path,
        )
        assert result.returncode == 1
        assert outputs.get("outcome") != "not_found"
        assert "malformed" in result.stdout
        # Must carry the same typed outputs every other resolution failure
        # does -- a caller running under continue-on-error or inspecting
        # this Action's outputs must be able to distinguish "malformed
        # archive" from an unrelated input/runner failure, not see no
        # outputs at all (Codex review, second round).
        assert outputs.get("outcome") == "ambiguous"
        assert outputs.get("bootstrap") == "false"
        assert outputs.get("channel") == "release-contract"
        assert "malformed" in outputs.get("message", "")

    def test_archive_with_symlink_hard_fails_with_typed_outputs(
        self, tmp_path: Path
    ) -> None:
        # A symlink-containing archive is a malformed baseline-set, the same
        # class of failure as no-manifest/ambiguous-subdirectories above --
        # it must carry the same typed outcome/bootstrap/message outputs, not
        # fail before any outputs are written at all (Codex review).
        baseline_dir = tmp_path / "baseline-src"
        _write_manifest(baseline_dir, artifacts=[_target_artifact("libpvxs")])
        (baseline_dir / "libpvxs.abicheck.json").write_text("{}", encoding="utf-8")
        (baseline_dir / "evil-link").symlink_to(baseline_dir / "manifest.json")

        archive_path = tmp_path / "baseline-symlink.tar.gz"
        with tarfile.open(archive_path, "w:gz") as tf:
            tf.add(baseline_dir, arcname=".")

        result, outputs = _run_action(
            {
                "INPUT_BASELINE_PATH": str(archive_path),
                "INPUT_CHANNEL": "release-contract",
                "INPUT_TARGET": "libpvxs",
                "INPUT_PROFILE": PROFILE,
                "INPUT_REQUIRED": "false",
            },
            tmp_path,
        )
        assert result.returncode == 1
        assert "symlink" in result.stdout
        assert outputs.get("outcome") == "ambiguous"
        assert outputs.get("bootstrap") == "false"
        assert outputs.get("channel") == "release-contract"
        assert "symlink" in outputs.get("message", "")

    def test_archive_with_ambiguous_subdirectories_hard_fails(
        self, tmp_path: Path
    ) -> None:
        root = tmp_path / "ambiguous-root"
        (root / "a").mkdir(parents=True)
        (root / "b").mkdir(parents=True)
        archive_path = tmp_path / "ambiguous.tar.gz"
        with tarfile.open(archive_path, "w:gz") as tf:
            tf.add(root, arcname=".")

        result, _ = _run_action(
            {
                "INPUT_BASELINE_PATH": str(archive_path),
                "INPUT_CHANNEL": "release-contract",
                "INPUT_TARGET": "libpvxs",
                "INPUT_PROFILE": PROFILE,
                "INPUT_REQUIRED": "false",
            },
            tmp_path,
        )
        assert result.returncode == 1
        assert "malformed" in result.stdout
