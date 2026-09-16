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

"""``validate_precaptured_baseline_set``: publishing a set we did not capture.

**Bug class:** ``baseline.unverified_capture_published_as_verified``. The
capture-then-publish path may trust its manifest -- it wrote it moments ago
from bytes it read itself. A set handed in from elsewhere is an *input*, and
an immutable channel gives no second chance to notice a mislabelled one.

The invariant these tests state, rather than any single reproducer: **a set
this validator accepts must be one the consumer-side resolver would also
accept, and every stated expectation it disagrees with is an error.** So the
digest cases are checked against real snapshot content (the oracle is
``compute_snapshot_content_hash`` over the bytes actually written, which is
what a resolver recomputes -- deliberately not the manifest's own declared
field), the label cases enumerate a small matrix of agree/disagree
combinations rather than one mismatch, and the path cases sweep a family of
traversal/portability shapes rather than the one ``..`` that started it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from abicheck.buildsource.baseline_precaptured import (
    locate_baseline_set,
    validate_precaptured_baseline_set,
)
from abicheck.buildsource.baseline_set import (
    BASELINE_MANIFEST_FILENAME,
    compute_snapshot_content_hash,
)

PROFILE = "linux-x86_64-gcc13-release"
PROJECT_REF = "v1.5.2"


def _snapshot_payload(library: str) -> dict:
    """A snapshot shaped like a real dump's, minimally."""
    return {
        "schema_version": 9,
        "library": library,
        "version": "1.5.2",
        "created_at": "2026-01-01T00:00:00Z",
        "functions": [{"name": f"{library}_open", "mangled_name": f"{library}_open"}],
        "variables": [],
    }


def _write_set(
    root: Path,
    *,
    profile: str = PROFILE,
    project_ref: str = PROJECT_REF,
    generation: int | None = None,
    manifest_version: int | None = 1,
    snapshot_schema: int | None = 9,
    libraries: tuple[str, ...] = ("libthing.so.1",),
    corrupt_digest: bool = False,
    snapshot_override: dict[str, str] | None = None,
    extra_rows: list[dict] | None = None,
) -> Path:
    """Write a real, self-consistent baseline-set and return its root.

    Self-consistent by *construction* -- each declared digest is computed
    from the bytes this helper just wrote, the same way the real capture
    Action computes it. A fixture that hard-coded digests would drift from
    ``compute_snapshot_content_hash`` and start passing for the wrong
    reason.
    """
    root.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    for library in libraries:
        payload = _snapshot_payload(library)
        rel = (
            snapshot_override.get(library, f"{library}.abicheck.json")
            if (snapshot_override)
            else f"{library}.abicheck.json"
        )
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
        digest = compute_snapshot_content_hash(payload)
        if corrupt_digest:
            digest = "0" * 64
        rows.append(
            {
                "library": library,
                "artifact": f"build/{library}",
                "snapshot": rel,
                "sha256": digest,
            }
        )
    rows.extend(extra_rows or [])
    (root / BASELINE_MANIFEST_FILENAME).write_text(
        json.dumps(
            {
                "manifest_version": manifest_version,
                "project_ref": project_ref,
                "profile": profile,
                "snapshot_schema": snapshot_schema,
                "baseline_generation": generation,
                "artifacts": rows,
            }
        ),
        encoding="utf-8",
    )
    return root


class TestAValidSetIsAccepted:
    def test_a_self_consistent_set_validates(self, tmp_path: Path) -> None:
        _write_set(tmp_path / "set", libraries=("liba.so.1", "libb.so.2"))
        result = validate_precaptured_baseline_set(
            tmp_path / "set",
            expected_profile=PROFILE,
            expected_project_ref=PROJECT_REF,
        )
        assert result.ok, result.errors
        assert result.libraries == ["liba.so.1", "libb.so.2"]
        assert (result.profile, result.project_ref) == (PROFILE, PROJECT_REF)

    def test_ok_and_errors_never_disagree(self, tmp_path: Path) -> None:
        """A caller gating on `ok` gates on `errors`; one cannot say yes
        while the other says no."""
        for corrupt in (False, True):
            root = _write_set(tmp_path / f"set-{corrupt}", corrupt_digest=corrupt)
            result = validate_precaptured_baseline_set(root)
            assert result.ok is (not result.errors)
            assert result.ok is (not corrupt)

    def test_a_nested_download_layout_is_found(self, tmp_path: Path) -> None:
        """`download-artifact` nests under `<name>/` when its pattern matched
        more than one artifact and extracts flat when it matched exactly one
        -- a caller cannot know which it got."""
        _write_set(tmp_path / "download" / "abicheck-baseline-linux")
        assert locate_baseline_set(tmp_path / "download") == (
            tmp_path / "download" / "abicheck-baseline-linux"
        )
        assert validate_precaptured_baseline_set(tmp_path / "download").ok

    def test_two_nested_sets_are_refused_rather_than_picked(
        self, tmp_path: Path
    ) -> None:
        _write_set(tmp_path / "download" / "one")
        _write_set(tmp_path / "download" / "two")
        with pytest.raises(ValueError, match="2 baseline-sets"):
            locate_baseline_set(tmp_path / "download")
        result = validate_precaptured_baseline_set(tmp_path / "download")
        assert not result.ok and "2 baseline-sets" in result.errors[0]

    def test_no_set_at_all_is_an_error_not_a_crash(self, tmp_path: Path) -> None:
        result = validate_precaptured_baseline_set(tmp_path / "empty")
        assert not result.ok
        assert BASELINE_MANIFEST_FILENAME in result.errors[0]


