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

"""Unit tests for :mod:`abicheck.workflows.bundle_compare_operand` (CLI
cleanup phase two, PR I): the classifier that replaced the removed
``compare --old-bundle-facts`` flag.
"""

from __future__ import annotations

import io
import json
import tarfile
import zipfile
from pathlib import Path

from abicheck.bundle_facts import capture_bundle_facts
from abicheck.serialization import save_bundle_facts
from abicheck.workflows.bundle_compare_operand import (
    BundleCompareRequest,
    classify_bundle_compare_operands,
    looks_like_stored_bundle_facts,
)

_MARKER_JSON = json.dumps(
    {
        "artifact_type": "abicheck.bundle-facts",
        "schema_version": 2,
        "per_library_snapshots": {},
    }
)


class TestLooksLikeStoredBundleFacts:
    def test_plain_json_with_marker_is_stored(self, tmp_path: Path) -> None:
        p = tmp_path / "old.bundlefacts.json"
        p.write_text(_MARKER_JSON)
        assert looks_like_stored_bundle_facts(p) is True

    def test_pretty_printed_json_with_marker_is_stored(self, tmp_path: Path) -> None:
        p = tmp_path / "old.bundlefacts.json"
        p.write_text(json.dumps(json.loads(_MARKER_JSON), indent=2))
        assert looks_like_stored_bundle_facts(p) is True

    def test_ordinary_abisnapshot_json_is_not_stored(self, tmp_path: Path) -> None:
        p = tmp_path / "snap.json"
        p.write_text(json.dumps({"library": "libfoo.so", "functions": []}))
        assert looks_like_stored_bundle_facts(p) is False

    def test_empty_object_is_not_stored(self, tmp_path: Path) -> None:
        p = tmp_path / "empty.json"
        p.write_text("{}")
        assert looks_like_stored_bundle_facts(p) is False

    def test_malformed_json_is_not_stored(self, tmp_path: Path) -> None:
        p = tmp_path / "malformed.json"
        p.write_text("not json{")
        assert looks_like_stored_bundle_facts(p) is False

    def test_directory_is_not_stored(self, tmp_path: Path) -> None:
        d = tmp_path / "adir"
        d.mkdir()
        assert looks_like_stored_bundle_facts(d) is False

    def test_missing_path_is_not_stored(self, tmp_path: Path) -> None:
        assert looks_like_stored_bundle_facts(tmp_path / "nonexistent") is False

    def test_g40_archive_is_stored(self, tmp_path: Path) -> None:
        """Codex review, PR #1042: the G40 content-addressed zip archive
        format is a real, supported BundleFacts encoding -- it starts with
        a zip local-file-header magic, not JSON, so the plain marker scan
        alone would never recognize it; --old-bundle-facts used to route
        it to load_bundle_facts(format="auto"), which reads either shape,
        but without this classifier fix there is no way to reach that
        reader at all post-flag-removal."""
        facts = capture_bundle_facts({})
        archive_path = tmp_path / "old.bundlefacts.zip"
        save_bundle_facts(facts, archive_path, format="archive")
        assert looks_like_stored_bundle_facts(archive_path) is True

    def test_a_real_wheel_is_not_stored(self, tmp_path: Path) -> None:
        """A .whl is itself a zip file (same PK\\x03\\x04 magic as a G40
        archive) but is not a BundleFacts archive -- must not be
        misrecognized just because it opens as a zip."""
        whl_path = tmp_path / "fake_package-1.0-py3-none-any.whl"
        with zipfile.ZipFile(whl_path, "w") as zf:
            zf.writestr("fake_package/__init__.py", "")
            zf.writestr("fake_package-1.0.dist-info/METADATA", "Name: fake_package\n")
        assert looks_like_stored_bundle_facts(whl_path) is False

    def test_a_corrupted_zip_is_not_stored(self, tmp_path: Path) -> None:
        p = tmp_path / "broken.zip"
        p.write_bytes(b"PK\x03\x04" + b"\x00" * 32)
        assert looks_like_stored_bundle_facts(p) is False

    def test_package_archive_with_nested_marker_text_is_not_stored(
        self, tmp_path: Path
    ) -> None:
        """Codex review, PR #1042: a compressed release package
        (recognized by abicheck.package.is_package, e.g. a .tar.gz of
        shared libraries) must never be scanned for the artifact_type
        marker at all -- a nested member's own content coincidentally
        containing the marker text (e.g. a BundleFacts fixture bundled
        inside a test release archive) must not misclassify the whole
        package as a stored-facts document."""
        tar_path = tmp_path / "release.tar.gz"
        with tarfile.open(tar_path, "w:gz") as tf:
            data = _MARKER_JSON.encode()
            info = tarfile.TarInfo(name="nested/embedded_fixture.bundlefacts.json")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        assert looks_like_stored_bundle_facts(tar_path) is False

    def test_a_real_deb_package_is_not_stored(self, tmp_path: Path) -> None:
        """.deb has its own magic-byte detection in is_package(); confirm
        the package-exclusion check reaches it too, not just the
        extension-based branch the .tar.gz/.whl cases above exercise."""
        import shutil
        import subprocess

        ar = shutil.which("ar")
        if ar is None:
            import pytest

            pytest.skip("ar is not available")
        staging = tmp_path / "staging"
        staging.mkdir()
        (staging / "dummy.txt").write_text("dummy\n")
        deb_path = tmp_path / "fake.deb"
        subprocess.run(
            [ar, "rcs", str(deb_path), "dummy.txt"],
            cwd=staging,
            check=True,
            capture_output=True,
        )
        assert looks_like_stored_bundle_facts(deb_path) is False


class TestClassifyBundleCompareOperands:
    def test_stored_old_live_new(self, tmp_path: Path) -> None:
        old = tmp_path / "old.json"
        old.write_text(_MARKER_JSON)
        new = tmp_path / "new_dir"
        new.mkdir()
        req = classify_bundle_compare_operands(old, new)
        assert req == BundleCompareRequest(old_is_stored=True, new_is_stored=False)
        assert req.any_stored is True

    def test_live_old_live_new(self, tmp_path: Path) -> None:
        old = tmp_path / "old_dir"
        new = tmp_path / "new_dir"
        old.mkdir()
        new.mkdir()
        req = classify_bundle_compare_operands(old, new)
        assert req == BundleCompareRequest(old_is_stored=False, new_is_stored=False)
        assert req.any_stored is False

    def test_stored_new_is_classified_too(self, tmp_path: Path) -> None:
        old = tmp_path / "old_dir"
        old.mkdir()
        new = tmp_path / "new.json"
        new.write_text(_MARKER_JSON)
        req = classify_bundle_compare_operands(old, new)
        assert req.new_is_stored is True
        assert req.old_is_stored is False
