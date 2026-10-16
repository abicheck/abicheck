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
``test_action_run_sh_dry_run_baseline.py``) and stub ``gh`` to distinguish
three call shapes: ``release download`` (the original single-snapshot
search, always "finds nothing" here so every test exercises the fallback),
``release view --json assets`` (the baseline-set archive's own exact-name
lookup, answering from a fixture-built assets list), and ``api <url>``
(the actual asset download, catting the fixture archive). The fallback
looks the asset up by EXACT name rather than a `--pattern` glob (a second
review round found the glob-escaping approach it used to use was itself
platform-dependent -- see ``TestExactAssetNameLookup`` below).
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

import pytest
from _action_run_sh_harness import annotation_helpers_source
from _workflow_exec import bash_executable, require_bash

RUN_SH = Path(__file__).resolve().parents[1] / "action" / "run.sh"
# Starts from _PY_BIN's own resolution, not _baseline_unavailable() -- the
# baseline-set fallback function now reads $_PY_BIN (resolved once, up
# front, so it works the same on a Windows runner where only `python`, not
# `python3`, is on PATH), so the extracted region must include that
# resolution too, or every test here would see an empty $_PY_BIN that
# doesn't reflect the real script's behavior.
_START_MARKER = '_PY_BIN="$(command -v python3'
_END_MARKER = 'if [[ "$MODE" == "dump" ]]; then'

#: The extracted region's own `$_PY_BIN` anchoring (and, further down,
#: `_report_query`'s report-path anchoring) both delegate to this shared,
#: `$OSTYPE`-gated helper rather than a duplicated `case` pattern -- must be
#: defined in the harness too, or every call resolves as "command not
#: found" and the whole region silently degrades (Codex review, fresh
#: evidence).
_PATH_QUALIFIED_HELPER_START = 'case "$OSTYPE" in'
_PATH_QUALIFIED_HELPER_END = "\n}\n"

PROFILE = "linux-x86_64-gcc13-release"


def _path_qualified_helper_source() -> str:
    text = RUN_SH.read_text(encoding="utf-8")
    start = text.index(_PATH_QUALIFIED_HELPER_START)
    end = text.index(_PATH_QUALIFIED_HELPER_END, start) + len(
        _PATH_QUALIFIED_HELPER_END
    )
    return text[start:end]


def _baseline_region() -> str:
    text = RUN_SH.read_text(encoding="utf-8")
    start = text.index(_START_MARKER)
    end = text.index(_END_MARKER, start)
    return (
        annotation_helpers_source()
        + "\n"
        + _path_qualified_helper_source()
        + "\n"
        + text[start:end]
    )


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


#: The release tag every fixture is published under. A baseline-set archive's
#: manifest project_ref must equal it for the default baseline-project-ref
#: ('tag') to accept the archive, so the two are kept in step here.
FIXTURE_TAG = "v1.0.0"