class TestStatedExpectationsAreEnforced:
    """A stated expectation is never silently adopted from the set itself.

    Enumerated rather than reproduced: the invariant is that agreement
    validates and *any* disagreement fails, for every label a consumer will
    later ask this asset for.
    """

    @pytest.mark.parametrize("declared", [PROFILE, "windows-msvc", ""])
    @pytest.mark.parametrize("expected", [PROFILE, "windows-msvc", ""])
    def test_profile_matrix(self, tmp_path: Path, declared: str, expected: str) -> None:
        root = _write_set(tmp_path / f"s{declared}{expected}", profile=declared)
        result = validate_precaptured_baseline_set(root, expected_profile=expected)
        # An unstated expectation checks nothing; a stated one must match.
        should_fail = bool(expected) and declared != expected
        assert any("published as" in e for e in result.errors) is should_fail, (
            result.errors
        )

    @pytest.mark.parametrize(
        ("declared", "expected", "should_fail"),
        [
            (PROJECT_REF, PROJECT_REF, False),
            (PROJECT_REF, "v1.5.3", True),
            ("main", PROJECT_REF, True),
            ("", PROJECT_REF, True),
            (PROJECT_REF, "", False),
        ],
    )
    def test_capture_revision_matrix(
        self, tmp_path: Path, declared: str, expected: str, should_fail: bool
    ) -> None:
        root = _write_set(tmp_path / f"s{declared}{expected}", project_ref=declared)
        result = validate_precaptured_baseline_set(root, expected_project_ref=expected)
        assert any("project_ref" in e for e in result.errors) is should_fail

    @pytest.mark.parametrize(
        ("declared", "expected", "should_fail"),
        [
            (None, None, False),
            (2, 2, False),
            (2, 3, True),
            (None, 2, True),
            (2, None, False),
        ],
    )
    def test_generation_matrix(
        self,
        tmp_path: Path,
        declared: int | None,
        expected: int | None,
        should_fail: bool,
    ) -> None:
        root = _write_set(tmp_path / f"s{declared}{expected}", generation=declared)
        result = validate_precaptured_baseline_set(root, expected_generation=expected)
        assert any("baseline_generation" in e for e in result.errors) is should_fail

    @pytest.mark.parametrize(
        ("manifest_version", "snapshot_schema", "should_fail"),
        [(1, 9, False), (2, 9, True), (None, 9, True), (1, 10**6, True)],
    )
    def test_schema_matrix(
        self,
        tmp_path: Path,
        manifest_version: int | None,
        snapshot_schema: int,
        should_fail: bool,
    ) -> None:
        root = _write_set(
            tmp_path / f"s{manifest_version}{snapshot_schema}",
            manifest_version=manifest_version,
            snapshot_schema=snapshot_schema,
        )
        result = validate_precaptured_baseline_set(root)
        assert bool(result.errors) is should_fail, result.errors


class TestContentIdentityIsRecomputed:
    """Declared digests are claims until the bytes on disk agree.

    The oracle is `compute_snapshot_content_hash` over what was written --
    the same function a consumer's resolver applies -- never the manifest's
    own field, which is the value under test.
    """

    def test_a_tampered_snapshot_fails_even_with_an_intact_manifest(
        self, tmp_path: Path
    ) -> None:
        root = _write_set(tmp_path / "set")
        snapshot = root / "libthing.so.1.abicheck.json"
        payload = json.loads(snapshot.read_text())
        payload["functions"].append({"name": "smuggled", "mangled_name": "smuggled"})
        snapshot.write_text(json.dumps(payload), encoding="utf-8")
        result = validate_precaptured_baseline_set(root)
        assert not result.ok
        assert any("self-inconsistent" in e for e in result.errors), result.errors

    def test_a_declared_digest_that_is_simply_wrong_fails(self, tmp_path: Path) -> None:
        root = _write_set(tmp_path / "set", corrupt_digest=True)
        result = validate_precaptured_baseline_set(root)
        assert not result.ok
        assert any("self-inconsistent" in e for e in result.errors)

    def test_an_undeclared_digest_is_refused_not_skipped(self, tmp_path: Path) -> None:
        """The resolver-side check no-ops on an empty digest, which is
        tolerable when reading someone's published asset and not tolerable
        when deciding to publish one: an unverifiable member would be minted
        into an immutable channel."""
        root = _write_set(tmp_path / "set")
        manifest = json.loads((root / BASELINE_MANIFEST_FILENAME).read_text())
        manifest["artifacts"][0]["sha256"] = ""
        (root / BASELINE_MANIFEST_FILENAME).write_text(json.dumps(manifest))
        result = validate_precaptured_baseline_set(root)
        assert not result.ok
        assert any("no snapshot sha256" in e for e in result.errors)

    def test_volatile_field_churn_alone_does_not_fail(self, tmp_path: Path) -> None:
        """The counterpart guard: the hash is over *stable* content, so a
        field a dump restamps every run must not read as tampering -- a
        validator that hashed raw bytes would reject every honest set."""
        root = _write_set(tmp_path / "set")
        snapshot = root / "libthing.so.1.abicheck.json"
        payload = json.loads(snapshot.read_text())
        payload["created_at"] = "2027-06-06T06:06:06Z"
        snapshot.write_text(json.dumps(payload), encoding="utf-8")
        assert validate_precaptured_baseline_set(root).ok


