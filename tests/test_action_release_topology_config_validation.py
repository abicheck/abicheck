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

"""Two further Codex review findings on PR #1159's
``add_release_topology_config_flags``/``_merge_config_overlay_with_
discovered_project_config`` (``action/run.sh``), split into this sibling
module rather than appended to ``test_action_release_topology_config.py``
itself -- that file was already at the 1200-line test-file cap when these
findings landed, and a brand-new test file (unlike an existing one) cannot
be exempted into ``architecture/debt.yaml``'s adoption-debt ledger
(``scripts/check_architecture.py``'s own ``debt-exemption`` rule). Reuses
that module's own ``_harness``/``_run_bash_script`` rather than duplicating
their verbatim-extraction machinery, since both new tests need nothing else
from it.

1. **Merge-subprocess-failure overlay leak.** ``_merge_config_overlay_with_
   discovered_project_config``'s own ``exit 1`` on a nonzero merge-
   subprocess status (malformed YAML, a real ``yaml.safe_load`` failure)
   happens after the overlay's ``mktemp`` and before the main ``EXIT`` trap,
   further down in ``run.sh``, installs -- the same early-exit leak window
   ``_rm_overlay_on_early_exit`` already closes at the missing-interpreter
   and ``_mktemp_canonical``-failure sites, but this particular site was
   missed.

2. **Unvalidated base config silently masked by the overlay merge.** A
   discovered/explicit base document that is syntactically VALID YAML but
   structurally invalid per ``BuildConfig``'s own strict schema (e.g.
   ``release: []`` instead of a mapping, or an unknown top-level key) used
   to be merged blindly -- an Action input sharing the same top-level key
   (e.g. ``dso-only`` sharing ``release:``) would unconditionally REPLACE
   the invalid value with the overlay's own valid one, silently masking the
   user's config error instead of surfacing the same usage error the
   equivalent native CLI invocation raises. Now validated via
   ``BuildConfig.from_dict`` (the real strict-schema check, not a
   reimplementation) before any merge happens.
"""

from __future__ import annotations

from pathlib import Path

from test_action_release_topology_config import (
    _harness,
    _read_config_overlay,
    _run_bash_script,
)


class TestReleaseTopologyOverlayCleansUpOnMergeSubprocessFailure:
    def test_merge_subprocess_failure_removes_the_overlay(self, tmp_path: Path) -> None:
        """This is the one early-exit site that reaches the merge
        subprocess itself (unlike the missing-interpreter guards, which
        fire before the subprocess even starts), so it exercises the
        cleanup call inside ``_merge_config_overlay_with_discovered_
        project_config`` specifically, using its own ``$out_path`` local
        rather than either script-global overlay variable. ``RUNNER_TEMP``
        is redirected to a private ``tmp_path`` so the overlay this run
        creates can be told apart from any other leaked file under the
        real ``/tmp``."""
        (tmp_path / ".abicheck.yml").write_text(
            "release: [unterminated\n", encoding="utf-8"
        )
        result = _run_bash_script(
            _harness(),
            {"INPUT_DSO_ONLY": "true", "RUNNER_TEMP": str(tmp_path)},
            cwd=tmp_path,
        )
        assert result.returncode != 0
        leftover = list(tmp_path.glob("abicheck-release-topology.*"))
        assert leftover == [], (
            f"the overlay should have been removed on the merge failure, found: {leftover}"
        )


