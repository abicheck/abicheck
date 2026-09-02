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

"""P1 (CLI-audit): a directory/package (release) `compare`'s own summary
``effective_config_fields``/``effective_config_digest`` must actually
reflect the ``--policy``/``--policy-file`` every library in the release was
compared under -- not read as though no policy existed at all.

Before this fix, ``_release_summary_effective_config_block`` computed the
baseline tier from a bare, empty ``SimpleNamespace()`` carrying only
``severity_config``, so ``policy.base``/``policy.overrides``/
``policy.reclassify`` all came back empty on the release path even though
the exact same policy demonstrably *did* apply (each library's own verdict
reflects it) -- indistinguishable from a run with no ``--policy-file`` at
all.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import snapshot_to_json

# ── helpers ──────────────────────────────────────────────────────────────


def _breaking_pair(lib: str = "libfoo.so") -> tuple[AbiSnapshot, AbiSnapshot]:
    """Remove a public function -- a FUNCTION_REMOVED finding, BREAKING by
    ``strict_abi`` default."""
    old = AbiSnapshot(
        library=lib,
        version="1.0",
        functions=[
            Function(
                name="foo",
                mangled="_Z3foov",
                return_type="int",
                visibility=Visibility.PUBLIC,
            ),
        ],
        from_headers=True,
    )
    new = AbiSnapshot(library=lib, version="2.0", functions=[], from_headers=True)
    return old, new


def _write_snap(path: Path, snap: AbiSnapshot) -> Path:
    path.write_text(snapshot_to_json(snap), encoding="utf-8")
    return path


def _write_policy(tmp_path: Path) -> Path:
    """A policy document with both an ``overrides:`` entry (demotes
    func_removed to a non-breaking risk) and a ``reclassify:`` rule
    (kept simple: re-affirms the same kind via a selector), so both
    ``policy.overrides`` and ``policy.reclassify`` have real, non-empty
    content to prove present in the release-level digest."""
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(
        "base_policy: strict_abi\n"
        "overrides:\n"
        "  func_removed: risk\n"
        "reclassify:\n"
        "  - symbol_pattern: '.*'\n"
        "    to: risk\n"
        "    reason: keep func_removed as risk for this release\n",
        encoding="utf-8",
    )
    return policy_path


def _invoke(*args: str) -> tuple[int, str]:
    """Runs the CLI and returns ``(exit_code, stdout)`` -- stdout only (not
    ``result.output``, which mixes in stderr): the reclassify rule this
    module's policy fixture exercises deliberately logs a WARNING to
    stderr, which would otherwise land ahead of the JSON payload."""
    from abicheck.cli import main

    result = CliRunner().invoke(main, list(args))
    return result.exit_code, result.stdout


class TestReleaseEffectiveConfigCarriesRealPolicy:
    def test_policy_base_is_not_empty_on_release_summary(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        old_foo, new_foo = _breaking_pair()
        _write_snap(old_dir / "libfoo.json", old_foo)
        _write_snap(new_dir / "libfoo.json", new_foo)
        policy_path = _write_policy(tmp_path)

        code, out = _invoke(
            "compare",
            str(old_dir),
            str(new_dir),
            "--policy",
            str(policy_path),
            "--jobs",
            "1",
            "--format",
            "json",
        )
        assert code == 0, out  # demoted to risk -> COMPATIBLE_WITH_RISK
        data = json.loads(out)
        fields = data["effective_config_fields"]
        assert fields["_tier"] == "baseline"
        assert fields["policy.base"] != ""
        assert fields["policy.overrides"] != ""
        assert "func_removed=" in fields["policy.overrides"]
        assert fields["policy.reclassify"] != ""

    def test_no_policy_file_still_reports_bare_default_base(
        self, tmp_path: Path
    ) -> None:
        """Regression guard: a release run with no --policy-file must still
        report a non-empty policy.base (the bare 'strict_abi' default),
        and empty overrides/reclassify -- not error, and not silently
        regress to always-empty."""
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        old_foo, new_foo = _breaking_pair()
        _write_snap(old_dir / "libfoo.json", old_foo)
        _write_snap(new_dir / "libfoo.json", new_foo)

        code, out = _invoke(
            "compare",
            str(old_dir),
            str(new_dir),
            "--jobs",
            "1",
            "--format",
            "json",
        )
        assert code == 4, out  # undemoted FUNCTION_REMOVED -> BREAKING
        data = json.loads(out)
        fields = data["effective_config_fields"]
        assert fields["policy.base"] != ""
        assert fields["policy.overrides"] == ""
        assert fields["policy.reclassify"] == "[]"

    def test_matches_what_the_identical_single_pair_compare_reports(
        self, tmp_path: Path
    ) -> None:
        """The release-level summary's policy.base/overrides/reclassify
        must match the exact same policy document's effect on a
        single-pair compare of the same two snapshots -- proving the
        release path is no longer reading as though no policy existed."""
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        old_foo, new_foo = _breaking_pair()
        old_path = _write_snap(old_dir / "libfoo.json", old_foo)
        new_path = _write_snap(new_dir / "libfoo.json", new_foo)
        policy_path = _write_policy(tmp_path)

        _, single_out = _invoke(
            "compare",
            str(old_path),
            str(new_path),
            "--policy",
            str(policy_path),
            "--format",
            "json",
        )
        single_fields = json.loads(single_out)["effective_config_fields"]

        _, release_out = _invoke(
            "compare",
            str(old_dir),
            str(new_dir),
            "--policy",
            str(policy_path),
            "--jobs",
            "1",
            "--format",
            "json",
        )
        release_fields = json.loads(release_out)["effective_config_fields"]

        assert release_fields["policy.base"] == single_fields["policy.base"]
        assert release_fields["policy.overrides"] == single_fields["policy.overrides"]
        assert release_fields["policy.reclassify"] == single_fields["policy.reclassify"]


class TestReleasePolicyOverrideWarningFiresOnce:
    """P3 (CLI-audit): a directory/package `compare` calls
    `_setup_verbosity` twice in one process (the outer `compare` command,
    then again inside the internal `compare_release_cmd` it dispatches to)
    -- before the fix, this accumulated a second handler on the shared
    "abicheck" logger, so `policy_file.validate_overrides()`'s "usually
    causes binary incompatibility" warning (and every other `_logger`
    call) printed twice per event, regardless of how many rules/libraries
    were involved."""

    def test_downgrade_warning_appears_exactly_once(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        old_foo, new_foo = _breaking_pair()
        _write_snap(old_dir / "libfoo.json", old_foo)
        _write_snap(new_dir / "libfoo.json", new_foo)
        policy_path = tmp_path / "policy.yaml"
        policy_path.write_text(
            "base_policy: strict_abi\noverrides:\n  func_removed: risk\n",
            encoding="utf-8",
        )

        from abicheck.cli import main

        result = CliRunner().invoke(
            main,
            [
                "compare",
                str(old_dir),
                str(new_dir),
                "--policy",
                str(policy_path),
                "--jobs",
                "1",
                "--format",
                "json",
            ],
        )
        assert result.exit_code == 0, result.output
        occurrences = result.output.count("usually causes binary incompatibility")
        assert occurrences == 1, (
            f"expected the downgrade warning exactly once, got {occurrences}"
        )
