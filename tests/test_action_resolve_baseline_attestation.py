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

"""``verify-baseline-attestation`` on ``actions/resolve-baseline``, end to end
through the real ``run.sh`` and the shared
``action/verify-baseline-attestation.sh``, with a stubbed ``gh``.

The stub models an attestation store: it accepts ``gh attestation verify``
only when the file it is handed has the SHA-256 the test "attested" and the
``--repo``/``--signer-workflow`` it is handed match the attested ones. That is
the oracle -- it is independent of the Action's own digest code -- so a test
that swaps the archive after attesting proves the check reads the bytes that
are then extracted.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
from test_action_resolve_baseline import _run_action, _target_artifact, _write_manifest
from test_action_resolve_baseline_archive_digest import TARGET, _archive, _env

REPO = "acme/libfoo"
SIGNER = "acme/libfoo/.github/workflows/publish-baseline.yml"

_STUB = r"""#!/usr/bin/env bash
# Stub gh: attestation verify <file> --repo R [--signer-workflow W]
printf '%s\n' "$*" >> "$STUB_GH_LOG"
[[ "$1 $2" == "attestation verify" ]] || exit 99
file="$3"; shift 3
repo=""; signer=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo) repo="$2"; shift 2 ;;
    --signer-workflow) signer="$2"; shift 2 ;;
    *) shift ;;
  esac
