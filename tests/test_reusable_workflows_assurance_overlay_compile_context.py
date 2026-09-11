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

from abicheck.cli_options import merge_compile_config
from abicheck.dry_run_estimate import CompileContext


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

    P1 finding, PR #1222 eighth round (Codex review, fresh evidence):
    within the single-sided (non-pairwise) bucket, ``source:``(singular)/
    ``debug:`` do NOT resolve the same way ``compile:`` used to. `compile:`
    genuinely two-stage-merges a sources-root document on top of the
    checkout config for `dump`/`scan`'s single-document shape
    (``SOURCES_MERGE_COMPILE`` unset) -- but `source:`/`debug:` are
    single-document-selected off the sources-root tree ONLY for `dump`/
    `scan`; `compare`'s own pipeline never re-resolves either from a
    per-side ``--sources`` tree at all, always taking both from the
    CHECKOUT project config alone.

    P1 finding, PR #1222 eleventh round (Codex review, fresh evidence --
    reverses the fourth round's own conclusion above): `compile:` is now
    treated like `source:`/`debug:`, not merged from the sources root, for
    `mode: compare`'s own single-sided shape (``SOURCES_MERGE_COMPILE:
    true``) either. `frontends/cli/commands/compare.py`'s
    ``_embed_inline_source_side`` already, unconditionally, folds the live
    NEW side's own ``--sources`` tree's ``compile:`` block onto the CLI's
    already-resolved compile context the moment ``--new-sources`` names a
    raw tree -- regardless of whether ``--config``/``build-config`` was
    explicit. So this step's own promotion additionally merging the same
    sources-root document's ``compile:`` block folds it in TWICE, applying
    a repeat-sensitive flag (``compile.options: [-include, foo.h]``) twice
    in the final compiler invocation purely because
    ``analysis.assurance: complete`` was enabled -- the exact P1 finding
    these tests now assert against. The corrected single-sided bucket is
    really two cases: `mode: compare` (``SOURCES_MERGE_COMPILE: true``)
    promotes only ``build``/``sources``, leaving ``compile:`` at whatever
    the checkout document/``BASE_CONFIG`` already supplied and letting the
    CLI's own second stage run unaided; `dump`/`scan`
    (``SOURCES_MERGE_COMPILE`` unset) promotes all five, unchanged.
    """

    def test_single_sided_kind_target_leaves_compile_unpromoted(
        self, tmp_path: Path
    ) -> None:
        """kind: target (baseline-channel set, mode: compare against a
        stored snapshot) is single-sided -- SOURCES_PAIRWISE stays empty,
        exactly as ``inputs.kind == 'bundle' && inputs.baseline-channel !=
        'none'`` evaluates for kind: target -- but this step's own env
        formula ALSO sets SOURCES_MERGE_COMPILE to ``true`` here
        (``inputs.baseline-channel != 'none'``), which is `mode: compare`'s
        own real single-sided signature.

        P1 finding (Codex review, fresh evidence, PR #1222 eleventh
        round): with no checkout-root ``.abicheck.yml`` at all, the
        overlay must carry NO ``compile:`` key whatsoever -- neither
        promoted nor merged from the sources-root document, which the CLI's
        own ``_embed_inline_source_side`` will fold in on its own, exactly
        once, when "Run analysis" actually runs. ``build:``/``sources:``
        stay promoted (unaffected by this fix); ``source:``/``debug:``
        stay absent, as before."""
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
                "SOURCES_MERGE_COMPILE": "true",
            },
        )
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        assert "compile" not in written
        assert "source" not in written
        assert "debug" not in written
        assert written["build"] == {"system": "cmake"}

    def test_single_sided_compare_mode_preserves_checkout_source_debug(
        self, tmp_path: Path
    ) -> None:
        """The companion positive control for the fix above -- when the
        CHECKOUT document (not the sources-root one) sets `source:`/
        `debug:`, those checkout values must survive into the overlay
        UNCHANGED for `mode: compare`'s single-sided shape
        (SOURCES_MERGE_COMPILE: true), exactly matching the real
        `compare` pipeline's own checkout-only resolution -- even though
        the sources-root document sets conflicting values of its own,
        which must be ignored entirely. ``compile:`` (P1 finding, PR #1222
        eleventh round) follows the identical rule now: the checkout
        document here declares no ``compile:`` block at all, so the
        overlay must carry none either -- the sources-root document's own
        ``compile: std: c++20`` is never promoted/merged into it, left
        entirely for the CLI's own single, unconditional fold."""
        workspace = make_workspace(tmp_path)
        (workspace / ".abicheck.yml").write_text(
            "source:\n  method: headers\ndebug:\n  format: btf\n",
            encoding="utf-8",
        )
        src_dir = workspace / "src"
        src_dir.mkdir()
        (src_dir / ".abicheck.yml").write_text(
            "source:\n  method: replay\n"
            "debug:\n  format: dwarf\n"
            "compile:\n  std: c++20\n",
            encoding="utf-8",
        )
        result = _run_overlay(
            workspace,
            {
                "BASE_CONFIG": "",
                "SOURCES_ROOT": str(src_dir),
                "SOURCES_PAIRWISE": "",
                "SOURCES_MERGE_COMPILE": "true",
            },
        )
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        assert written["source"] == {"method": "headers"}
        assert written["debug"] == {"format": "btf"}
        assert "compile" not in written

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
        baseline-channel is 'none'. Unlike `mode: compare` above,
        ``SOURCES_MERGE_COMPILE`` is unset here (`scan`'s own
        single-document-REPLACE shape), so `compile:` IS still promoted --
        this fix is scoped to `mode: compare` only."""
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
        assert written["compile"]["include_dirs"] == [
            str((workspace / "include").resolve())
        ]

    def test_single_sided_compare_mode_rebases_compile_include_dirs_from_checkout_root(
        self, tmp_path: Path
    ) -> None:
        """P1 finding (Codex review, fresh evidence, PR #1222 eleventh
        round): the `mode: compare` single-sided sibling of the pairwise
        test above -- now that `compile:` is NEVER promoted/merged from
        the sources root for this shape either (see this class's own
        docstring), a checkout-root document's own `compile.include_dirs`
        must rebase against the CHECKOUT root exactly like the pairwise
        case, and the sources-root document's own conflicting
        `include_dirs` entry must be ignored entirely (not appended) --
        the previous round's own test here asserted the opposite (a
        two-document merge anchored partly against the sources root),
        which was the double-fold bug itself."""
        workspace = make_workspace(tmp_path)
        (workspace / ".abicheck.yml").write_text(
            "compile:\n  include_dirs: [checkout_only]\n", encoding="utf-8"
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
                "SOURCES_MERGE_COMPILE": "true",
            },
        )
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        assert written["compile"]["include_dirs"] == [
            str((workspace / "checkout_only").resolve())
        ]

    def test_single_sided_scan_mode_still_replaces_compile_include_dirs(
        self, tmp_path: Path
    ) -> None:
        """The REPLACE-mode sibling of the test above: with
        ``SOURCES_MERGE_COMPILE`` left at its default (empty, modeling
        ``mode: scan``), a checkout document's own conflicting
        ``compile.include_dirs`` is DISCARDED entirely -- ``scan``
        genuinely selects ``compile:`` from exactly ONE document (its
        ``--sources`` tree's own, when no explicit ``--build-config`` is
        given), the same single-document-exclusive shape ``build:``/
        ``sources:``/``source:``/``debug:`` already use. Unaffected by
        the ``mode: compare`` fix above -- ``dump``/``scan`` still
        genuinely need this single-document selection."""
        workspace = make_workspace(tmp_path)
        (workspace / ".abicheck.yml").write_text(
            "compile:\n  include_dirs: [checkout_only]\n", encoding="utf-8"
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
        assert written["compile"]["include_dirs"] == [
            str((src_dir / "include").resolve())
        ]

    def test_single_sided_compare_mode_leaves_checkout_only_compile_keys_untouched(
        self, tmp_path: Path
    ) -> None:
        """P1 finding (Codex review, fresh evidence, PR #1222 eleventh
        round): the `mode: compare` single-sided sibling of the scan-mode
        REPLACE test above, proving the two really differ in the OPPOSITE
        direction from before -- when BOTH the checkout root and the
        sources root carry their own ``compile:``, `mode: compare`'s own
        overlay must carry ONLY the checkout document's own keys, with the
        sources-root document's own ``compile.include_dirs`` never
        appearing at all (not merged in, not replacing). A previous round
        (fourth) merged the two per-field here, believing this step needed
        to reproduce the real pipeline's SECOND merge stage itself; the CLI
        already performs that second stage, unconditionally, inside
        ``_embed_inline_source_side`` when "Run analysis" actually runs --
        this step supplies only the checkout-side FIRST stage now."""
        workspace = make_workspace(tmp_path)
        (workspace / ".abicheck.yml").write_text(
            "compile:\n  std: c++20\n", encoding="utf-8"
        )
        src_dir = workspace / "src"
        src_dir.mkdir()
        (src_dir / ".abicheck.yml").write_text(
            "compile:\n  include_dirs: [foo]\n", encoding="utf-8"
        )
        result = _run_overlay(
            workspace,
            {
                "BASE_CONFIG": "",
                "SOURCES_ROOT": str(src_dir),
                "SOURCES_PAIRWISE": "",
                "SOURCES_MERGE_COMPILE": "true",
            },
        )
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        assert written["compile"] == {"std": "c++20"}

    def test_single_sided_compare_mode_ignores_sources_root_nostdinc(
        self, tmp_path: Path
    ) -> None:
        """P1 finding (Codex review, fresh evidence, PR #1222 eleventh
        round): a sources-root ``compile.nostdinc`` setting must have NO
        effect at all on `mode: compare`'s own overlay any more -- the
        checkout's own ``nostdinc: true`` survives completely unchanged,
        and the sources-root document's conflicting ``nostdinc: false`` is
        never consulted, let alone OR-merged with it (the fourth/fifth
        round's own per-field merge behavior, now removed for this shape)."""
        workspace = make_workspace(tmp_path)
        (workspace / ".abicheck.yml").write_text(
            "compile:\n  nostdinc: true\n", encoding="utf-8"
        )
        src_dir = workspace / "src"
        src_dir.mkdir()
        (src_dir / ".abicheck.yml").write_text(
            "compile:\n  nostdinc: false\n", encoding="utf-8"
        )
        result = _run_overlay(
            workspace,
            {
                "BASE_CONFIG": "",
                "SOURCES_ROOT": str(src_dir),
                "SOURCES_PAIRWISE": "",
                "SOURCES_MERGE_COMPILE": "true",
            },
        )
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        assert written["compile"] == {"nostdinc": True}

    def test_single_sided_compare_mode_ignores_sources_root_frontend(
        self, tmp_path: Path
    ) -> None:
        """Companion to the ``nostdinc`` test above for ``compile.
        frontend``: even the ``"auto"`` sentinel case (which the old
        per-field merge treated specially, letting a concrete sources-root
        value win) must now leave the checkout's own ``"auto"`` completely
        untouched -- the sources-root document's ``compile.frontend:
        clang`` is never consulted at all for this shape any more."""
        workspace = make_workspace(tmp_path)
        (workspace / ".abicheck.yml").write_text(
            "compile:\n  frontend: auto\n", encoding="utf-8"
        )
        src_dir = workspace / "src"
        src_dir.mkdir()
        (src_dir / ".abicheck.yml").write_text(
            "compile:\n  frontend: clang\n", encoding="utf-8"
        )
        result = _run_overlay(
            workspace,
            {
                "BASE_CONFIG": "",
                "SOURCES_ROOT": str(src_dir),
                "SOURCES_PAIRWISE": "",
                "SOURCES_MERGE_COMPILE": "true",
            },
        )
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        assert written["compile"] == {"frontend": "auto"}

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


class TestAssuranceOverlayClearsSourcesRootBlocksWhenNoSourcesConfigExists:
    """P1 finding (Codex review, fresh evidence, PR #1222 ninth round): a
    ``--sources`` tree with NO ``.abicheck.yml`` ANYWHERE in it at all
    (``discover_build_config`` returns ``None``) previously left the
    checkout-root document's own ``build:``/``sources:``/``compile:``/
    ``source:``(singular)/``debug:`` (whichever this run's own
    ``_sources_root_blocks`` names as sources-root-exclusive) untouched in
    the generated overlay -- forwarding it as an explicit ``--config`` the
    real, non-overlay ``--sources <dir>`` invocation would never have
    applied. ``embed_build_source()``'s own ``cfg_path = build_config or
    discover_build_config(raw_sources)`` resolves to ``None`` either way
    (an existing-but-empty sources-root document, ALREADY covered by
    ``TestAssuranceOverlayPromotesSourcesRootBuildAndSourcesBlocks``'s own
    ``test_empty_sources_root_config_clears_the_checkout_root_blocks`` in
    the sibling module, or no document at all), so every sources-root-
    exclusive block must clear to pure defaults in both cases -- merely
    enabling ``analysis.assurance: complete`` (this Action's own
    ``analysis-assurance-complete`` input) must never change the collected
    evidence and findings purely as a side effect of this overlay-
    generation step. Split into this sibling module (rather than added to
    ``tests/test_reusable_workflows_require_complete_analysis.py``, a
    ``debt.yaml`` test-file ``no_growth``-tracked module already at its
    line-count cap) for the same reason this module itself was split out --
    see this module's own top docstring. See the identical fix (and test)
    at ``action/run.sh``'s own equivalent
    ``_merge_config_overlay_with_discovered_project_config`` call site in
    ``tests/test_action_compile_context_parity.py::
    TestCompileContextDiscoversSourcesRootOwnConfig::
    test_no_sources_root_config_clears_to_defaults``."""

    def test_default_single_sided_mode_clears_build_and_sources_blocks(
        self, tmp_path: Path
    ) -> None:
        """kind: target, baseline-channel: none (mode: scan, the "audit"
        path -- ``SOURCES_PAIRWISE``/``SOURCES_MERGE_COMPILE`` both unset,
        the default env this module's ``_run_overlay`` helper supplies) is
        the single-document-exclusive shape: ``build:``/``sources:``/
        ``compile:``/``source:``/``debug:`` are ALL sources-root-exclusive
        here, so all five must clear to defaults when the ``--sources``
        tree has no config of its own."""
        workspace = make_workspace(tmp_path)
        (workspace / ".abicheck.yml").write_text(
            "severity:\n  preset: strict\n"
            "build:\n  system: bazel\n"
            "sources:\n  graph: summary\n"
            "compile:\n  std: c++20\n"
            "source:\n  method: headers\n"
            "debug:\n  format: btf\n",
            encoding="utf-8",
        )
        src_dir = workspace / "src"
        src_dir.mkdir()
        # Deliberately no `.abicheck.yml` at all under src_dir.
        result = _run_overlay(
            workspace, {"BASE_CONFIG": "", "SOURCES_ROOT": str(src_dir)}
        )
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        assert "build" not in written
        assert "sources" not in written
        assert "compile" not in written
        assert "source" not in written
        assert "debug" not in written
        # Every OTHER checkout-root setting survives untouched.
        assert written["severity"] == {"preset": "strict"}

    def test_single_sided_compare_mode_clears_build_sources_only(
        self, tmp_path: Path
    ) -> None:
        """`mode: compare`'s single-sided shape (``SOURCES_MERGE_COMPILE:
        true``) has a NARROWER sources-root-exclusive set than the default
        above -- ``build:``/``sources:`` still clear to pure defaults, but
        `compile:` stays untouched at the checkout's own value (an absent
        sources-root document is a genuine no-op fold onto it, per
        ``_merge_compile_block``'s documented empty-fold rule -- not a
        clearing one), and `source:`/`debug:` are never in this mode's own
        ``_sources_root_blocks`` at all, with or without a discovered
        sources-root document."""
        workspace = make_workspace(tmp_path)
        (workspace / ".abicheck.yml").write_text(
            "build:\n  system: bazel\n"
            "sources:\n  graph: summary\n"
            "compile:\n  std: c++20\n"
            "source:\n  method: headers\n"
            "debug:\n  format: btf\n",
            encoding="utf-8",
        )
        src_dir = workspace / "src"
        src_dir.mkdir()
        # Deliberately no `.abicheck.yml` at all under src_dir.
        result = _run_overlay(
            workspace,
            {
                "BASE_CONFIG": "",
                "SOURCES_ROOT": str(src_dir),
                "SOURCES_PAIRWISE": "",
                "SOURCES_MERGE_COMPILE": "true",
            },
        )
        assert result.returncode == 0, result.stderr
        written = _written_overlay(result)
        assert "build" not in written
        assert "sources" not in written
        assert written["compile"] == {"std": "c++20"}
        assert written["source"] == {"method": "headers"}
        assert written["debug"] == {"format": "btf"}


class TestAssuranceOverlayEscapesWorkflowCommandInjection:
    """P1 finding (Codex review, fresh evidence, PR #1222 tenth round):
    every ``::error::``/``::warning::`` this step's own "Generate
    assurance-overlay config" Python heredoc emits interpolated an
    untrusted path (``ABICHECK_BASE_CONFIG``/``ABICHECK_SOURCES_ROOT`` --
    fully controlled by a direct ``check-target`` caller, and a filesystem
    path may legally contain a raw newline byte on Linux) or exception
    text (a PyYAML parser error, which echoes attacker-controlled
    config-file bytes verbatim) with no escaping at all. GitHub's runner
    parses workflow commands line-by-line from stdout/stderr, so an
    embedded literal newline lets the remainder of the value start a
    *second*, attacker-authored command (e.g. a smuggled
    ``::add-mask::...``) rather than being rendered as part of the single
    intended annotation. Fixed by adding a ``_gha_escape`` helper to this
    step's own heredoc, mirroring ``action/run.sh``'s own identical
    helper (``%``->``%25``, CR->``%0D``, LF->``%0A``) rather than
    inventing a new escaping scheme, and applying it to every
    interpolated path/exception in this step's error-emission call
    sites."""

    def test_newline_in_explicit_build_config_path_does_not_smuggle_a_command(
        self, tmp_path: Path
    ) -> None:
        """An explicit ``build-config`` naming a schema-invalid file whose
        own path contains a literal newline must not let that newline
        start a second workflow command line -- the whole error stays on
        one logical ``::error::`` line, with the embedded newline rendered
        as the literal ``%0A`` escape sequence instead."""
        workspace = make_workspace(tmp_path)
        evil_dir = workspace / "evil\n::add-mask::pwned"
        evil_dir.mkdir()
        build_config = evil_dir / "my-build-config.yml"
        build_config.write_text("build:\n  query: 7\n", encoding="utf-8")
        result = _run_overlay(workspace, {"BASE_CONFIG": str(build_config)})
        assert result.returncode != 0
        assert "::error::" in result.stderr
        # The raw newline the malicious path contained must never reach
        # stderr unescaped -- every line but the first must NOT itself
        # start a new `::`-prefixed workflow command (a real second
        # `::error::`/`::add-mask::` line would mean the injection landed).
        lines = result.stderr.splitlines()
        assert not any(line.startswith("::") for line in lines[1:])
        assert "%0A" in result.stderr

    def test_newline_in_sources_root_path_does_not_smuggle_a_command(
        self, tmp_path: Path
    ) -> None:
        """The sources-root parse-error path interpolates the discovered
        sources-root config's own path and the PyYAML exception text --
        both must be escaped too, exercised here via a sources-root
        directory whose name embeds a literal newline and an unparsable
        ``.abicheck.yml``."""
        workspace = make_workspace(tmp_path)
        src_dir = workspace / "src\n::add-mask::pwned"
        src_dir.mkdir()
        (src_dir / ".abicheck.yml").write_text("build: [unterminated", encoding="utf-8")
        result = _run_overlay(
            workspace, {"BASE_CONFIG": "", "SOURCES_ROOT": str(src_dir)}
        )
        assert result.returncode != 0
        assert "::error::" in result.stderr
        lines = result.stderr.splitlines()
        assert not any(line.startswith("::") for line in lines[1:])
        assert "%0A" in result.stderr


class TestAssuranceOverlayDoesNotDoubleFoldRepeatSensitiveCompileFlags:
    """P1 finding (Codex review, fresh evidence, PR #1222 eleventh round):
    ``actions/check-target/action.yml``'s "Generate assurance-overlay
    config" step, for a stored-baseline ``mode: compare`` run
    (``analysis-assurance-complete: true``), used to fold a sources-root
    document's ``compile:`` block into the synthesized overlay as a
    genuine per-field MERGE onto the checkout document's own ``compile:``
    -- believing it needed to reproduce the real pipeline's OWN two-stage
    ``compile:`` merge itself, because forwarding the overlay as an
    explicit ``--config`` would otherwise suppress the second stage.

    It doesn't: ``frontends/cli/commands/compare.py``'s
    ``_embed_inline_source_side`` performs that second stage
    UNCONDITIONALLY whenever ``--new-sources`` names a raw tree, via its
    own ``cli_options.merge_compile_config`` call, regardless of whether
    ``--config``/``build-config`` was explicit. So the old overlay-level
    merge folded the SAME sources-root document's ``compile:`` block in
    TWICE -- once via the overlay's own promotion, once again inside
    ``_embed_inline_source_side``'s always-running merge -- and a
    repeat-sensitive raw compiler flag like ``-include shim.h``
    (``compile.options``) ended up applied TWICE in the final resolved
    compiler invocation purely because ``analysis.assurance: complete``
    was enabled.

    These tests exercise the REAL glue end to end rather than asserting
    the overlay's own YAML text in isolation (the "assert behavior via
    the real pipeline, not structural text" principle root ``AGENTS.md``
    states): first the step's real Python body generates the overlay
    (exactly as ``TestAssuranceOverlayPromotesSourcesRootCompileSourceDebugBlocks``
    above does), then the REAL ``cli_options.merge_compile_config`` --
    the exact function both ``resolve_compile_context`` (stage 1, the
    checkout side) and ``_embed_inline_source_side`` (stage 2, the
    sources-root side) call -- is invoked twice against that generated
    overlay and the sources-root tree, in the identical shape ``compare``'s
    own real dispatch invokes it, so a regression reintroducing the
    double-fold at either layer would reproduce the doubled flag here."""

    def test_minus_include_flag_applies_exactly_once_through_the_real_two_stage_merge(
        self, tmp_path: Path
    ) -> None:
        workspace = make_workspace(tmp_path)
        # Deliberately no checkout-root .abicheck.yml at all -- the
        # repeat-sensitive flag lives ONLY in the sources-root document,
        # the exact shape that manufactures a duplicate out of thin air if
        # the overlay generator (wrongly) also promotes it.
        src_dir = workspace / "src"
        src_dir.mkdir()
        (src_dir / ".abicheck.yml").write_text(
            "compile:\n  options: ['-include', 'shim.h']\n", encoding="utf-8"
        )

        # Stage 0: the real "Generate assurance-overlay config" step, for
        # the exact env shape a stored-baseline `mode: compare` run
        # produces (SOURCES_MERGE_COMPILE: true, single-sided).
        result = _run_overlay(
            workspace,
            {
                "BASE_CONFIG": "",
                "SOURCES_ROOT": str(src_dir),
                "SOURCES_PAIRWISE": "",
                "SOURCES_MERGE_COMPILE": "true",
            },
        )
        assert result.returncode == 0, result.stderr
        overlay_config = Path(result.outputs["config-path"])
        written = _written_overlay(result)
        # The fix itself, restated as a precondition: the generated
        # overlay must not already carry the sources-root's own
        # -include/shim.h -- if it did, the assertion below would pass
        # for the wrong reason (a doubled flag hiding behind a stage-1
        # value that already contains it once).
        assert "compile" not in written or "-include" not in written.get(
            "compile", {}
        ).get("options", [])

        # Stage 1: what `compare`'s own `resolve_compile_context` does --
        # fold the (now checkout-only) overlay config into a fresh
        # CompileContext, with no --sources tree involved yet.
        stage1_ctx, stage1_includes = merge_compile_config(
            CompileContext(),
            (),
            overlay_config,
            sources=None,
        )
        # Stage 2: what `_embed_inline_source_side` does for the live NEW
        # side -- fold the SAME sources-root tree's own document on top of
        # the already-resolved stage-1 context, unconditionally.
        stage2_ctx, _stage2_includes = merge_compile_config(
            stage1_ctx,
            stage1_includes,
            None,
            sources=src_dir,
        )

        assert stage2_ctx.gcc_option_tokens.count("-include") == 1
        assert stage2_ctx.gcc_option_tokens.count("shim.h") == 1
        include_idx = stage2_ctx.gcc_option_tokens.index("-include")
        assert stage2_ctx.gcc_option_tokens[include_idx + 1] == "shim.h"

    def test_minus_include_flag_from_checkout_also_applies_exactly_once(
        self, tmp_path: Path
    ) -> None:
        """Companion case: the repeat-sensitive flag lives in the CHECKOUT
        document instead. The fix leaves it there untouched (never
        promoted from the sources root, which sets nothing conflicting
        here), and stage 2's own sources-root fold has nothing of its own
        to contribute -- so it must still apply exactly once, sourced
        entirely from stage 1."""
        workspace = make_workspace(tmp_path)
        (workspace / ".abicheck.yml").write_text(
            "compile:\n  options: ['-include', 'shim.h']\n", encoding="utf-8"
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
                "SOURCES_PAIRWISE": "",
                "SOURCES_MERGE_COMPILE": "true",
            },
        )
        assert result.returncode == 0, result.stderr
        overlay_config = Path(result.outputs["config-path"])

        stage1_ctx, stage1_includes = merge_compile_config(
            CompileContext(),
            (),
            overlay_config,
            sources=None,
        )
        stage2_ctx, _stage2_includes = merge_compile_config(
            stage1_ctx,
            stage1_includes,
            None,
            sources=src_dir,
        )

        assert stage2_ctx.gcc_option_tokens.count("-include") == 1
        assert stage2_ctx.gcc_option_tokens.count("shim.h") == 1
