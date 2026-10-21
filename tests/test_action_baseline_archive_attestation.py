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

"""The root Action's ``verify-baseline-attestation`` on its baseline-set
fallback, through the real fetch region of ``action/run.sh`` and the real
``action/verify-baseline-attestation.sh``, plus the input validation in both
``run.sh`` and ``validate-inputs.sh``.

The release is served by the same in-shell ``gh`` function the digest table
uses; ``gh attestation verify`` runs in the helper's own process, so it is
answered by the stub *executable* from
``test_action_resolve_baseline_attestation`` (accepts only the attested bytes,
repo and signer). The extracted region is written next to a copy of the helper,
so the region's ``${BASH_SOURCE[0]}``-relative lookup finds it exactly as it
does in the shipped layout.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from _workflow_exec import bash_executable, require_bash
from test_action_baseline_archive_decision import (
    _GH_STUB,
    _TRAILER,
    ARCHIVE_ASSET,
    TARGET,
    _archive_with_snapshot,
    _provenance,
    _sha256,
)
from test_action_resolve_baseline_attestation import REPO, SIGNER, _bin_dir
from test_action_run_sh_baseline_set_fallback import (
    FIXTURE_TAG,
    PROFILE,
    _baseline_region,
)

ACTION_DIR = Path(__file__).resolve().parents[1] / "action"
HELPER = ACTION_DIR / "verify-baseline-attestation.sh"
VALIDATE = ACTION_DIR / "validate-inputs.sh"

pytestmark = pytest.mark.skipif(
    os.name == "nt", reason="symlinked PATH stub is POSIX-only"
)


def _run(
    tmp_path: Path,
    archive: Path,
    *,
    attested: Path | None,
    with_gh: bool = True,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    require_bash()
    script_dir = tmp_path / "action"
    script_dir.mkdir()
    shutil.copy(HELPER, script_dir / HELPER.name)
    script = script_dir / "region.sh"
    script.write_text(
        'MODE="${INPUT_MODE:-compare}"\nFORCE_AUDIT_ONLY="${INPUT_AUDIT:-false}"\n'
        + _GH_STUB
        + _baseline_region()
        + _TRAILER,
        encoding="utf-8",
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("INPUT_")}
    env.update(
        {
            "PATH": _bin_dir(tmp_path, with_gh=with_gh),
            "INPUT_ABI_BASELINE": FIXTURE_TAG,
            "INPUT_BASELINE_PROFILE": PROFILE,
            "INPUT_BASELINE_TARGET": TARGET,
            "INPUT_VERIFY_BASELINE_ATTESTATION": "true",
            "FIXTURE_LEGACY_DIR": str(tmp_path),
            "FIXTURE_ASSETS_JSON": json.dumps(
                {
                    "tagName": FIXTURE_TAG,
                    "assets": [{"name": ARCHIVE_ASSET, "apiUrl": "fixture://archive"}],
                }
            ),
            "FIXTURE_ARCHIVE": str(archive),
            "STUB_GH_LOG": str(tmp_path / "gh-attest.log"),
            "STUB_ATTESTED_SHA": _sha256(attested) if attested else "0" * 64,
            "STUB_ATTESTED_REPO": REPO,
            "STUB_ATTESTED_SIGNER": SIGNER,
            "GITHUB_REPOSITORY": REPO,
        }
    )
    env.update(extra_env or {})
    return subprocess.run(
        [bash_executable(), str(script)],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        check=False,
    )


def _combined(result: subprocess.CompletedProcess[str]) -> str:
    return result.stdout + result.stderr


class TestRootAttestationDecisionTable:
    def test_attested_archive_is_used(self, tmp_path: Path) -> None:
        archive = _archive_with_snapshot(tmp_path / "v1", '{"v": 1}')
        result = _run(
            tmp_path,
            archive,
            attested=archive,
            extra_env={"INPUT_BASELINE_ATTESTATION_SIGNER_WORKFLOW": SIGNER},
        )
        assert result.returncode == 0, _combined(result)
        assert _provenance(result)["SOURCE"] == "baseline-set"
        assert "attestation verified" in result.stdout
        assert f"--signer-workflow {SIGNER}" in (tmp_path / "gh-attest.log").read_text()

    def test_repacked_archive_fails_even_under_dry_run(self, tmp_path: Path) -> None:
        attested = _archive_with_snapshot(tmp_path / "v1", '{"v": 1}')
        served = _archive_with_snapshot(tmp_path / "v2", '{"v": 2}')
        result = _run(
            tmp_path, served, attested=attested, extra_env={"INPUT_DRY_RUN": "true"}
        )
        assert result.returncode != 0
        assert "attestation verification failed" in _combined(result)
        assert "REACHED_END" not in result.stdout

    def test_wrong_signer_fails(self, tmp_path: Path) -> None:
        archive = _archive_with_snapshot(tmp_path / "v1", '{"v": 1}')
        result = _run(
            tmp_path,
            archive,
            attested=archive,
            extra_env={
                "INPUT_BASELINE_ATTESTATION_SIGNER_WORKFLOW": "acme/libfoo/.github/workflows/evil.yml"
            },
        )
        assert result.returncode != 0
        assert "REACHED_END" not in result.stdout

    def test_missing_gh_fails_closed(self, tmp_path: Path) -> None:
        archive = _archive_with_snapshot(tmp_path / "v1", '{"v": 1}')
        result = _run(tmp_path, archive, attested=archive, with_gh=False)
        assert result.returncode != 0
        assert "GitHub CLI (gh) is not on PATH" in _combined(result)

    def test_off_never_calls_attestation(self, tmp_path: Path) -> None:
        archive = _archive_with_snapshot(tmp_path / "v1", '{"v": 1}')
        result = _run(
            tmp_path,
            archive,
            attested=None,
            extra_env={"INPUT_VERIFY_BASELINE_ATTESTATION": "false"},
        )
        assert result.returncode == 0, _combined(result)
        assert not (tmp_path / "gh-attest.log").exists()


_INVALID = [
    ({"INPUT_VERIFY_BASELINE_ATTESTATION": "maybe"}, "is not recognized"),
    (
        {"INPUT_BASELINE_ATTESTATION_REPO": REPO},
        "verify-baseline-attestation is 'false'",
    ),
    (
        {"INPUT_BASELINE_ATTESTATION_SIGNER_WORKFLOW": SIGNER},
        "verify-baseline-attestation is 'false'",
    ),
    (
        {
            "INPUT_VERIFY_BASELINE_ATTESTATION": "true",
            "INPUT_BASELINE_ATTESTATION_REPO": "x",
        },
        "is not an owner/repo name",
    ),
    (
        {
            "INPUT_VERIFY_BASELINE_ATTESTATION": "true",
            "INPUT_BASELINE_ATTESTATION_SIGNER_WORKFLOW": "a b",
        },
        "is not a workflow path",
    ),
    (
        {
            "INPUT_VERIFY_BASELINE_ATTESTATION": "true",
            "INPUT_BASELINE_PROFILE": "",
            "INPUT_BASELINE_TARGET": "",
        },
        "Set both",
    ),
]


@pytest.mark.parametrize(("env_extra", "needle"), _INVALID)
def test_validate_inputs_rejects(env_extra: dict[str, str], needle: str) -> None:
    require_bash()
    env = {k: v for k, v in os.environ.items() if not k.startswith("INPUT_")}
    env.update(
        {
            "INPUT_MODE": "compare",
            "INPUT_NEW_LIBRARY": "libfoo.so",
            "INPUT_ABI_BASELINE": FIXTURE_TAG,
            "INPUT_BASELINE_PROFILE": PROFILE,
            "INPUT_BASELINE_TARGET": TARGET,
        }
    )
    env.update(env_extra)
    result = subprocess.run(
        [bash_executable(), str(VALIDATE)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode != 0
    assert needle in _combined(result)


@pytest.mark.parametrize(("env_extra", "needle"), _INVALID)
def test_run_sh_rejects_the_same(
    tmp_path: Path, env_extra: dict[str, str], needle: str
) -> None:
    archive = _archive_with_snapshot(tmp_path / "v1", '{"v": 1}')
    extra = {"INPUT_VERIFY_BASELINE_ATTESTATION": "false", **env_extra}
    result = _run(tmp_path, archive, attested=archive, extra_env=extra)
    assert result.returncode != 0
    assert needle in _combined(result)


def test_validate_inputs_accepts_a_valid_attestation_request() -> None:
    require_bash()
    env = {k: v for k, v in os.environ.items() if not k.startswith("INPUT_")}
    env.update(
        {
            "INPUT_MODE": "compare",
            "INPUT_NEW_LIBRARY": "libfoo.so",
            "INPUT_ABI_BASELINE": FIXTURE_TAG,
            "INPUT_BASELINE_PROFILE": PROFILE,
            "INPUT_BASELINE_TARGET": TARGET,
            "INPUT_VERIFY_BASELINE_ATTESTATION": "true",
            "INPUT_BASELINE_ATTESTATION_SIGNER_WORKFLOW": SIGNER,
        }
    )
    result = subprocess.run(
        [bash_executable(), str(VALIDATE)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert "attestation" not in _combined(result), _combined(result)
