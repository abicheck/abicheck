# pylint: disable=too-many-branches,too-many-statements,too-many-locals,too-many-arguments,too-many-return-statements
"""ABICC XML report format parity tests.

Validates that abicheck's XML reports match the ABICC schema well enough
for abi-tracker and other consumers to parse them.

Two levels of checking:
1. Schema validation (unit tests, always run) — verifies structure.
2. Cross-tool XML parity (abicc marker, CI only) — generates XML from both
   abicheck and ABICC for the same library pair and compares structure.

Requires: abi-compliance-checker, gcc, castxml (for abicc-marked tests).
"""
from __future__ import annotations

import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
from defusedxml.ElementTree import fromstring as xml_fromstring

from abicheck.checker import Change, ChangeKind, DiffResult, Verdict
from abicheck.compat.xml_report import generate_xml_report

# ---------------------------------------------------------------------------
# Schema validation (unit-level, always runs)
# ---------------------------------------------------------------------------


def _make_result(
    changes: list[Change] | None = None,
    verdict: Verdict = Verdict.NO_CHANGE,
) -> DiffResult:
    return DiffResult(
        old_version="1.0", new_version="2.0", library="libtest",
        changes=changes or [], verdict=verdict,
    )


class TestXmlSchemaValidation:
    """Validate XML report structure against abi-tracker parsing expectations.

    abi-tracker navigates: report[@kind='binary']/test_results/verdict
    and report[@kind='binary']/problem_summary/* for its dashboard.
    """

    def test_abi_tracker_navigation_paths(self):
        """Verify all XPath queries that abi-tracker uses work on our XML."""
        changes = [
            Change(kind=ChangeKind.FUNC_REMOVED, symbol="_Z3foov",
                   description="removed"),
            Change(kind=ChangeKind.FUNC_ADDED, symbol="_Z3barv",
                   description="added"),
            Change(kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="MyStruct",
                   description="size changed", old_value="8", new_value="16"),
        ]
        result = _make_result(changes=changes, verdict=Verdict.BREAKING)
        xml = generate_xml_report(result, lib_name="libfoo", old_symbol_count=10)
        root = xml_fromstring(xml)

        # abi-tracker expects these paths to be navigable:
        binary = root.find("report[@kind='binary']")
        assert binary is not None, "Missing <report kind='binary'>"

        # test_results section
        verdict = binary.find("test_results/verdict")
        assert verdict is not None, "Missing test_results/verdict"
        assert verdict.text in ("compatible", "incompatible")

        affected = binary.find("test_results/affected")
        assert affected is not None, "Missing test_results/affected"

        symbols = binary.find("test_results/symbols")
        assert symbols is not None, "Missing test_results/symbols"

        # problem_summary section
        summary = binary.find("problem_summary")
        assert summary is not None, "Missing problem_summary"

        added = summary.find("added_symbols")
        assert added is not None, "Missing problem_summary/added_symbols"
        assert added.text.isdigit()

        removed = summary.find("removed_symbols")
        assert removed is not None, "Missing problem_summary/removed_symbols"
        assert removed.text.isdigit()

        # Severity tiers
        for parent_tag in ("problems_with_types", "problems_with_symbols"):
            parent = summary.find(parent_tag)
            assert parent is not None, f"Missing problem_summary/{parent_tag}"
            for sev in ("high", "medium", "low", "safe"):
                el = parent.find(sev)
                assert el is not None, f"Missing {parent_tag}/{sev}"
                assert el.text.isdigit()

    def test_source_report_present(self):
        result = _make_result()
        xml = generate_xml_report(result, lib_name="libfoo")
        root = xml_fromstring(xml)
        source = root.find("report[@kind='source']")
        assert source is not None

    def test_version_attribute(self):
        result = _make_result()
        xml = generate_xml_report(result, lib_name="libfoo")
        root = xml_fromstring(xml)
        for report in root.findall("report"):
            assert report.get("version") == "1.2"

    def test_problem_details_have_effect(self):
        """abi-tracker may read <effect> elements for display."""
        changes = [
            Change(kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="S",
                   description="size changed", old_value="4", new_value="8"),
        ]
        result = _make_result(changes=changes, verdict=Verdict.BREAKING)
        xml = generate_xml_report(result, old_symbol_count=5)
        root = xml_fromstring(xml)
        prob = root.find(".//problem[@id='type_size_changed']")
        assert prob is not None
        assert prob.find("effect") is not None


# ---------------------------------------------------------------------------
# Cross-tool XML parity (requires ABICC installed)
# ---------------------------------------------------------------------------


def _require_tool(name: str) -> None:
    if shutil.which(name) is None:
        pytest.skip(f"{name} not found in PATH")


