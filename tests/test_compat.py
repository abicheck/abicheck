"""Tests for Sprint 5: ABICC compat layer (descriptor parser + HTML report)."""

from __future__ import annotations

import re
import textwrap
from pathlib import Path

import pytest

from abicheck.compat import CompatDescriptor, parse_descriptor
from abicheck.html_report import generate_html_report

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_xml(tmp_path: Path, content: str, name: str = "desc.xml") -> Path:
    p = tmp_path / name
    p.write_text(textwrap.dedent(content).strip(), encoding="utf-8")
    return p


def _make_fake_result(verdict: str = "COMPATIBLE", breaking: int = 0) -> object:
    """Minimal stand-in for CompareResult (avoids importing checker)."""
    from types import SimpleNamespace

    summary = {
        "breaking": breaking,
        "compatible_additions": 0,
        "total_changes": breaking,
        "source_breaks": 0,
    }
    changes = []
    v = SimpleNamespace(value=verdict)
    return SimpleNamespace(
        verdict=v,
        summary=summary,
        changes=changes,
        suppressed_count=0,
        suppression_file_provided=False,
    )


# ---------------------------------------------------------------------------
# Descriptor parsing
# ---------------------------------------------------------------------------


def test_parse_descriptor_basic(tmp_path: Path) -> None:
    xml = """
    <descriptor>
      <version>2025.3</version>
      <headers>/usr/include/mylib</headers>
      <libs>/usr/lib/libmylib.so</libs>
    </descriptor>
    """
    desc_path = _write_xml(tmp_path, xml)
    desc = parse_descriptor(desc_path)

    assert isinstance(desc, CompatDescriptor)
    assert desc.version == "2025.3"
    assert len(desc.libs) == 1
    assert re.fullmatch(r"([A-Za-z]:)?/usr/lib/libmylib\.so", desc.libs[0].as_posix())
    assert len(desc.headers) == 1
    assert re.fullmatch(r"([A-Za-z]:)?/usr/include/mylib", desc.headers[0].as_posix())


def test_parse_descriptor_multiple_headers(tmp_path: Path) -> None:
    xml = """
    <descriptor>
      <version>1.0</version>
      <headers>/usr/include/foo</headers>
      <headers>/usr/include/foo/detail</headers>
      <libs>/usr/lib/libfoo.so</libs>
    </descriptor>
    """
    desc = parse_descriptor(_write_xml(tmp_path, xml))
    assert len(desc.headers) == 2
    assert any(
        re.fullmatch(r"([A-Za-z]:)?/usr/include/foo/detail", h.as_posix())
        for h in desc.headers
    )


def test_parse_descriptor_multiple_libs(tmp_path: Path) -> None:
    xml = """
    <descriptor>
      <version>3.0</version>
      <headers>/inc</headers>
      <libs>/lib/liba.so</libs>
      <libs>/lib/libb.so</libs>
    </descriptor>
    """
    desc = parse_descriptor(_write_xml(tmp_path, xml))
    assert len(desc.libs) == 2


def test_parse_descriptor_no_headers_ok(tmp_path: Path) -> None:
    """Headers are optional — not all ABICC descriptors include them."""
    xml = """
    <descriptor>
      <version>1.0</version>
      <libs>/usr/lib/libx.so</libs>
    </descriptor>
    """
    desc = parse_descriptor(_write_xml(tmp_path, xml))
    assert desc.version == "1.0"
    assert desc.headers == []


def test_parse_descriptor_missing_libs_raises(tmp_path: Path) -> None:
    xml = """
    <descriptor>
      <version>1.0</version>
      <headers>/usr/include/mylib</headers>
    </descriptor>
    """
    with pytest.raises(ValueError, match="missing <libs>"):
        parse_descriptor(_write_xml(tmp_path, xml))


def test_parse_descriptor_missing_version_raises(tmp_path: Path) -> None:
    xml = """
    <descriptor>
      <headers>/usr/include/mylib</headers>
      <libs>/usr/lib/libx.so</libs>
    </descriptor>
    """
    with pytest.raises(ValueError, match="missing <version>"):
        parse_descriptor(_write_xml(tmp_path, xml))


