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

"""Behavioral tests for ``actions/stage-baseline/run.sh`` -- the packaging
adapter factored out of ``publish-baseline.yml``'s own "Package
baseline-set" step so an external caller (not just this repository's
release-contract flow) can reuse the identical archive-suffix-dispatch
logic instead of hand-rolling it.

``publish-baseline.yml`` itself now calls this Action (see
``tests/test_publish_baseline_workflows.py``'s
``TestPublishBaselinePackageStepUsesStageBaselineAction``) -- these tests
cover the standalone script directly, the same "parse/run the real file"
discipline ``test_action_resolve_baseline.py`` uses for its sibling.
"""

from __future__ import annotations

import os
import subprocess
import tarfile
from pathlib import Path

import pytest

ACTION_DIR = Path(__file__).resolve().parents[1] / "actions" / "stage-baseline"
RUN_SH = ACTION_DIR / "run.sh"


def _tar_zstd_available() -> bool:
    """Probe whether the real ``tar --zstd`` this script shells out to
    actually works on this platform/runner -- not every ``tar`` build links
    zstd support (this is a real, not hypothetical, cross-platform gap;
    see ``test_action_run_sh_baseline_set_fallback.py``'s own zstd/
    zstandard fallback handling for the analogous concern on the consuming
    side). Skip rather than fail when it's unavailable -- this repo's own
    production ``publish-baseline.yml`` step has the identical
    dependency, unguarded, so a skip here does not hide a real gap this
    test suite could otherwise catch.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "f.txt"
        src.write_text("x", encoding="utf-8")
        result = subprocess.run(
            ["tar", "--zstd", "-cf", str(Path(td) / "f.tar.zst"), "-C", td, "f.txt"],
            capture_output=True,
            check=False,
        )
        return result.returncode == 0


_TAR_ZSTD_AVAILABLE = _tar_zstd_available()


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


def _parse_kv_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k] = v
    return out


def _run_action(
    env_extra: dict[str, str], cwd: Path
) -> tuple[subprocess.CompletedProcess[str], dict[str, str]]:
    github_output = cwd / "github_output"
    github_output.write_text("")
    base_env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("BASELINE_PATH", "ASSET_NAME_TEMPLATE", "PROFILE")
    }
    env = {**base_env, "GITHUB_OUTPUT": str(github_output), **env_extra}
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


def _make_baseline_dir(root: Path) -> Path:
    baseline_dir = root / "baseline-set"
    baseline_dir.mkdir()
    (baseline_dir / "manifest.json").write_text('{"profile": "p"}', encoding="utf-8")
    (baseline_dir / "libfoo.abicheck.json").write_text("{}", encoding="utf-8")
    return baseline_dir


@pytest.mark.skipif(
    not (ACTION_DIR / "action.yml").is_file(), reason="stage-baseline Action not found"
)
class TestStageBaseline:
    @pytest.mark.skipif(
        not _TAR_ZSTD_AVAILABLE, reason="this platform's tar has no zstd support"
    )
    def test_default_template_produces_a_tar_zst(self, tmp_path: Path) -> None:
        baseline_dir = _make_baseline_dir(tmp_path)
        result, outputs = _run_action(
            {"BASELINE_PATH": str(baseline_dir), "PROFILE": "linux-x86_64"},
            tmp_path,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert outputs["asset-name"] == "abicheck-baseline-linux-x86_64.tar.zst"
        assert (tmp_path / outputs["asset-name"]).is_file()

    @pytest.mark.parametrize(
        ("template", "expected_name", "open_mode"),
        [
            ("out-{profile}.tar.gz", "out-p1.tar.gz", "r:gz"),
            ("out-{profile}.tgz", "out-p1.tgz", "r:gz"),
            ("out-{profile}.tar", "out-p1.tar", "r:"),
        ],
    )
    def test_each_recognized_suffix_produces_a_real_extractable_archive(
        self, tmp_path: Path, template: str, expected_name: str, open_mode: str
    ) -> None:
        baseline_dir = _make_baseline_dir(tmp_path)
        result, outputs = _run_action(
            {
                "BASELINE_PATH": str(baseline_dir),
                "ASSET_NAME_TEMPLATE": template,
                "PROFILE": "p1",
            },
            tmp_path,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert outputs["asset-name"] == expected_name
        archive_path = tmp_path / expected_name
        assert archive_path.is_file()
        with tarfile.open(archive_path, open_mode) as tf:
            names = {Path(m.name).name for m in tf.getmembers()}
        assert "manifest.json" in names
        assert "libfoo.abicheck.json" in names

    def test_unrecognized_suffix_is_a_hard_error(self, tmp_path: Path) -> None:
        baseline_dir = _make_baseline_dir(tmp_path)
        result, _ = _run_action(
            {
                "BASELINE_PATH": str(baseline_dir),
                "ASSET_NAME_TEMPLATE": "out-{profile}.zip",
                "PROFILE": "p1",
            },
            tmp_path,
        )
        assert result.returncode == 1
        assert "no recognized archive extension" in (result.stdout + result.stderr)

    @pytest.mark.skipif(
        not _TAR_ZSTD_AVAILABLE, reason="this platform's tar has no zstd support"
    )
    def test_default_template_brace_is_not_mangled(self, tmp_path: Path) -> None:
        # Regression for the ${VAR:-default} brace-parsing bug found
        # elsewhere in this same review round (action/run.sh's
        # _try_baseline_set_fallback): the default must be computed
        # separately, not embedded in a ${ASSET_NAME_TEMPLATE:-...}
        # expansion, or a literal '}' inside "{profile}" silently mangles
        # the result to "abicheck-baseline-{profile.tar.zst}".
        baseline_dir = _make_baseline_dir(tmp_path)
        result, outputs = _run_action(
            {"BASELINE_PATH": str(baseline_dir), "PROFILE": "linux"},
            tmp_path,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "{profile" not in outputs["asset-name"]
        assert outputs["asset-name"] == "abicheck-baseline-linux.tar.zst"

    @pytest.mark.skipif(
        os.name == "nt", reason="PATH-shadowing setup below is POSIX-specific"
    )
    def test_falls_back_to_python_zstandard_when_zstd_binary_is_absent(
        self, tmp_path: Path
    ) -> None:
        # Regression (Codex review, P2): `tar --zstd` shells out to a
        # separate `zstd` executable, and this composite Action -- unlike
        # actions/baseline -- has no dependency-install step, so a minimal/
        # self-hosted runner without zstd pre-installed would otherwise
        # hard-fail on the DEFAULT asset-name-template alone. Simulates
        # that by giving the subprocess a PATH built from symlinks to every
        # needed tool EXCEPT zstd, so `command -v zstd` genuinely fails,
        # then confirms the script still produces a real, extractable
        # zstd archive via the Python `zstandard` fallback.
        import shutil

        baseline_dir = _make_baseline_dir(tmp_path)
        scratch_bin = tmp_path / "no-zstd-bin"
        scratch_bin.mkdir()
        for tool in ("bash", "tar", "python3", "pip", "sh", "env", "gzip", "rm"):
            resolved = shutil.which(tool)
            if resolved is not None:
                (scratch_bin / tool).symlink_to(resolved)
        assert (scratch_bin / "tar").exists(), "tar must be on the scratch PATH"
        assert not (scratch_bin / "zstd").exists()

        result, outputs = _run_action(
            {
                "BASELINE_PATH": str(baseline_dir),
                "PROFILE": "linux",
                "PATH": str(scratch_bin),
            },
            tmp_path,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "zstandard" in (result.stdout + result.stderr)
        archive_path = tmp_path / outputs["asset-name"]
        assert archive_path.is_file()

        # Confirm it's a real, valid zstd archive TarExtractor can read
        # back -- not just that the script exited 0.
        from abicheck.package import TarExtractor

        extracted = tmp_path / "extracted"
        extracted.mkdir()
        TarExtractor().extract(archive_path, extracted)
        assert (extracted / "manifest.json").is_file()
        assert (extracted / "libfoo.abicheck.json").is_file()

    def test_newline_in_resolved_name_is_rejected(self, tmp_path: Path) -> None:
        # Regression (Codex review, P1): a newline embedded in profile (or
        # asset-name-template) survives substitution into asset_name, and
        # writing "asset-name=<value-with-a-newline>" straight to
        # $GITHUB_OUTPUT lets the runner parse the remainder as ADDITIONAL,
        # attacker-chosen output key=value lines -- a real GitHub Actions
        # output-injection vector when profile/asset-name-template values
        # are influenced by external/PR-controlled metadata.
        baseline_dir = _make_baseline_dir(tmp_path)
        result, outputs = _run_action(
            {
                "BASELINE_PATH": str(baseline_dir),
                "PROFILE": "linux\nasset-name=injected.tar.zst",
            },
            tmp_path,
        )
        assert result.returncode == 1
        assert "newline" in (result.stdout + result.stderr)
        assert "asset-name" not in outputs

    def test_carriage_return_in_resolved_name_is_rejected(self, tmp_path: Path) -> None:
        baseline_dir = _make_baseline_dir(tmp_path)
        result, outputs = _run_action(
            {"BASELINE_PATH": str(baseline_dir), "PROFILE": "linux\rx"},
            tmp_path,
        )
        assert result.returncode == 1
        assert "carriage return" in (result.stdout + result.stderr)
        assert "asset-name" not in outputs

    def test_path_separator_in_resolved_name_is_rejected(self, tmp_path: Path) -> None:
        baseline_dir = _make_baseline_dir(tmp_path)
        result, outputs = _run_action(
            {
                "BASELINE_PATH": str(baseline_dir),
                "ASSET_NAME_TEMPLATE": "../escaped-{profile}.tar",
                "PROFILE": "x",
            },
            tmp_path,
        )
        assert result.returncode == 1
        assert "path separator" in (result.stdout + result.stderr)
        assert "asset-name" not in outputs

    def test_missing_baseline_path_fails(self, tmp_path: Path) -> None:
        result, _ = _run_action({}, tmp_path)
        assert result.returncode == 1
        assert "baseline-path" in (result.stdout + result.stderr)

    def test_nonexistent_baseline_path_fails(self, tmp_path: Path) -> None:
        result, _ = _run_action(
            {"BASELINE_PATH": str(tmp_path / "does-not-exist")}, tmp_path
        )
        assert result.returncode == 1
        assert "does not exist" in (result.stdout + result.stderr)

    def test_empty_profile_still_substitutes(self, tmp_path: Path) -> None:
        baseline_dir = _make_baseline_dir(tmp_path)
        result, outputs = _run_action({"BASELINE_PATH": str(baseline_dir)}, tmp_path)
        assert result.returncode == 0, result.stdout + result.stderr
        assert outputs["asset-name"] == "abicheck-baseline-.tar.zst"
