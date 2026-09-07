# SPDX-License-Identifier: Apache-2.0
"""Behavioral half of the (now closed) pattern_scan gap (plan §3 #6, Phase 2b).

``test_engine_primitive_call_sites.py`` proves ``scan_files`` has exactly
two production callers now: ``scan_engine.py`` and
``workflows/pattern_preprocessor_scan.py``. This module proves the positive
side concretely and cheaply -- ``scan_files`` is pure lexical text scanning
(no compiler, no castxml) and really does find an ABI-risk construct scan
surfaces today, over a tiny fixture file, and that the same construct now
also reaches ``compare()``'s own JSON report.
"""

from __future__ import annotations

from pathlib import Path

from abicheck.buildsource.pattern_scan import scan_files

from .gaps import EXPECTED_GAPS
from .runner import compare_json, invoke_cli


def test_scan_files_finds_explicit_template_instantiation(tmp_path: Path) -> None:
    header = tmp_path / "risky.hpp"
    header.write_text("template class Widget<int>;\n", encoding="utf-8")

    result = scan_files([tmp_path])
    kinds = {f.kind.value for f in result.facts}
    assert "explicit_template_instantiation" in kinds
    assert result.files_scanned == 1


def test_pattern_scan_no_longer_a_registered_gap() -> None:
    """`gaps.py` is the migration's definition of done: a capability
    `compare` has reached must not still be listed as scan-only."""
    assert "pattern_scan" not in EXPECTED_GAPS


def test_compare_has_no_pattern_scan_cli_surface() -> None:
    """ADR-068 D4/D5: the stage is automatic, evidence-gated per side --
    never a CLI opt-in flag, even now that `compare` reaches it."""
    result = invoke_cli("compare", "--help-all")
    assert result.exit_code == 0
    assert "pattern-scan" not in result.output.lower()
    assert "pattern_scan" not in result.output.lower()


def test_compare_surfaces_a_pattern_scan_escalation_introduced_in_new(
    tmp_path: Path,
) -> None:
    """The construct `scan_files` finds above now also reaches `compare`'s
    own report -- present only on NEW's header, so it reads `introduced`."""
    from abicheck.model import AbiSnapshot, Function

    old_header = tmp_path / "old.hpp"
    old_header.write_text("int widget_get(void);\n", encoding="utf-8")
    new_header = tmp_path / "new.hpp"
    new_header.write_text("template class Widget<int>;\n", encoding="utf-8")

    old = AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        functions=[
            Function(
                name="widget_get",
                mangled="widget_get",
                return_type="int",
                source_header=str(old_header),
            )
        ],
    )
    new = AbiSnapshot(
        library="libfoo.so",
        version="2.0",
        functions=[
            Function(
                name="widget_get",
                mangled="widget_get",
                return_type="int",
                source_header=str(new_header),
            )
        ],
    )

    from abicheck.serialization import snapshot_to_json

    old_path = tmp_path / "old.abi.json"
    new_path = tmp_path / "new.abi.json"
    old_path.write_text(snapshot_to_json(old), encoding="utf-8")
    new_path.write_text(snapshot_to_json(new), encoding="utf-8")

    report = compare_json(old_path, new_path)
    block = report["pattern_preprocessor_scan"]
    assert (
        block["pattern"]["escalation_evolution"].get("explicit_template_instantiation")
        == "introduced"
    )
