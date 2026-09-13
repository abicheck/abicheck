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

"""Direct unit coverage for ``compare --dry-run``'s "Cost preview" leaf
modules (one-comparison-product.md #35, Phase 2f): the compute half
(``workflows/compare_cost_preview.py``) and the render half
(``frontends/cli/compare_dry_run.py``). ``tests/test_dry_run_contract.py``
covers the end-to-end CLI path; this file exercises the branches that path
doesn't reach on its own (each precedence rung of the level resolver, and
the probe-failure path both modules share)."""

from __future__ import annotations

from pathlib import Path

from abicheck.dry_run import DryRunResult
from abicheck.frontends.cli.compare_dry_run import add_compare_cost_preview_section
from abicheck.model.evidence_depth_levels import EvidenceDepth, SourceMethod
from abicheck.workflows.compare_cost_preview import (
    _resolve_compare_estimate_level,
    estimate_compare_dry_run_cost,
)


class TestResolveCompareEstimateLevel:
    def test_explicit_depth_wins_over_everything(self, tmp_path: Path) -> None:
        assert _resolve_compare_estimate_level(
            "build",
            "s5",
            tmp_path,
            tmp_path,
            None,
            None,
        ) == (SourceMethod.S1, EvidenceDepth.BUILD)

    def test_explicit_depth_binary_has_no_source_method(self) -> None:
        assert _resolve_compare_estimate_level(
            "binary",
            None,
            None,
            None,
            None,
            None,
        ) == (SourceMethod.S0, EvidenceDepth.BINARY)

    def test_source_method_config_wins_over_sources_inference(
        self, tmp_path: Path
    ) -> None:
        assert _resolve_compare_estimate_level(
            None,
            "s1",
            tmp_path,
            None,
            None,
            None,
        ) == (SourceMethod.S1, EvidenceDepth.BUILD)

    def test_source_method_auto_prices_the_headers_only_floor(self) -> None:
        # compare has no PR change seed for `auto` to score a risk-driven
        # escalation against (unlike `scan --mode auto`).
        assert _resolve_compare_estimate_level(
            None,
            "auto",
            None,
            None,
            None,
            None,
        ) == (SourceMethod.S0, EvidenceDepth.HEADERS)

    def test_sources_given_infers_source_depth(self, tmp_path: Path) -> None:
        assert _resolve_compare_estimate_level(
            None,
            None,
            tmp_path,
            None,
            None,
            None,
        ) == (SourceMethod.S5, EvidenceDepth.SOURCE)

    def test_build_info_given_infers_build_depth(self, tmp_path: Path) -> None:
        assert _resolve_compare_estimate_level(
            None,
            None,
            None,
            None,
            tmp_path,
            None,
        ) == (SourceMethod.S1, EvidenceDepth.BUILD)

    def test_nothing_given_prices_the_headers_only_floor(self) -> None:
        assert _resolve_compare_estimate_level(
            None,
            None,
            None,
            None,
            None,
            None,
        ) == (SourceMethod.S0, EvidenceDepth.HEADERS)


class TestEstimateCompareDryRunCost:
    def test_success_sums_both_sides(self, tmp_path: Path) -> None:
        old = tmp_path / "old.so"
        new = tmp_path / "new.so"
        old.write_bytes(b"\x7fELF" + b"\x00" * 60)
        new.write_bytes(b"\x7fELF" + b"\x00" * 60)
        estimates, error = estimate_compare_dry_run_cost(
            old_input=old,
            new_input=new,
            depth="binary",
            source_method=None,
            headers=(),
            includes=(),
            old_headers_only=(),
            new_headers_only=(),
            old_sources=None,
            new_sources=None,
            old_build_info=None,
            new_build_info=None,
        )
        assert error is None
        assert estimates is not None
        assert estimates
        # L0_binary prices `len(binaries)` — one per side, summed to 2.
        l0 = next(e for e in estimates if e.layer == "L0_binary")
        assert l0.tus == 2

    def test_probe_failure_is_reported_not_raised(self, tmp_path: Path) -> None:
        # A header path that doesn't exist makes estimate_scan's own
        # `expand_header_inputs` raise -- the dry run must degrade to an
        # error string, never propagate the exception (mirrors cli_scan.py's
        # own best-effort `estimate_scan` call under --dry-run).
        old = tmp_path / "old.so"
        new = tmp_path / "new.so"
        old.write_bytes(b"\x7fELF" + b"\x00" * 60)
        new.write_bytes(b"\x7fELF" + b"\x00" * 60)
        missing_header = tmp_path / "does-not-exist.h"
        estimates, error = estimate_compare_dry_run_cost(
            old_input=old,
            new_input=new,
            depth="headers",
            source_method=None,
            headers=(missing_header,),
            includes=(),
            old_headers_only=(),
            new_headers_only=(),
            old_sources=None,
            new_sources=None,
            old_build_info=None,
            new_build_info=None,
        )
        assert estimates is None
        assert error is not None
        assert "does-not-exist.h" in error


class TestAddCompareCostPreviewSection:
    def test_warns_on_error_and_adds_no_section(self) -> None:
        result = DryRunResult(command="compare")
        add_compare_cost_preview_section(result, None, "boom")
        assert "Cost preview" not in result.sections
        assert any("boom" in w for w in result.warnings)

    def test_adds_section_on_success(self, tmp_path: Path) -> None:
        old = tmp_path / "old.so"
        new = tmp_path / "new.so"
        old.write_bytes(b"\x7fELF" + b"\x00" * 60)
        new.write_bytes(b"\x7fELF" + b"\x00" * 60)
        estimates, error = estimate_compare_dry_run_cost(
            old_input=old,
            new_input=new,
            depth="binary",
            source_method=None,
            headers=(),
            includes=(),
            old_headers_only=(),
            new_headers_only=(),
            old_sources=None,
            new_sources=None,
            old_build_info=None,
            new_build_info=None,
        )
        assert error is None
        result = DryRunResult(command="compare")
        add_compare_cost_preview_section(result, estimates, error)
        assert "Cost preview" in result.sections
        assert any("projected total" in ln for ln in result.sections["Cost preview"])
