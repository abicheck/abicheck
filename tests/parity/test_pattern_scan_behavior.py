# SPDX-License-Identifier: Apache-2.0
"""Behavioral half of the pattern_scan gap (plan §3 #6, F-row context).

``test_engine_primitive_call_sites.py`` proves ``scan_files`` structurally
has no caller outside ``scan_engine.py``. This module proves the positive
side concretely and cheaply -- ``scan_files`` is pure lexical text
scanning (no compiler, no castxml) and really does find an ABI-risk
construct scan surfaces today, over a tiny fixture file.
"""

from __future__ import annotations

from pathlib import Path

from abicheck.buildsource.pattern_scan import scan_files

from .gaps import EXPECTED_GAPS
from .runner import invoke_cli


def test_scan_files_finds_explicit_template_instantiation(tmp_path: Path) -> None:
    assert "pattern_scan" in EXPECTED_GAPS

    header = tmp_path / "risky.hpp"
    header.write_text("template class Widget<int>;\n", encoding="utf-8")

    result = scan_files([tmp_path])
    kinds = {f.kind.value for f in result.facts}
    assert "explicit_template_instantiation" in kinds
    assert result.files_scanned == 1


def test_compare_has_no_pattern_scan_surface(tmp_path: Path) -> None:
    """`compare` accepts `--sources`, but (per the call-site proof) never
    routes it through `scan_files` -- there is no flag or report field
    that surfaces a lexical-pattern finding on `compare` at all."""
    result = invoke_cli("compare", "--help-all")
    assert result.exit_code == 0
    # None of pattern_scan's own vocabulary appears anywhere in compare's
    # option surface (no flag *named* for it, unlike --pattern-verdicts
    # which modulates verdicts using pre-existing, unrelated risk facts).
    assert "pattern-scan" not in result.output.lower()
    assert "pattern_scan" not in result.output.lower()