def test_parse_descriptor_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        parse_descriptor(tmp_path / "nonexistent.xml")


def test_parse_descriptor_relative_lib_resolved(tmp_path: Path) -> None:
    """Relative <libs> paths are resolved relative to the descriptor file."""
    xml = """
    <descriptor>
      <version>0.1</version>
      <libs>mylib.so</libs>
    </descriptor>
    """
    desc = parse_descriptor(_write_xml(tmp_path, xml))
    # Must be absolute and relative to descriptor's directory
    assert desc.libs[0].is_absolute()
    assert desc.libs[0].parent == tmp_path.resolve()


def test_parse_descriptor_fragment_rejected(tmp_path: Path) -> None:
    """Fragment descriptors without a root element are not well-formed XML."""
    xml = """
    <version>1.0</version>
    <libs>/usr/lib/libx.so</libs>
    """
    with pytest.raises(ValueError, match="Invalid XML"):
        parse_descriptor(_write_xml(tmp_path, xml))


def test_parse_descriptor_invalid_xml_raises(tmp_path: Path) -> None:
    bad = tmp_path / "bad.xml"
    bad.write_text("<unclosed>", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid XML"):
        parse_descriptor(bad)


# ---------------------------------------------------------------------------
# HTML report generation
# ---------------------------------------------------------------------------


def test_html_report_contains_verdict() -> None:
    result = _make_fake_result(verdict="BREAKING", breaking=3)
    html = generate_html_report(
        result, lib_name="libtest", old_version="1.0", new_version="2.0"
    )
    assert "BREAKING" in html
    assert "libtest" in html


def test_html_report_compatible_verdict() -> None:
    result = _make_fake_result(verdict="COMPATIBLE")
    html = generate_html_report(result)
    assert "COMPATIBLE" in html


def test_html_report_bc_percent_shown() -> None:
    result = _make_fake_result(verdict="COMPATIBLE")
    html = generate_html_report(result)
    assert "Binary Compatibility" in html
    assert "100.0%" in html


def test_html_report_bc_percent_breaking() -> None:
    result = _make_fake_result(verdict="BREAKING", breaking=2)
    html = generate_html_report(
        result, lib_name="lib", old_version="1", new_version="2"
    )
    assert "0.0%" in html


def test_html_report_is_valid_html() -> None:
    result = _make_fake_result(verdict="NO_CHANGE")
    html_out = generate_html_report(result)
    assert html_out.startswith("<!DOCTYPE html>")
    assert "</html>" in html_out


def test_html_report_versions_in_title(tmp_path: Path) -> None:
    result = _make_fake_result(verdict="COMPATIBLE")
    html_out = generate_html_report(
        result, lib_name="libfoo", old_version="2025.0", new_version="2025.3"
    )
    assert "2025.0" in html_out
    assert "2025.3" in html_out
    assert "libfoo" in html_out


def test_html_report_xss_escape() -> None:
    """Library name with HTML special chars must be escaped."""
    result = _make_fake_result()
    html_out = generate_html_report(result, lib_name="<script>alert(1)</script>")
    assert "<script>" not in html_out
    assert "&lt;script&gt;" in html_out


def test_write_html_report_creates_dirs(tmp_path: Path) -> None:
    from abicheck.html_report import write_html_report

    result = _make_fake_result()
    out = tmp_path / "deep" / "nested" / "report.html"
    write_html_report(result, out)
    assert out.exists()
    assert out.stat().st_size > 100


def test_parse_descriptor_first_lib_used_warning(tmp_path: Path) -> None:
    """When descriptor has multiple <libs>, parse still succeeds with both libs captured."""
    xml = """
    <descriptor>
      <version>2.0</version>
      <libs>/lib/liba.so</libs>
      <libs>/lib/libb.so</libs>
    </descriptor>
    """
    desc = parse_descriptor(_write_xml(tmp_path, xml))
    # Both are captured — CLI is responsible for emitting the warning
    assert len(desc.libs) == 2
    assert re.fullmatch(r"([A-Za-z]:)?/lib/liba\.so", desc.libs[0].as_posix())