def _build_baseline_set_archive(
    tmp_path: Path,
    *,
    target: str = "libpvxs",
    profile: str = PROFILE,
    baseline_generation: int | None = None,
    project_ref: str = FIXTURE_TAG,
) -> Path:
    src_dir = tmp_path / "baseline-set-src"
    src_dir.mkdir()
    manifest = {
        "manifest_version": 1,
        "project_ref": project_ref,
        "profile": profile,
        "snapshot_schema": None,
        "fact_set": None,
        "baseline_generation": baseline_generation,
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
    # A fixed, platform-safe on-disk filename -- NOT one embedding *profile*
    # directly. A profile built to exercise a glob metacharacter (e.g.
    # '?', '*') is a legal, if unusual, string for the resolved *logical*
    # asset name (what `gh` advertises and this fallback looks up by exact
    # name), but several of those characters are illegal in an actual NTFS
    # filename -- writing this fixture archive under a profile-derived name
    # would fail on a Windows test runner for the identical reason the
    # production fix this file's own TestExactAssetNameLookup covers
    # exists (Codex review, confirmed by a real windows-latest CI failure
    # on this exact fixture before this change). The archive's real
    # on-disk path is never asserted against by any test here -- only its
    # *content*, via $FIXTURE_ARCHIVE -- so decoupling it from the profile
    # string is free.
    archive_path = tmp_path / "fixture-baseline-set.tar.zst"
    _make_tar_zst(archive_path, src_dir)
    return archive_path


# A `gh` stub covering the three real call shapes the fallback (plus the
# single-snapshot search ahead of it) makes:
#   - `release download ... --pattern ...` (the original single-snapshot
#     search) always "finds nothing" -- every test here exercises the
#     baseline-set fallback, never the single-snapshot path.
#   - `release view [tag] ... --json assets` answers from
#     $FIXTURE_ASSETS_JSON (a JSON `{"assets": [...]}` document the test
#     builds -- see `_resolved_asset_name`/`_run_baseline_fallback` below),
#     or an empty assets list when unset.
#   - `api <url>` cats $FIXTURE_ARCHIVE regardless of the exact URL (these
#     tests only ever register zero or one asset, so there is nothing to
#     disambiguate) when $FIXTURE_ARCHIVE is set, else fails.
_GH_STUB = r"""
gh() {
  if [[ "$1" == "release" && "$2" == "download" ]]; then
    return 1
  fi
  if [[ "$1" == "release" && "$2" == "view" ]]; then
    if [[ -n "${FIXTURE_ASSETS_JSON:-}" ]]; then
      printf '%s' "$FIXTURE_ASSETS_JSON"
    else
      printf '{"assets": []}'
    fi
    return 0
  fi
  if [[ "$1" == "api" ]]; then
    if [[ -n "${FIXTURE_ARCHIVE:-}" ]]; then
      cat "$FIXTURE_ARCHIVE"
      return 0
    fi
    return 1
  fi
  return 1
}
"""


def _resolved_asset_name(env: dict[str, str]) -> str:
    """Mirrors run.sh's own asset_name computation (template + profile +
    generation), so a fixture's advertised asset name always matches what
    the real script will actually look up."""
    template = env.get("INPUT_BASELINE_ASSET_NAME_TEMPLATE") or ""
    if not template:
        template = "abicheck-baseline-{profile}.tar.zst"
    profile = env.get("INPUT_BASELINE_PROFILE", "")
    generation = env.get("INPUT_BASELINE_GENERATION", "")
    return template.replace("{profile}", profile).replace("{generation}", generation)


def _run_bash_script(
    script: str, env: dict[str, str], *, cwd: Path | None = None
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
            cwd=cwd,
            check=False,
        )
    finally:
        os.unlink(path)


def _run_baseline_fallback(
    env_extra: dict[str, str],
    *,
    trailer: str = (
        '\necho "REACHED_END OLD_LIBRARY=${INPUT_OLD_LIBRARY:-} '
        'AGAINST=${INPUT_AGAINST:-}"\n'
    ),
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, **env_extra}
    if env.get("FIXTURE_ARCHIVE") and "FIXTURE_ASSETS_JSON" not in env_extra:
        asset_name = _resolved_asset_name(env)
        # tagName is what `gh release view --json assets,tagName` reports,
        # and what a latest-release lookup's baseline-project-ref='tag'
        # default is checked against.
        env["FIXTURE_ASSETS_JSON"] = json.dumps(
            {
                "tagName": env.get("FIXTURE_TAG", FIXTURE_TAG),
                "assets": [{"name": asset_name, "apiUrl": "fixture://asset"}],
            }
        )
    script = (
        'MODE="${INPUT_MODE:-compare}"\n'
        'FORCE_AUDIT_ONLY="${INPUT_AUDIT:-false}"\n'
        + _GH_STUB
        + _baseline_region()
        + trailer
    )
    return _run_bash_script(script, env, cwd=cwd)


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestBaselineSetFallback:
    def _run(self, env_extra: dict[str, str]) -> subprocess.CompletedProcess[str]:
        return _run_baseline_fallback(env_extra)

    def _run_with_cwd(
        self, env_extra: dict[str, str], *, cwd: Path
    ) -> subprocess.CompletedProcess[str]:
        return _run_baseline_fallback(env_extra, cwd=cwd)

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

    def test_resolves_with_a_relative_tmpdir(self, tmp_path: Path) -> None:
        """Codex review, fresh evidence: `mktemp -d` returns a path
        relative to `$TMPDIR` when that variable itself holds a relative
        value (a real, if unusual, self-hosted-runner configuration,
        confirmed directly: `TMPDIR=relbase mktemp -d` really does emit a
        relative path). Since baseline-set extraction now runs its Python
        helpers through `$_PY_SAFE_DIR` isolation (a `(cd "$_PY_SAFE_DIR"
        && ...)` wrapper), an un-canonicalized `$BASELINE_DIR` -- and every
        path derived from it (`archive_path`/`extracted_dir`/
        `manifest_root`) -- would resolve against the wrong directory once
        inside that wrapper, misreporting a perfectly valid archive as
        corrupt or missing. `BASELINE_DIR` is canonicalized to an absolute
        path immediately after creation specifically to survive this."""
        archive = _build_baseline_set_archive(tmp_path)
        work_dir = tmp_path / "work"
        work_dir.mkdir()
        relative_tmpdir = "relative_tmp"
        (work_dir / relative_tmpdir).mkdir()
        result = self._run_with_cwd(
            {
                "INPUT_MODE": "compare",
                "INPUT_ABI_BASELINE": "latest-release",
                "INPUT_BASELINE_PROFILE": PROFILE,
                "INPUT_BASELINE_TARGET": "libpvxs",
                "FIXTURE_ARCHIVE": str(archive),
                "TMPDIR": relative_tmpdir,
            },
            cwd=work_dir,
        )
        assert result.returncode == 0, result.stderr
        assert "REACHED_END" in result.stdout
        assert "libpvxs.abicheck.json" in result.stdout

    def test_resolves_from_baseline_set_for_a_tag_release(self, tmp_path: Path) -> None:
        # Published under v2.3.0, so the default baseline-project-ref ('tag')
        # -- the tag abi-baseline names -- matches its manifest.
        archive = _build_baseline_set_archive(tmp_path, project_ref="v2.3.0")
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

    def test_single_snapshot_asset_still_takes_priority_when_no_selection(
        self, tmp_path: Path
    ) -> None:
        # With baseline-profile/baseline-target UNSET, the single-snapshot
        # search keeps priority: when it DOES find something, the baseline-set
        # fallback must never even be attempted. (With both set, the selection
        # wins instead -- see TestBaselineSetSelectionPriority.) A real
        # 3-pattern hit is simulated by a small stub override here instead of
        # the shared one.
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
            # No baseline-profile/baseline-target: the selection is unset.
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

    def test_baseline_profile_required_when_baseline_target_set_alone(self) -> None:
        # Regression (Codex review, P2): baseline-target set WITHOUT
        # baseline-profile or abi-baseline previously never reached ANY
        # pairing check at all -- the whole auto-fetch block (and
        # therefore _try_baseline_set_fallback, whose own inline check
        # only fires from inside a call this shape never triggers) is
        # gated on `-n "$ABI_BASELINE"`, so with abi-baseline unset too,
        # baseline-target was silently discarded and a separately-supplied
        # old-library/against ran in its place instead of erroring.
        # validate-inputs.sh already caught this shape (AGENTS.md: "keep
        # validate-inputs.sh and run.sh in sync"), but a direct run.sh
        # invocation -- e.g. this repository's own shell tests -- bypasses
        # that script entirely.
        result = self._run(
            {
                "INPUT_MODE": "compare",
                "INPUT_BASELINE_TARGET": "libpvxs",
                # No INPUT_BASELINE_PROFILE, no INPUT_ABI_BASELINE.
            }
        )
        assert result.returncode == 1
        assert "baseline-profile is not" in result.stdout + result.stderr
        assert "REACHED_END" not in result.stdout

    def test_baseline_profile_and_target_require_abi_baseline(self) -> None:
        # Regression (Codex review, P2): baseline-profile AND
        # baseline-target both set, but abi-baseline is not, previously
        # skipped the entire auto-fetch block (gated on
        # `-n "$ABI_BASELINE"`) and therefore never even attempted the
        # release-contract baseline-set fallback -- both inputs silently
        # discarded instead of erroring.
        result = self._run(
            {
                "INPUT_MODE": "compare",
                "INPUT_BASELINE_PROFILE": PROFILE,
                "INPUT_BASELINE_TARGET": "libpvxs",
                # No INPUT_ABI_BASELINE.
            }
        )
        assert result.returncode == 1
        assert "abi-baseline is not" in result.stdout + result.stderr
        assert "REACHED_END" not in result.stdout

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
                # `_run_baseline_fallback` auto-advertises a fixture asset
                # named for the REQUESTED profile (matching what run.sh's
                # own asset_name computation resolves to), so this archive
                # -- built under the default PROFILE -- IS found and
                # fetched successfully under the requested
                # "windows-x86_64-msvc-release" name. The failure is
                # entirely `resolve_target()`'s own wrong_profile check
                # (manifest.profile != the requested profile), not an
                # archive-not-found path.
                "FIXTURE_ARCHIVE": str(archive),
            }
        )
        assert result.returncode == 1
        assert "outcome: wrong_profile" in result.stdout + result.stderr
        assert "REACHED_END" not in result.stdout

    def test_stale_generation_reports_typed_failure(self, tmp_path: Path) -> None:
        # Regression (Codex review): the resolved asset's FILENAME matching
        # baseline-asset-name-template's {generation} placeholder is not
        # itself proof the manifest INSIDE it actually carries that
        # generation -- a custom publisher, a stale release, or a hand-
        # authored asset could name a generation-2 (or ungenerationed)
        # manifest as a generation-3 archive. The manifest stays the
        # authoritative identity: resolve_target()'s own
        # expected_baseline_generation check must still run here, not just
        # at check-target/resolve-baseline.
        archive = _build_baseline_set_archive(tmp_path, baseline_generation=2)
        result = self._run(
            {
                "INPUT_MODE": "compare",
                "INPUT_ABI_BASELINE": "latest-release",
                "INPUT_BASELINE_PROFILE": PROFILE,
                "INPUT_BASELINE_TARGET": "libpvxs",
                "INPUT_BASELINE_GENERATION": "3",
                "FIXTURE_ARCHIVE": str(archive),
            }
        )
        assert result.returncode == 1
        assert "outcome: stale_generation" in result.stdout + result.stderr
        assert "REACHED_END" not in result.stdout

    def test_matching_generation_resolves(self, tmp_path: Path) -> None:
        archive = _build_baseline_set_archive(tmp_path, baseline_generation=3)
        result = self._run(
            {
                "INPUT_MODE": "compare",
                "INPUT_ABI_BASELINE": "latest-release",
                "INPUT_BASELINE_PROFILE": PROFILE,
                "INPUT_BASELINE_TARGET": "libpvxs",
                "INPUT_BASELINE_GENERATION": "3",
                "FIXTURE_ARCHIVE": str(archive),
            }
        )
        assert result.returncode == 0, result.stderr
        assert "REACHED_END" in result.stdout

    def test_invalid_baseline_generation_is_a_usage_error(self, tmp_path: Path) -> None:
        result = self._run(
            {
                "INPUT_MODE": "compare",
                "INPUT_ABI_BASELINE": "latest-release",
                "INPUT_BASELINE_PROFILE": PROFILE,
                "INPUT_BASELINE_TARGET": "libpvxs",
                "INPUT_BASELINE_GENERATION": "3x",
            }
        )
        assert result.returncode == 1
        assert "not a non-negative integer" in result.stdout + result.stderr

    def test_ampersand_in_profile_is_substituted_literally(
        self, tmp_path: Path
    ) -> None:
        # Regression (Codex review, P2): Bash 5.2's default
        # `patsub_replacement` shopt gives '&' in a
        # ${var//pattern/REPLACEMENT}'s replacement TEXT the special
        # "insert the matched pattern text" meaning (like sed's `&`
        # backreference) -- a baseline-profile containing a literal '&'
        # previously expanded to "...{profile}..." instead of the literal
        # string, and resolved DIFFERENTLY on Bash 3.2 (macOS stock, no
        # such interpretation).
        profile = "linux&asan"
        archive = _build_baseline_set_archive(tmp_path, profile=profile)
        result = self._run(
            {
                "INPUT_MODE": "compare",
                "INPUT_ABI_BASELINE": "latest-release",
                "INPUT_BASELINE_PROFILE": profile,
                "INPUT_BASELINE_TARGET": "libpvxs",
                "FIXTURE_ARCHIVE": str(archive),
            }
        )
        assert result.returncode == 0, result.stderr
        assert "REACHED_END" in result.stdout
        assert "libpvxs.abicheck.json" in result.stdout

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

    def test_generation_placeholder_is_substituted(self, tmp_path: Path) -> None:
        # Regression (Codex review): actions/stage-baseline's
        # {generation} template opt-in (for publishing a scanner-
        # compatibility-generation-scoped baseline-set archive, see
        # docs/use/baseline-management.md#scanner-upgrades-and-baseline-
        # generations) only reached the PRODUCER side -- this consumer-
        # side fallback substituted {profile} but left a literal
        # "{generation}" in its exact-name lookup, so a caller following
        # the documented recipe on both sides could never actually
        # resolve the asset this Action's own baseline-generation input
        # was added to name.
        src_dir = tmp_path / "baseline-set-src"
        src_dir.mkdir()
        manifest = {
            "manifest_version": 1,
            "project_ref": "v1.0.0",
            "profile": PROFILE,
            "snapshot_schema": None,
            "fact_set": None,
            "baseline_generation": 3,
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
        archive_path = tmp_path / f"abicheck-baseline-g3-{PROFILE}.tar.zst"
        _make_tar_zst(archive_path, src_dir)

        result = self._run(
            {
                "INPUT_MODE": "compare",
                "INPUT_ABI_BASELINE": "latest-release",
                "INPUT_BASELINE_PROFILE": PROFILE,
                "INPUT_BASELINE_TARGET": "libpvxs",
                "INPUT_BASELINE_ASSET_NAME_TEMPLATE": "abicheck-baseline-g{generation}-{profile}.tar.zst",
                "INPUT_BASELINE_GENERATION": "3",
                "FIXTURE_ARCHIVE": str(archive_path),
            }
        )
        assert result.returncode == 0, result.stderr
        assert "libpvxs.abicheck.json" in result.stdout

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason=(
            "This test builds an in-archive symlink via tarfile.TarInfo's "
            "SYMTYPE, which extracts to a real filesystem symlink -- "
            "creating one needs Developer Mode or an elevated process on "
            "Windows (raises OSError otherwise), a privilege this test has "
            "no need to require."
        ),
    )
    def test_symlink_in_archive_is_rejected(self, tmp_path: Path) -> None:
        # Regression (Codex review, P2): TarExtractor's own member
        # validation only rejects a symlink escaping the extraction root,
        # not one that stays inside it -- so a real in-root symlink could
        # previously be silently followed by resolve_target() here, even
        # though actions/resolve-baseline/run.sh (the canonical baseline-set
        # consumer) rejects ANY symlink at all. The same archive could then
        # be accepted by this root Action's fallback while being rejected
        # as ambiguous through check-target/resolve-baseline -- two
        # consumers of the identical unified baseline-set protocol
        # disagreeing on whether the same archive is usable.
        target = "libpvxs"
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
                    "library": target,
                    "artifact": f"build/{target}.so",
                    "snapshot": f"{target}.abicheck.json",
                    "sha256": "",
                }
            ],
        }
        (src_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (src_dir / f"{target}.abicheck.json").write_text("{}", encoding="utf-8")

        archive_path = tmp_path / "fixture-baseline-set-symlink.tar"
        with tarfile.open(archive_path, "w") as tf:
            tf.add(src_dir, arcname=".")
            # An in-root symlink -- its target stays inside the archive
            # (never escapes), so TarExtractor's own path-traversal guard
            # doesn't reject it.
            link_info = tarfile.TarInfo(name=f"./{target}.abicheck.json.link")
            link_info.type = tarfile.SYMTYPE
            link_info.linkname = f"{target}.abicheck.json"
            tf.addfile(link_info)

        result = self._run(
            {
                "INPUT_MODE": "compare",
                "INPUT_ABI_BASELINE": "latest-release",
                "INPUT_BASELINE_PROFILE": PROFILE,
                "INPUT_BASELINE_TARGET": target,
                "INPUT_BASELINE_ASSET_NAME_TEMPLATE": "abicheck-baseline-{profile}.tar",
                "FIXTURE_ARCHIVE": str(archive_path),
            }
        )
        assert result.returncode == 1
        assert "symlink" in result.stdout + result.stderr
        assert "REACHED_END" not in result.stdout

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason=(
            "This test builds a `python` shell-script wrapper to simulate "
            "a python3-less PATH -- a `#!/bin/sh` shebang script isn't "
            "executable on Windows, a privilege this test has no need to "
            "require just to exercise a fallback that already has its own "
            "dedicated coverage of the real Windows shape via the "
            "win32-only `only 'python' on PATH` runners this whole PR "
            "targets."
        ),
    )
    def test_extraction_and_resolution_work_when_python3_is_absent(
        self, tmp_path: Path
    ) -> None:
        """Regression (Codex review): the fallback used to hardcode
        ``python3`` at its three extraction/resolution call sites, so a
        runner exposing only ``python`` (a real, not hypothetical, Windows
        Git Bash shape -- see ``docs/reference/publish-baseline.md``'s own
        sibling ``actions/stage-baseline`` zstd-fallback fix for the
        identical concern) would fail every otherwise-valid baseline-set
        fallback with "command not found".

        Simulates that shape without depending on the test runner's own
        PATH layout: a `command` shell-function override makes
        ``command -v python3`` fail regardless of what's really installed,
        while a fabricated ``python`` on ``PATH`` -- an exec wrapper around
        `sys.executable`, not merely *some* interpreter `shutil.which`
        happens to find first, which can be a bare system Python with no
        `abicheck` installed whenever the dev environment installs
        `abicheck` into a separate venv than the one that's first on
        ``PATH`` -- proves `_PY_BIN`'s fallback resolution actually gets
        exercised against an interpreter the fallback script can really
        import `abicheck` from, not merely defined. A plain symlink to
        `sys.executable` isn't enough: when `sys.executable` is itself a
        venv symlink chain down to a base interpreter (the common venv
        shape), adding one more symlink hop from an unrelated directory can
        make CPython's own venv/pyvenv.cfg discovery walk straight past the
        venv to the base interpreter, silently losing `abicheck`'s editable
        install -- an `exec` wrapper invokes `sys.executable`'s real path
        directly, the same single hop a normal invocation would.
        """
        archive = _build_baseline_set_archive(tmp_path)
        real_python = sys.executable
        fake_bin = tmp_path / "fake-bin"
        fake_bin.mkdir()
        fake_python = fake_bin / "python"
        fake_python.write_text(
            f'#!/bin/sh\nexec "{real_python}" "$@"\n', encoding="utf-8"
        )
        fake_python.chmod(0o755)

        script = (
            f'PATH="{fake_bin}:$PATH"\n'
            "command() {\n"
            '  if [[ "$1" == "-v" && "$2" == "python3" ]]; then\n'
            "    return 1\n"
            "  fi\n"
            '  builtin command "$@"\n'
            "}\n"
            'MODE="${INPUT_MODE:-compare}"\n'
            'FORCE_AUDIT_ONLY="${INPUT_AUDIT:-false}"\n'
            + _GH_STUB
            + _baseline_region()
            + '\necho "REACHED_END"\n'
        )
        asset_name = _resolved_asset_name({"INPUT_BASELINE_PROFILE": PROFILE})
        env = {
            **os.environ,
            "INPUT_MODE": "compare",
            "INPUT_ABI_BASELINE": "latest-release",
            "INPUT_BASELINE_PROFILE": PROFILE,
            "INPUT_BASELINE_TARGET": "libpvxs",
            "FIXTURE_ARCHIVE": str(archive),
            "FIXTURE_ASSETS_JSON": json.dumps(
                {
                    "tagName": FIXTURE_TAG,
                    "assets": [{"name": asset_name, "apiUrl": "fixture://asset"}],
                }
            ),
        }
        result = _run_bash_script(script, env)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "REACHED_END" in result.stdout
        assert "libpvxs.abicheck.json" in result.stdout


