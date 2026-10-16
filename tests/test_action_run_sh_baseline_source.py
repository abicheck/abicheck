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

"""End-to-end: which baseline did the whole root Action compare against?

``test_action_run_sh_baseline_set_fallback.py`` exercises the baseline
resolution as an extracted fragment of ``action/run.sh``. This module runs
the *whole* script (through the shared full-run harness, with a stub
``abicheck`` and a stub ``gh`` on ``PATH``) to check what the job publishes
for the baseline that was used: the ``Baseline source`` step-summary block
and the ``baseline-*`` outputs, both of which are written by code outside
the extracted region.
"""

from __future__ import annotations

import json
from pathlib import Path

from _action_run_sh_harness import REQUIRES_POSIX_SHELL, _run_action, _stub_abicheck
from test_action_run_sh_baseline_set_fallback import (
    FIXTURE_TAG,
    PROFILE,
    _build_baseline_set_archive,
)

pytestmark = REQUIRES_POSIX_SHELL

_GH_SCRIPT = """#!/usr/bin/env bash
if [[ "$1" == "release" && "$2" == "download" ]]; then
  # A legacy single-snapshot asset the baseline selection must ignore.
  dest=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -D) dest="$2"; shift 2 ;;
      *) shift ;;
    esac
  done
  mkdir -p "$dest"
  echo '{}' > "$dest/libpvxs.abicheck.json"
  exit 0
fi
if [[ "$1" == "release" && "$2" == "view" ]]; then
  printf '%s' "$FIXTURE_ASSETS_JSON"
  exit 0
fi
if [[ "$1" == "api" ]]; then
  cat "$FIXTURE_ARCHIVE"
  exit 0
fi
exit 1
"""


def _valid_report() -> bytes:
    return json.dumps({"verdict": "COMPATIBLE", "changes": []}).encode("utf-8")


def _gh_on_path(bindir: Path) -> None:
    gh = bindir / "gh"
    gh.write_text(_GH_SCRIPT, encoding="utf-8")
    gh.chmod(0o755)


def _selected_env(
    tmp_path: Path,
    *,
    legacy: bool,
    project_ref: str = FIXTURE_TAG,
    tag: str = FIXTURE_TAG,
) -> dict[str, str]:
    archive = _build_baseline_set_archive(tmp_path, project_ref=project_ref)
    asset_name = f"abicheck-baseline-{PROFILE}.tar.zst"
    assets: dict = {
        "tagName": tag,
        "assets": [{"name": asset_name, "apiUrl": "fixture://asset"}],
    }
    if legacy:
        assets["assets"].append(
            {"name": "libpvxs.abicheck.json", "apiUrl": "fixture://legacy"}
        )
    return {
        "INPUT_MODE": "compare",
        "INPUT_NEW_LIBRARY": str(tmp_path / "libnew.so"),
        "INPUT_OLD_LIBRARY": "",
        "INPUT_ABI_BASELINE": "latest-release",
        "INPUT_BASELINE_PROFILE": PROFILE,
        "INPUT_BASELINE_TARGET": "libpvxs",
        "INPUT_FORMAT": "json",
        "INPUT_OUTPUT_FILE": str(tmp_path / "report.json"),
        "FIXTURE_ARCHIVE": str(archive),
        "FIXTURE_ASSETS_JSON": json.dumps(assets),
    }


def _write_new_library(tmp_path: Path) -> None:
    (tmp_path / "libnew.so").write_bytes(b"\x7fELF")


class TestBaselineSourceInTheWholeAction:
    def test_summary_and_outputs_name_the_baseline_set_used(
        self, tmp_path: Path
    ) -> None:
        bindir = _stub_abicheck(tmp_path, exit_code=0, payload=_valid_report())
        _gh_on_path(bindir)
        _write_new_library(tmp_path)
        outputs = _run_action(tmp_path, _selected_env(tmp_path, legacy=True), bindir)

        assert outputs["_exit"] == 0, outputs["_stdout"]
        assert outputs["baseline-source"] == "baseline-set"
        assert outputs["baseline-asset-name"] == f"abicheck-baseline-{PROFILE}.tar.zst"
        assert outputs["baseline-project-ref"] == FIXTURE_TAG
        assert outputs["baseline-generation"] == ""
        assert outputs["baseline-profile"] == PROFILE

        summary = outputs["_summary"]
        assert "### Baseline source" in summary
        assert "| Source | `baseline-set` |" in summary
        assert f"| Profile | `{PROFILE}` |" in summary
        assert f"| Project ref | `{FIXTURE_TAG}` |" in summary

    def test_no_release_baseline_writes_no_summary_block(self, tmp_path: Path) -> None:
        # Direct old-library, no abi-baseline: nothing was resolved from a
        # release, so the job must not claim a baseline source at all.
        bindir = _stub_abicheck(tmp_path, exit_code=0, payload=_valid_report())
        _write_new_library(tmp_path)
        (tmp_path / "libold.so").write_bytes(b"\x7fELF")
        env = {
            "INPUT_MODE": "compare",
            "INPUT_NEW_LIBRARY": str(tmp_path / "libnew.so"),
            "INPUT_OLD_LIBRARY": str(tmp_path / "libold.so"),
            "INPUT_FORMAT": "json",
            "INPUT_OUTPUT_FILE": str(tmp_path / "report.json"),
        }
        outputs = _run_action(tmp_path, env, bindir)
        assert outputs["baseline-source"] == ""
        assert "### Baseline source" not in outputs["_summary"]

    def test_a_hostile_manifest_value_cannot_add_an_output(
        self, tmp_path: Path
    ) -> None:
        # The archive's own project_ref carries a newline and a fake output
        # record; it must land in one output, flattened to a single line.
        hostile = "v1\nextra=injected"
        bindir = _stub_abicheck(tmp_path, exit_code=0, payload=_valid_report())
        _gh_on_path(bindir)
        _write_new_library(tmp_path)
        env = _selected_env(tmp_path, legacy=False, project_ref=hostile, tag=hostile)
        # The check is off here: the point is what the output carries, not
        # whether the hostile ref is accepted.
        env["INPUT_BASELINE_PROJECT_REF"] = ""
        outputs = _run_action(tmp_path, env, bindir)
        assert outputs["_exit"] == 0, outputs["_stdout"]
        assert "extra" not in outputs
        assert outputs["baseline-project-ref"] == "v1 extra=injected"