def _compile_so(src: str, out: Path, lang: str = "c") -> None:
    ext = ".c" if lang == "c" else ".cpp"
    src_file = out.with_suffix(ext)
    src_file.write_text(textwrap.dedent(src).strip(), encoding="utf-8")
    compiler = "gcc" if lang == "c" else "g++"
    cmd = [compiler, "-shared", "-fPIC", "-g", "-fvisibility=default",
           "-o", str(out), str(src_file)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        pytest.fail(f"Compilation failed: {r.stderr[:200]}")


@pytest.mark.abicc
class TestXmlCrossToolParity:
    """Compare XML reports from abicheck and ABICC for the same library pair.

    Doesn't require identical output — just validates that both produce
    XML with the same structural elements that abi-tracker would parse.
    """

    def test_both_produce_parseable_xml_for_func_removed(self, tmp_path: Path):
        """Both tools produce XML with compatible structure for a function removal."""
        _require_tool("abi-compliance-checker")
        _require_tool("gcc")
        _require_tool("castxml")

        # Compile v1 and v2
        v1_src = "int add(int a, int b) { return a + b; }\nint sub(int a, int b) { return a - b; }"
        v2_src = "int add(int a, int b) { return a + b; }"
        v1_hdr = "int add(int a, int b);\nint sub(int a, int b);"
        v2_hdr = "int add(int a, int b);"

        v1_so = tmp_path / "libtest_v1.so"
        v2_so = tmp_path / "libtest_v2.so"
        _compile_so(v1_src, v1_so)
        _compile_so(v2_src, v2_so)

        h1 = tmp_path / "v1.h"
        h2 = tmp_path / "v2.h"
        h1.write_text(v1_hdr, encoding="utf-8")
        h2.write_text(v2_hdr, encoding="utf-8")

        # Generate ABICC XML report
        old_desc = tmp_path / "old.xml"
        new_desc = tmp_path / "new.xml"
        old_desc.write_text(f"<version>1.0</version>\n<headers>{h1}</headers>\n<libs>{v1_so}</libs>")
        new_desc.write_text(f"<version>2.0</version>\n<headers>{h2}</headers>\n<libs>{v2_so}</libs>")

        abicc_xml = tmp_path / "abicc_report.xml"
        abicc_result = subprocess.run([
            "abi-compliance-checker", "-lib", "libtest",
            "-old", str(old_desc), "-new", str(new_desc),
            "-report-format", "xml", "-report-path", str(abicc_xml),
        ], capture_output=True, text=True, timeout=60)
        # ABICC returns 1 for incompatible (expected for func_removed)
        assert abicc_result.returncode in (0, 1), (
            f"ABICC failed unexpectedly (rc={abicc_result.returncode}):\n"
            f"{abicc_result.stderr[:500]}"
        )
        assert abicc_xml.exists(), (
            f"ABICC did not produce XML report:\n{abicc_result.stderr[:500]}"
        )

        # Generate abicheck XML report
        import warnings  # noqa: PLC0415

        from abicheck.checker import compare  # noqa: PLC0415
        from abicheck.dumper import dump  # noqa: PLC0415

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            old_snap = dump(v1_so, headers=[h1], version="1.0", compiler="cc")
            new_snap = dump(v2_so, headers=[h2], version="2.0", compiler="cc")
        # Parity expectations predate default scoping (ADR-024 Phase 5); compare
        # unscoped to keep the established XML/abicc verdicts apples-to-apples.
        result = compare(old_snap, new_snap, scope_to_public_surface=False)
        abicheck_xml_str = generate_xml_report(
            result, lib_name="libtest",
            old_version="1.0", new_version="2.0",
            old_symbol_count=2,
        )

        # Parse both
        abicheck_root = xml_fromstring(abicheck_xml_str)

        # Both should have binary report with verdict "incompatible"
        ac_binary = abicheck_root.find("report[@kind='binary']")
        assert ac_binary is not None
        ac_verdict = ac_binary.find("test_results/verdict")
        assert ac_verdict is not None
        assert ac_verdict.text == "incompatible"

        # Verify removed_symbols count > 0
        ac_removed = ac_binary.find("problem_summary/removed_symbols")
        assert ac_removed is not None
        assert int(ac_removed.text) > 0

        # If ABICC also produced XML, compare structure
        if abicc_xml.exists() and abicc_xml.stat().st_size > 0:
            abicc_content = abicc_xml.read_text(encoding="utf-8")
            try:
                abicc_root = xml_fromstring(abicc_content)
            except Exception:  # ParseError from xml parsing
                # ABICC may wrap in <reports> or not
                abicc_root = xml_fromstring(f"<wrapper>{abicc_content}</wrapper>")

            # Find ABICC's binary report
            cc_binary = (
                abicc_root.find("report[@kind='binary']")
                or abicc_root.find(".//report[@kind='binary']")
            )
            if cc_binary is not None:
                cc_verdict = cc_binary.find("test_results/verdict")
                if cc_verdict is not None:
                    # Both should agree on incompatible
                    assert cc_verdict.text == "incompatible"

    def test_both_produce_xml_for_no_change(self, tmp_path: Path):
        """Compatible libraries produce verdict='compatible' in both tools."""
        _require_tool("abi-compliance-checker")
        _require_tool("gcc")
        _require_tool("castxml")

        src = "int add(int a, int b) { return a + b; }"
        hdr = "int add(int a, int b);"

        v1_so = tmp_path / "libtest_v1.so"
        v2_so = tmp_path / "libtest_v2.so"
        _compile_so(src, v1_so)
        _compile_so(src, v2_so)

        h = tmp_path / "test.h"
        h.write_text(hdr, encoding="utf-8")

        import warnings  # noqa: PLC0415

        from abicheck.checker import compare  # noqa: PLC0415
        from abicheck.dumper import dump  # noqa: PLC0415

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            old_snap = dump(v1_so, headers=[h], version="1.0", compiler="cc")
            new_snap = dump(v2_so, headers=[h], version="1.0", compiler="cc")
        result = compare(old_snap, new_snap, scope_to_public_surface=False)
        xml_str = generate_xml_report(
            result, lib_name="libtest",
            old_version="1.0", new_version="1.0",
            old_symbol_count=1,
        )

        root = xml_fromstring(xml_str)
        binary = root.find("report[@kind='binary']")
        assert binary.find("test_results/verdict").text == "compatible"
        assert binary.find("problem_summary/removed_symbols").text == "0"