class TestMemberPathsMustBePublishable:
    @pytest.mark.parametrize(
        "rel",
        [
            "../escape.json",
            "nested/../../escape.json",
            "/absolute.json",
            "C:/windows.json",
            "back\\slash.json",
            "star*.json",
            "",
        ],
        ids=[
            "parent",
            "parent-via-nesting",
            "absolute-posix",
            "absolute-windows",
            "backslash",
            "wildcard",
            "empty",
        ],
    )
    def test_an_unpublishable_member_path_is_refused(
        self, tmp_path: Path, rel: str
    ) -> None:
        root = tmp_path / "set"
        root.mkdir()
        (root / BASELINE_MANIFEST_FILENAME).write_text(
            json.dumps(
                {
                    "manifest_version": 1,
                    "project_ref": PROJECT_REF,
                    "profile": PROFILE,
                    "snapshot_schema": 9,
                    "artifacts": [
                        {"library": "libthing.so.1", "snapshot": rel, "sha256": "x"}
                    ],
                }
            ),
            encoding="utf-8",
        )
        result = validate_precaptured_baseline_set(root)
        assert not result.ok, rel

    def test_a_relocated_but_in_tree_member_is_fine(self, tmp_path: Path) -> None:
        """Relocation inside the set is normal (`snapshots/<name>.json`) and
        must keep validating -- this check is about escaping the set, not
        about a flat layout."""
        root = _write_set(
            tmp_path / "set",
            snapshot_override={"libthing.so.1": "snapshots/libthing.so.1.json"},
        )
        assert validate_precaptured_baseline_set(root).ok

    @pytest.mark.skipif(
        os.name == "nt", reason="symlink creation needs privilege on Windows"
    )
    def test_a_symlinked_member_is_refused_even_when_it_points_in_tree(
        self, tmp_path: Path
    ) -> None:
        """An archive member that is a symlink is refused structurally, the
        same rule publish-baseline.yml already applies to an existing asset
        -- where it points is not the question."""
        root = _write_set(tmp_path / "set")
        real = root / "libthing.so.1.abicheck.json"
        link = root / "link.json"
        link.symlink_to(real.name)
        manifest = json.loads((root / BASELINE_MANIFEST_FILENAME).read_text())
        manifest["artifacts"][0]["snapshot"] = "link.json"
        (root / BASELINE_MANIFEST_FILENAME).write_text(json.dumps(manifest))
        result = validate_precaptured_baseline_set(root)
        assert not result.ok
        assert any("symlink" in e for e in result.errors), result.errors

    def test_a_missing_member_is_refused(self, tmp_path: Path) -> None:
        root = _write_set(tmp_path / "set")
        (root / "libthing.so.1.abicheck.json").unlink()
        assert not validate_precaptured_baseline_set(root).ok


class TestStructuralManifestProblems:
    def test_an_empty_artifact_list_is_refused(self, tmp_path: Path) -> None:
        root = _write_set(tmp_path / "set", libraries=())
        result = validate_precaptured_baseline_set(root)
        assert not result.ok
        assert any("no artifacts" in e for e in result.errors)

    def test_a_duplicate_library_row_is_refused(self, tmp_path: Path) -> None:
        root = _write_set(
            tmp_path / "set",
            extra_rows=[
                {
                    "library": "libthing.so.1",
                    "snapshot": "libthing.so.1.abicheck.json",
                    "sha256": "0" * 64,
                }
            ],
        )
        result = validate_precaptured_baseline_set(root)
        assert not result.ok
        assert any("more than one" in e for e in result.errors), result.errors

    def test_a_corrupt_manifest_is_an_error_not_an_exception(
        self, tmp_path: Path
    ) -> None:
        root = tmp_path / "set"
        root.mkdir()
        (root / BASELINE_MANIFEST_FILENAME).write_text("{not json", encoding="utf-8")
        result = validate_precaptured_baseline_set(root)
        assert not result.ok

    def test_every_problem_is_reported_in_one_pass(self, tmp_path: Path) -> None:
        """A publisher that surfaced one problem per attempt would turn a
        mislabelled capture into several round trips through a workflow."""
        root = _write_set(
            tmp_path / "set", profile="wrong", project_ref="wrong", corrupt_digest=True
        )
        result = validate_precaptured_baseline_set(
            root,
            expected_profile=PROFILE,
            expected_project_ref=PROJECT_REF,
            expected_generation=4,
        )
        assert len(result.errors) >= 4, result.errors
