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

"""``actions/resolve-baseline`` with ``kind: members``.

Split out of ``test_action_resolve_baseline.py`` rather than appended to
it: that module is already at this repository's per-file test ceiling, and
member-set resolution is its own question -- naming SEVERAL snapshots
inside one staged set, on a candidate path as readily as a baseline one.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from _workflow_exec import bash_executable, require_bash

ACTION_DIR = Path(__file__).resolve().parents[1] / "actions" / "resolve-baseline"
RUN_SH = ACTION_DIR / "run.sh"
PROFILE = "linux-x86_64-gcc13-release"


def _run_action(
    env_extra: dict[str, str], cwd: Path
) -> tuple[subprocess.CompletedProcess[str], dict[str, str]]:
    """Invoke the real script end-to-end with a GITHUB_OUTPUT file."""
    require_bash()
    github_output = cwd / "github_output"
    github_output.write_text("")
    base_env = {k: v for k, v in os.environ.items() if not k.startswith("INPUT_")}
    result = subprocess.run(
        [bash_executable(), str(RUN_SH)],
        capture_output=True,
        text=True,
        env={
            **base_env,
            "GITHUB_OUTPUT": str(github_output),
            "ACTION_PATH": str(ACTION_DIR),
            **env_extra,
        },
        cwd=cwd,
        check=False,
    )
    outputs: dict[str, str] = {}
    for line in github_output.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            outputs[key] = value
    return result, outputs


def _write_manifest(
    baseline_dir: Path,
    *,
    profile: str = PROFILE,
    baseline_generation: int | None = None,
    artifacts: list[dict] | None = None,
) -> None:
    baseline_dir.mkdir(parents=True, exist_ok=True)
    (baseline_dir / "manifest.json").write_text(
        json.dumps(
            {
                "manifest_version": 1,
                "project_ref": "v1.0.0",
                "profile": profile,
                "snapshot_schema": 9,
                "fact_set": None,
                "baseline_generation": baseline_generation,
                "artifacts": artifacts if artifacts is not None else [],
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _target_artifact(name: str) -> dict:
    # No real "sha256": these shell-level tests are not about digest
    # verification (tests/test_baseline_set.py covers that at unit level),
    # and an absent recorded digest makes the check a no-op rather than a
    # false mismatch against placeholder snapshot content.
    return {
        "library": name,
        "artifact": f"build/{name}.so",
        "snapshot": f"{name}.abicheck.json",
        "sha256": "",
    }


class TestMemberSetResolution:
    """``kind: members`` -- name SEVERAL snapshots inside one staged set.

    This is how a multi-component project names its *candidate* snapshots
    before comparing any of them. The alternative every integrator reached for
    was re-parsing ``manifest.json`` in shell, which reliably picks up the
    manifest's ``artifact`` field -- the producing runner's absolute binary
    path, meaningless once the set has been downloaded into another job --
    instead of the portable ``snapshot`` entry, and skips content identity
    altogether. Both of those are pinned below.
    """

    @staticmethod
    def _staged(tmp_path: Path, names: tuple[str, ...] = ("libfoo", "libbar")) -> Path:
        baseline_dir = tmp_path / "set"
        _write_manifest(
            baseline_dir,
            artifacts=[_target_artifact(name) for name in names],
        )
        for name in names:
            (baseline_dir / f"{name}.abicheck.json").write_text(
                json.dumps({"library": name}), encoding="utf-8"
            )
        return baseline_dir

    @staticmethod
    def _env(baseline_dir: Path, members: list[str], **extra: str) -> dict[str, str]:
        return {
            "INPUT_BASELINE_PATH": str(baseline_dir),
            "INPUT_CHANNEL": "candidate",
            "INPUT_KIND": "members",
            "INPUT_BUNDLE_MEMBERS": json.dumps(members),
            "INPUT_PROFILE": PROFILE,
            "INPUT_REQUIRED": "true",
            **extra,
        }

    def test_every_member_resolves_to_its_portable_snapshot(
        self, tmp_path: Path
    ) -> None:
        baseline_dir = self._staged(tmp_path)
        result, outputs = _run_action(
            self._env(baseline_dir, ["libfoo", "libbar"]), tmp_path
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert outputs["outcome"] == "resolved"
        paths = json.loads(outputs["snapshot-paths"])
        assert set(paths) == {"libfoo", "libbar"}
        for name, path in paths.items():
            # The portable in-set name, never the manifest's `artifact`
            # (`build/<name>.so`, the producing runner's own path).
            assert Path(path).name == f"{name}.abicheck.json"
            assert Path(path).is_file()
            assert "build/" not in path

    def test_each_member_reports_its_own_row(self, tmp_path: Path) -> None:
        baseline_dir = self._staged(tmp_path)
        _, outputs = _run_action(
            self._env(baseline_dir, ["libfoo", "libbar"]), tmp_path
        )
        rows = json.loads(outputs["members"])
        assert [row["target"] for row in rows] == ["libfoo", "libbar"]
        assert {row["outcome"] for row in rows} == {"resolved"}

    def test_a_missing_member_is_a_real_taxonomy_outcome(self, tmp_path: Path) -> None:
        """Not a synthetic "partial": a caller branching on ``outcome`` must
        see a value the existing vocabulary already defines."""
        baseline_dir = self._staged(tmp_path)
        result, outputs = _run_action(
            self._env(baseline_dir, ["libfoo", "libabsent"]), tmp_path
        )
        assert result.returncode == 1
        assert outputs["outcome"] == "ambiguous"
        rows = json.loads(outputs["members"])
        assert [row["outcome"] for row in rows] == ["resolved", "ambiguous"]
        # The member that DID resolve keeps its path: one member's failure
        # never costs the others their diagnostics.
        assert json.loads(outputs["snapshot-paths"]).keys() == {"libfoo"}

    def test_a_duplicate_member_is_a_usage_error(self, tmp_path: Path) -> None:
        """Last-one-wins would hand back one path for a member named twice,
        which is indistinguishable from resolving it once -- and hides that the
        caller's own declaration disagrees with itself.

        The resolver exits 64 (the repo-wide usage-error code); ``run.sh``
        translates that to this Action's own hard failure, same as every other
        usage error it reports.
        """
        baseline_dir = self._staged(tmp_path)
        result, _ = _run_action(self._env(baseline_dir, ["libfoo", "libfoo"]), tmp_path)
        assert result.returncode == 1
        assert "usage error" in result.stdout
        assert "more than once" in result.stderr

    def test_an_empty_member_list_is_refused(self, tmp_path: Path) -> None:
        baseline_dir = self._staged(tmp_path)
        result, _ = _run_action(self._env(baseline_dir, []), tmp_path)
        assert result.returncode != 0
        assert "bundle-members" in result.stdout + result.stderr

    def test_the_wrong_profile_refuses_the_whole_set(self, tmp_path: Path) -> None:
        baseline_dir = self._staged(tmp_path)
        _, outputs = _run_action(
            self._env(baseline_dir, ["libfoo"], INPUT_PROFILE="some-other-profile"),
            tmp_path,
        )
        assert outputs["outcome"] == "wrong_profile"

    def test_an_unexpected_project_ref_refuses_the_whole_set(
        self, tmp_path: Path
    ) -> None:
        """Applies to a CANDIDATE path too, not only a baseline one -- which is
        the point of routing both through one resolver."""
        baseline_dir = self._staged(tmp_path)
        _, outputs = _run_action(
            self._env(
                baseline_dir, ["libfoo"], INPUT_EXPECTED_PROJECT_REF="not-the-ref"
            ),
            tmp_path,
        )
        assert outputs["outcome"] == "wrong_project_ref"

    def test_an_unexpected_generation_refuses_the_whole_set(
        self, tmp_path: Path
    ) -> None:
        baseline_dir = tmp_path / "set"
        _write_manifest(
            baseline_dir,
            baseline_generation=1,
            artifacts=[_target_artifact("libfoo")],
        )
        (baseline_dir / "libfoo.abicheck.json").write_text("{}", encoding="utf-8")
        _, outputs = _run_action(
            self._env(baseline_dir, ["libfoo"], INPUT_EXPECTED_BASELINE_GENERATION="2"),
            tmp_path,
        )
        assert outputs["outcome"] == "stale_generation"

    def test_a_snapshot_pointing_out_of_the_set_is_refused(
        self, tmp_path: Path
    ) -> None:
        """The set travels as an artifact, so its own manifest is data."""
        outside = tmp_path / "outside.json"
        outside.write_text("{}", encoding="utf-8")
        baseline_dir = tmp_path / "set"
        _write_manifest(
            baseline_dir,
            artifacts=[
                {
                    "library": "libfoo",
                    "artifact": "build/libfoo.so",
                    "snapshot": "../outside.json",
                    "sha256": "",
                }
            ],
        )
        result, outputs = _run_action(self._env(baseline_dir, ["libfoo"]), tmp_path)
        assert result.returncode != 0
        assert outputs["outcome"] != "resolved"

    def test_kinds_target_and_bundle_still_report_empty_set_fields(
        self, tmp_path: Path
    ) -> None:
        """One key set for every branch, so a caller's expression never reads a
        key some paths simply never define."""
        baseline_dir = self._staged(tmp_path)
        _, outputs = _run_action(
            {
                "INPUT_BASELINE_PATH": str(baseline_dir),
                "INPUT_CHANNEL": "accepted-main",
                "INPUT_KIND": "target",
                "INPUT_TARGET": "libfoo",
                "INPUT_PROFILE": PROFILE,
                "INPUT_REQUIRED": "true",
            },
            tmp_path,
        )
        assert outputs["snapshot-paths"] == "{}"
        assert outputs["members"] == "[]"