class TestReleaseTopologyOverlayValidatesTheBaseConfigBeforeMerging:
    def test_structurally_invalid_release_block_fails_loud_not_masked(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / ".abicheck.yml").write_text("release: []\n", encoding="utf-8")
        result = _run_bash_script(_harness(), {"INPUT_DSO_ONLY": "true"}, cwd=tmp_path)
        assert result.returncode != 0
        assert "release must be a mapping" in result.stderr
        assert "failed to merge" in result.stdout
        # The invalid release: [] must NOT have been silently replaced by
        # the overlay's own valid release.dso_only and written through.
        assert "--config" not in result.stdout

    def test_unknown_top_level_key_fails_loud_not_masked(self, tmp_path: Path) -> None:
        (tmp_path / ".abicheck.yml").write_text(
            "release:\n  dso_only: false\nnot_a_real_key: 1\n", encoding="utf-8"
        )
        result = _run_bash_script(_harness(), {"INPUT_DSO_ONLY": "true"}, cwd=tmp_path)
        assert result.returncode != 0
        assert "unknown .abicheck.yml key 'not_a_real_key'" in result.stderr
        assert "--config" not in result.stdout


class TestReleaseTopologyOverlayHonorsExplicitFalseInputs:
    """Codex review, PR #1159 (P1, fresh evidence): ``dso-only``/
    ``include-private-dso``/``fail-on-removed-library`` used to collapse
    "omitted" and "explicit false" onto the identical value
    (``${INPUT_DSO_ONLY:-false}``, plus a matching declared ``default:
    'false'`` in ``action.yml``), so a workflow that explicitly wrote
    ``dso-only: false`` to override a discovered/explicit ``.abicheck.yml``'s
    own ``release.dso_only: true`` had that override silently ignored --
    the early-return guard fired (every input read as the literal string
    "false") and the discovered ``true`` value remained in effect
    untouched. ``action.yml`` now declares no default for any of the three
    inputs, so an omitted one resolves to an empty string, distinguishable
    from an explicit ``"true"``/``"false"``; the guard and the overlay
    generator key off that emptiness, not truthiness."""

    def test_explicit_false_overrides_a_discovered_true(self, tmp_path: Path) -> None:
        (tmp_path / ".abicheck.yml").write_text(
            "release:\n  dso_only: true\n", encoding="utf-8"
        )
        result = _run_bash_script(_harness(), {"INPUT_DSO_ONLY": "false"}, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        doc = _read_config_overlay(result.stdout.splitlines())
        assert doc["release"]["dso_only"] is False

    def test_explicit_false_fail_on_removed_library_overrides_discovered_true(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / ".abicheck.yml").write_text(
            "gate:\n  fail_on_removed_library: true\n", encoding="utf-8"
        )
        result = _run_bash_script(
            _harness(), {"INPUT_FAIL_ON_REMOVED_LIBRARY": "false"}, cwd=tmp_path
        )
        assert result.returncode == 0, result.stderr
        doc = _read_config_overlay(result.stdout.splitlines())
        assert doc["gate"]["fail_on_removed_library"] is False

    def test_omitted_input_leaves_a_discovered_true_untouched(
        self, tmp_path: Path
    ) -> None:
        """The companion case: when dso-only is never mentioned at all (as
        opposed to explicitly set to false), the discovered config's own
        true value must survive -- proving the fix distinguishes the two
        rather than always forcing a value through. Triggers
        add_release_topology_config_flags via a different, explicitly-set
        input (fail-on-removed-library) so the function still runs."""
        (tmp_path / ".abicheck.yml").write_text(
            "release:\n  dso_only: true\n", encoding="utf-8"
        )
        result = _run_bash_script(
            _harness(), {"INPUT_FAIL_ON_REMOVED_LIBRARY": "true"}, cwd=tmp_path
        )
        assert result.returncode == 0, result.stderr
        doc = _read_config_overlay(result.stdout.splitlines())
        assert doc["release"]["dso_only"] is True
        assert doc["gate"]["fail_on_removed_library"] is True

    def test_all_inputs_omitted_is_still_a_no_op(self, tmp_path: Path) -> None:
        """Every input empty (never given) must still take the early-return
        path -- the fix must not turn this function into a no-longer-
        skippable no-op that always synthesizes an overlay."""
        result = _run_bash_script(_harness(), {}, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        assert result.stdout.splitlines() == ["compare"]
