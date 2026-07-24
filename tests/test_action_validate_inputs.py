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

"""Behavioral tests for ``action/validate-inputs.sh``.

This script runs as the composite Action's very first step (action.yml),
before Python setup, system-dependency installation, or `pip install
abicheck` -- it exists to fail fast on a mode/input combination that can
never work (a directory/package handed to ``dump``/``scan``, which have no
per-library fan-out) or that used to silently do the wrong thing (an
unsupported ``format`` for the mode used to warn and fall back instead of
erroring, which is unsafe paired with ``upload-sarif``).

These tests invoke the real script as a subprocess (not an extracted
fragment) since it is small and fully self-contained. A separate drift-guard
class checks its duplicated ``_is_release_style_operand`` copy against
``action/run.sh``'s original on the same fixture set.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path

import pytest

ACTION_DIR = Path(__file__).resolve().parents[1] / "action"
VALIDATE_SH = ACTION_DIR / "validate-inputs.sh"
RUN_SH = ACTION_DIR / "run.sh"


def _bash_executable() -> str:
    """Resolve a real bash, bypassing Windows' WSL-launcher stub.

    See ``test_action_run_sh_helpers._bash_executable`` for the full
    rationale.
    """
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


_VALIDATOR_INPUT_VARS = (
    "INPUT_MODE",
    "INPUT_NEW_LIBRARY",
    "INPUT_OLD_LIBRARY",
    "INPUT_FORMAT",
    "INPUT_UPLOAD_SARIF",
    "INPUT_DEBUG_INFO1",
    "INPUT_DEBUG_INFO2",
    "INPUT_DEVEL_PKG1",
    "INPUT_DEVEL_PKG2",
    "INPUT_DSO_ONLY",
    "INPUT_INCLUDE_PRIVATE_DSO",
    "INPUT_KEEP_EXTRACTED",
    "INPUT_FAIL_ON_REMOVED_LIBRARY",
    "INPUT_JOBS",
    "INPUT_ABI_BASELINE",
    "INPUT_ESTIMATE",
    "INPUT_AUDIT",
    "INPUT_USED_BY",
    "INPUT_VERIFY_RUNTIME",
    "INPUT_REQUIRED_SYMBOL",
    "INPUT_REQUIRED_SYMBOLS",
    "INPUT_AST_FRONTEND",
    "INPUT_GCC_PATH",
    "INPUT_GCC_PREFIX",
    "INPUT_GCC_OPTIONS",
    "INPUT_SYSROOT",
    "INPUT_NOSTDINC",
)


def _run_validate(env_extra: dict[str, str]) -> subprocess.CompletedProcess[str]:
    # Strip any of validate-inputs.sh's own INPUT_* vars the *test process*
    # inherited (e.g. if pytest itself ran inside a composite-action step)
    # before layering env_extra back on top -- otherwise a test that
    # deliberately omits an input (e.g. test_no_inputs_at_all_passes) could
    # pick up an ambient value instead of a true unset.
    env = os.environ.copy()
    for name in _VALIDATOR_INPUT_VARS:
        env.pop(name, None)
    env.update(env_extra)
    return subprocess.run(
        [_bash_executable(), str(VALIDATE_SH)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


@pytest.mark.skipif(
    not VALIDATE_SH.is_file(), reason="action/validate-inputs.sh not found"
)
class TestDefaultsPassValidation:
    def test_no_inputs_at_all_passes(self) -> None:
        result = _run_validate({})
        assert result.returncode == 0, result.stdout + result.stderr

    def test_plain_compare_passes(self) -> None:
        result = _run_validate({"INPUT_MODE": "compare", "INPUT_FORMAT": "sarif"})
        assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(
    not VALIDATE_SH.is_file(), reason="action/validate-inputs.sh not found"
)
class TestUnknownModeIsRejected:
    """The `case "$MODE" in ...` has no arm for an unrecognized value, so
    without an explicit catch-all a typo like 'scna' falls through silently
    and every other check in the script is skipped -- Python setup,
    dependency install, and pip install would all still run before run.sh's
    own "Unknown mode" check finally reports it (Codex review, PR #594)."""

    def test_typo_mode_is_rejected(self) -> None:
        result = _run_validate({"INPUT_MODE": "scna"})
        assert result.returncode == 1
        assert "Unknown mode" in result.stdout
        assert "scna" in result.stdout

    @pytest.mark.parametrize(
        "mode", ["compare", "dump", "scan", "deps-tree", "deps-compare"]
    )
    def test_every_real_mode_is_accepted(self, mode: str) -> None:
        result = _run_validate({"INPUT_MODE": mode})
        assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(
    not VALIDATE_SH.is_file(), reason="action/validate-inputs.sh not found"
)
class TestDumpRejectsDirectoryOrPackage:
    def test_directory_is_rejected(self, tmp_path: Path) -> None:
        lib_dir = tmp_path / "lib"
        lib_dir.mkdir()
        result = _run_validate(
            {"INPUT_MODE": "dump", "INPUT_NEW_LIBRARY": str(lib_dir)}
        )
        assert result.returncode == 1
        assert "does not accept a directory or package" in result.stdout
        assert "dump" in result.stdout

    def test_package_extension_is_rejected(self, tmp_path: Path) -> None:
        pkg = tmp_path / "libfoo.rpm"
        pkg.write_text("")
        result = _run_validate({"INPUT_MODE": "dump", "INPUT_NEW_LIBRARY": str(pkg)})
        assert result.returncode == 1
        assert "does not accept a directory or package" in result.stdout

    def test_plain_binary_passes(self, tmp_path: Path) -> None:
        lib = tmp_path / "libfoo.so.1"
        lib.write_text("")
        result = _run_validate({"INPUT_MODE": "dump", "INPUT_NEW_LIBRARY": str(lib)})
        assert result.returncode == 0, result.stdout + result.stderr

    def test_source_only_dump_with_no_new_library_passes(self) -> None:
        # dump's new-library is optional (source-only dump); the required-
        # ness check lives in run.sh, not this validator.
        result = _run_validate({"INPUT_MODE": "dump"})
        assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(
    not VALIDATE_SH.is_file(), reason="action/validate-inputs.sh not found"
)
class TestScanRejectsDirectoryOrPackage:
    def test_directory_is_rejected(self, tmp_path: Path) -> None:
        lib_dir = tmp_path / "release" / "lib" / "intel64"
        lib_dir.mkdir(parents=True)
        result = _run_validate(
            {"INPUT_MODE": "scan", "INPUT_NEW_LIBRARY": str(lib_dir)}
        )
        assert result.returncode == 1
        assert "does not accept a directory or package" in result.stdout
        assert "scan" in result.stdout

    def test_plain_binary_passes(self, tmp_path: Path) -> None:
        lib = tmp_path / "libfoo.so.1"
        lib.write_text("")
        result = _run_validate({"INPUT_MODE": "scan", "INPUT_NEW_LIBRARY": str(lib)})
        assert result.returncode == 0, result.stdout + result.stderr

    def test_json_snapshot_passes(self, tmp_path: Path) -> None:
        snap = tmp_path / "baseline.json"
        snap.write_text("{}")
        result = _run_validate({"INPUT_MODE": "scan", "INPUT_NEW_LIBRARY": str(snap)})
        assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(
    not VALIDATE_SH.is_file(), reason="action/validate-inputs.sh not found"
)
class TestFormatIsHardErrorNotSilentFallback:
    @pytest.mark.parametrize("fmt", ["sarif", "html", "xml", "csv"])
    def test_scan_rejects_unsupported_format(self, fmt: str) -> None:
        # An allowlist, not a denylist of known-bad values (CodeRabbit
        # review, PR #594): a typo/garbage value like 'xml' must be caught
        # here too, not just sarif/html specifically.
        result = _run_validate({"INPUT_MODE": "scan", "INPUT_FORMAT": fmt})
        assert result.returncode == 1
        assert "does not support format" in result.stdout
        assert "warning" not in result.stdout.lower()

    @pytest.mark.parametrize("fmt", ["text", "json"])
    def test_scan_accepts_supported_format(self, fmt: str) -> None:
        result = _run_validate({"INPUT_MODE": "scan", "INPUT_FORMAT": fmt})
        assert result.returncode == 0, result.stdout + result.stderr

    @pytest.mark.parametrize("mode", ["deps-tree", "deps-compare"])
    @pytest.mark.parametrize("fmt", ["sarif", "xml"])
    def test_deps_modes_reject_unsupported_format(self, mode: str, fmt: str) -> None:
        result = _run_validate({"INPUT_MODE": mode, "INPUT_FORMAT": fmt})
        assert result.returncode == 1
        assert "does not support format" in result.stdout

    @pytest.mark.parametrize("mode", ["deps-tree", "deps-compare"])
    @pytest.mark.parametrize("fmt", ["markdown", "json", "html"])
    def test_deps_modes_accept_supported_format(self, mode: str, fmt: str) -> None:
        # deps-tree/deps-compare's CLI supports markdown|json|html (`deps
        # tree --help`; html renders via cli_stack.py's stack_to_html) —
        # Codex review, PR #594: this validator originally rejected html
        # here too, which would have blocked a real, supported CLI format.
        result = _run_validate({"INPUT_MODE": mode, "INPUT_FORMAT": fmt})
        assert result.returncode == 0, result.stdout + result.stderr

    def test_compare_still_allows_sarif(self) -> None:
        result = _run_validate({"INPUT_MODE": "compare", "INPUT_FORMAT": "sarif"})
        assert result.returncode == 0, result.stdout + result.stderr

    @pytest.mark.parametrize("mode", ["deps-tree", "deps-compare"])
    def test_deps_modes_reject_directory_or_package(
        self, mode: str, tmp_path: Path
    ) -> None:
        # Regression (Codex review): `abicheck deps tree`/`deps compare`
        # both take a single BINARY, the same per-artifact contract dump/
        # scan have -- this branch only validated format, so an
        # unsupported directory/package operand passed this fail-fast step
        # and would only fail later in the CLI, after setup/dependency
        # installation.
        lib_dir = tmp_path / "lib"
        lib_dir.mkdir()
        result = _run_validate({"INPUT_MODE": mode, "INPUT_NEW_LIBRARY": str(lib_dir)})
        assert result.returncode == 1
        assert "does not accept a directory or package" in result.stdout

    @pytest.mark.parametrize("mode", ["deps-tree", "deps-compare"])
    def test_deps_modes_accept_plain_binary(self, mode: str, tmp_path: Path) -> None:
        lib = tmp_path / "libfoo.so.1"
        lib.write_text("")
        result = _run_validate({"INPUT_MODE": mode, "INPUT_NEW_LIBRARY": str(lib)})
        assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(
    not VALIDATE_SH.is_file(), reason="action/validate-inputs.sh not found"
)
class TestCompareFormatAllowlists:
    """compare's full --format choice set (`abicheck compare --help-all`) is
    json|markdown|sarif|html|junit|review; a directory/package operand fans
    out through the release engine, which narrows that to cli.py's
    _RELEASE_FORMATS = {json, markdown, junit} (sarif/html/review rejected
    -- a clear UsageError, surfaced as VERDICT=ERROR by run.sh -- but only
    after Python/deps are installed). Codex + CodeRabbit review, PR #594:
    catch both restrictions here too, checking BOTH old-library and
    new-library for the release-style case, and any garbage value (not
    just sarif/html) for both cases. See TestCompareFormatAllowlistMatchesCli
    for the drift guard against the live CLI."""

    @pytest.mark.parametrize("fmt", ["sarif", "html", "review", "xml"])
    def test_directory_new_library_rejects_non_release_format(
        self, tmp_path: Path, fmt: str
    ) -> None:
        lib_dir = tmp_path / "release"
        lib_dir.mkdir()
        result = _run_validate(
            {
                "INPUT_MODE": "compare",
                "INPUT_OLD_LIBRARY": "old.so",
                "INPUT_NEW_LIBRARY": str(lib_dir),
                "INPUT_FORMAT": fmt,
            }
        )
        assert result.returncode == 1
        assert "directory/package comparison" in result.stdout

    @pytest.mark.parametrize("fmt", ["sarif", "html", "review", "xml"])
    def test_directory_old_library_rejects_non_release_format(
        self, tmp_path: Path, fmt: str
    ) -> None:
        lib_dir = tmp_path / "release"
        lib_dir.mkdir()
        result = _run_validate(
            {
                "INPUT_MODE": "compare",
                "INPUT_OLD_LIBRARY": str(lib_dir),
                "INPUT_NEW_LIBRARY": "new.so",
                "INPUT_FORMAT": fmt,
            }
        )
        assert result.returncode == 1
        assert "directory/package comparison" in result.stdout

    @pytest.mark.parametrize("fmt", ["json", "markdown", "junit"])
    def test_directory_operand_accepts_release_formats(
        self, tmp_path: Path, fmt: str
    ) -> None:
        lib_dir = tmp_path / "release"
        lib_dir.mkdir()
        result = _run_validate(
            {
                "INPUT_MODE": "compare",
                "INPUT_OLD_LIBRARY": str(lib_dir),
                "INPUT_NEW_LIBRARY": "new.so",
                "INPUT_FORMAT": fmt,
            }
        )
        assert result.returncode == 0, result.stdout + result.stderr

    @pytest.mark.parametrize(
        "fmt", ["json", "markdown", "sarif", "html", "junit", "review"]
    )
    def test_single_pair_operands_accept_full_format_set(self, fmt: str) -> None:
        result = _run_validate(
            {
                "INPUT_MODE": "compare",
                "INPUT_OLD_LIBRARY": "old.so",
                "INPUT_NEW_LIBRARY": "new.so",
                "INPUT_FORMAT": fmt,
            }
        )
        assert result.returncode == 0, result.stdout + result.stderr

    def test_single_pair_operands_reject_garbage_format(self) -> None:
        result = _run_validate(
            {
                "INPUT_MODE": "compare",
                "INPUT_OLD_LIBRARY": "old.so",
                "INPUT_NEW_LIBRARY": "new.so",
                "INPUT_FORMAT": "xml",
            }
        )
        assert result.returncode == 1
        assert "does not support format" in result.stdout


@pytest.mark.skipif(
    not VALIDATE_SH.is_file(), reason="action/validate-inputs.sh not found"
)
class TestCompareFormatAllowlistMatchesCli:
    """Drift guard: validate-inputs.sh hardcodes two format allowlists for
    compare (single-pair, and directory/package). Parse them out of the
    script and cross-check against the live CLI's actual choices -- click's
    own Choice.choices for the single-pair set (introspected directly rather
    than scraped from --help-all's output, which rich-click hard-wraps
    mid-word inside its option table at this terminal width) and
    abicheck/cli.py's _RELEASE_FORMATS constant for the release-style set --
    so a future CLI format addition/removal doesn't silently desync the
    Action's fail-fast validator from what the CLI really accepts."""

    def _extract_allowlist(self, fail_marker: str) -> set[str]:
        """The `"$FORMAT" != "x"` chain in the `if`/`elif [[ ... ]]` block
        that guards the `_fail` call containing *fail_marker* -- scanned
        backward from that line to its nearest preceding `if`/`elif` line so
        two adjacent blocks (release-style vs. single-pair) aren't conflated."""
        lines = VALIDATE_SH.read_text(encoding="utf-8").splitlines()
        fail_idx = next(i for i, line in enumerate(lines) if fail_marker in line)
        start = fail_idx
        while start > 0 and not re.search(r"\b(if|elif)\s*\[\[", lines[start]):
            start -= 1
        condition_text = " ".join(lines[start : fail_idx + 1])
        return set(re.findall(r'"\$FORMAT" != "([a-z]+)"', condition_text))

    def test_single_pair_allowlist_matches_cli(self) -> None:
        validator_formats = self._extract_allowlist(
            "only 'json', 'markdown', 'sarif', 'html', 'junit', and 'review'"
        )
        from abicheck.cli import main as abicheck_main

        fmt_param = next(
            p for p in abicheck_main.commands["compare"].params if p.name == "fmt"
        )
        cli_formats = set(fmt_param.type.choices)
        assert validator_formats == cli_formats, (
            f"validate-inputs.sh's single-pair compare allowlist {sorted(validator_formats)} "
            f"has drifted from the live CLI's {sorted(cli_formats)}"
        )

    def test_release_style_allowlist_matches_cli_constant(self) -> None:
        validator_formats = self._extract_allowlist(
            "only 'json', 'markdown', and 'junit' are available"
        )
        cli_source = (
            Path(__file__).resolve().parents[1] / "abicheck" / "cli.py"
        ).read_text(encoding="utf-8")
        m = re.search(r"_RELEASE_FORMATS = frozenset\(\{([^}]+)\}\)", cli_source)
        assert m, "could not find _RELEASE_FORMATS in abicheck/cli.py"
        cli_formats = {f.strip().strip('"') for f in m.group(1).split(",")}
        assert validator_formats == cli_formats, (
            f"validate-inputs.sh's release-style compare allowlist {sorted(validator_formats)} "
            f"has drifted from abicheck/cli.py's _RELEASE_FORMATS {sorted(cli_formats)}"
        )


@pytest.mark.skipif(
    not VALIDATE_SH.is_file(), reason="action/validate-inputs.sh not found"
)
class TestCompareRejectsCompileContextForDirectoryOrPackage:
    """Mirrors run.sh's compile-context guard (Codex review): the per-
    library release fan-out never threads ast-frontend/gcc-*/sysroot/
    nostdinc to each pair's header dump, so a directory/package compare
    with any of these set must fail here, before dependency install --
    not only later in run.sh, reopening the exact silent-fallback-until-
    late-failure bug this validator exists to prevent (action/AGENTS.md:
    "Keep validate-inputs.sh and run.sh in sync")."""

    def test_gcc_path_against_directory_is_rejected(self, tmp_path: Path) -> None:
        lib_dir = tmp_path / "lib"
        lib_dir.mkdir()
        result = _run_validate(
            {
                "INPUT_MODE": "compare",
                "INPUT_OLD_LIBRARY": str(lib_dir),
                "INPUT_NEW_LIBRARY": "new.so",
                "INPUT_GCC_PATH": "/opt/gcc-14/bin/g++",
            }
        )
        assert result.returncode == 1
        assert "does not support ast-frontend/gcc-path" in result.stdout

    @pytest.mark.parametrize(
        "var,value",
        [
            ("INPUT_AST_FRONTEND", "clang"),
            ("INPUT_GCC_PATH", "/opt/gcc-14/bin/g++"),
            ("INPUT_GCC_PREFIX", "aarch64-linux-gnu-"),
            ("INPUT_GCC_OPTIONS", "-DFOO=1"),
            ("INPUT_SYSROOT", "/opt/sysroot"),
            ("INPUT_NOSTDINC", "true"),
        ],
    )
    def test_each_compile_context_input_is_rejected(
        self, tmp_path: Path, var: str, value: str
    ) -> None:
        pkg = tmp_path / "libfoo.rpm"
        pkg.write_text("")
        result = _run_validate(
            {
                "INPUT_MODE": "compare",
                "INPUT_OLD_LIBRARY": "old.so",
                "INPUT_NEW_LIBRARY": str(pkg),
                var: value,
            }
        )
        assert result.returncode == 1, f"{var}={value} should have been rejected"
        assert "does not support ast-frontend/gcc-path" in result.stdout

    def test_ast_frontend_auto_is_not_rejected(self, tmp_path: Path) -> None:
        """ "auto" is the documented no-op spelling -- resolves to the same
        default castxml selection as leaving the input unset -- and must
        not trip this guard, unlike a real frontend choice."""
        lib_dir = tmp_path / "lib"
        lib_dir.mkdir()
        result = _run_validate(
            {
                "INPUT_MODE": "compare",
                "INPUT_OLD_LIBRARY": str(lib_dir),
                "INPUT_NEW_LIBRARY": "new.so",
                "INPUT_AST_FRONTEND": "auto",
            }
        )
        assert result.returncode == 0, result.stdout + result.stderr

    def test_single_pair_compare_with_gcc_path_passes(self) -> None:
        result = _run_validate(
            {
                "INPUT_MODE": "compare",
                "INPUT_OLD_LIBRARY": "old.so",
                "INPUT_NEW_LIBRARY": "new.so",
                "INPUT_GCC_PATH": "/opt/gcc-14/bin/g++",
            }
        )
        assert result.returncode == 0, result.stdout + result.stderr

    def test_directory_compare_with_no_compile_context_passes(
        self, tmp_path: Path
    ) -> None:
        lib_dir = tmp_path / "lib"
        lib_dir.mkdir()
        result = _run_validate(
            {
                "INPUT_MODE": "compare",
                "INPUT_OLD_LIBRARY": str(lib_dir),
                "INPUT_NEW_LIBRARY": "new.so",
            }
        )
        assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(
    not VALIDATE_SH.is_file(), reason="action/validate-inputs.sh not found"
)
class TestUnsetFormatUsesEachModesOwnDefault:
    """Regression (Codex review, PR #594): action.yml's `format` input must
    NOT declare a static top-level default. GitHub Actions applies a
    declared default to `inputs.format` even when the caller's workflow
    never sets `format:` at all, so a single 'markdown' default would reach
    run.sh as INPUT_FORMAT=markdown for every mode -- including scan, whose
    own default is 'text' -- and this validator would then hard-reject the
    ordinary, most common scan invocation that never touches `format`.
    Leaving the input's default unset means an un-set `format:` reaches
    here as an empty string, and each run.sh mode branch already supplies
    its own correct per-mode default (`${INPUT_FORMAT:-text}` for scan,
    `${INPUT_FORMAT:-markdown}` elsewhere)."""

    @pytest.mark.parametrize(
        "mode", ["scan", "compare", "dump", "deps-tree", "deps-compare"]
    )
    def test_format_left_completely_unset_passes(self, mode: str) -> None:
        result = _run_validate({"INPUT_MODE": mode})
        assert result.returncode == 0, result.stdout + result.stderr

    def test_action_yml_format_input_has_no_static_default(self) -> None:
        """The actual regression: action.yml declaring `default: 'markdown'`
        on `format` would silently populate INPUT_FORMAT=markdown for scan
        runs that never set it, defeating the empty-string sentinel every
        mode branch's `${INPUT_FORMAT:-...}` relies on."""
        import yaml

        action_yml = ACTION_DIR.parent / "action.yml"
        data = yaml.safe_load(action_yml.read_text(encoding="utf-8"))
        assert "default" not in data["inputs"]["format"], (
            "action.yml's `format` input must not declare a default — see "
            "this class's docstring for why a single default breaks scan's "
            "own 'text' default."
        )


@pytest.mark.skipif(
    not VALIDATE_SH.is_file(), reason="action/validate-inputs.sh not found"
)
class TestUploadSarif:
    def test_upload_sarif_with_compare_and_sarif_format_passes(self) -> None:
        result = _run_validate(
            {
                "INPUT_MODE": "compare",
                "INPUT_FORMAT": "sarif",
                "INPUT_UPLOAD_SARIF": "true",
            }
        )
        assert result.returncode == 0, result.stdout + result.stderr

    def test_upload_sarif_with_non_compare_mode_is_rejected(self) -> None:
        result = _run_validate(
            {
                "INPUT_MODE": "scan",
                "INPUT_FORMAT": "json",
                "INPUT_UPLOAD_SARIF": "true",
            }
        )
        assert result.returncode == 1
        assert "upload-sarif is only meaningful with mode: compare" in result.stdout

    def test_upload_sarif_with_non_sarif_format_is_rejected(self) -> None:
        result = _run_validate(
            {
                "INPUT_MODE": "compare",
                "INPUT_FORMAT": "json",
                "INPUT_UPLOAD_SARIF": "true",
            }
        )
        assert result.returncode == 1
        assert "upload-sarif requires format: sarif" in result.stdout

    def test_upload_sarif_false_default_never_triggers_the_check(self) -> None:
        result = _run_validate({"INPUT_MODE": "scan", "INPUT_FORMAT": "json"})
        assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(
    not VALIDATE_SH.is_file(), reason="action/validate-inputs.sh not found"
)
class TestModeScopedInputWarnings:
    """P0.1: setting a mode-scoped input on an incompatible mode used to be
    a silent no-op. These inputs are legal-but-inert on the wrong mode, not
    errors, so the validator warns (exit 0, `::warning::` annotation)
    instead of failing the step."""

    @pytest.mark.parametrize(
        "env_name",
        [
            "INPUT_DEBUG_INFO1",
            "INPUT_DEBUG_INFO2",
            "INPUT_DEVEL_PKG1",
            "INPUT_DEVEL_PKG2",
        ],
    )
    def test_package_input_warns_on_non_compare_mode(self, env_name: str) -> None:
        result = _run_validate({"INPUT_MODE": "scan", env_name: "some-package.rpm"})
        assert result.returncode == 0, result.stdout + result.stderr
        assert "::warning::" in result.stdout
        assert "has no effect" in result.stdout

    def test_package_input_warns_on_compare_single_pair(self) -> None:
        result = _run_validate(
            {
                "INPUT_MODE": "compare",
                "INPUT_OLD_LIBRARY": "old.so",
                "INPUT_NEW_LIBRARY": "new.so",
                "INPUT_DEBUG_INFO1": "old-debuginfo.rpm",
            }
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "::warning::" in result.stdout

    def test_package_input_silent_on_compare_directory_operand(
        self, tmp_path: Path
    ) -> None:
        lib_dir = tmp_path / "release"
        lib_dir.mkdir()
        result = _run_validate(
            {
                "INPUT_MODE": "compare",
                "INPUT_OLD_LIBRARY": str(lib_dir),
                "INPUT_NEW_LIBRARY": "new.so",
                "INPUT_DEBUG_INFO1": "old-debuginfo.rpm",
            }
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "::warning::" not in result.stdout

    @pytest.mark.parametrize(
        "env_name",
        [
            "INPUT_DSO_ONLY",
            "INPUT_INCLUDE_PRIVATE_DSO",
            "INPUT_KEEP_EXTRACTED",
            "INPUT_FAIL_ON_REMOVED_LIBRARY",
        ],
    )
    def test_bool_input_warns_when_true_on_non_compare_mode(
        self, env_name: str
    ) -> None:
        result = _run_validate({"INPUT_MODE": "dump", env_name: "true"})
        assert result.returncode == 0, result.stdout + result.stderr
        assert "::warning::" in result.stdout

    @pytest.mark.parametrize(
        "env_name",
        [
            "INPUT_DSO_ONLY",
            "INPUT_INCLUDE_PRIVATE_DSO",
            "INPUT_KEEP_EXTRACTED",
            "INPUT_FAIL_ON_REMOVED_LIBRARY",
        ],
    )
    def test_bool_input_silent_when_false_default(self, env_name: str) -> None:
        result = _run_validate({"INPUT_MODE": "dump", env_name: "false"})
        assert result.returncode == 0, result.stdout + result.stderr
        assert "::warning::" not in result.stdout

    def test_jobs_warns_on_scan_mode(self) -> None:
        result = _run_validate({"INPUT_MODE": "scan", "INPUT_JOBS": "8"})
        assert result.returncode == 0, result.stdout + result.stderr
        assert "::warning::" in result.stdout
        assert "jobs" in result.stdout

    def test_jobs_warns_on_compare_single_pair(self) -> None:
        result = _run_validate(
            {
                "INPUT_MODE": "compare",
                "INPUT_OLD_LIBRARY": "old.so",
                "INPUT_NEW_LIBRARY": "new.so",
                "INPUT_JOBS": "8",
            }
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "::warning::" in result.stdout

    def test_jobs_default_value_never_warns(self) -> None:
        result = _run_validate({"INPUT_MODE": "scan"})
        assert result.returncode == 0, result.stdout + result.stderr
        assert "::warning::" not in result.stdout

    def test_jobs_silent_on_compare_directory_operand(self, tmp_path: Path) -> None:
        lib_dir = tmp_path / "release"
        lib_dir.mkdir()
        result = _run_validate(
            {
                "INPUT_MODE": "compare",
                "INPUT_OLD_LIBRARY": str(lib_dir),
                "INPUT_NEW_LIBRARY": "new.so",
                "INPUT_JOBS": "8",
            }
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "::warning::" not in result.stdout

    @pytest.mark.parametrize("mode", ["dump", "deps-tree", "deps-compare"])
    def test_abi_baseline_warns_outside_compare_and_scan(self, mode: str) -> None:
        result = _run_validate(
            {"INPUT_MODE": mode, "INPUT_ABI_BASELINE": "latest-release"}
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "::warning::" in result.stdout
        assert "abi-baseline" in result.stdout

    @pytest.mark.parametrize("mode", ["compare", "scan"])
    def test_abi_baseline_silent_on_compare_and_scan(self, mode: str) -> None:
        result = _run_validate(
            {"INPUT_MODE": mode, "INPUT_ABI_BASELINE": "latest-release"}
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "::warning::" not in result.stdout

    @pytest.mark.parametrize("env_name", ["INPUT_ESTIMATE", "INPUT_AUDIT"])
    def test_deprecated_scan_alias_warns_outside_scan(self, env_name: str) -> None:
        result = _run_validate({"INPUT_MODE": "compare", env_name: "true"})
        assert result.returncode == 0, result.stdout + result.stderr
        assert "::warning::" in result.stdout

    @pytest.mark.parametrize("env_name", ["INPUT_ESTIMATE", "INPUT_AUDIT"])
    def test_deprecated_scan_alias_silent_on_scan(self, env_name: str) -> None:
        result = _run_validate({"INPUT_MODE": "scan", env_name: "true"})
        assert result.returncode == 0, result.stdout + result.stderr
        assert "::warning::" not in result.stdout

    def test_no_mode_scoped_inputs_set_produces_no_warnings(self) -> None:
        result = _run_validate({"INPUT_MODE": "compare"})
        assert result.returncode == 0, result.stdout + result.stderr
        assert "::warning::" not in result.stdout


@pytest.mark.skipif(
    not VALIDATE_SH.is_file(), reason="action/validate-inputs.sh not found"
)
class TestScopedComparisonInputs:
    """ADR-043 --used-by/--required-symbol(s) contracts (G30 P1.3: resolves
    the ADR-047 S22/S23 gap -- these were previously not forwarded by the
    root Action at all)."""

    def test_used_by_and_required_symbol_together_is_hard_error(self) -> None:
        # The CLI itself rejects this combination, but only after Python
        # setup/dependency install/pip install -- catch it here instead,
        # before any of that (same rationale as every other check in this
        # script).
        result = _run_validate(
            {
                "INPUT_MODE": "compare",
                "INPUT_USED_BY": "app1",
                "INPUT_REQUIRED_SYMBOL": "abi_do_thing",
            }
        )
        assert result.returncode == 1
        assert "mutually exclusive" in result.stdout

    def test_used_by_and_required_symbols_file_together_is_hard_error(self) -> None:
        result = _run_validate(
            {
                "INPUT_MODE": "compare",
                "INPUT_USED_BY": "app1",
                "INPUT_REQUIRED_SYMBOLS": "symbols.txt",
            }
        )
        assert result.returncode == 1
        assert "mutually exclusive" in result.stdout

    def test_used_by_alone_passes(self) -> None:
        result = _run_validate({"INPUT_MODE": "compare", "INPUT_USED_BY": "app1"})
        assert result.returncode == 0, result.stdout + result.stderr

    def test_required_symbol_alone_passes(self) -> None:
        result = _run_validate(
            {"INPUT_MODE": "compare", "INPUT_REQUIRED_SYMBOL": "abi_do_thing"}
        )
        assert result.returncode == 0, result.stdout + result.stderr

    @pytest.mark.parametrize(
        "env_name,value",
        [
            ("INPUT_USED_BY", "app1"),
            ("INPUT_VERIFY_RUNTIME", "true"),
            ("INPUT_REQUIRED_SYMBOL", "abi_do_thing"),
            ("INPUT_REQUIRED_SYMBOLS", "symbols.txt"),
        ],
    )
    def test_scoped_input_warns_on_non_compare_mode(
        self, env_name: str, value: str
    ) -> None:
        result = _run_validate({"INPUT_MODE": "scan", env_name: value})
        assert result.returncode == 0, result.stdout + result.stderr
        assert "::warning::" in result.stdout
        assert "has no effect" in result.stdout

    def test_no_scoped_inputs_set_produces_no_warnings(self) -> None:
        result = _run_validate({"INPUT_MODE": "scan"})
        assert result.returncode == 0, result.stdout + result.stderr
        assert "::warning::" not in result.stdout


# ─────────────────────────────────────────────────────────────────────────
# Drift guard: validate-inputs.sh intentionally duplicates run.sh's
# `_is_release_style_operand()` (documented at the top of validate-inputs.sh
# as to why: this step must have zero dependency on run.sh's layout). Run
# both copies against the same fixtures and assert identical verdicts so a
# future edit to one classifier that isn't mirrored in the other is caught
# here instead of shipping a validator that disagrees with run.sh itself.
# ─────────────────────────────────────────────────────────────────────────

_RUN_SH_MARKER = "# Build the abicheck command"
_VALIDATE_SH_MARKER = "_fail() {"


def _run_sh_operand_fn() -> str:
    text = RUN_SH.read_text(encoding="utf-8")
    idx = text.index(_RUN_SH_MARKER)
    return text[:idx]


def _validate_sh_operand_fn() -> str:
    text = VALIDATE_SH.read_text(encoding="utf-8")
    idx = text.index(_VALIDATE_SH_MARKER)
    return text[:idx]


def _classify(fn_region: str, path: str) -> bool:
    script = (
        fn_region
        + f'\nif _is_release_style_operand "{path}"; then exit 0; else exit 1; fi\n'
    )
    with tempfile.NamedTemporaryFile(
        "w",
        suffix=".sh",
        delete=False,
        encoding="utf-8",
        newline="\n",
    ) as f:
        f.write(script)
        script_path = f.name
    try:
        result = subprocess.run(
            [_bash_executable(), script_path],
            capture_output=True,
            text=True,
        )
    finally:
        os.unlink(script_path)
    return result.returncode == 0


@pytest.mark.skipif(
    not (VALIDATE_SH.is_file() and RUN_SH.is_file()),
    reason="action scripts not found",
)
class TestClassifierParityWithRunSh:
    @pytest.mark.parametrize(
        "kind", ["directory", "plain_file", "json_snapshot", "rpm_extension"]
    )
    def test_both_copies_agree(self, tmp_path: Path, kind: str) -> None:
        if kind == "directory":
            path = tmp_path / "dir"
            path.mkdir()
        elif kind == "plain_file":
            path = tmp_path / "libfoo.so.1"
            path.write_text("")
        elif kind == "json_snapshot":
            path = tmp_path / "snap.json"
            path.write_text("{}")
        else:
            path = tmp_path / "libfoo.rpm"
            path.write_text("")

        run_sh_verdict = _classify(_run_sh_operand_fn(), str(path))
        validate_sh_verdict = _classify(_validate_sh_operand_fn(), str(path))
        assert run_sh_verdict == validate_sh_verdict, (
            f"run.sh and validate-inputs.sh disagree on {kind} ({path}): "
            f"run.sh={run_sh_verdict} validate-inputs.sh={validate_sh_verdict}"
        )
