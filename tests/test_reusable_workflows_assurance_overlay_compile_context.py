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

"""``actions/check-target/action.yml``'s "Generate assurance-overlay
config" step: single-sided-vs-ambiguous ``compile:``/``source:``
(singular)/``debug:`` sources-root promotion (PR #1222 fourth round).

Split out of
``tests/test_reusable_workflows_require_complete_analysis.py`` purely to
keep that file under its ``architecture/debt.yaml`` ``no_growth`` test-file
size baseline -- the same "move responsibility, don't trim to fit"
convention the root ``AGENTS.md`` states. Shares the same execution
helpers (``tests/_assurance_overlay_exec.py``) that file's own
``TestAssuranceOverlayPromotesSourcesRootBuildAndSourcesBlocks`` class
uses, unchanged.

See ``tests/_assurance_overlay_exec.py``'s own module docstring and this
module's class docstring below for the full account of the bug this fixes.
"""

from __future__ import annotations

from pathlib import Path

from _assurance_overlay_exec import _run_overlay, _written_overlay
from _workflow_exec import make_workspace


class TestAssuranceOverlayPromotesSourcesRootCompileSourceDebugBlocks:
    """P1 finding (Codex review, fresh evidence, PR #1222 fourth round):
    the previous round's ``apply_sources_root_config_blocks`` fix (see
    ``TestAssuranceOverlayPromotesSourcesRootBuildAndSourcesBlocks`` above)
    deliberately scoped promotion to ``build:``/``sources:`` only,
    believing this step had "no reliable way to determine" whether the
    nested "Run analysis" invocation resolves ``compile:``/``source:``
    (singular)/``debug:`` single-sidedly or pair-wide for a given
    kind/mode combination. It does: this step's own inputs (``kind``,
    ``baseline-channel``) already determine "Run analysis"'s own ``mode:``
    and ``old-library:`` (see the "Generate assurance-overlay config"
    step's own ``SOURCES_PAIRWISE`` env docstring in
    ``actions/check-target/action.yml`` for the exact reasoning), mirroring
    ``action/run.sh``'s own ``_compile_context_sources_pairwise``:

    * ``kind: target`` (baseline OR audit path) always resolves
      old-library to resolve-baseline's own STORED JSON snapshot (audit
      mode has no old-library at all) -- single-sided, safe to promote.
    * ``kind: bundle`` with a real baseline (``mode: compare``) resolves
      old-library to a directory of live, real ELF member binaries --
      genuinely pair-wide, still excluded.

    These tests exercise the step's real Python body directly via the
    ``SOURCES_PAIRWISE`` env var this step's own ``env:`` block computes
    from ``inputs.kind``/``inputs.baseline-channel`` -- the same execution
    style ``TestAssuranceOverlayPromotesSourcesRootBuildAndSourcesBlocks``
    above already uses, so a change to the underlying merge logic is
    caught the same way (not just asserting the workflow YAML's text).
    """

    def test_single_sided_kind_target_promotes_compile_source_debug(
        self, tmp_path: Path
    ) -> None:
        """kind: target (baseline-channel set, mode: compare against a
        stored snapshot) is single-sided -- SOURCES_PAIRWISE stays empty,
        exactly as ``inputs.kind == 'bundle' && inputs.baseline-channel !=
        'none'`` evaluates for kind: target."""
        workspace = make_workspace(tmp_path)
        src_dir = workspace / "src"
        src_dir.mkdir()
        (src_dir / ".abicheck.yml").write_text(
            "compile:\n  std: c++20\n"
            "source:\n  method: replay\n"
            "debug:\n  format: dwarf\n"
            "build:\n  system: cmake\n",
            encoding="utf-8",
        )
        result = _run_overlay(
            workspace,
            {
                "BASE_CONFIG": "",
                "SOURCES_ROOT": str(src_dir),
                "SOURCES_PAIRWISE": "",
            },
        )
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        assert written["compile"] == {"std": "c++20"}
        assert written["source"] == {"method": "replay"}
        assert written["debug"] == {"format": "dwarf"}
        assert written["build"] == {"system": "cmake"}

    def test_single_sided_audit_mode_promotes_compile_source_debug(
        self, tmp_path: Path
    ) -> None:
        """kind: target, baseline-channel: none (mode: scan, the "audit"
        path) is ALSO single-sided -- scan resolves ONE shared compile
        context for the whole invocation, applied to both the baseline and
        the candidate, unconditionally regardless of kind. Same
        SOURCES_PAIRWISE value (empty) as the baseline path above, since
        check-target's own env formula is ``inputs.kind == 'bundle' &&
        inputs.baseline-channel != 'none'`` -- false for EITHER kind when
        baseline-channel is 'none'."""
        workspace = make_workspace(tmp_path)
        src_dir = workspace / "src"
        src_dir.mkdir()
        (src_dir / ".abicheck.yml").write_text(
            "compile:\n  std: c++20\n", encoding="utf-8"
        )
        result = _run_overlay(
            workspace,
            {
                "BASE_CONFIG": "",
                "SOURCES_ROOT": str(src_dir),
                "SOURCES_PAIRWISE": "",
            },
        )
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        assert written["compile"] == {"std": "c++20"}

    def test_ambiguous_bundle_with_baseline_excludes_compile_source_debug(
        self, tmp_path: Path
    ) -> None:
        """kind: bundle with a real baseline-channel (mode: compare against
        a live binaries-dir) is genuinely pair-wide/ambiguous -- this
        step's own env formula sets SOURCES_PAIRWISE to 'pairwise', and
        compile:/source:/debug: must stay excluded, matching the
        documented rationale. build:/sources: (unconditionally
        single-sided in every mode) are still promoted."""
        workspace = make_workspace(tmp_path)
        src_dir = workspace / "src"
        src_dir.mkdir()
        (src_dir / ".abicheck.yml").write_text(
            "compile:\n  std: c++20\n"
            "source:\n  method: replay\n"
            "debug:\n  format: dwarf\n"
            "build:\n  system: cmake\n",
            encoding="utf-8",
        )
        result = _run_overlay(
            workspace,
            {
                "BASE_CONFIG": "",
                "SOURCES_ROOT": str(src_dir),
                "SOURCES_PAIRWISE": "pairwise",
            },
        )
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        assert "compile" not in written
        assert "source" not in written
        assert "debug" not in written
        assert written["build"] == {"system": "cmake"}

    def test_ambiguous_mode_still_rebases_compile_include_dirs_from_checkout_root(
        self, tmp_path: Path
    ) -> None:
        """When compile: is excluded from sources-root promotion (pairwise),
        a checkout-root document's own compile.include_dirs must still be
        rebased against the CHECKOUT root, not silently left pointing at the
        sources-root's own project root -- found_path must stay unchanged
        (the pairwise branch never reassigns it), mirroring run.sh's own
        identical rule."""
        workspace = make_workspace(tmp_path)
        (workspace / ".abicheck.yml").write_text(
            "compile:\n  include_dirs: [include]\n", encoding="utf-8"
        )
        src_dir = workspace / "src"
        src_dir.mkdir()
        (src_dir / ".abicheck.yml").write_text(
            "compile:\n  std: c++20\n", encoding="utf-8"
        )
        result = _run_overlay(
            workspace,
            {
                "BASE_CONFIG": "",
                "SOURCES_ROOT": str(src_dir),
                "SOURCES_PAIRWISE": "pairwise",
            },
        )
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        # compile: was NOT promoted from the sources root (pairwise), so
        # the checkout-root document's own compile.include_dirs survives,
        # rebased against the CHECKOUT root (workspace), not src_dir.
        assert written["compile"]["include_dirs"] == [str(workspace / "include")]

    def test_single_sided_mode_rebases_compile_include_dirs_from_sources_root(
        self, tmp_path: Path
    ) -> None:
        """When compile: IS promoted from the sources root (single-sided),
        a relative compile.include_dirs entry in THAT document must rebase
        against the SOURCES root, not the checkout root -- found_path must
        be reassigned to the sources-root document, mirroring run.sh's own
        identical rule."""
        workspace = make_workspace(tmp_path)
        (workspace / ".abicheck.yml").write_text(
            "compile:\n  include_dirs: [wrong]\n", encoding="utf-8"
        )
        src_dir = workspace / "src"
        src_dir.mkdir()
        (src_dir / ".abicheck.yml").write_text(
            "compile:\n  include_dirs: [include]\n", encoding="utf-8"
        )
        result = _run_overlay(
            workspace,
            {
                "BASE_CONFIG": "",
                "SOURCES_ROOT": str(src_dir),
                "SOURCES_PAIRWISE": "",
            },
        )
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        assert written["compile"]["include_dirs"] == [str(src_dir / "include")]

    def test_ambiguous_sources_root_promotion_still_subject_to_stripping(
        self, tmp_path: Path
    ) -> None:
        """Negative control: excluding compile:/source:/debug: for an
        ambiguous kind: bundle + baseline combination must not disable
        build:/sources: promotion, nor the execution-key stripping that
        already covers the promoted build: block."""
        workspace = make_workspace(tmp_path)
        src_dir = workspace / "src"
        src_dir.mkdir()
        (src_dir / ".abicheck.yml").write_text(
            "build:\n  query: cmake --build .\n  system: cmake\n"
            "compile:\n  std: c++20\n",
            encoding="utf-8",
        )
        result = _run_overlay(
            workspace,
            {
                "BASE_CONFIG": "",
                "SOURCES_ROOT": str(src_dir),
                "SOURCES_PAIRWISE": "pairwise",
            },
        )
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        assert written["build"] == {"system": "cmake"}
        assert "build.query" in result.stderr
        assert "compile" not in written
