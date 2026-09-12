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

"""Behavioral tests for ``action/run.sh``'s ``--dry-run``/``abi-baseline``
interaction (Codex review).

An unresolved ``abi-baseline`` is the one deliberate exception
``action.yml``'s ``dry-run`` description carves out (tolerated rather than
hard-failed, since a preview shouldn't require the comparison already be
resolvable) -- but the baseline auto-fetch block used to run (and
``exit 1`` on a missing release/token/asset) before any mode branch ever
consulted ``INPUT_DRY_RUN`` -- so a workflow previewing its config with
`dry-run: true` plus an `abi-baseline` that hadn't been published yet got a
hard failure instead of the promised no-op preview.

These tests extract the relevant fragment verbatim from run.sh (the same
"parse the real file, don't hand-copy it" discipline as
``test_action_run_sh_legacy_aliases.py``) rather than re-implementing the
logic, and stub out ``gh`` (not available/authenticated in the test
environment) with a shell function.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from _workflow_exec import bash_executable, require_bash

RUN_SH = Path(__file__).resolve().parents[1] / "action" / "run.sh"
_START_MARKER = "_baseline_unavailable() {"
_END_MARKER = 'if [[ "$MODE" == "dump" ]]; then'


def _baseline_region() -> str:
    """The baseline auto-fetch block, extracted verbatim from run.sh."""
    text = RUN_SH.read_text(encoding="utf-8")
    start = text.index(_START_MARKER)
    end = text.index(_END_MARKER, start)
    return text[start:end]


_FAILING_GH_STUB = "gh() { return 1; }\n"


def _run_bash_script(
    script: str, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    """Run *script* via a temp file, not ``bash -c "<script>"``.

    The extracted ``_baseline_region()`` has grown past several KB (G30's
    release-contract baseline-set fallback added its own function to the
    same region) -- large enough that Windows' Git-Bash `-c` argument
    passing truncates it mid-parse, surfacing as a bash "unexpected end of
    file" syntax error purely from where the string got cut, not a real
    syntax error in the script itself (confirmed: identical content passed
    via a file runs cleanly). A temp-file invocation has no such
    command-line-length ceiling on any platform.
    """
    require_bash()
    fd, path = tempfile.mkstemp(suffix=".sh")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(script)
        return subprocess.run(
            [bash_executable(), path],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
    finally:
        os.unlink(path)


class TestDryRunToleratesUnavailableBaseline:
    def _run(
        self,
        env_extra: dict[str, str],
        *,
        gh_stub: str = _FAILING_GH_STUB,
    ) -> subprocess.CompletedProcess[str]:
        # MODE/FORCE_AUDIT_ONLY are set earlier in run.sh (outside the
        # extracted region); the baseline block reads both, so the harness
        # must set them too.
        script = (
            'MODE="${INPUT_MODE:-compare}"\n'
            'FORCE_AUDIT_ONLY="${INPUT_AUDIT:-false}"\n'
            + gh_stub
            + _baseline_region()
            + '\necho "REACHED_END OLD_LIBRARY=${INPUT_OLD_LIBRARY:-} '
            'AGAINST=${INPUT_AGAINST:-}"\n'
        )
        env = {**os.environ, **env_extra}
        return _run_bash_script(script, env)

    def test_non_dry_run_still_fails_hard_on_unavailable_baseline(self) -> None:
        """Baseline gate for real invocations is unchanged: still exit 1."""
        result = self._run(
            {"INPUT_MODE": "compare", "INPUT_ABI_BASELINE": "latest-release"}
        )
        assert result.returncode == 1
        assert "REACHED_END" not in result.stdout

    def test_dry_run_exits_0_instead_of_failing_on_unavailable_baseline(self) -> None:
        """Regression: --dry-run must never hard-fail on a missing baseline
        -- action.yml documents this as the one deliberate exception to
        dry-run's own contract (a malformed invocation or an unsatisfiable
        requested depth still exits nonzero)."""
        result = self._run(
            {
                "INPUT_MODE": "compare",
                "INPUT_ABI_BASELINE": "latest-release",
                "INPUT_DRY_RUN": "true",
            }
        )
        assert result.returncode == 0, result.stderr
        assert "::warning::" in result.stdout
        # Nothing else to preview (no other old-library given) -- the block
        # reports and exits before reaching the harness's trailing echo.
        assert "REACHED_END" not in result.stdout

    def test_dry_run_with_explicit_old_library_still_proceeds(self) -> None:
        """An explicitly-given old-library must not be discarded just because
        the (redundant) baseline fetch also failed under --dry-run."""
        result = self._run(
            {
                "INPUT_MODE": "compare",
                "INPUT_ABI_BASELINE": "latest-release",
                "INPUT_DRY_RUN": "true",
                "INPUT_OLD_LIBRARY": "libfoo.so.1",
            }
        )
        assert result.returncode == 0, result.stderr
        assert "REACHED_END OLD_LIBRARY=libfoo.so.1" in result.stdout

    def test_direct_file_path_baseline_unaffected(self, tmp_path: Path) -> None:
        """A direct existing-file abi-baseline never calls gh at all — must
        keep working exactly as before, dry-run or not."""
        baseline = tmp_path / "abi-baseline.json"
        baseline.write_text("{}")
        result = self._run(
            {"INPUT_MODE": "compare", "INPUT_ABI_BASELINE": str(baseline)},
            gh_stub="",
        )
        assert result.returncode == 0, result.stderr
        assert f"REACHED_END OLD_LIBRARY={baseline}" in result.stdout


class TestGhReleaseDownloadRepoFlag:
    """Regression (Codex review): `gh release download` relies on local git
    repo context ("the latest release in the project") when no -R/--repo is
    given, so a job that never ran actions/checkout (e.g. comparing
    downloaded release artifacts only) would fail before ever reaching a
    missing-asset error. Stubs `gh` to record its own argv to a file
    (real gh isn't available/authenticated in the test environment) instead
    of asserting behavior indirectly through exit codes.
    """

    def _run(
        self, env_extra: dict[str, str], argv_file: Path
    ) -> subprocess.CompletedProcess[str]:
        script = (
            'MODE="${INPUT_MODE:-compare}"\n'
            'FORCE_AUDIT_ONLY="${INPUT_AUDIT:-false}"\n'
            "gh() {\n"
            '  printf "%s\\n" "$@" > "$GH_ARGV_FILE"\n'
            '  local dir=""; while [[ $# -gt 0 ]]; do '
            '[[ "$1" == "-D" ]] && dir="$2"; shift; done\n'
            '  touch "$dir/lib.abicheck.json"\n'
            "}\n" + _baseline_region() + '\necho "REACHED_END"\n'
        )
        env = {**os.environ, **env_extra, "GH_ARGV_FILE": str(argv_file)}
        return _run_bash_script(script, env)

    def test_latest_release_passes_repo_flag(self, tmp_path: Path) -> None:
        argv_file = tmp_path / "gh_argv"
        result = self._run(
            {
                "INPUT_MODE": "compare",
                "INPUT_ABI_BASELINE": "latest-release",
                "GITHUB_REPOSITORY": "abicheck/abicheck",
            },
            argv_file,
        )
        assert result.returncode == 0, result.stderr
        argv = argv_file.read_text(encoding="utf-8").splitlines()
        assert "-R" in argv, argv
        assert argv[argv.index("-R") + 1] == "abicheck/abicheck"

    def test_tagged_release_passes_repo_flag(self, tmp_path: Path) -> None:
        argv_file = tmp_path / "gh_argv"
        result = self._run(
            {
                "INPUT_MODE": "compare",
                "INPUT_ABI_BASELINE": "v1.2.3",
                "GITHUB_REPOSITORY": "abicheck/abicheck",
            },
            argv_file,
        )
        assert result.returncode == 0, result.stderr
        argv = argv_file.read_text(encoding="utf-8").splitlines()
        assert "-R" in argv, argv
        assert argv[argv.index("-R") + 1] == "abicheck/abicheck"

    def test_no_repo_flag_when_github_repository_unset(self, tmp_path: Path) -> None:
        """Sanity: -R is only added when the repo is actually known -- an
        empty flag value would be worse than omitting it."""
        argv_file = tmp_path / "gh_argv"
        env = {**os.environ}
        env.pop("GITHUB_REPOSITORY", None)
        script = (
            'MODE="${INPUT_MODE:-compare}"\n'
            'FORCE_AUDIT_ONLY="${INPUT_AUDIT:-false}"\n'
            "gh() {\n"
            '  printf "%s\\n" "$@" > "$GH_ARGV_FILE"\n'
            '  local dir=""; while [[ $# -gt 0 ]]; do '
            '[[ "$1" == "-D" ]] && dir="$2"; shift; done\n'
            '  touch "$dir/lib.abicheck.json"\n'
            "}\n" + _baseline_region() + '\necho "REACHED_END"\n'
        )
        env.update(
            {
                "INPUT_MODE": "compare",
                "INPUT_ABI_BASELINE": "latest-release",
                "GH_ARGV_FILE": str(argv_file),
            }
        )
        result = _run_bash_script(script, env)
        assert result.returncode == 0, result.stderr
        argv = argv_file.read_text(encoding="utf-8").splitlines()
        assert "-R" not in argv, argv


class TestAmbiguousBaselineAssets:
    def _run(self, gh_body: str) -> subprocess.CompletedProcess[str]:
        script = (
            'MODE="${INPUT_MODE:-compare}"\n'
            'FORCE_AUDIT_ONLY="${INPUT_AUDIT:-false}"\n'
            f"gh() {{ {gh_body}; }}\n"
            + _baseline_region()
            + '\necho "REACHED_END OLD_LIBRARY=${INPUT_OLD_LIBRARY:-}"\n'
        )
        env = {
            **os.environ,
            "INPUT_MODE": "compare",
            "INPUT_ABI_BASELINE": "latest-release",
        }
        return _run_bash_script(script, env)

    def test_single_asset_still_resolves(self) -> None:
        """Sanity: the ordinary one-asset case still works after switching
        off `find | head -1`."""
        result = self._run(
            'local dir=""; while [[ $# -gt 0 ]]; do '
            '[[ "$1" == "-D" ]] && dir="$2"; shift; done; '
            'touch "$dir/lib.abicheck.json"'
        )
        assert result.returncode == 0, result.stderr
        assert "REACHED_END OLD_LIBRARY=" in result.stdout
        assert "lib.abicheck.json" in result.stdout

    def test_multiple_assets_rejected_as_ambiguous(self) -> None:
        """Regression (CodeRabbit review): a release with more than one
        *.abicheck.json asset must not silently pick an arbitrary one via
        `find | head -1` — that could compare against the wrong library."""
        result = self._run(
            'local dir=""; while [[ $# -gt 0 ]]; do '
            '[[ "$1" == "-D" ]] && dir="$2"; shift; done; '
            'touch "$dir/a.abicheck.json" "$dir/b.abicheck.json"'
        )
        assert result.returncode == 1
        assert "Multiple *.abicheck.json assets found" in result.stdout
        assert "REACHED_END" not in result.stdout


class TestCompressedBaselineAssets:
    """ADR-059 (Codex review): a release baseline may be published under any
    of the three canonical snapshot suffixes (dump --compression writes
    .abicheck.json.gz/.abicheck.json.zst too, not just plain
    .abicheck.json) -- `gh release download --pattern '*.abicheck.json'`
    alone silently missed the compressed forms, reporting "no baseline
    found" for a perfectly valid published asset."""

    def _run(self, gh_body: str, argv_file: Path) -> subprocess.CompletedProcess[str]:
        script = (
            'MODE="${INPUT_MODE:-compare}"\n'
            'FORCE_AUDIT_ONLY="${INPUT_AUDIT:-false}"\n'
            f'gh() {{ printf "%s\\n" "$@" >> "$GH_ARGV_FILE"; {gh_body}; }}\n'
            + _baseline_region()
            + '\necho "REACHED_END OLD_LIBRARY=${INPUT_OLD_LIBRARY:-}"\n'
        )
        env = {
            **os.environ,
            "INPUT_MODE": "compare",
            "INPUT_ABI_BASELINE": "latest-release",
            "GH_ARGV_FILE": str(argv_file),
        }
        return _run_bash_script(script, env)

    def test_all_three_pattern_flags_passed(self, tmp_path: Path) -> None:
        argv_file = tmp_path / "gh_argv"
        result = self._run(
            'local dir=""; while [[ $# -gt 0 ]]; do '
            '[[ "$1" == "-D" ]] && dir="$2"; shift; done; '
            'touch "$dir/lib.abicheck.json"',
            argv_file,
        )
        assert result.returncode == 0, result.stderr
        argv = argv_file.read_text(encoding="utf-8").splitlines()
        assert argv.count("--pattern") == 3, argv
        patterns = [argv[i + 1] for i, tok in enumerate(argv) if tok == "--pattern"]
        assert patterns == [
            "*.abicheck.json",
            "*.abicheck.json.gz",
            "*.abicheck.json.zst",
        ]

    def test_gzip_asset_discovered(self, tmp_path: Path) -> None:
        argv_file = tmp_path / "gh_argv"
        result = self._run(
            'local dir=""; while [[ $# -gt 0 ]]; do '
            '[[ "$1" == "-D" ]] && dir="$2"; shift; done; '
            'touch "$dir/lib.abicheck.json.gz"',
            argv_file,
        )
        assert result.returncode == 0, result.stderr
        assert "lib.abicheck.json.gz" in result.stdout
        assert "REACHED_END" in result.stdout

    def test_zstd_asset_discovered(self, tmp_path: Path) -> None:
        argv_file = tmp_path / "gh_argv"
        result = self._run(
            'local dir=""; while [[ $# -gt 0 ]]; do '
            '[[ "$1" == "-D" ]] && dir="$2"; shift; done; '
            'touch "$dir/lib.abicheck.json.zst"',
            argv_file,
        )
        assert result.returncode == 0, result.stderr
        assert "lib.abicheck.json.zst" in result.stdout
        assert "REACHED_END" in result.stdout

    def test_mixed_suffix_assets_still_rejected_as_ambiguous(
        self, tmp_path: Path
    ) -> None:
        """A plain and a compressed asset together are still two candidate
        baselines, not one -- the ambiguity check must count across all
        three suffixes combined, not per-suffix."""
        argv_file = tmp_path / "gh_argv"
        result = self._run(
            'local dir=""; while [[ $# -gt 0 ]]; do '
            '[[ "$1" == "-D" ]] && dir="$2"; shift; done; '
            'touch "$dir/a.abicheck.json" "$dir/b.abicheck.json.zst"',
            argv_file,
        )
        assert result.returncode == 1
        assert "Multiple *.abicheck.json assets found" in result.stdout
        assert "REACHED_END" not in result.stdout
