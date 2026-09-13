# Copyright 2026 Nikolay Petrov
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

"""ABICC-compatible XML report generator.

Produces XML reports matching the structure expected by abi-compliance-checker
consumers (abi-tracker, lvc-monitor, Fedora/openSUSE ABI infrastructure).

Real ABICC XML schema (``-report-format xml``):

    <reports>
      <report kind="binary" version="1.2">
        <test_info>
          <library>LIBNAME</library>
          <version1><number>V1</number></version1>
          <version2><number>V2</number></version2>
        </test_info>
        <test_results>
          <verdict>compatible|incompatible</verdict>
          <affected>N.N</affected>
          <symbols>N</symbols>
        </test_results>
        <problem_summary>
          <added_symbols>N</added_symbols>
          <removed_symbols>N</removed_symbols>
          <problems_with_types>
            <high>N</high><medium>N</medium><low>N</low><safe>N</safe>
          </problems_with_types>
          <problems_with_symbols>
            <high>N</high><medium>N</medium><low>N</low><safe>N</safe>
          </problems_with_symbols>
        </problem_summary>
        <added_symbols>...</added_symbols>
        <removed_symbols>...</removed_symbols>
        <problems_with_types severity="High">...</problems_with_types>
        ...
      </report>
      <report kind="source" version="1.2">
        ...
      </report>
    </reports>

No formal DTD/XSD exists — the format is defined by the ABICC Perl source.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from ..model.change_catalog.kinds import HasKind
from ..report_classifications import (
    ADDED_KINDS,
    BINARY_ONLY_KINDS,
    REMOVED_KINDS,
    is_breaking,
    is_type_problem,
    kind_str,
    severity,
)
from ..report_summary import compatibility_metrics

if TYPE_CHECKING:
    from ..checker import DiffResult

# ABICC XML report version
_REPORT_VERSION = "1.2"

# ── Change-kind classification — delegated to report_classifications ────────


def _severity_lower(kind_s: str) -> str:
    """Map to ABICC severity tier (lowercase for XML)."""
    return severity(kind_s).lower()


def _compute_section(
    changes: list[object],
    old_symbol_count: int | None,
    *,
    exclude_binary_only: bool = False,
) -> dict[str, Any]:
    """Compute counts for one section (binary or source) of the XML report."""
    filtered = changes
    if exclude_binary_only:
        filtered = [c for c in changes if kind_str(c) not in BINARY_ONLY_KINDS]

    breaking = [c for c in filtered if is_breaking(c)]
    removed = [c for c in filtered if kind_str(c) in REMOVED_KINDS]
    added = [c for c in filtered if kind_str(c) in ADDED_KINDS]
    # "problems" = breaking changes that are not simple removals/additions
    problems = [
        c
        for c in breaking
        if kind_str(c) not in REMOVED_KINDS and kind_str(c) not in ADDED_KINDS
    ]

    # Classify problems by category and severity
    type_problems = {"high": 0, "medium": 0, "low": 0, "safe": 0}
    symbol_problems = {"high": 0, "medium": 0, "low": 0, "safe": 0}

    for c in problems:
        ks = kind_str(c)
        sev = _severity_lower(ks)
        if is_type_problem(ks):
            type_problems[sev] += 1
        else:
            symbol_problems[sev] += 1

    total_type = sum(type_problems.values())
    total_symbol = sum(symbol_problems.values())

    metrics = compatibility_metrics(cast(list[HasKind], filtered), old_symbol_count)
    breaking_count = metrics.breaking_count
    bc_pct = metrics.binary_compatibility_pct
    affected_pct = metrics.affected_pct

    verdict = "incompatible" if breaking_count > 0 else "compatible"

    return {
        "compatible_pct": f"{bc_pct:.1f}",
        "verdict": verdict,
        "affected_pct": f"{affected_pct:.1f}",
        "symbols": str(old_symbol_count or 0),
        "removed": len(removed),
        "added": len(added),
        "type_problems": type_problems,
        "symbol_problems": symbol_problems,
        "total_type_problems": total_type,
        "total_symbol_problems": total_symbol,
        "problems_total": total_type + total_symbol,
        "changes": filtered,
    }


# ── ABICC effect text templates ──────────────────────────────────────────────

_EFFECT_TEXT: dict[str, str] = {
    "type_size_changed": "Size of this type changed, which may break binary compatibility.",
    "type_alignment_changed": "Alignment of this type changed, which may affect struct layout.",
    "type_vtable_changed": "Virtual table layout changed, which breaks binary compatibility.",
    "type_base_changed": "Base class changed, which may break binary compatibility.",
    "type_field_offset_changed": "Field offset changed, which breaks binary compatibility.",
    "type_field_type_changed": "Field type changed, which may break binary compatibility.",
    "type_field_removed": "Field was removed from this type.",
    "struct_size_changed": "Size of this struct changed, which may break binary compatibility.",
    "struct_field_offset_changed": "Field offset changed, which breaks binary compatibility.",
    "struct_field_removed": "Field was removed from this struct.",
    "struct_field_type_changed": "Field type changed, which may break binary compatibility.",
    "func_return_changed": "Return type changed, which may break binary compatibility.",
    "func_params_changed": "Parameter types changed, which may break binary compatibility.",
    "func_removed": "Symbol was removed from the library.",
    "var_removed": "Global variable was removed from the library.",
    "var_type_changed": "Variable type changed, which may break binary compatibility.",
    "soname_changed": "Library SONAME changed, which breaks binary compatibility.",
    "calling_convention_changed": "Calling convention changed, which breaks binary compatibility.",
    "typedef_base_changed": "Underlying type of typedef changed.",
    "enum_member_value_changed": "Enum member value changed, which may affect compiled code.",
}


def _add_problem_element(parent: ET.Element, change: object) -> None:
    """Add a <problem> element with <change>, <effect>, and optional <overcome>."""
    ks = kind_str(change)
    prob = ET.SubElement(parent, "problem")
    prob.set("id", ks)

    change_el = ET.SubElement(prob, "change")
    old_val = str(getattr(change, "old_value", "") or "")
    new_val = str(getattr(change, "new_value", "") or "")
    if old_val:
        change_el.set("old_value", old_val)
    if new_val:
        change_el.set("new_value", new_val)
    change_el.text = getattr(change, "description", "") or ""

    # <effect> — describes the impact of this change
    effect_text = _EFFECT_TEXT.get(ks, "")
    if effect_text:
        effect_el = ET.SubElement(prob, "effect")
        effect_el.text = effect_text

    # <caused_by> — redundancy annotation
    caused_by = getattr(change, "caused_by_type", None)
    if caused_by:
        caused_el = ET.SubElement(prob, "caused_by")
        caused_el.text = str(caused_by)
    caused_count = getattr(change, "caused_count", 0)
    if caused_count > 0:
        count_el = ET.SubElement(prob, "caused_count")
        count_el.text = str(caused_count)

    # <overcome> — remediation hint for removals
    if ks in ("func_removed", "var_removed", "type_removed"):
        overcome_el = ET.SubElement(prob, "overcome")
        overcome_el.text = (
            "Recompile the client application against the new library version."
        )


def _build_version_element(
    parent: ET.Element,
    tag: str,
    version: str,
    arch: str,
    compiler: str,
) -> None:
    """Build a <version1> or <version2> sub-element with optional arch/gcc."""
    vel = ET.SubElement(parent, tag)
    num = ET.SubElement(vel, "number")
    num.text = version or ("old" if tag == "version1" else "new")
    if arch:
        a = ET.SubElement(vel, "arch")
        a.text = arch
    if compiler:
        g = ET.SubElement(vel, "gcc")
        g.text = compiler


def _build_symbol_list(
    parent: ET.Element,
    tag: str,
    changes: list[object],
    kind_set: frozenset[str],
) -> None:
    """Build <added_symbols> or <removed_symbols> detail section."""
    matched = [c for c in changes if kind_str(c) in kind_set]
    if matched:
        detail = ET.SubElement(parent, tag)
        for c in matched:
            sym = ET.SubElement(detail, "name")
            sym.text = getattr(c, "symbol", "") or ""


def _build_problem_details(parent: ET.Element, changes: list[object]) -> None:
    """Build severity-tiered <problems_with_types/symbols> detail sections."""
    problem_changes = [
        c
        for c in changes
        if is_breaking(c)
        and kind_str(c) not in REMOVED_KINDS
        and kind_str(c) not in ADDED_KINDS
    ]

    for sev_label, sev_key in [("High", "high"), ("Medium", "medium"), ("Low", "low")]:
        sev_changes = [
            c for c in problem_changes if _severity_lower(kind_str(c)) == sev_key
        ]
        if not sev_changes:
            continue

        type_changes = [c for c in sev_changes if is_type_problem(kind_str(c))]
        if type_changes:
            types_detail = ET.SubElement(parent, "problems_with_types")
            types_detail.set("severity", sev_label)
            for c in type_changes:
                type_el = ET.SubElement(types_detail, "type")
                type_el.set("name", getattr(c, "symbol", "") or "")
                _add_problem_element(type_el, c)

        sym_changes = [c for c in sev_changes if not is_type_problem(kind_str(c))]
        if sym_changes:
            syms_detail = ET.SubElement(parent, "problems_with_symbols")
            syms_detail.set("severity", sev_label)
            for c in sym_changes:
                sym_el = ET.SubElement(syms_detail, "symbol")
                sym_el.set("name", getattr(c, "symbol", "") or "")
                _add_problem_element(sym_el, c)


def _build_report_element(
    kind: str,
    data: dict[str, Any],
    lib_name: str,
    old_version: str,
    new_version: str,
    arch: str = "",
    compiler: str = "",
) -> ET.Element:
    """Build a single <report kind="binary|source"> element."""
    report = ET.Element("report")
    report.set("kind", kind)
    report.set("version", _REPORT_VERSION)

    # <test_info>
    test_info = ET.SubElement(report, "test_info")
    lib_el = ET.SubElement(test_info, "library")
    lib_el.text = lib_name or "unknown"
    _build_version_element(test_info, "version1", old_version, arch, compiler)
    _build_version_element(test_info, "version2", new_version, arch, compiler)

    # File metadata for traceability (inside <test_info> to stay schema-compatible)
    old_meta = data.get("old_metadata")
    new_meta = data.get("new_metadata")
    if old_meta or new_meta:
        file_info = ET.SubElement(test_info, "file_info")
        if old_meta:
            old_el = ET.SubElement(file_info, "old_file")
            ET.SubElement(old_el, "path").text = getattr(old_meta, "path", "")
            ET.SubElement(old_el, "sha256").text = getattr(old_meta, "sha256", "")
            ET.SubElement(old_el, "size_bytes").text = str(
                getattr(old_meta, "size_bytes", 0)
            )
        if new_meta:
            new_el = ET.SubElement(file_info, "new_file")
            ET.SubElement(new_el, "path").text = getattr(new_meta, "path", "")
            ET.SubElement(new_el, "sha256").text = getattr(new_meta, "sha256", "")
            ET.SubElement(new_el, "size_bytes").text = str(
                getattr(new_meta, "size_bytes", 0)
            )

    # <test_results>
    test_results = ET.SubElement(report, "test_results")
    verdict_el = ET.SubElement(test_results, "verdict")
    verdict_el.text = str(data["verdict"])
    affected_el = ET.SubElement(test_results, "affected")
    affected_el.text = str(data["affected_pct"])
    symbols_el = ET.SubElement(test_results, "symbols")
    symbols_el.text = str(data["symbols"])

    # <problem_summary>
    summary = ET.SubElement(report, "problem_summary")

    added_count = ET.SubElement(summary, "added_symbols")
    added_count.text = str(data["added"])
    removed_count = ET.SubElement(summary, "removed_symbols")
    removed_count.text = str(data["removed"])

    tp = data["type_problems"]
    types_el = ET.SubElement(summary, "problems_with_types")
    for sev in ("high", "medium", "low", "safe"):
        s = ET.SubElement(types_el, sev)
        s.text = str(tp[sev])

    sp = data["symbol_problems"]
    symbols_problems_el = ET.SubElement(summary, "problems_with_symbols")
    for sev in ("high", "medium", "low", "safe"):
        s = ET.SubElement(symbols_problems_el, sev)
        s.text = str(sp[sev])

    # Redundancy info (if available)
    redundant_count = data.get("redundant_count", 0)
    if redundant_count > 0:
        red_el = ET.SubElement(summary, "redundant_changes")
        red_el.text = str(redundant_count)

    # Detail sections
    _build_symbol_list(report, "added_symbols", data["changes"], ADDED_KINDS)
    _build_symbol_list(report, "removed_symbols", data["changes"], REMOVED_KINDS)
    _build_problem_details(report, data["changes"])

    return report


def generate_xml_report(
    result: DiffResult,
    lib_name: str = "",
    old_version: str = "",
    new_version: str = "",
    old_symbol_count: int | None = None,
    arch: str = "",
    compiler: str = "",
) -> str:
    """Generate an ABICC-compatible XML ABI report.

    Args:
        result: DiffResult from checker.compare().
        lib_name: Library name for the report.
        old_version: Old library version string.
        new_version: New library version string.
        old_symbol_count: Total exported public symbol count in the old library.
        arch: Target architecture (e.g. "x86_64"). Populates <arch> in test_info.
        compiler: Compiler version string (e.g. "12.2.0"). Populates <gcc> in test_info.

    Returns:
        XML document as a string matching the ABICC report schema.
    """
    changes: list[object] = list(getattr(result, "changes", None) or [])

    # Respect DiffResult.verdict for policy promotions (-strict, -warn-newsym)
    # which set verdict=BREAKING without changing individual change kinds.
    result_verdict: object = getattr(result, "verdict", None)
    final_verdict_str = str(
        result_verdict.value if hasattr(result_verdict, "value") else result_verdict
    )
    verdict_override: str | None = None
    if final_verdict_str == "BREAKING":
        verdict_override = "incompatible"

    root = ET.Element("reports")

    # File metadata — passed into each <report> via the data dict so it
    # appears inside <test_info>, not as a sibling of <report>.
    old_meta = getattr(result, "old_metadata", None)
    new_meta = getattr(result, "new_metadata", None)

    redundant_count = getattr(result, "redundant_count", 0)

    # Binary compatibility section (all changes)
    binary_data = _compute_section(changes, old_symbol_count, exclude_binary_only=False)
    binary_data["redundant_count"] = redundant_count
    binary_data["old_metadata"] = old_meta
    binary_data["new_metadata"] = new_meta
    if verdict_override:
        binary_data["verdict"] = verdict_override
    binary_el = _build_report_element(
        "binary",
        binary_data,
        lib_name,
        old_version,
        new_version,
        arch=arch,
        compiler=compiler,
    )
    root.append(binary_el)

    # Source compatibility section (exclude binary-only kinds)
    source_data = _compute_section(changes, old_symbol_count, exclude_binary_only=True)
    # Count source-level redundant changes (exclude binary-only kinds)
    redundant_changes = getattr(result, "redundant_changes", []) or []
    if redundant_changes and redundant_count > 0:
        source_redundant = sum(
            1 for c in redundant_changes if kind_str(c) not in BINARY_ONLY_KINDS
        )
        source_data["redundant_count"] = source_redundant
    else:
        source_data["redundant_count"] = 0
    source_data["old_metadata"] = old_meta
    source_data["new_metadata"] = new_meta
    if verdict_override:
        source_data["verdict"] = verdict_override
    source_el = _build_report_element(
        "source",
        source_data,
        lib_name,
        old_version,
        new_version,
        arch=arch,
        compiler=compiler,
    )
    root.append(source_el)

    ET.indent(root, space="  ")
    xml_str = ET.tostring(root, encoding="unicode", xml_declaration=True)
    return xml_str


def write_xml_report(
    result: DiffResult,
    output_path: Path,
    lib_name: str = "",
    old_version: str = "",
    new_version: str = "",
    old_symbol_count: int | None = None,
    arch: str = "",
    compiler: str = "",
) -> None:
    """Write ABICC-compatible XML report to *output_path*."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    content = generate_xml_report(
        result,
        lib_name=lib_name,
        old_version=old_version,
        new_version=new_version,
        old_symbol_count=old_symbol_count,
        arch=arch,
        compiler=compiler,
    )
    output_path.write_text(content, encoding="utf-8")