@pytest.mark.skipif(not RUN_SH.is_file(), reason="action/run.sh not found")
class TestExactAssetNameLookup:
    """Regression (Codex review, second round): the earlier fix backslash-
    escaped glob metacharacters before using the resolved asset name as a
    `gh release download --pattern` value -- but that escaping is ITSELF
    wrong on a Windows runner: Go's path/filepath.Match disables escaping
    entirely there (treating '\\' as the OS path separator instead), so
    the escaped pattern would fail to match on exactly the runners this
    whole area of the fallback exists to support. The fallback now looks
    the asset up by EXACT name via `gh release view --json assets` + `gh
    api <apiUrl>` instead of a `--pattern` glob at all, sidestepping
    platform-dependent glob semantics entirely -- confirmed here by
    resolving a baseline whose profile (and therefore resolved asset name)
    is built from every glob metacharacter this fallback used to have to
    escape."""

    def test_resolves_an_asset_name_containing_glob_metacharacters(
        self, tmp_path: Path
    ) -> None:
        profile = "linux-[x86*64]-gcc13?release"
        archive = _build_baseline_set_archive(tmp_path, profile=profile)
        result = _run_baseline_fallback(
            {
                "INPUT_MODE": "compare",
                "INPUT_ABI_BASELINE": "latest-release",
                "INPUT_BASELINE_PROFILE": profile,
                "INPUT_BASELINE_TARGET": "libpvxs",
                "FIXTURE_ARCHIVE": str(archive),
            }
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "libpvxs.abicheck.json" in result.stdout

    def test_an_asset_whose_name_only_differs_is_not_matched(
        self, tmp_path: Path
    ) -> None:
        """Exact-string lookup, not a glob: an advertised asset with a
        DIFFERENT name (even one that would have matched a naively
        unescaped glob pattern) must never resolve."""
        archive = _build_baseline_set_archive(tmp_path, profile=PROFILE)
        env = {
            "INPUT_MODE": "compare",
            "INPUT_ABI_BASELINE": "latest-release",
            "INPUT_BASELINE_PROFILE": PROFILE,
            "INPUT_BASELINE_TARGET": "libpvxs",
            "FIXTURE_ARCHIVE": str(archive),
            "FIXTURE_ASSETS_JSON": json.dumps(
                {
                    "assets": [
                        {
                            "name": "totally-unrelated.tar.zst",
                            "apiUrl": "fixture://asset",
                        }
                    ]
                }
            ),
        }
        result = _run_baseline_fallback(env)
        assert result.returncode == 1
        assert "baseline-set archive" in result.stdout + result.stderr

    def test_downloaded_archive_uses_a_platform_safe_local_filename(
        self, tmp_path: Path
    ) -> None:
        """Regression (Codex review, third round): the downloaded archive
        used to be stored locally under `$asset_name` itself -- a
        documented literal metacharacter in a custom
        baseline-asset-name-template (e.g. '?', which this exact-name
        lookup no longer forbids) is legal on the Linux filesystem that
        PUBLISHED it but reserved on NTFS, so a Windows consumer's `>`
        redirection into that same-named local file would fail even
        though the exact-name lookup itself succeeded. The local download
        path is now a fixed, platform-safe filename, independent of
        $asset_name (which is still used to select the extractor)."""
        profile = "linux-x86_64-weird?name"
        archive = _build_baseline_set_archive(tmp_path, profile=profile)
        result = _run_baseline_fallback(
            {
                "INPUT_MODE": "compare",
                "INPUT_ABI_BASELINE": "latest-release",
                "INPUT_BASELINE_PROFILE": profile,
                "INPUT_BASELINE_TARGET": "libpvxs",
                "FIXTURE_ARCHIVE": str(archive),
            },
            trailer=(
                '\necho "LISTING_START"\n'
                'ls "$BASELINE_DIR/baseline-set-download"\n'
                'echo "LISTING_END"\n'
                '\necho "REACHED_END"\n'
            ),
        )
        assert result.returncode == 0, result.stdout + result.stderr
        listing = result.stdout.split("LISTING_START", 1)[1].split("LISTING_END")[0]
        assert listing.strip() == "downloaded-baseline-set"


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
            f"BASELINE_SET_PROJECT_REF={shlex_quote(hostile)}\n"
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


def shlex_quote(value: str) -> str:
    import shlex

    return shlex.quote(value)
