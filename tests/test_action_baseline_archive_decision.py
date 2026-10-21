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

"""Decision table for the root Action's baseline source, and the
``expected-archive-digest`` pin on its baseline-set fallback.

The root Action picks its baseline from three inputs: what the release holds
(no assets, stray single-snapshot assets, a baseline-set archive), whether
``baseline-profile``/``baseline-target`` select the archive, and whether
``abi-baseline`` is a local file. Each cell of that table is run here through
the real fetch region of ``action/run.sh`` (the same extraction the fallback
tests use) against a ``gh`` stub that serves the release listing, the legacy
downloads and the archive, and records every call it receives. The assertions
are on the chosen source, the asset it names, the error, and whether a legacy
download happened at all -- so a selection that is silently ignored is caught
even when the final source happens to look right.

``actions/resolve-baseline`` has no release lookup and so no equivalent of the
asset dimension; its counterpart table lives in
``test_action_resolve_baseline_archive_digest.py``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

import pytest
from test_action_run_sh_baseline_set_fallback import (
    FIXTURE_TAG,
    PROFILE,
    _baseline_region,
    _build_baseline_set_archive,
    _make_tar_zst,
    _run_bash_script,
)

RUN_SH = Path(__file__).resolve().parents[1] / "action" / "run.sh"
ARCHIVE_ASSET = f"abicheck-baseline-{PROFILE}.tar.zst"
TARGET = "libfoo"
STRAY = "libfoo.abicheck.json"
OTHER_STRAY = "libbar.abicheck.json"

# Every gh call is appended to $FIXTURE_CALL_LOG. `release download -D DIR`
# copies the stray legacy assets named in $FIXTURE_LEGACY_DIR into DIR and
# fails, as gh does, when nothing matched. `release view` answers from
# $FIXTURE_ASSETS_JSON. `api` cats $FIXTURE_ARCHIVE.
_GH_STUB = r"""
gh() {
  if [[ -n "${FIXTURE_CALL_LOG:-}" ]]; then
    printf '%s\n' "$*" >> "$FIXTURE_CALL_LOG"
  fi
  if [[ "$1" == "release" && "$2" == "download" ]]; then
    local dest="" copied=0 f
    while [[ $# -gt 0 ]]; do
      if [[ "$1" == "-D" ]]; then dest="$2"; shift 2; continue; fi
      shift
    done
    mkdir -p "$dest"
    for f in "$FIXTURE_LEGACY_DIR"/*.abicheck.json; do
      if [[ -f "$f" ]]; then cp "$f" "$dest/"; copied=$((copied + 1)); fi
    done
    [[ $copied -gt 0 ]]
    return $?
  fi
  if [[ "$1" == "release" && "$2" == "view" ]]; then
    printf '%s' "$FIXTURE_ASSETS_JSON"
    return 0
  fi
  if [[ "$1" == "api" ]]; then
    cat "$FIXTURE_ARCHIVE"
    return $?
  fi
  return 1
}
"""

# Prints the provenance the Action recorded, on one line the tests parse.
_TRAILER = (
    '\necho "REACHED_END SOURCE=${BASELINE_SOURCE:-} '
    "ASSET=${BASELINE_SOURCE_ASSET:-} OLD=${INPUT_OLD_LIBRARY:-} "
    'DIGEST=${BASELINE_ARCHIVE_DIGEST:-} VERIFIED=${BASELINE_ARCHIVE_VERIFIED:-}"\n'
)


def _manifest(snapshot_target: str = TARGET) -> dict:
    return {
        "manifest_version": 1,
        "project_ref": FIXTURE_TAG,
        "profile": PROFILE,
        "snapshot_schema": None,
        "fact_set": None,
        "baseline_generation": None,
        "artifacts": [
            {
                "library": snapshot_target,
                "artifact": f"build/{snapshot_target}.so",
                "snapshot": f"{snapshot_target}.abicheck.json",
                "sha256": "",
            }
        ],
    }


def _archive_with_snapshot(root: Path, snapshot: str) -> Path:
    """A baseline-set archive whose snapshot is *snapshot*. Every in-archive
    check passes for any content, so only a digest pinned outside the archive
    can tell two such archives apart."""
    src = root / "baseline-set-src"
    src.mkdir(parents=True)
    (src / "manifest.json").write_text(json.dumps(_manifest()), encoding="utf-8")
    (src / f"{TARGET}.abicheck.json").write_text(snapshot, encoding="utf-8")
    archive = root / "fixture-baseline-set.tar.zst"
    _make_tar_zst(archive, src)
    return archive


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(
    tmp_path: Path,
    *,
    strays: tuple[str, ...] = (),
    archive: Path | None = None,
    selected: bool = False,
    abi_baseline: str = FIXTURE_TAG,
    extra_env: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """Run the fetch region once. Returns the process and the gh calls made."""
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir(exist_ok=True)
    for name in strays:
        (legacy_dir / name).write_text("{}", encoding="utf-8")

    assets = [{"name": name, "apiUrl": f"fixture://{name}"} for name in strays]
    if archive is not None:
        assets.append({"name": ARCHIVE_ASSET, "apiUrl": "fixture://archive"})

    call_log = tmp_path / "gh-calls.log"
    env = {k: v for k, v in os.environ.items() if not k.startswith("INPUT_")}
    env.update(
        {
            "INPUT_ABI_BASELINE": abi_baseline,
            "FIXTURE_LEGACY_DIR": str(legacy_dir),
            "FIXTURE_ASSETS_JSON": json.dumps(
                {"tagName": FIXTURE_TAG, "assets": assets}
            ),
            "FIXTURE_CALL_LOG": str(call_log),
        }
    )
    if archive is not None:
        env["FIXTURE_ARCHIVE"] = str(archive)
    if selected:
        env["INPUT_BASELINE_PROFILE"] = PROFILE
        env["INPUT_BASELINE_TARGET"] = TARGET
    env.update(extra_env or {})

    script = (
        'MODE="${INPUT_MODE:-compare}"\n'
        'FORCE_AUDIT_ONLY="${INPUT_AUDIT:-false}"\n'
        + _GH_STUB
        + _baseline_region()
        + _TRAILER
    )
    result = _run_bash_script(script, env, cwd=tmp_path)
    calls = (
        call_log.read_text(encoding="utf-8").splitlines() if call_log.exists() else []
    )
    return result, calls


def _provenance(result: subprocess.CompletedProcess[str]) -> dict[str, str]:
    match = re.search(r"^REACHED_END (.*)$", result.stdout, re.MULTILINE)
    assert match is not None, result.stdout + result.stderr
    return dict(token.split("=", 1) for token in match.group(1).split())


def _combined(result: subprocess.CompletedProcess[str]) -> str:
    return result.stdout + result.stderr


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestBaselineSourceDecisionTable:
    @pytest.mark.parametrize(
        ("assets", "selected", "expected"),
        [
            pytest.param(
                "none",
                False,
                {"error": "No ABI baseline found"},
                id="no-assets-unselected",
            ),
            pytest.param(
                "none",
                True,
                {"error": "baseline-set archive 'abicheck-baseline-"},
                id="no-assets-selected",
            ),
            pytest.param(
                "one-stray",
                False,
                {"source": "legacy-asset", "asset": STRAY},
                id="one-stray-unselected",
            ),
            pytest.param(
                "one-stray",
                True,
                {"error": "baseline-set archive 'abicheck-baseline-"},
                id="one-stray-selected-without-archive",
            ),
            pytest.param(
                "two-stray",
                False,
                {"error": "Multiple *.abicheck.json assets found"},
                id="two-stray-unselected",
            ),
            pytest.param(
                "two-stray",
                True,
                {"error": "baseline-set archive 'abicheck-baseline-"},
                id="two-stray-selected-without-archive",
            ),
            pytest.param(
                "baseline-set",
                False,
                {"error": "No ABI baseline found"},
                id="archive-only-unselected-ignores-archive",
            ),
            pytest.param(
                "baseline-set",
                True,
                {"source": "baseline-set", "asset": ARCHIVE_ASSET},
                id="archive-only-selected",
            ),
            pytest.param(
                "baseline-set+one-stray",
                False,
                {"source": "legacy-asset", "asset": STRAY},
                id="archive-and-one-stray-unselected",
            ),
            pytest.param(
                "baseline-set+one-stray",
                True,
                {"source": "baseline-set", "asset": ARCHIVE_ASSET},
                id="archive-and-one-stray-selected-stray-ignored",
            ),
            pytest.param(
                "baseline-set+two-stray",
                False,
                {"error": "Multiple *.abicheck.json assets found"},
                id="archive-and-two-stray-unselected",
            ),
            pytest.param(
                "baseline-set+two-stray",
                True,
                {"source": "baseline-set", "asset": ARCHIVE_ASSET},
                id="archive-and-two-stray-selected-no-ambiguity",
            ),
            pytest.param(
                "file-path",
                False,
                {"source": "direct-path"},
                id="file-path-unselected",
            ),
            pytest.param(
                "file-path",
                True,
                {"error": "abi-baseline is a file path"},
                id="file-path-selected-refused",
            ),
        ],
    )
    def test_source_or_refusal_for_each_cell(
        self,
        tmp_path: Path,
        assets: str,
        selected: bool,
        expected: dict[str, str],
    ) -> None:
        strays: tuple[str, ...] = ()
        archive = None
        abi_baseline = FIXTURE_TAG
        file_path = tmp_path / "abi-baseline.json"
        if assets in ("one-stray", "baseline-set+one-stray"):
            strays = (STRAY,)
        if assets in ("two-stray", "baseline-set+two-stray"):
            strays = (STRAY, OTHER_STRAY)
        if assets.startswith("baseline-set"):
            case_dir = tmp_path / "case"
            case_dir.mkdir()
            archive = _build_baseline_set_archive(case_dir, target=TARGET)
        if assets == "file-path":
            file_path.write_text("{}", encoding="utf-8")
            abi_baseline = str(file_path)

        result, calls = _run(
            tmp_path,
            strays=strays,
            archive=archive,
            selected=selected,
            abi_baseline=abi_baseline,
        )

        if "error" in expected:
            assert result.returncode != 0, _combined(result)
            assert expected["error"] in _combined(result)
            assert "REACHED_END" not in result.stdout
        else:
            assert result.returncode == 0, _combined(result)
            provenance = _provenance(result)
            assert provenance["SOURCE"] == expected["source"]
            if "asset" in expected:
                assert provenance["ASSET"] == expected["asset"]
            if expected["source"] == "direct-path":
                assert provenance["OLD"] == str(file_path)

        # The selection is what decides whether the legacy search runs at all:
        # a selected run must never download a stray single-snapshot asset.
        if assets == "file-path":
            assert calls == []
        elif selected:
            assert not any(call.startswith("release download") for call in calls)
        else:
            assert any(call.startswith("release download") for call in calls)


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestArchiveDigestPin:
    def test_matching_pin_resolves_and_records_the_verified_digest(
        self, tmp_path: Path
    ) -> None:
        archive = _archive_with_snapshot(tmp_path / "v1", '{"version": 1}')
        digest = _sha256(archive)
        result, _ = _run(
            tmp_path,
            archive=archive,
            selected=True,
            extra_env={"INPUT_EXPECTED_ARCHIVE_DIGEST": f"sha256:{digest}"},
        )
        assert result.returncode == 0, _combined(result)
        provenance = _provenance(result)
        assert provenance["SOURCE"] == "baseline-set"
        assert provenance["DIGEST"] == f"sha256:{digest}"
        assert provenance["VERIFIED"] == "true"

    def test_unpinned_archive_records_its_digest_as_unverified(
        self, tmp_path: Path
    ) -> None:
        archive = _archive_with_snapshot(tmp_path / "v1", '{"version": 1}')
        result, _ = _run(tmp_path, archive=archive, selected=True)
        assert result.returncode == 0, _combined(result)
        provenance = _provenance(result)
        assert provenance["DIGEST"] == f"sha256:{_sha256(archive)}"
        assert provenance["VERIFIED"] == "false"

    def test_consistent_repack_is_refused_under_the_original_pin(
        self, tmp_path: Path
    ) -> None:
        original = _archive_with_snapshot(tmp_path / "v1", '{"version": 1}')
        repacked = _archive_with_snapshot(tmp_path / "v2", '{"version": 2}')
        assert _sha256(original) != _sha256(repacked)

        result, _ = _run(
            tmp_path,
            archive=repacked,
            selected=True,
            extra_env={"INPUT_EXPECTED_ARCHIVE_DIGEST": _sha256(original)},
        )
        assert result.returncode != 0, _combined(result)
        assert "does not match the pinned digest" in _combined(result)
        assert "REACHED_END" not in result.stdout

    def test_correct_pin_on_the_repacked_archive_uses_the_repacked_snapshot(
        self, tmp_path: Path
    ) -> None:
        repacked = _archive_with_snapshot(tmp_path / "v2", '{"version": 2}')
        result, _ = _run(
            tmp_path,
            archive=repacked,
            selected=True,
            extra_env={"INPUT_EXPECTED_ARCHIVE_DIGEST": _sha256(repacked)},
        )
        assert result.returncode == 0, _combined(result)
        provenance = _provenance(result)
        assert provenance["VERIFIED"] == "true"
        assert Path(provenance["OLD"]).read_text(encoding="utf-8") == '{"version": 2}'

    def test_mismatch_is_fatal_even_under_dry_run(self, tmp_path: Path) -> None:
        archive = _archive_with_snapshot(tmp_path / "v1", '{"version": 1}')
        result, _ = _run(
            tmp_path,
            archive=archive,
            selected=True,
            extra_env={
                "INPUT_EXPECTED_ARCHIVE_DIGEST": "0" * 64,
                "INPUT_DRY_RUN": "true",
            },
        )
        # A dry run tolerates an unresolved baseline, never an altered one.
        assert result.returncode != 0, _combined(result)
        assert "does not match the pinned digest" in _combined(result)

    def test_malformed_pin_is_refused_before_any_fetch(self, tmp_path: Path) -> None:
        archive = _archive_with_snapshot(tmp_path / "v1", '{"version": 1}')
        result, calls = _run(
            tmp_path,
            archive=archive,
            selected=True,
            extra_env={"INPUT_EXPECTED_ARCHIVE_DIGEST": "sha256:not-a-digest"},
        )
        assert result.returncode != 0, _combined(result)
        assert "is not a SHA-256 digest" in _combined(result)
        assert calls == []

    def test_pin_without_profile_and_target_is_refused(self, tmp_path: Path) -> None:
        archive = _archive_with_snapshot(tmp_path / "v1", '{"version": 1}')
        result, _ = _run(
            tmp_path,
            archive=archive,
            selected=False,
            extra_env={"INPUT_EXPECTED_ARCHIVE_DIGEST": _sha256(archive)},
        )
        assert result.returncode != 0, _combined(result)
        assert "pins a release-contract baseline-set archive" in _combined(result)
