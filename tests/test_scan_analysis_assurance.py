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

"""``scan --against --require-complete-analysis``: P0.4's analysis-assurance
axis (``abicheck/analysis_assurance.py``), on the ``scan`` command.

A sibling of ``tests/test_analysis_assurance.py`` (which owns the model,
``compare``-CLI, and fold-level tests for this axis) rather than a class
inside it: that file sits at the AGENTS.md 2000-line hard cap, and splitting
by front end (``compare`` vs. ``scan``) rather than by axis internals keeps
each file's own fixtures self-contained.

Mirrors ``scan --against`` mirroring ``compare``'s own P0.4 gate exactly
(same fold, same flag), on the shared ``_run_baseline_compare``/
``run_scan_core`` engine rather than a second implementation. Before this,
``scan`` neither accepted the flag nor emitted ``analysis_assurance`` in its
JSON summary at all -- a run whose evidence was genuinely incomplete gated
on `compare` and passed on `scan` for the identical pair, even with the
flag given (the flag did not exist), and even for a *reader* checking the
report (the field did not exist).
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from abicheck.analysis_assurance import (
    ANALYSIS_ASSURANCE_SCHEMA_VERSION,
    ASSURANCE_STATUS_VALUES,
)
from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import snapshot_to_json


def _fn(name: str, mangled: str) -> Function:
    return Function(
        name=name, mangled=mangled, return_type="int", visibility=Visibility.PUBLIC
    )


def _write(tmp_path: Path, old: AbiSnapshot, new: AbiSnapshot) -> tuple[Path, Path]:
    old_p = tmp_path / "old.json"
    new_p = tmp_path / "new.json"
    old_p.write_text(snapshot_to_json(old), encoding="utf-8")
    new_p.write_text(snapshot_to_json(new), encoding="utf-8")
    return old_p, new_p


def _header_pair() -> tuple[AbiSnapshot, AbiSnapshot]:
    """A real, header-scoped, unchanged pair -- resolves a clean public
    surface, so ``status`` should read ``"complete"`` with no gating flag
    involved. Mirrors ``test_analysis_assurance.py``'s own fixture.
    """
    common = {"library": "libfoo.so.1", "from_headers": True}
    fns = [_fn("pub_a", "_Z5pub_av")]
    return (
        AbiSnapshot(version="1.0", functions=fns, **common),
        AbiSnapshot(version="2.0", functions=fns, **common),
    )


def _elf_only_pair() -> tuple[AbiSnapshot, AbiSnapshot]:
    """No headers, no build evidence -- the minimal invocation shape, whose
    ``status`` is never ``"complete"``. Mirrors
    ``test_analysis_assurance.py``'s own fixture.
    """
    fns = [_fn("pub_a", "_Z5pub_av")]
    return (
        AbiSnapshot(version="1.0", library="libfoo.so.1", functions=fns, elf_only_mode=True),
        AbiSnapshot(version="2.0", library="libfoo.so.1", functions=fns, elf_only_mode=True),
    )


def _breaking_pair() -> tuple[AbiSnapshot, AbiSnapshot]:
    """Mirrors ``test_analysis_assurance.py``'s own fixture."""
    common = {"library": "libfoo.so.1", "from_headers": True}
    return (
        AbiSnapshot(
            version="1.0",
            functions=[_fn("pub_a", "_Z5pub_av"), _fn("pub_b", "_Z5pub_bv")],
            **common,
        ),
        AbiSnapshot(version="2.0", functions=[_fn("pub_a", "_Z5pub_av")], **common),
    )


def _scan(tmp_path: Path, pair: tuple[AbiSnapshot, AbiSnapshot], *extra: str):
    # *pair* is (old, new). ``scan``'s own operand order is the opposite of
    # ``compare``'s OLD NEW positionals: ARTIFACT is the candidate (new) and
    # ``--against`` is the baseline (old).
    old_p, new_p = _write(tmp_path, *pair)
    return CliRunner().invoke(
        main, ["scan", str(new_p), "--against", str(old_p), *extra]
    )


