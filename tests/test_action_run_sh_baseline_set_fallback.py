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

"""Behavioral tests for ``action/run.sh``'s release-contract baseline-set
fallback (the root Action's half of unifying the single-snapshot and
baseline-set release-baseline protocols).

When ``abi-baseline`` resolves to a release with no single
``*.abicheck.json[.gz|.zst]`` asset, but ``baseline-profile``/
``baseline-target`` are set, ``run.sh`` now falls back to fetching a
release-contract baseline-set archive (``abicheck-baseline-<profile>
.tar.zst``, ``publish-baseline.yml``'s own format) and resolving the named
target's snapshot from it via ``abicheck.buildsource.baseline_set
.resolve_target()`` -- the same resolver ``actions/resolve-baseline`` uses.

These tests extract the relevant fragment verbatim from run.sh (the same
"parse the real file, don't hand-copy it" discipline as
``test_action_run_sh_dry_run_baseline.py``) and stub ``gh`` with a shell
function that distinguishes the two download shapes by how many
``--pattern`` flags it receives (three for the original single-snapshot
search, one for the baseline-set archive fallback).
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

import pytest

RUN_SH = Path(__file__).resolve().parents[1] / "action" / "run.sh"
_START_MARKER = "_baseline_unavailable() {"
_END_MARKER = 'if [[ "$MODE" == "dump" ]]; then'

PROFILE = "linux-x86_64-gcc13-release"


def _baseline_region() -> str:
    text = RUN_SH.read_text(encoding="utf-8")
    start = text.index(_START_MARKER)
    end = text.index(_END_MARKER, start)
    return text[start:end]


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


def _make_tar_zst(archive_path: Path, src_dir: Path) -> None:
    """Build a .tar.zst using whichever zstd backend is available."""
    if shutil.which("zstd") is not None:
        tar_path = archive_path.with_suffix("")
        with tarfile.open(tar_path, "w") as tf:
            tf.add(src_dir, arcname=".")
        subprocess.run(
            ["zstd", "-f", "-q", str(tar_path), "-o", str(archive_path)],
            check=True,
        )
        tar_path.unlink()
        return
    try:
        import zstandard
    except ImportError:
        pytest.skip("neither zstd nor the zstandard package is available")

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        tf.add(src_dir, arcname=".")
    cctx = zstandard.ZstdCompressor()
    with open(archive_path, "wb") as out, cctx.stream_writer(out) as writer:
        writer.write(buf.getvalue())


def _build_baseline_set_archive(
    tmp_path: Path, *, target: str = "libpvxs", profile: str = PROFILE
) -> Path:
    src_dir = tmp_path / "baseline-set-src"
    src_dir.mkdir()
    manifest = {
        "manifest_version": 1,
        "project_ref": "v1.0.0",
        "profile": profile,
        "snapshot_schema": None,
        "fact_set": None,
        "artifacts": [
            {
                "library": target,
                "artifact": f"build/{target}.so",
                "snapshot": f"{target}.abicheck.json",
                "sha256": "",
            }
        ],
    }
    (src_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (src_dir / f"{target}.abicheck.json").write_text("{}", encoding="utf-8")
    archive_path = tmp_path / f"abicheck-baseline-{profile}.tar.zst"
    _make_tar_zst(archive_path, src_dir)
    return archive_path


# A `gh` stub that distinguishes the two download shapes by --pattern count:
# 3 (the original *.abicheck.json[.gz|.zst] search) always "finds nothing"
# (return 1, download nothing) so every test here exercises the fallback;
# 1 (the baseline-set archive fetch) copies $FIXTURE_ARCHIVE into the -D
# directory under the requested pattern's exact name (a literal filename in
# this code path, not a real glob) when $FIXTURE_ARCHIVE is set, else also
# "finds nothing".
_GH_STUB = r"""
gh() {
  local dest="" pattern="" pattern_count=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --pattern) pattern="$2"; pattern_count=$((pattern_count + 1)); shift 2 ;;
      -D) dest="$2"; shift 2 ;;
      *) shift ;;
    esac
  done
  if [[ "$pattern_count" -eq 1 && -n "${FIXTURE_ARCHIVE:-}" ]]; then
    mkdir -p "$dest"
    cp "$FIXTURE_ARCHIVE" "$dest/$pattern"
    return 0
  fi
  return 1
}
"""


def _run_bash_script(
    script: str, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    """Run *script* via a temp file, not ``bash -c "<script>"``.

    The extracted ``_baseline_region()`` is now large enough (this
    fallback's own function included) that Windows' Git-Bash ``-c``
    argument passing truncates it mid-parse, surfacing as a bash
    "unexpected end of file" syntax error purely from where the string got
    cut, not a real syntax error in the script itself (confirmed: identical
    content passed via a file runs cleanly). A temp-file invocation has no
    such command-line-length ceiling on any platform. Mirrors
    ``test_action_run_sh_dry_run_baseline.py``'s identical helper.
    """
    fd, path = tempfile.mkstemp(suffix=".sh")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(script)
        return subprocess.run(
            [_bash_executable(), path],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
    finally:
        os.unlink(path)


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestBaselineSetFallback:
    def _run(self, env_extra: dict[str, str]) -> subprocess.CompletedProcess[str]:
        script = (
            'MODE="${INPUT_MODE:-compare}"\n'
            'FORCE_AUDIT_ONLY="${INPUT_AUDIT:-false}"\n'
            + _GH_STUB
            + _baseline_region()
            + '\necho "REACHED_END OLD_LIBRARY=${INPUT_OLD_LIBRARY:-} '
            'AGAINST=${INPUT_AGAINST:-}"\n'
        )
        env = {**os.environ, **env_extra}
        return _run_bash_script(script, env)

    def test_resolves_from_baseline_set_when_no_single_snapshot_asset(
        self, tmp_path: Path
    ) -> None:
        archive = _build_baseline_set_archive(tmp_path)
        result = self._run(
            {
                "INPUT_MODE": "compare",
                "INPUT_ABI_BASELINE": "latest-release",
                "INPUT_BASELINE_PROFILE": PROFILE,
                "INPUT_BASELINE_TARGET": "libpvxs",
                "FIXTURE_ARCHIVE": str(archive),
            }
        )
        assert result.returncode == 0, result.stderr
        assert "REACHED_END" in result.stdout
        assert "OLD_LIBRARY=" in result.stdout
        # The resolved path lives under the mktemp'd BASELINE_DIR, ending in
        # exactly the snapshot filename the manifest recorded.
        assert "libpvxs.abicheck.json" in result.stdout

    def test_resolves_from_baseline_set_for_a_tag_release(self, tmp_path: Path) -> None:
        archive = _build_baseline_set_archive(tmp_path)
        result = self._run(
            {
                "INPUT_MODE": "compare",
                "INPUT_ABI_BASELINE": "v2.3.0",
                "INPUT_BASELINE_PROFILE": PROFILE,
                "INPUT_BASELINE_TARGET": "libpvxs",
                "FIXTURE_ARCHIVE": str(archive),
            }
        )
        assert result.returncode == 0, result.stderr
        assert "libpvxs.abicheck.json" in result.stdout

    def test_single_snapshot_asset_still_takes_priority(self, tmp_path: Path) -> None:
        # When the single-snapshot search DOES find something, the
        # baseline-set fallback must never even be attempted -- unrelated to
        # this test's own gh stub (which only "finds" a single-snapshot
        # asset when pattern_count == 3), a real 3-pattern hit is simulated
        # by a small stub override here instead of the shared one.
        script = (
            'MODE="${INPUT_MODE:-compare}"\n'
            'FORCE_AUDIT_ONLY="${INPUT_AUDIT:-false}"\n'
            "gh() {\n"
            '  local dest=""\n'
            "  while [[ $# -gt 0 ]]; do\n"
            '    case "$1" in\n'
            '      -D) dest="$2"; shift 2 ;;\n'
            "      *) shift ;;\n"
            "    esac\n"
            "  done\n"
            '  mkdir -p "$dest"\n'
            '  echo "{}" > "$dest/libpvxs.abicheck.json"\n'
            "  return 0\n"
            "}\n"
            + _baseline_region()
            + '\necho "REACHED_END OLD_LIBRARY=${INPUT_OLD_LIBRARY:-}"\n'
        )
        env = {
            **os.environ,
            "INPUT_MODE": "compare",
            "INPUT_ABI_BASELINE": "latest-release",
            "INPUT_BASELINE_PROFILE": PROFILE,
            "INPUT_BASELINE_TARGET": "libpvxs",
            # No FIXTURE_ARCHIVE -- if the fallback were mistakenly
            # attempted anyway it would find nothing and fail loudly.
        }
        result = _run_bash_script(script, env)
        assert result.returncode == 0, result.stderr
        assert "libpvxs.abicheck.json" in result.stdout

    def test_baseline_target_required_when_baseline_profile_set(self) -> None:
        result = self._run(
            {
                "INPUT_MODE": "compare",
                "INPUT_ABI_BASELINE": "latest-release",
                "INPUT_BASELINE_PROFILE": PROFILE,
                # No INPUT_BASELINE_TARGET.
            }
        )
        assert result.returncode == 1
        assert "baseline-target is not" in result.stdout + result.stderr

    def test_wrong_target_reports_typed_failure(self, tmp_path: Path) -> None:
        archive = _build_baseline_set_archive(tmp_path)
        result = self._run(
            {
                "INPUT_MODE": "compare",
                "INPUT_ABI_BASELINE": "latest-release",
                "INPUT_BASELINE_PROFILE": PROFILE,
                "INPUT_BASELINE_TARGET": "libnotpresent",
                "FIXTURE_ARCHIVE": str(archive),
            }
        )
        assert result.returncode == 1
        assert "libnotpresent" in result.stdout + result.stderr
        assert "REACHED_END" not in result.stdout

    def test_wrong_profile_reports_typed_failure(self, tmp_path: Path) -> None:
        archive = _build_baseline_set_archive(tmp_path, profile=PROFILE)
        result = self._run(
            {
                "INPUT_MODE": "compare",
                "INPUT_ABI_BASELINE": "latest-release",
                "INPUT_BASELINE_PROFILE": "windows-x86_64-msvc-release",
                "INPUT_BASELINE_TARGET": "libpvxs",
                # asset-name-template substitutes {profile} with the
                # REQUESTED profile, so the fixture -- built under the
                # default PROFILE -- is never even found under this name;
                # confirms the archive-not-found path, not just wrong_profile.
                "FIXTURE_ARCHIVE": str(archive),
            }
        )
        assert result.returncode == 1
        assert "REACHED_END" not in result.stdout

    def test_no_asset_at_all_reports_combined_failure(self) -> None:
        # No single-snapshot asset AND no baseline-set archive either.
        result = self._run(
            {
                "INPUT_MODE": "compare",
                "INPUT_ABI_BASELINE": "latest-release",
                "INPUT_BASELINE_PROFILE": PROFILE,
                "INPUT_BASELINE_TARGET": "libpvxs",
                # No FIXTURE_ARCHIVE.
            }
        )
        assert result.returncode == 1
        assert "baseline-set archive" in result.stdout + result.stderr

    def test_dry_run_tolerates_unresolvable_baseline_set(self, tmp_path: Path) -> None:
        result = self._run(
            {
                "INPUT_MODE": "compare",
                "INPUT_ABI_BASELINE": "latest-release",
                "INPUT_BASELINE_PROFILE": PROFILE,
                "INPUT_BASELINE_TARGET": "libpvxs",
                "INPUT_DRY_RUN": "true",
                # No FIXTURE_ARCHIVE -- unresolvable, same as an ordinary
                # unavailable abi-baseline, must not hard-fail under dry-run.
            }
        )
        assert result.returncode == 0, result.stderr
        assert "::warning::" in result.stdout

    def test_custom_asset_name_template_is_honored(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "baseline-set-src"
        src_dir.mkdir()
        manifest = {
            "manifest_version": 1,
            "project_ref": "v1.0.0",
            "profile": PROFILE,
            "snapshot_schema": None,
            "fact_set": None,
            "artifacts": [
                {
                    "library": "libpvxs",
                    "artifact": "build/libpvxs.so",
                    "snapshot": "libpvxs.abicheck.json",
                    "sha256": "",
                }
            ],
        }
        (src_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (src_dir / "libpvxs.abicheck.json").write_text("{}", encoding="utf-8")
        archive_path = tmp_path / f"custom-{PROFILE}-baseline.tar.zst"
        _make_tar_zst(archive_path, src_dir)

        result = self._run(
            {
                "INPUT_MODE": "compare",
                "INPUT_ABI_BASELINE": "latest-release",
                "INPUT_BASELINE_PROFILE": PROFILE,
                "INPUT_BASELINE_TARGET": "libpvxs",
                "INPUT_BASELINE_ASSET_NAME_TEMPLATE": "custom-{profile}-baseline.tar.zst",
                "FIXTURE_ARCHIVE": str(archive_path),
            }
        )
        assert result.returncode == 0, result.stderr
        assert "libpvxs.abicheck.json" in result.stdout
