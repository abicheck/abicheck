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

"""Behavioural tests for the root Action's baseline selection and provenance.

Companion to ``test_action_run_sh_baseline_set_fallback.py`` (which holds the
shared fixture builders and the extracted-region harness). This module covers
the selection priority (``baseline-profile`` + ``baseline-target`` select the
archive alone), ``baseline-project-ref`` (the expected manifest ``project_ref``),
and the baseline-source provenance written to the step summary and outputs.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path

import pytest
from test_action_run_sh_baseline_set_fallback import (
    _GH_STUB,
    FIXTURE_TAG,
    PROFILE,
    _baseline_region,
    _build_baseline_set_archive,
    _resolved_asset_name,
    _run_bash_script,
)

# --- Selection priority (baseline-profile + baseline-target), baseline-project-ref,
# and baseline-source reporting. These drive the same extracted region as the
# class above, with stubs that can also serve a legacy *.abicheck.json download
# and a git-ref lookup, and record every `gh` call so "was the legacy asset
# downloaded?" is an assertion rather than an inference.

_LEGACY_GH_STUB = r"""
gh() {
  if [[ "$1" == "release" && "$2" == "download" ]]; then
    echo download >> "$FIXTURE_CALLS_LOG"
    local dest=""
    while [[ $# -gt 0 ]]; do
      case "$1" in
        -D) dest="$2"; shift 2 ;;
        *) shift ;;
      esac
    done
    mkdir -p "$dest"
    local name found=0
    for name in $FIXTURE_LEGACY_NAMES; do
      echo '{}' > "$dest/$name"
      found=1
    done
    [[ "$found" == 1 ]]
    return
  fi
  if [[ "$1" == "release" && "$2" == "view" ]]; then
    printf '%s' "${FIXTURE_ASSETS_JSON:-{\"assets\": []\}}"
    return 0
  fi
  if [[ "$1" == "api" ]]; then
    echo api >> "$FIXTURE_CALLS_LOG"
    if [[ -n "${FIXTURE_ARCHIVE:-}" ]]; then
      cat "$FIXTURE_ARCHIVE"
      return 0
    fi
    return 1
  fi
  return 1
}
"""

# Like _GH_STUB, but also answers the two git-ref documents baseline-project-ref
# =commit reads, keyed on the request path.
_GIT_REF_GH_STUB = r"""
gh() {
  if [[ "$1" == "release" && "$2" == "download" ]]; then
    return 1
  fi
  if [[ "$1" == "release" && "$2" == "view" ]]; then
    printf '%s' "${FIXTURE_ASSETS_JSON:-{\"assets\": []\}}"
    return 0
  fi
  if [[ "$1" == "api" ]]; then
    echo "api $2" >> "$FIXTURE_CALLS_LOG"
    case "$2" in
      repos/*/git/ref/tags/*) printf '%s' "$FIXTURE_REF_JSON" ;;
      repos/*/git/tags/*) printf '%s' "$FIXTURE_TAG_OBJECT_JSON" ;;
      *) cat "$FIXTURE_ARCHIVE" ;;
    esac
    return 0
  fi
  return 1
}
"""

_RECORD_TRAILER = (
    '\necho "REACHED_END SOURCE=${BASELINE_SOURCE:-} ASSET=${BASELINE_SOURCE_ASSET:-}'
    " FILE=${BASELINE_FILE:-} PROJECT_REF=${BASELINE_SET_PROJECT_REF:-}"
    ' GENERATION=${BASELINE_SET_GENERATION:-} PROFILE=${BASELINE_SET_PROFILE:-}"\n'
)

SHA_COMMIT = "1" * 40
SHA_TAG_OBJECT = "2" * 40


def _run_with_stub(
    stub: str,
    env_extra: dict[str, str],
    tmp_path: Path,
    *,
    trailer: str = _RECORD_TRAILER,
) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, **env_extra}
    # A CI runner exports these; the tests must not inherit them.
    env.pop("GITHUB_REPOSITORY", None)
    env.pop("INPUT_BASELINE_PROJECT_REF", None)
    env.update(env_extra)
    env.setdefault("FIXTURE_CALLS_LOG", str(tmp_path / "gh-calls.log"))
    Path(env["FIXTURE_CALLS_LOG"]).touch()
    if env.get("FIXTURE_ARCHIVE") and "FIXTURE_ASSETS_JSON" not in env_extra:
        asset_name = _resolved_asset_name(env)
        env["FIXTURE_ASSETS_JSON"] = json.dumps(
            {
                "tagName": env.get("FIXTURE_TAG", FIXTURE_TAG),
                "assets": [{"name": asset_name, "apiUrl": "fixture://asset"}],
            }
        )
    script = (
        'MODE="${INPUT_MODE:-compare}"\n'
        'FORCE_AUDIT_ONLY="${INPUT_AUDIT:-false}"\n'
        + stub
        + _baseline_region()
        + trailer
    )
    return _run_bash_script(script, env)


def _gh_calls(tmp_path: Path) -> str:
    return (tmp_path / "gh-calls.log").read_text(encoding="utf-8")


def _record_fields(stdout: str) -> dict[str, str]:
    line = next(x for x in stdout.splitlines() if x.startswith("REACHED_END"))
    fields = {}
    for token in line.split()[1:]:
        key, _, value = token.partition("=")
        fields[key] = value
    return fields


def _selection_env(tmp_path: Path, **extra: str) -> dict[str, str]:
    env = {
        "INPUT_MODE": "compare",
        "INPUT_ABI_BASELINE": "latest-release",
        "INPUT_BASELINE_PROFILE": PROFILE,
        "INPUT_BASELINE_TARGET": "libpvxs",
    }
    env.update(extra)
    return env


class TestBaselineSetSelectionPriority:
    """baseline-profile + baseline-target select ONE source: the archive.

    Before this, a single stray *.abicheck.json asset on the release silently
    replaced the selected baseline, and two of them failed the job with
    "Multiple *.abicheck.json assets found" -- even though the caller had
    named exactly which baseline they wanted.
    """

    def test_archive_wins_over_a_legacy_asset_and_legacy_is_never_downloaded(
        self, tmp_path: Path
    ) -> None:
        archive = _build_baseline_set_archive(tmp_path)
        result = _run_with_stub(
            _LEGACY_GH_STUB,
            _selection_env(
                tmp_path,
                FIXTURE_ARCHIVE=str(archive),
                FIXTURE_LEGACY_NAMES="libpvxs.abicheck.json",
            ),
            tmp_path,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        fields = _record_fields(result.stdout)
        assert fields["SOURCE"] == "baseline-set"
        assert "baseline-set-extracted" in fields["FILE"]
        assert "download" not in _gh_calls(tmp_path).split()

    def test_two_legacy_assets_are_not_ambiguous_when_selected(
        self, tmp_path: Path
    ) -> None:
        archive = _build_baseline_set_archive(tmp_path)
        result = _run_with_stub(
            _LEGACY_GH_STUB,
            _selection_env(
                tmp_path,
                FIXTURE_ARCHIVE=str(archive),
                FIXTURE_LEGACY_NAMES="libpvxs.abicheck.json other.abicheck.json",
            ),
            tmp_path,
        )
        combined = result.stdout + result.stderr
        assert result.returncode == 0, combined
        assert "Multiple" not in combined
        assert _record_fields(result.stdout)["SOURCE"] == "baseline-set"

    def test_legacy_asset_without_the_archive_is_unavailable_and_names_the_archive(
        self, tmp_path: Path
    ) -> None:
        result = _run_with_stub(
            _LEGACY_GH_STUB,
            _selection_env(tmp_path, FIXTURE_LEGACY_NAMES="libpvxs.abicheck.json"),
            tmp_path,
        )
        combined = result.stdout + result.stderr
        assert result.returncode == 1, combined
        assert f"{PROFILE}" in combined
        assert _resolved_asset_name({"INPUT_BASELINE_PROFILE": PROFILE}) in combined
        assert "not found on release" in combined
        assert "REACHED_END" not in result.stdout
        # Never fell back to the legacy asset it could see.
        assert "download" not in _gh_calls(tmp_path).split()

    def test_unselected_legacy_asset_still_resolves_and_is_recorded_as_legacy(
        self, tmp_path: Path
    ) -> None:
        result = _run_with_stub(
            _LEGACY_GH_STUB,
            {
                "INPUT_MODE": "compare",
                "INPUT_ABI_BASELINE": "latest-release",
                "FIXTURE_LEGACY_NAMES": "libpvxs.abicheck.json",
            },
            tmp_path,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        fields = _record_fields(result.stdout)
        assert fields["SOURCE"] == "legacy-asset"
        assert fields["ASSET"] == "libpvxs.abicheck.json"
        assert fields["PROJECT_REF"] == ""


class TestBaselineProjectRef:
    """baseline-project-ref: what the selected archive's project_ref must be.

    Unset (the action.yml default 'tag') and explicit '' are different: '' is
    the only way to turn the check off, so an unset variable here must behave
    as 'tag', never as ''.
    """

    def test_mismatched_project_ref_fails_with_a_typed_outcome(
        self, tmp_path: Path
    ) -> None:
        archive = _build_baseline_set_archive(tmp_path, project_ref="v9.9.9")
        result = _run_with_stub(
            _GH_STUB,
            _selection_env(
                tmp_path,
                INPUT_ABI_BASELINE="v2.3.0",
                FIXTURE_ARCHIVE=str(archive),
                FIXTURE_TAG="v2.3.0",
            ),
            tmp_path,
        )
        combined = result.stdout + result.stderr
        assert result.returncode == 1, combined
        assert "outcome: wrong_project_ref" in combined
        assert "REACHED_END" not in result.stdout

    def test_explicit_empty_value_turns_the_check_off(self, tmp_path: Path) -> None:
        archive = _build_baseline_set_archive(tmp_path, project_ref="v9.9.9")
        result = _run_with_stub(
            _GH_STUB,
            _selection_env(
                tmp_path,
                INPUT_ABI_BASELINE="v2.3.0",
                INPUT_BASELINE_PROJECT_REF="",
                FIXTURE_ARCHIVE=str(archive),
                FIXTURE_TAG="v2.3.0",
            ),
            tmp_path,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert _record_fields(result.stdout)["SOURCE"] == "baseline-set"

    @pytest.mark.parametrize(
        ("release_tag", "project_ref", "expect_ok"),
        [
            ("v5.0.0", "v5.0.0", True),
            ("v5.0.0", "v1.0.0", False),
        ],
    )
    def test_latest_release_checks_against_the_resolved_tag(
        self,
        tmp_path: Path,
        release_tag: str,
        project_ref: str,
        expect_ok: bool,
    ) -> None:
        # A latest-release lookup names no tag in abi-baseline itself; the
        # tag the release reports is the one 'tag' means.
        archive = _build_baseline_set_archive(tmp_path, project_ref=project_ref)
        result = _run_with_stub(
            _GH_STUB,
            _selection_env(
                tmp_path,
                FIXTURE_ARCHIVE=str(archive),
                FIXTURE_TAG=release_tag,
            ),
            tmp_path,
        )
        combined = result.stdout + result.stderr
        if expect_ok:
            assert result.returncode == 0, combined
        else:
            assert result.returncode == 1, combined
            assert "outcome: wrong_project_ref" in combined

    def test_commit_matches_a_lightweight_tag_commit(self, tmp_path: Path) -> None:
        archive = _build_baseline_set_archive(tmp_path, project_ref=SHA_COMMIT)
        ref = {
            "ref": "refs/tags/v2.3.0",
            "object": {"type": "commit", "sha": SHA_COMMIT},
        }
        result = _run_with_stub(
            _GIT_REF_GH_STUB,
            _selection_env(
                tmp_path,
                INPUT_ABI_BASELINE="v2.3.0",
                INPUT_BASELINE_PROJECT_REF="commit",
                GITHUB_REPOSITORY="abicheck/abicheck",
                FIXTURE_ARCHIVE=str(archive),
                FIXTURE_REF_JSON=json.dumps(ref),
            ),
            tmp_path,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert _record_fields(result.stdout)["PROJECT_REF"] == SHA_COMMIT
        assert "api repos/abicheck/abicheck/git/ref/tags/v2.3.0" in _gh_calls(tmp_path)

    @pytest.mark.parametrize(
        ("project_ref", "expect_ok"),
        [(SHA_COMMIT, True), (SHA_TAG_OBJECT, False)],
    )
    def test_commit_peels_an_annotated_tag_to_its_commit(
        self, tmp_path: Path, project_ref: str, expect_ok: bool
    ) -> None:
        # The annotated tag's own object SHA is NOT the commit anyone
        # captured: only the peeled commit is an acceptable project_ref.
        archive = _build_baseline_set_archive(tmp_path, project_ref=project_ref)
        ref = {
            "ref": "refs/tags/v2.3.0",
            "object": {"type": "tag", "sha": SHA_TAG_OBJECT},
        }
        tag_object = {
            "sha": SHA_TAG_OBJECT,
            "object": {"type": "commit", "sha": SHA_COMMIT},
        }
        result = _run_with_stub(
            _GIT_REF_GH_STUB,
            _selection_env(
                tmp_path,
                INPUT_ABI_BASELINE="v2.3.0",
                INPUT_BASELINE_PROJECT_REF="commit",
                GITHUB_REPOSITORY="abicheck/abicheck",
                FIXTURE_ARCHIVE=str(archive),
                FIXTURE_REF_JSON=json.dumps(ref),
                FIXTURE_TAG_OBJECT_JSON=json.dumps(tag_object),
            ),
            tmp_path,
        )
        combined = result.stdout + result.stderr
        if expect_ok:
            assert result.returncode == 0, combined
        else:
            assert result.returncode == 1, combined
            assert "outcome: wrong_project_ref" in combined
        assert f"api repos/abicheck/abicheck/git/tags/{SHA_TAG_OBJECT}" in _gh_calls(
            tmp_path
        )

    def test_commit_fails_closed_when_the_tag_cannot_be_read(
        self, tmp_path: Path
    ) -> None:
        archive = _build_baseline_set_archive(tmp_path)
        result = _run_with_stub(
            _GIT_REF_GH_STUB,
            _selection_env(
                tmp_path,
                INPUT_ABI_BASELINE="v2.3.0",
                INPUT_BASELINE_PROJECT_REF="commit",
                # No GITHUB_REPOSITORY: the commit cannot be looked up at all.
                FIXTURE_ARCHIVE=str(archive),
            ),
            tmp_path,
        )
        combined = result.stdout + result.stderr
        assert result.returncode == 1, combined
        assert "could not be resolved to a commit" in combined

    def test_full_sha_is_compared_literally(self, tmp_path: Path) -> None:
        archive = _build_baseline_set_archive(tmp_path, project_ref=SHA_COMMIT)
        result = _run_with_stub(
            _GH_STUB,
            _selection_env(
                tmp_path,
                INPUT_ABI_BASELINE="v2.3.0",
                INPUT_BASELINE_PROJECT_REF=SHA_COMMIT,
                FIXTURE_ARCHIVE=str(archive),
                FIXTURE_TAG="v2.3.0",
            ),
            tmp_path,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    def test_an_unrecognised_value_is_refused_before_resolving(
        self, tmp_path: Path
    ) -> None:
        archive = _build_baseline_set_archive(tmp_path)
        result = _run_with_stub(
            _GH_STUB,
            _selection_env(
                tmp_path,
                INPUT_ABI_BASELINE="v2.3.0",
                INPUT_BASELINE_PROJECT_REF="v2.3.0",
                FIXTURE_ARCHIVE=str(archive),
                FIXTURE_TAG="v2.3.0",
            ),
            tmp_path,
        )
        combined = result.stdout + result.stderr
        assert result.returncode == 1, combined
        assert "is not one of" in combined
        assert "REACHED_END" not in result.stdout


class TestBaselineSourceReporting:
    """Baseline source provenance: what the outputs and summary say was used."""

    def test_resolved_set_records_its_manifest_fields(self, tmp_path: Path) -> None:
        archive = _build_baseline_set_archive(tmp_path, baseline_generation=3)
        result = _run_with_stub(
            _GH_STUB,
            _selection_env(
                tmp_path,
                FIXTURE_ARCHIVE=str(archive),
                INPUT_BASELINE_GENERATION="3",
            ),
            tmp_path,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        fields = _record_fields(result.stdout)
        assert fields["SOURCE"] == "baseline-set"
        assert fields["ASSET"] == _resolved_asset_name(
            {"INPUT_BASELINE_PROFILE": PROFILE}
        )
        assert fields["PROJECT_REF"] == FIXTURE_TAG
        assert fields["GENERATION"] == "3"
        assert fields["PROFILE"] == PROFILE

    def test_direct_file_path_is_recorded_as_direct_path(self, tmp_path: Path) -> None:
        baseline = tmp_path / "abi-baseline.json"
        baseline.write_text("{}", encoding="utf-8")
        result = _run_with_stub(
            _GH_STUB,
            {"INPUT_MODE": "compare", "INPUT_ABI_BASELINE": str(baseline)},
            tmp_path,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        fields = _record_fields(result.stdout)
        assert fields["SOURCE"] == "direct-path"
        assert fields["ASSET"] == str(baseline)


def _region_script(body: str) -> str:
    return (
        'MODE="${INPUT_MODE:-compare}"\n'
        'FORCE_AUDIT_ONLY="${INPUT_AUDIT:-false}"\n'
        + _baseline_region()
        + "\n"
        + body
        + "\n"
    )


class TestBaselineSourceOutputsAndSummary:
    def test_summary_block_names_the_baseline_set_and_its_manifest(
        self, tmp_path: Path
    ) -> None:
        script = _region_script(
            "BASELINE_SOURCE=baseline-set\n"
            'BASELINE_SOURCE_ASSET="abicheck-baseline-x.tar.zst"\n'
            'BASELINE_SET_PROJECT_REF="v1.2.3"\n'
            'BASELINE_SET_GENERATION="7"\n'
            'BASELINE_SET_PROFILE="linux-x86_64"\n'
            "_write_baseline_source_summary\n"
        )
        result = _run_bash_script(script, dict(os.environ))
        assert result.returncode == 0, result.stderr
        out = result.stdout
        assert "### Baseline source" in out
        assert "| Source | `baseline-set` |" in out
        assert "| Asset | `abicheck-baseline-x.tar.zst` |" in out
        assert "| Project ref | `v1.2.3` |" in out
        assert "| Baseline generation | `7` |" in out
        assert "| Profile | `linux-x86_64` |" in out

    def test_summary_block_for_a_legacy_asset_has_no_manifest_rows(
        self, tmp_path: Path
    ) -> None:
        script = _region_script(
            "BASELINE_SOURCE=legacy-asset\n"
            'BASELINE_SOURCE_ASSET="libpvxs.abicheck.json"\n'
            "_write_baseline_source_summary\n"
        )
        result = _run_bash_script(script, dict(os.environ))
        assert result.returncode == 0, result.stderr
        assert "| Source | `legacy-asset` |" in result.stdout
        assert "Project ref" not in result.stdout
        assert "Profile" not in result.stdout

    def test_no_summary_block_when_no_release_baseline_was_used(self) -> None:
        result = _run_bash_script(
            _region_script("_write_baseline_source_summary\n"), dict(os.environ)
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == ""

    def test_summary_cell_escapes_a_pipe_so_it_cannot_open_a_column(self) -> None:
        script = _region_script(
            "BASELINE_SOURCE=baseline-set\n"
            'BASELINE_SOURCE_ASSET="a|b"\n'
            'BASELINE_SET_PROJECT_REF="v1"\n'
            'BASELINE_SET_PROFILE="p"\n'
            "_write_baseline_source_summary\n"
        )
        result = _run_bash_script(script, dict(os.environ))
        assert "| Asset | `a\\|b` |" in result.stdout

    def test_outputs_are_one_record_each_even_for_a_hostile_manifest_value(
        self,
    ) -> None:
        # A publisher-controlled project_ref with a newline and a fake
        # workflow command must not end its own GITHUB_OUTPUT record early.
        hostile = "v1\n::add-mask::secret\nextra=injected"
        script = _region_script(
            "BASELINE_SOURCE=baseline-set\n"
            'BASELINE_SOURCE_ASSET="abicheck-baseline-x.tar.zst"\n'
            f"BASELINE_SET_PROJECT_REF={shlex.quote(hostile)}\n"
            'BASELINE_SET_GENERATION=""\n'
            'BASELINE_SET_PROFILE="p"\n'
            "_emit_baseline_source_outputs\n"
        )
        result = _run_bash_script(script, dict(os.environ))
        assert result.returncode == 0, result.stderr
        lines = result.stdout.splitlines()
        assert len(lines) == 5, lines
        assert [line.split("=", 1)[0] for line in lines] == [
            "baseline-source",
            "baseline-asset-name",
            "baseline-project-ref",
            "baseline-generation",
            "baseline-profile",
        ]
        assert "extra=injected" not in lines
        assert lines[2] == "baseline-project-ref=v1 ::add-mask::secret extra=injected"


class TestHostileSummaryValuesAreInert:
    def test_command_substitution_in_summary_is_inert(self, tmp_path: Path) -> None:
        sentinel = tmp_path / "pwned"
        script = _region_script(
            "BASELINE_SOURCE=baseline-set\n"
            'BASELINE_SOURCE_ASSET="a"\n'
            f"BASELINE_SET_PROJECT_REF='$(touch {sentinel})'\n"
            'BASELINE_SET_PROFILE="p"\n'
            "_write_baseline_source_summary\n"
        )
        result = _run_bash_script(script, dict(os.environ))
        assert result.returncode == 0, result.stderr
        assert not sentinel.exists()
        assert f"$(touch {sentinel})" in result.stdout
