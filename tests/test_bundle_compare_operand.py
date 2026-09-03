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

    def test_reordered_root_keys_still_classify_as_stored(
        self, tmp_path: Path
    ) -> None:
        """Codex review, PR #1042 (round 3): bundle_facts_to_dict always
        writes artifact_type first, but a document re-serialized by
        another conforming tool (a pretty-printer, a key-sorting
        formatter) can freely reorder root members -- bundle_facts_from_
        dict itself never requires a particular order, so classification
        must not either."""
        p = tmp_path / "reordered.json"
        # schema_version and per_library_snapshots both precede
        # artifact_type here, unlike the writer's own real output.
        p.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "per_library_snapshots": {},
                    "artifact_type": "abicheck.bundle-facts",
                    "variant_fingerprint": "default",
                }
            )
        )
        assert looks_like_stored_bundle_facts(p) is True

    def test_artifact_type_as_a_sibling_value_does_not_confuse_the_scan(
        self, tmp_path: Path
    ) -> None:
        """The literal string "artifact_type" appearing as some other
        field's *value* (not a key) at the root must not be mistaken for
        the marker key itself."""
        p = tmp_path / "confusing.json"
        p.write_text(
            json.dumps(
                {
                    "some_field": "artifact_type",
                    "another_field": "abicheck.bundle-facts",
                }
            )
        )
        assert looks_like_stored_bundle_facts(p) is False

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
        """Codex review, PR #1042 (round 1): a compressed release package
        (e.g. a .tar.gz of shared libraries) whose *nested* member content
        coincidentally contains the marker text (e.g. a BundleFacts fixture
        bundled inside a test release archive) must not misclassify the
        whole package as a stored-facts document. Closed by root-anchoring
        the marker match (round 2), not by excluding recognized packages
        outright (round 1's own fix, reverted -- see
        test_bundle_facts_json_with_a_package_like_suffix_is_still_stored
        for why): a tar/gzip stream's own framing (a 512-byte tar header
        block before any member content) never decodes to bytes starting
        with ``{"artifact_type"`` at position 0, root-anchoring rules this
        out on its own."""
        tar_path = tmp_path / "release.tar.gz"
        with tarfile.open(tar_path, "w:gz") as tf:
            data = _MARKER_JSON.encode()
            info = tarfile.TarInfo(name="nested/embedded_fixture.bundlefacts.json")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        assert looks_like_stored_bundle_facts(tar_path) is False

    def test_a_real_deb_package_is_not_stored(self, tmp_path: Path) -> None:
        """.deb's own ar-archive magic bytes never decode to ``{...`` at
        position 0 either -- root-anchoring rules it out the same way as
        the .tar.gz/.whl cases above, with no is_package() call involved."""
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

    def test_bundle_facts_json_with_a_package_like_suffix_is_still_stored(
        self, tmp_path: Path
    ) -> None:
        """Codex review, PR #1042 (round 2): a genuine stored BundleFacts
        JSON document named with a package-like suffix (a plausible
        --bundle-facts-out output path from a templated CI naming
        convention, e.g. baseline.tar.gz) must still classify as stored --
        round 1's is_package() pre-check would have vetoed this purely by
        filename suffix, with no remaining route back to the BundleFacts
        loader post-flag-removal. Not an actual tar/gzip stream -- just a
        plain JSON file wearing that extension, exactly what
        --bundle-facts-out would write there."""
        p = tmp_path / "baseline.tar.gz"
        p.write_text(_MARKER_JSON)
        assert looks_like_stored_bundle_facts(p) is True

    def test_nested_artifact_type_in_an_ordinary_snapshot_is_not_stored(
        self, tmp_path: Path
    ) -> None:
        """Codex review, PR #1042 (round 2), fresh evidence: an ordinary
        AbiSnapshot whose own `constants` mapping happens to define a C
        constant literally named "artifact_type" with this exact string
        value JSON-serializes as a *nested* object -- an unanchored search
        matched it too, misrouting a real single-snapshot compare into the
        BundleFacts loader. The root object's own first key is
        "constants"' sibling top-level AbiSnapshot fields (library/version/
        functions/...), never "artifact_type" -- root-anchoring rejects
        this shape correctly."""
        p = tmp_path / "snap.json"
        p.write_text(
            json.dumps(
                {
                    "library": "libfoo.so",
                    "version": "1.0",
                    "functions": [],
                    "variables": [],
                    "types": [],
                    "enums": [],
                    "typedefs": [],
                    "constants": [
                        {
                            "name": "artifact_type",
                            "value": "abicheck.bundle-facts",
                        }
                    ],
                }
            )
        )
        assert looks_like_stored_bundle_facts(p) is False


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