class TestScanRequireCompleteAnalysisCliIntegration:
    def test_json_report_always_carries_analysis_assurance(
        self, tmp_path: Path
    ) -> None:
        res = _scan(tmp_path, _header_pair(), "--format", "json")
        assert res.exit_code == 0, res.output
        payload = json.loads(res.output[res.output.index("{") :])
        # scan's per-target report nests the baseline-comparison summary
        # (built by ``_baseline_summary``) under ``diff`` -- unlike
        # ``compare``'s top-level report, which is ``_baseline_summary``'s
        # counterpart at the root.
        aa = payload["diff"]["analysis_assurance"]
        assert aa["schema_version"] == ANALYSIS_ASSURANCE_SCHEMA_VERSION
        assert aa["status"] in ASSURANCE_STATUS_VALUES

    def test_complete_run_is_unaffected_by_the_flag(self, tmp_path: Path) -> None:
        res = _scan(tmp_path, _header_pair(), "--require-complete-analysis")
        assert res.exit_code == 0, res.output

    def test_flag_omitted_never_changes_the_exit_code(self, tmp_path: Path) -> None:
        res = _scan(tmp_path, _elf_only_pair())
        assert res.exit_code == 0, res.output
        payload_res = _scan(tmp_path, _elf_only_pair(), "--format", "json")
        payload = json.loads(payload_res.output[payload_res.output.index("{") :])
        # Sanity: this fixture really is a non-"complete" case, so the next
        # assertion (flag raises it to 1) is actually testing something.
        assert payload["diff"]["analysis_assurance"]["status"] != "complete"

    def test_flag_raises_a_clean_exit_to_one_on_incomplete_status(
        self, tmp_path: Path
    ) -> None:
        res = _scan(tmp_path, _elf_only_pair(), "--require-complete-analysis")
        assert res.exit_code == 1, res.output

    def test_the_floor_diagnostic_is_echoed_to_stderr(self, tmp_path: Path) -> None:
        """`compare`'s own `_exit_with_severity_or_verdict` echoes
        `assurance_floor_diagnostic` to stderr unconditionally (regardless
        of `--format`) whenever the flag floors the exit code -- the
        composite Action's `_assurance_gated()` (`action/run.sh`) greps for
        exactly this line, since the JSON `analysis_assurance` block alone
        cannot tell "the flag was passed and gated" apart from "the
        evidence merely happens to be partial" (Codex review, PR #780).
        `scan` must echo the identical diagnostic, not just fold the exit
        code silently."""
        res = _scan(tmp_path, _elf_only_pair(), "--require-complete-analysis")
        assert res.exit_code == 1, res.output
        assert "Analysis assurance incomplete" in res.output, res.output
        assert "--require-complete-analysis" in res.output, res.output

    def test_flag_never_lowers_a_real_abi_break_end_to_end(
        self, tmp_path: Path
    ) -> None:
        res = _scan(tmp_path, _breaking_pair(), "--require-complete-analysis")
        assert res.exit_code == 4, res.output

    def test_an_unreached_pinned_source_depth_is_reported_incomplete(
        self, tmp_path: Path
    ) -> None:
        """Codex review, PR #780: `checker.compare()`'s own internal
        `compute_analysis_assurance` call runs *inside* `compare_snapshots`,
        before `_run_baseline_compare` ever sees the returned `diff` -- so
        it always read `DiffResult.requested_depth` as `None`, regardless
        of what this scan was actually pinned to. An explicit `--depth
        source --sources <tree>` whose tree has no compile database only
        gets an *advisory* (`_check_scan_evidence_contract`'s
        "gave_source_input and not l3" branch -- not a hard failure, since
        a source input genuinely was supplied), and the effective depth
        silently stayed `headers`. Without stamping/recomputing
        `requested_depth` after the fact, that silently read
        `status="complete"` (nothing to compare the unset `requested_depth`
        against) even though the requested depth was demonstrably not
        reached -- defeating `--require-complete-analysis` for exactly the
        invocation shape it exists to catch."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "empty.txt").write_text("nothing here\n", encoding="utf-8")

        res = _scan(
            tmp_path,
            _header_pair(),
            "--depth", "source", "--sources", str(src_dir),
            "--require-complete-analysis", "--format", "json",
        )
        assert res.exit_code == 1, res.output
        payload = json.loads(res.output[res.output.index("{") :])
        aa = payload["diff"]["analysis_assurance"]
        assert aa["status"] != "complete", aa
        assert aa["requested_depth"] == "source", aa
        assert aa["effective_depth"] == "headers", aa