done
actual=$(python3 -c 'import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$file")
if [[ "$actual" != "$STUB_ATTESTED_SHA" ]]; then echo "no attestation for sha256:$actual" >&2; exit 1; fi
if [[ "$repo" != "$STUB_ATTESTED_REPO" ]]; then echo "repo mismatch" >&2; exit 1; fi
if [[ -n "$signer" && "$signer" != "$STUB_ATTESTED_SIGNER" ]]; then echo "signer mismatch" >&2; exit 1; fi
echo "Verification succeeded!"
"""


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _bin_dir(tmp_path: Path, *, with_gh: bool) -> str:
    """A PATH with no real ``gh`` on it, plus the stub ``gh`` first when
    *with_gh*. A host directory that holds a ``gh`` is replaced by a farm of
    symlinks to its other executables; every other directory (the venv's
    interpreter among them) is kept as is."""
    root = tmp_path / ("bin-gh" if with_gh else "bin-nogh")
    root.mkdir()
    entries: list[str] = []
    if with_gh:
        stub_dir = root / "stub"
        stub_dir.mkdir()
        stub = stub_dir / "gh"
        stub.write_text(_STUB, encoding="utf-8")
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        entries.append(str(stub_dir))
    for i, entry in enumerate(os.environ.get("PATH", "").split(os.pathsep)):
        d = Path(entry)
        if not (d / "gh").exists():
            entries.append(entry)
            continue
        farm = root / f"farm{i}"
        farm.mkdir()
        for exe in d.iterdir():
            if exe.name != "gh":
                try:
                    (farm / exe.name).symlink_to(exe)
                except OSError:
                    continue
        entries.append(str(farm))
    return os.pathsep.join(entries)


def _attest_env(
    tmp_path: Path,
    archive: Path,
    *,
    attested_sha: str,
    with_gh: bool = True,
    **extra: str,
) -> dict[str, str]:
    env = _env(archive, INPUT_VERIFY_BASELINE_ATTESTATION="true", **extra)
    env.update(
        PATH=(_bin_dir(tmp_path, with_gh=with_gh)),
        STUB_GH_LOG=str(tmp_path / "gh.log"),
        STUB_ATTESTED_SHA=attested_sha,
        STUB_ATTESTED_REPO=REPO,
        STUB_ATTESTED_SIGNER=SIGNER,
        GITHUB_REPOSITORY=REPO,
    )
    return env


def _combined(result: subprocess.CompletedProcess[str]) -> str:
    return result.stdout + result.stderr


pytestmark = pytest.mark.skipif(
    os.name == "nt", reason="symlinked PATH stub is POSIX-only"
)


class TestAttestationDecisionTable:
    def test_attested_archive_resolves_verified(self, tmp_path: Path) -> None:
        archive = _archive(tmp_path, '{"version": 1}')
        env = _attest_env(
            tmp_path,
            archive,
            attested_sha=_sha(archive),
            INPUT_BASELINE_ATTESTATION_SIGNER_WORKFLOW=SIGNER,
        )
        result, outputs = _run_action(env, tmp_path)
        assert result.returncode == 0, _combined(result)
        assert outputs["outcome"] == "resolved"
        assert outputs["archive-attestation-verified"] == "true"
        log = (tmp_path / "gh.log").read_text(encoding="utf-8")
        assert f"--repo {REPO}" in log and f"--signer-workflow {SIGNER}" in log

    def test_repo_defaults_to_the_current_repository(self, tmp_path: Path) -> None:
        archive = _archive(tmp_path, '{"version": 1}')
        result, outputs = _run_action(
            _attest_env(tmp_path, archive, attested_sha=_sha(archive)), tmp_path
        )
        assert result.returncode == 0, _combined(result)
        assert outputs["archive-attestation-verified"] == "true"

    def test_unattested_bytes_fail_closed_and_extract_nothing(
        self, tmp_path: Path
    ) -> None:
        # Attest v1, then serve a consistently repacked v2 at the same path.
        attested = _archive(tmp_path / "v1", '{"version": 1}')
        served = _archive(tmp_path / "v2", '{"version": 2}')
        result, outputs = _run_action(
            _attest_env(tmp_path, served, attested_sha=_sha(attested)), tmp_path
        )
        assert result.returncode != 0, _combined(result)
        assert outputs["outcome"] == "ambiguous"
        assert "attestation verification failed" in outputs["message"]
        assert outputs["snapshot-path"] == ""
        assert outputs["archive-attestation-verified"] == "false"

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("INPUT_BASELINE_ATTESTATION_REPO", "evil/fork"),
            (
                "INPUT_BASELINE_ATTESTATION_SIGNER_WORKFLOW",
                "acme/libfoo/.github/workflows/other.yml",
            ),
        ],
    )
    def test_wrong_signer_identity_fails_closed(
        self, tmp_path: Path, field: str, value: str
    ) -> None:
        archive = _archive(tmp_path, '{"version": 1}')
        result, outputs = _run_action(
            _attest_env(
                tmp_path, archive, attested_sha=_sha(archive), **{field: value}
            ),
            tmp_path,
        )
        assert result.returncode != 0, _combined(result)
        assert outputs["outcome"] == "ambiguous"

    def test_missing_gh_fails_closed(self, tmp_path: Path) -> None:
        archive = _archive(tmp_path, '{"version": 1}')
        result, outputs = _run_action(
            _attest_env(tmp_path, archive, attested_sha=_sha(archive), with_gh=False),
            tmp_path,
        )
        assert result.returncode != 0, _combined(result)
        assert outputs["outcome"] == "ambiguous"
        assert "GitHub CLI (gh) is not on PATH" in outputs["message"]

    def test_off_by_default_never_calls_gh(self, tmp_path: Path) -> None:
        archive = _archive(tmp_path, '{"version": 1}')
        env = _env(archive)
        env.update(
            PATH=(_bin_dir(tmp_path, with_gh=True)),
            STUB_GH_LOG=str(tmp_path / "gh.log"),
        )
        result, outputs = _run_action(env, tmp_path)
        assert result.returncode == 0, _combined(result)
        assert outputs["archive-attestation-verified"] == "false"
        assert not (tmp_path / "gh.log").exists()

    def test_directory_is_refused(self, tmp_path: Path) -> None:
        baseline_dir = tmp_path / "staged"
        _write_manifest(baseline_dir, artifacts=[_target_artifact(TARGET)])
        (baseline_dir / f"{TARGET}.abicheck.json").write_text("{}", encoding="utf-8")
        result, _ = _run_action(
            _attest_env(tmp_path, baseline_dir, attested_sha="0" * 64), tmp_path
        )
        assert result.returncode != 0
        assert "is a directory, not an archive" in _combined(result)


class TestAttestationInputValidation:
    @pytest.mark.parametrize(
        ("extra", "needle"),
        [
            ({"INPUT_VERIFY_BASELINE_ATTESTATION": "yes"}, "is not recognized"),
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
                    "INPUT_BASELINE_ATTESTATION_REPO": "not a repo",
                },
                "is not an owner/repo name",
            ),
            (
                {
                    "INPUT_VERIFY_BASELINE_ATTESTATION": "true",
                    "INPUT_BASELINE_ATTESTATION_SIGNER_WORKFLOW": "publish.yml",
                },
                "is not a workflow path",
            ),
        ],
    )
    def test_invalid_combination_is_an_input_error(
        self, tmp_path: Path, extra: dict[str, str], needle: str
    ) -> None:
        archive = _archive(tmp_path, '{"version": 1}')
        result, outputs = _run_action(_env(archive, **extra), tmp_path)
        assert result.returncode != 0
        assert needle in _combined(result)
        assert "outcome" not in outputs

    def test_no_repository_anywhere_is_an_input_error(self, tmp_path: Path) -> None:
        archive = _archive(tmp_path, '{"version": 1}')
        env = _attest_env(tmp_path, archive, attested_sha=_sha(archive))
        env["GITHUB_REPOSITORY"] = ""
        result, _ = _run_action(env, tmp_path)
        assert result.returncode != 0
        assert "names the repository" in _combined(result)


def test_helper_is_executable_and_shellchecks_syntax() -> None:
    helper = (
        Path(__file__).resolve().parent.parent
        / "action"
        / "verify-baseline-attestation.sh"
    )
    assert helper.is_file()
    bash = shutil.which("bash")
    assert bash
    assert subprocess.run([bash, "-n", str(helper)], check=False).returncode == 0
