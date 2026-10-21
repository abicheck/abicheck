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

"""``actions/resolve-baseline``'s ``expected-archive-digest`` pin, end to end
through the real ``run.sh``.

``resolve-baseline`` takes an already-staged ``baseline-path`` rather than
looking anything up in a release, so its decision table is over what sits at
that path (nothing, a baseline-set archive, a file that is not an archive, a
directory) crossed with the pin (none, correct, wrong, malformed). The root
Action's table, which has the release-asset dimension, is in
``test_action_baseline_archive_decision.py``.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import tarfile
from pathlib import Path

import pytest
from test_action_resolve_baseline import (
    PROFILE,
    _run_action,
    _target_artifact,
    _write_manifest,
)

TARGET = "libpvxs"


def _archive(root: Path, snapshot: str, name: str = "baseline.tar.gz") -> Path:
    """A baseline-set archive whose snapshot is *snapshot*. The manifest records
    no snapshot digest, so every in-archive check passes for any content."""
    src = root / "baseline-src"
    _write_manifest(src, artifacts=[_target_artifact(TARGET)])
    (src / f"{TARGET}.abicheck.json").write_text(snapshot, encoding="utf-8")
    archive = root / name
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(src, arcname=".")
    return archive


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _cwd(tmp_path: Path, name: str) -> Path:
    """A fresh working directory for one run, so GITHUB_OUTPUT never collides."""
    cwd = tmp_path / name
    cwd.mkdir(parents=True)
    return cwd


def _env(baseline_path: Path, **extra: str) -> dict[str, str]:
    env = {
        "INPUT_BASELINE_PATH": str(baseline_path),
        "INPUT_CHANNEL": "release-contract",
        "INPUT_TARGET": TARGET,
        "INPUT_PROFILE": PROFILE,
    }
    env.update(extra)
    return env


def _combined(result: subprocess.CompletedProcess[str]) -> str:
    return result.stdout + result.stderr


class TestArchiveDigestDecisionTable:
    """Rows: what sits at baseline-path. Columns: the pin."""

    @pytest.mark.parametrize("pin", ["none", "correct", "wrong"])
    def test_absent_path_has_nothing_to_verify(self, tmp_path: Path, pin: str) -> None:
        missing = tmp_path / "not-staged.tar.gz"
        pin_value = {"none": "", "correct": "0" * 64, "wrong": "f" * 64}[pin]
        result, outputs = _run_action(
            _env(missing, INPUT_EXPECTED_ARCHIVE_DIGEST=pin_value), tmp_path
        )
        # not_found, never "verified": a pin with no bytes behind it is not a
        # pass, and it is not a silently ignored pin either.
        assert outputs.get("outcome") == "not_found", _combined(result)
        assert outputs.get("archive-digest", "") == ""
        assert outputs.get("archive-digest-verified") == "false"

    def test_archive_unpinned_resolves_and_reports_the_digest(
        self, tmp_path: Path
    ) -> None:
        archive = _archive(tmp_path, '{"version": 1}')
        result, outputs = _run_action(_env(archive), tmp_path)
        assert result.returncode == 0, _combined(result)
        assert outputs["outcome"] == "resolved"
        assert outputs["archive-digest"] == f"sha256:{_sha256(archive)}"
        assert outputs["archive-digest-verified"] == "false"

    def test_archive_with_correct_pin_resolves_verified(self, tmp_path: Path) -> None:
        archive = _archive(tmp_path, '{"version": 1}')
        result, outputs = _run_action(
            _env(archive, INPUT_EXPECTED_ARCHIVE_DIGEST=f"sha256:{_sha256(archive)}"),
            tmp_path,
        )
        assert result.returncode == 0, _combined(result)
        assert outputs["outcome"] == "resolved"
        assert outputs["archive-digest-verified"] == "true"

    def test_archive_with_wrong_pin_is_ambiguous_and_extracts_nothing(
        self, tmp_path: Path
    ) -> None:
        archive = _archive(tmp_path, '{"version": 1}')
        result, outputs = _run_action(
            _env(archive, INPUT_EXPECTED_ARCHIVE_DIGEST="0" * 64), tmp_path
        )
        assert result.returncode != 0, _combined(result)
        assert outputs["outcome"] == "ambiguous"
        assert "does not match the pinned digest" in outputs["message"]
        assert outputs["snapshot-path"] == ""
        assert outputs["archive-digest-verified"] == "false"

    def test_non_archive_file_is_ambiguous_whatever_the_pin(
        self, tmp_path: Path
    ) -> None:
        not_archive = tmp_path / "baseline.json"
        not_archive.write_text("{}", encoding="utf-8")
        result, outputs = _run_action(
            _env(not_archive, INPUT_EXPECTED_ARCHIVE_DIGEST="0" * 64), tmp_path
        )
        assert result.returncode != 0, _combined(result)
        assert outputs["outcome"] == "ambiguous"
        assert "not a recognized archive" in outputs["message"]

    def test_directory_resolves_without_a_digest(self, tmp_path: Path) -> None:
        baseline_dir = tmp_path / "staged"
        _write_manifest(baseline_dir, artifacts=[_target_artifact(TARGET)])
        (baseline_dir / f"{TARGET}.abicheck.json").write_text("{}", encoding="utf-8")
        result, outputs = _run_action(_env(baseline_dir), tmp_path)
        assert result.returncode == 0, _combined(result)
        assert outputs["outcome"] == "resolved"
        assert outputs["archive-digest"] == ""
        assert outputs["archive-digest-verified"] == "false"

    @pytest.mark.parametrize("pinned_to", ["correct", "wrong"])
    def test_directory_with_a_pin_is_refused_rather_than_unverified(
        self, tmp_path: Path, pinned_to: str
    ) -> None:
        baseline_dir = tmp_path / "staged"
        _write_manifest(baseline_dir, artifacts=[_target_artifact(TARGET)])
        (baseline_dir / f"{TARGET}.abicheck.json").write_text("{}", encoding="utf-8")
        pin = "0" * 64 if pinned_to == "wrong" else "f" * 64
        result, _ = _run_action(
            _env(baseline_dir, INPUT_EXPECTED_ARCHIVE_DIGEST=pin), tmp_path
        )
        # A directory has no bytes to check, so a pin on it must not read as a
        # verified baseline. It is a usage error, not a resolution.
        assert result.returncode != 0, _combined(result)
        assert "is a directory, not an archive" in _combined(result)

    @pytest.mark.parametrize(
        "pin",
        ["sha256:not-a-digest", "md5:" + "0" * 32, "0" * 63],
    )
    def test_malformed_pin_is_an_input_error_for_every_kind_of_path(
        self, tmp_path: Path, pin: str
    ) -> None:
        archive = _archive(tmp_path, '{"version": 1}')
        result, outputs = _run_action(
            _env(archive, INPUT_EXPECTED_ARCHIVE_DIGEST=pin), tmp_path
        )
        assert result.returncode != 0, _combined(result)
        assert "is not a SHA-256 digest" in _combined(result)
        assert "outcome" not in outputs


class TestConsistentRepackThroughResolve:
    def test_repack_resolves_unpinned_and_is_refused_under_the_original_pin(
        self, tmp_path: Path
    ) -> None:
        original = _archive(tmp_path / "v1", '{"version": 1}')
        repacked = _archive(tmp_path / "v2", '{"version": 2}')
        original_digest = _sha256(original)

        # Unpinned: every in-archive check passes, so the repack is accepted.
        result, outputs = _run_action(_env(repacked), _cwd(tmp_path, "unpinned"))
        assert result.returncode == 0, _combined(result)
        assert outputs["outcome"] == "resolved"
        assert Path(outputs["snapshot-path"]).read_text(encoding="utf-8") == (
            '{"version": 2}'
        )

        # Pinned to the digest the producer reported: the repack fails closed.
        result, outputs = _run_action(
            _env(repacked, INPUT_EXPECTED_ARCHIVE_DIGEST=original_digest),
            _cwd(tmp_path, "pinned-old"),
        )
        assert result.returncode != 0, _combined(result)
        assert outputs["outcome"] == "ambiguous"
        assert "does not match the pinned digest" in outputs["message"]

        # Pinned to the repacked archive's own digest: it passes, verified.
        result, outputs = _run_action(
            _env(repacked, INPUT_EXPECTED_ARCHIVE_DIGEST=_sha256(repacked)),
            _cwd(tmp_path, "pinned-new"),
        )
        assert result.returncode == 0, _combined(result)
        assert outputs["archive-digest-verified"] == "true"
        assert Path(outputs["snapshot-path"]).read_text(encoding="utf-8") == (
            '{"version": 2}'
        )


class TestStagedDigestRoundTrip:
    def test_digest_recorded_by_the_producer_pins_the_consumer(
        self, tmp_path: Path
    ) -> None:
        # The producer's archive-sha256 output is the sha256 of the archive's bytes,
        # which is what the consumer's pin is checked against.
        archive = _archive(tmp_path, '{"version": 1}', name="staged.tar.gz")
        producer_output = _sha256(archive)
        result, outputs = _run_action(
            _env(archive, INPUT_EXPECTED_ARCHIVE_DIGEST=producer_output),
            tmp_path,
        )
        assert result.returncode == 0, _combined(result)
        assert outputs["archive-digest"] == f"sha256:{producer_output}"
        assert json.loads(
            Path(outputs["snapshot-path"]).read_text(encoding="utf-8")
        ) == {"version": 1}
