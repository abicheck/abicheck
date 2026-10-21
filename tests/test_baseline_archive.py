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

"""Unit tests for ``abicheck.workflows.baseline_archive``: the one owner of
"verify a baseline-set archive's bytes, then extract it" that both the root
Action and ``actions/resolve-baseline`` call.

The shell-level behaviour (what each Action does with these refusals) is
covered in ``test_action_baseline_archive_decision.py`` and
``test_action_resolve_baseline_archive_digest.py``; these tests pin the
helper's own contract, and in particular that a digest mismatch is detected
before a single member reaches disk.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tarfile
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.frontends.action.cli import action_cli
from abicheck.workflows import baseline_archive as ba

PROFILE = "linux-x86_64-gcc13-release"


def _manifest() -> dict:
    return {
        "manifest_version": 1,
        "project_ref": "v1.0.0",
        "profile": PROFILE,
        "snapshot_schema": 9,
        "fact_set": None,
        "baseline_generation": None,
        "artifacts": [
            {
                "library": "libfoo",
                "artifact": "build/libfoo.so",
                "snapshot": "libfoo.abicheck.json",
                "sha256": "",
            }
        ],
    }


def _set_dir(root: Path, snapshot: str = "{}") -> Path:
    src = root / "set"
    src.mkdir(parents=True)
    (src / "manifest.json").write_text(json.dumps(_manifest()), encoding="utf-8")
    (src / "libfoo.abicheck.json").write_text(snapshot, encoding="utf-8")
    return src


def _pack(src: Path, archive: Path) -> Path:
    with tarfile.open(archive, "w") as tf:
        tf.add(src, arcname=".")
    return archive


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _dest(tmp_path: Path) -> Path:
    dest = tmp_path / "extracted"
    dest.mkdir(parents=True)
    return dest


class TestNormalizeExpectedDigest:
    HEX = "ab" * 32

    @pytest.mark.parametrize(
        "value",
        [
            f"sha256:{HEX}",
            HEX,
            f"SHA256:{HEX.upper()}",
            f"  sha256:{HEX}  ",
        ],
    )
    def test_accepts_prefixed_or_bare_hex_in_any_case(self, value: str) -> None:
        assert ba.normalize_expected_digest(value) == self.HEX

    @pytest.mark.parametrize(
        "value",
        [
            f"md5:{HEX[:32]}",
            f"sha512:{HEX}",
            f"sha256:{HEX[:-1]}",
            f"sha256:{HEX}0",
            f"sha256:{'g' * 64}",
            "",
            "latest",
        ],
    )
    def test_rejects_anything_that_is_not_a_sha256_digest(self, value: str) -> None:
        with pytest.raises(ba.BaselineArchiveError) as exc:
            ba.normalize_expected_digest(value)
        assert exc.value.code == ba.CODE_USAGE
        assert exc.value.exit_status == ba.EXIT_USAGE


class TestPrepareVerifiesBeforeExtracting:
    def test_mismatched_pin_refuses_and_writes_no_member(self, tmp_path: Path) -> None:
        archive = _pack(_set_dir(tmp_path), tmp_path / "set.tar")
        dest = _dest(tmp_path)
        with pytest.raises(ba.BaselineArchiveError) as exc:
            ba.prepare_baseline_set(
                archive,
                dest,
                archive_format="tar",
                expected_digest="0" * 64,
            )
        assert exc.value.code == ba.CODE_DIGEST_MISMATCH
        assert exc.value.exit_status == ba.EXIT_DIGEST_MISMATCH
        assert "does not match the pinned digest" in exc.value.message
        # The digest check runs before extraction, so nothing was written.
        assert list(dest.iterdir()) == []

    def test_matching_pin_extracts_and_reports_the_verified_digest(
        self, tmp_path: Path
    ) -> None:
        archive = _pack(_set_dir(tmp_path), tmp_path / "set.tar")
        digest = _sha256(archive)
        prepared = ba.prepare_baseline_set(
            archive,
            _dest(tmp_path),
            archive_format="tar",
            expected_digest=f"sha256:{digest}",
        )
        assert prepared.verified is True
        assert prepared.archive_digest == f"sha256:{digest}"
        assert (prepared.manifest_root / "manifest.json").is_file()

    def test_no_pin_extracts_and_reports_unverified(self, tmp_path: Path) -> None:
        archive = _pack(_set_dir(tmp_path), tmp_path / "set.tar")
        prepared = ba.prepare_baseline_set(
            archive, _dest(tmp_path), archive_format="tar"
        )
        assert prepared.verified is False
        assert prepared.archive_digest == f"sha256:{_sha256(archive)}"

    def test_wrapped_archive_narrows_to_its_single_subdirectory(
        self, tmp_path: Path
    ) -> None:
        wrapper = tmp_path / "wrapper-src"
        wrapper.mkdir()
        _set_dir(wrapper)
        archive = _pack(wrapper, tmp_path / "wrapped.tar")
        dest = _dest(tmp_path)
        prepared = ba.prepare_baseline_set(archive, dest, archive_format="tar")
        assert prepared.manifest_root == dest / "set"

    def test_missing_manifest_is_reported_as_the_extracted_root_not_refused(
        self, tmp_path: Path
    ) -> None:
        # The caller decides what a manifest-less archive means (resolve-baseline
        # calls it ambiguous; the root Action lets resolve_target report it), so
        # the helper must not decide for it.
        src = tmp_path / "bare-src"
        src.mkdir()
        (src / "libfoo.abicheck.json").write_text("{}", encoding="utf-8")
        archive = _pack(src, tmp_path / "bare.tar")
        dest = _dest(tmp_path)
        prepared = ba.prepare_baseline_set(archive, dest, archive_format="tar")
        assert prepared.manifest_root == dest
        assert not (prepared.manifest_root / "manifest.json").exists()


class TestTamperedRepack:
    """A consistent repack: the snapshot changed and the archive was rebuilt, so
    every in-archive check still passes. Only a digest pinned outside the
    archive tells the two apart."""

    def test_repack_is_accepted_without_a_pin_and_refused_under_the_old_pin(
        self, tmp_path: Path
    ) -> None:
        original = _pack(
            _set_dir(tmp_path / "v1", '{"version": 1}'), tmp_path / "v1.tar"
        )
        repacked = _pack(
            _set_dir(tmp_path / "v2", '{"version": 2}'), tmp_path / "v2.tar"
        )
        original_digest = _sha256(original)
        repacked_digest = _sha256(repacked)
        assert original_digest != repacked_digest

        # No pin: consistent repack passes, as an in-archive check always would.
        unpinned = ba.prepare_baseline_set(
            repacked, _dest(tmp_path / "a"), archive_format="tar"
        )
        assert unpinned.verified is False
        assert (unpinned.manifest_root / "libfoo.abicheck.json").read_text(
            encoding="utf-8"
        ) == '{"version": 2}'

        # Pinned to the digest the producer reported: the repack fails closed.
        with pytest.raises(ba.BaselineArchiveError) as exc:
            ba.prepare_baseline_set(
                repacked,
                _dest(tmp_path / "b"),
                archive_format="tar",
                expected_digest=original_digest,
            )
        assert exc.value.code == ba.CODE_DIGEST_MISMATCH

        # Pinned to the repacked archive's own digest: it passes, verified.
        verified = ba.prepare_baseline_set(
            repacked,
            _dest(tmp_path / "c"),
            archive_format="tar",
            expected_digest=repacked_digest,
        )
        assert verified.verified is True


class TestSymlinkRefusal:
    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="creating a symlink during extraction needs privileges on Windows",
    )
    def test_in_root_symlink_is_refused_after_extraction(self, tmp_path: Path) -> None:
        # An in-root symlink passes TarExtractor's escape check, so only the
        # helper's own post-extraction rule rejects it.
        archive = tmp_path / "linked.tar"
        with tarfile.open(archive, "w") as tf:
            info = tarfile.TarInfo("evil-link")
            info.type = tarfile.SYMTYPE
            info.linkname = "manifest.json"
            tf.addfile(info)
        with pytest.raises(ba.BaselineArchiveError) as exc:
            ba.prepare_baseline_set(archive, _dest(tmp_path), archive_format="tar")
        assert exc.value.code == ba.CODE_MALFORMED
        assert "symlink" in exc.value.message
        assert "evil-link" in exc.value.message


class TestCommandLine:
    """The Actions reach the helper through this command, so its exit codes and
    record format are the contract the shell scripts parse."""

    @staticmethod
    def _invoke(*args: str) -> tuple[int, list[str]]:
        result = CliRunner().invoke(
            action_cli, ["prepare-baseline-set", *args], catch_exceptions=False
        )
        return result.exit_code, result.output.splitlines()

    def test_success_prints_the_three_records(self, tmp_path: Path) -> None:
        archive = _pack(_set_dir(tmp_path), tmp_path / "set.tar")
        digest = _sha256(archive)
        status, out = self._invoke(
            "--archive",
            str(archive),
            "--dest",
            str(_dest(tmp_path)),
            "--format",
            "tar",
            "--expected-digest",
            digest,
        )
        assert status == ba.EXIT_OK
        assert out[0].startswith("manifest-root=")
        assert out[1] == f"archive-digest=sha256:{digest}"
        assert out[2] == "verified=true"

    def test_digest_mismatch_exit_status_and_single_line_message(
        self, tmp_path: Path
    ) -> None:
        archive = _pack(_set_dir(tmp_path), tmp_path / "set.tar")
        status, out = self._invoke(
            "--archive",
            str(archive),
            "--dest",
            str(_dest(tmp_path)),
            "--format",
            "tar",
            "--expected-digest",
            "0" * 64,
        )
        assert status == ba.EXIT_DIGEST_MISMATCH
        assert out[0] == "error-code=digest-mismatch"
        assert out[1].startswith("message=")
        assert len(out) == 2

    def test_malformed_pin_is_a_usage_exit(self, tmp_path: Path) -> None:
        archive = _pack(_set_dir(tmp_path), tmp_path / "set.tar")
        status, out = self._invoke(
            "--archive",
            str(archive),
            "--dest",
            str(_dest(tmp_path)),
            "--format",
            "tar",
            "--expected-digest",
            "md5:" + "0" * 32,
        )
        assert status == ba.EXIT_USAGE
        assert out[0] == "error-code=usage"

    def test_missing_extraction_directory_is_a_malformed_refusal(
        self, tmp_path: Path
    ) -> None:
        archive = _pack(_set_dir(tmp_path), tmp_path / "set.tar")
        status, out = self._invoke(
            "--archive",
            str(archive),
            "--dest",
            str(tmp_path / "absent"),
            "--format",
            "tar",
        )
        assert status == ba.EXIT_MALFORMED
        assert out[0] == "error-code=malformed"
        assert "does not exist" in out[1]
