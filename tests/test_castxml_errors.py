"""Tests for castxml failure detection.

Verifies that abicheck raises a clear RuntimeError (rather than returning
a silently-empty COMPATIBLE result) when castxml:

1. Exits with a non-zero return code.
2. Exits with code 0 but produces an empty output file.
3. Exits with code 0 but produces an empty XML root element (no declarations).
4. Exits with code 0 but produces invalid/malformed XML.

These tests use unittest.mock to isolate castxml invocation without
requiring the actual binary to be installed.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helper: produce a minimal valid castxml XML document with some content
# ---------------------------------------------------------------------------

_VALID_CASTXML_XML = b"""\
<?xml version="1.0"?>
<CastXML format="1.1.0">
  <Namespace id="_1" name="::" context="_1"/>
  <FundamentalType id="_2" name="int" size="32"/>
</CastXML>
"""

_EMPTY_CASTXML_XML = b"""\
<?xml version="1.0"?>
<CastXML format="1.1.0">
</CastXML>
"""


def _make_completed_process(returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    """Build a fake CompletedProcess result."""
    result: subprocess.CompletedProcess = MagicMock(spec=subprocess.CompletedProcess)
    result.returncode = returncode
    result.stderr = stderr
    result.stdout = ""
    return result


class TestCastxmlNonZeroExit:
    """castxml exits with non-zero → RuntimeError with informative message."""

    def test_nonzero_exit_raises_runtime_error(self, tmp_path: Path) -> None:
        """castxml exit 1 → RuntimeError mentioning exit code."""
        with (
            patch("abicheck.dumper._castxml_available", return_value=True),
            patch("abicheck.dumper.subprocess.run",
                  return_value=_make_completed_process(returncode=1, stderr="error: no such file")),
            patch("abicheck.dumper._cache_path", return_value=tmp_path / "nonexistent_cache.xml"),
        ):
            from abicheck.dumper import _castxml_dump
            header = tmp_path / "test.hpp"
            header.write_text("// empty", encoding="utf-8")
            with pytest.raises(RuntimeError, match="castxml failed"):
                _castxml_dump([header], [])

    def test_nonzero_exit_includes_stderr(self, tmp_path: Path) -> None:
        """Error message should include stderr from castxml."""
        stderr_text = "fatal error: myheader.h: No such file or directory"
        with (
            patch("abicheck.dumper._castxml_available", return_value=True),
            patch("abicheck.dumper.subprocess.run",
                  return_value=_make_completed_process(returncode=2, stderr=stderr_text)),
            patch("abicheck.dumper._cache_path", return_value=tmp_path / "nonexistent_cache.xml"),
        ):
            from abicheck.dumper import _castxml_dump
            header = tmp_path / "test.hpp"
            header.write_text("// empty", encoding="utf-8")
            with pytest.raises(RuntimeError, match="No such file"):
                _castxml_dump([header], [])

    def test_nonzero_exit_includes_exit_code(self, tmp_path: Path) -> None:
        """Error message should include the exit code."""
        with (
            patch("abicheck.dumper._castxml_available", return_value=True),
            patch("abicheck.dumper.subprocess.run",
                  return_value=_make_completed_process(returncode=127, stderr="")),
            patch("abicheck.dumper._cache_path", return_value=tmp_path / "nonexistent_cache.xml"),
        ):
            from abicheck.dumper import _castxml_dump
            header = tmp_path / "test.hpp"
            header.write_text("// empty", encoding="utf-8")
            with pytest.raises(RuntimeError, match="127"):
                _castxml_dump([header], [])


class TestCastxmlEmptyOutput:
    """castxml exits 0 but produces empty/missing output → RuntimeError."""

    def _patch_run_writes_file(self, tmp_path: Path, content: bytes) -> Path:
        """Returns the out_xml path that will be written by the mock subprocess.run."""
        return tmp_path  # will be resolved dynamically

    def test_empty_output_file_raises(self, tmp_path: Path) -> None:
        """castxml exits 0 but writes empty file → RuntimeError."""
        out_xml_path: list[Path] = []  # capture via side_effect

        def fake_run(cmd, **kwargs):  # noqa: ANN001
            # Find the -o argument and write empty content there
            o_idx = cmd.index("-o")
            out_path = Path(cmd[o_idx + 1])
            out_path.write_bytes(b"")
            out_xml_path.append(out_path)
            return _make_completed_process(returncode=0)

        with (
            patch("abicheck.dumper._castxml_available", return_value=True),
            patch("abicheck.dumper.subprocess.run", side_effect=fake_run),
            patch("abicheck.dumper._cache_path", return_value=tmp_path / "nonexistent_cache.xml"),
        ):
            from abicheck.dumper import _castxml_dump
            header = tmp_path / "test.hpp"
            header.write_text("// empty", encoding="utf-8")
            with pytest.raises(RuntimeError, match="empty"):
                _castxml_dump([header], [])

    def test_missing_output_file_raises(self, tmp_path: Path) -> None:
        """castxml exits 0 but does NOT write output file → RuntimeError."""
        def fake_run(cmd, **kwargs):  # noqa: ANN001
            # Do NOT write to -o path — simulate crash without writing output
            return _make_completed_process(returncode=0)

        with (
            patch("abicheck.dumper._castxml_available", return_value=True),
            patch("abicheck.dumper.subprocess.run", side_effect=fake_run),
            patch("abicheck.dumper._cache_path", return_value=tmp_path / "nonexistent_cache.xml"),
        ):
            from abicheck.dumper import _castxml_dump
            header = tmp_path / "test.hpp"
            header.write_text("// empty", encoding="utf-8")
            with pytest.raises(RuntimeError):
                _castxml_dump([header], [])


class TestCastxmlEmptyXmlRoot:
    """castxml exits 0 with valid XML but empty root element → RuntimeError."""

    def test_empty_xml_root_raises(self, tmp_path: Path) -> None:
        """castxml exits 0 but XML root has no children → RuntimeError."""
        def fake_run(cmd, **kwargs):  # noqa: ANN001
            o_idx = cmd.index("-o")
            out_path = Path(cmd[o_idx + 1])
            out_path.write_bytes(_EMPTY_CASTXML_XML)
            return _make_completed_process(returncode=0)

        with (
            patch("abicheck.dumper._castxml_available", return_value=True),
            patch("abicheck.dumper.subprocess.run", side_effect=fake_run),
            patch("abicheck.dumper._cache_path", return_value=tmp_path / "nonexistent_cache.xml"),
        ):
            from abicheck.dumper import _castxml_dump
            header = tmp_path / "test.hpp"
            header.write_text("// empty", encoding="utf-8")
            with pytest.raises(RuntimeError, match="empty"):
                _castxml_dump([header], [])

    def test_empty_xml_error_message_is_informative(self, tmp_path: Path) -> None:
        """Error message should direct user to check header paths."""
        def fake_run(cmd, **kwargs):  # noqa: ANN001
            o_idx = cmd.index("-o")
            out_path = Path(cmd[o_idx + 1])
            out_path.write_bytes(_EMPTY_CASTXML_XML)
            return _make_completed_process(returncode=0, stderr="warning: unused variable")

        with (
            patch("abicheck.dumper._castxml_available", return_value=True),
            patch("abicheck.dumper.subprocess.run", side_effect=fake_run),
            patch("abicheck.dumper._cache_path", return_value=tmp_path / "nonexistent_cache.xml"),
        ):
            from abicheck.dumper import _castxml_dump
            header = tmp_path / "test.hpp"
            header.write_text("// empty", encoding="utf-8")
            with pytest.raises(RuntimeError) as exc_info:
                _castxml_dump([header], [])
            msg = str(exc_info.value)
            # Should mention "empty" and give actionable guidance
            assert "empty" in msg.lower() or "no declarations" in msg.lower()


class TestCastxmlInvalidXml:
    """castxml exits 0 but writes malformed XML → RuntimeError."""

    def test_invalid_xml_raises(self, tmp_path: Path) -> None:
        """castxml writes malformed XML → RuntimeError with parse context."""
        def fake_run(cmd, **kwargs):  # noqa: ANN001
            o_idx = cmd.index("-o")
            out_path = Path(cmd[o_idx + 1])
            out_path.write_bytes(b"<notclosed>this is not valid xml")
            return _make_completed_process(returncode=0)

        with (
            patch("abicheck.dumper._castxml_available", return_value=True),
            patch("abicheck.dumper.subprocess.run", side_effect=fake_run),
            patch("abicheck.dumper._cache_path", return_value=tmp_path / "nonexistent_cache.xml"),
        ):
            from abicheck.dumper import _castxml_dump
            header = tmp_path / "test.hpp"
            header.write_text("// empty", encoding="utf-8")
            with pytest.raises(RuntimeError, match="invalid XML"):
                _castxml_dump([header], [])

    def test_truncated_xml_raises(self, tmp_path: Path) -> None:
        """castxml writes truncated XML (starts valid but truncated) → RuntimeError."""
        def fake_run(cmd, **kwargs):  # noqa: ANN001
            o_idx = cmd.index("-o")
            out_path = Path(cmd[o_idx + 1])
            # Write only the first half of valid XML
            out_path.write_bytes(_VALID_CASTXML_XML[:50])
            return _make_completed_process(returncode=0)

        with (
            patch("abicheck.dumper._castxml_available", return_value=True),
            patch("abicheck.dumper.subprocess.run", side_effect=fake_run),
            patch("abicheck.dumper._cache_path", return_value=tmp_path / "nonexistent_cache.xml"),
        ):
            from abicheck.dumper import _castxml_dump
            header = tmp_path / "test.hpp"
            header.write_text("// empty", encoding="utf-8")
            with pytest.raises(RuntimeError):
                _castxml_dump([header], [])


class TestCastxmlSuccessPath:
    """Happy path: castxml exits 0 with valid non-empty XML → returns Element."""

    def test_valid_output_returns_element(self, tmp_path: Path) -> None:
        """castxml exits 0 with valid non-empty XML → returns parsed Element."""
        def fake_run(cmd, **kwargs):  # noqa: ANN001
            o_idx = cmd.index("-o")
            out_path = Path(cmd[o_idx + 1])
            out_path.write_bytes(_VALID_CASTXML_XML)
            return _make_completed_process(returncode=0)

        with (
            patch("abicheck.dumper._castxml_available", return_value=True),
            patch("abicheck.dumper.subprocess.run", side_effect=fake_run),
            patch("abicheck.dumper._cache_path", return_value=tmp_path / "nonexistent_cache.xml"),
        ):
            from abicheck.dumper import _castxml_dump
            header = tmp_path / "test.hpp"
            header.write_text("// empty", encoding="utf-8")
            from xml.etree.ElementTree import Element
            root = _castxml_dump([header], [])
            assert isinstance(root, Element)
            assert len(root) > 0  # has children


class TestCastxmlNotFound:
    """castxml binary not available → RuntimeError with install instructions."""

    def test_castxml_not_found_raises(self, tmp_path: Path) -> None:
        """When castxml is not in PATH → RuntimeError."""
        with (
            patch("abicheck.dumper._castxml_available", return_value=False),
            patch("abicheck.dumper._cache_path", return_value=tmp_path / "nonexistent_cache.xml"),
        ):
            from abicheck.dumper import _castxml_dump
            header = tmp_path / "test.hpp"
            header.write_text("// empty", encoding="utf-8")
            with pytest.raises(RuntimeError, match="castxml not found"):
                _castxml_dump([header], [])


def test_build_castxml_command_gcc_option_tokens_verbatim(tmp_path):
    """G21.5: repeatable --gcc-option tokens reach castxml as literal argv
    elements (no shlex split), so a flag value with whitespace survives intact
    and identically across platforms — the cross-platform-correct design that
    the string-quoting approach could not provide on Windows."""
    from pathlib import Path

    from abicheck.dumper import _build_castxml_command

    cmd = _build_castxml_command(
        "gcc", "gnu", [], Path("out.xml"), Path("agg.hpp"),
        gcc_options="-O2 -DA",
        gcc_option_tokens=("-include", "some header.h"),
        force_cpp=True,
    )
    # --gcc-options still whitespace-splits into separate flags.
    assert "-O2" in cmd and "-DA" in cmd
    # Each --gcc-option is one literal argv element; the spaced value is NOT split.
    i = cmd.index("-include")
    assert cmd[i + 1] == "some header.h"
    assert "some" not in cmd and "header.h" not in cmd


def test_has_explicit_std_checks_both_flag_forms():
    """Codex review: an explicit -std supplied via the repeatable --gcc-option
    must be honoured, not just one in the whitespace --gcc-options string."""
    from abicheck.dumper import _has_explicit_std

    assert _has_explicit_std("-O2 -std=gnu++23", ()) is True
    assert _has_explicit_std(None, ("-std=gnu++23",)) is True
    assert _has_explicit_std(None, ("/std:c++latest",)) is True
    assert _has_explicit_std("-O2", ("-Wall",)) is False
    assert _has_explicit_std(None, ()) is False


def test_castxml_command_user_std_token_not_overridden(tmp_path):
    """A -std passed via --gcc-option suppresses the automatic C++20 bump, so the
    user's dialect is the last (winning) standard flag (Codex review)."""
    from pathlib import Path

    from abicheck.dumper import _build_castxml_command

    cmd = _build_castxml_command(
        "g++", "gnu", [], Path("o.xml"), Path("a.hpp"),
        gcc_option_tokens=("-std=gnu++23",),
        force_cpp=True, force_cpp20=True,
    )
    assert "-std=gnu++23" in cmd
    assert "-std=gnu++20" not in cmd  # abicheck did not append its own after


def test_clang_header_command_carries_gcc_option_tokens(tmp_path):
    """The clang L2 backend honours --gcc-option too (verbatim argv + std guard)."""
    from pathlib import Path

    from abicheck.dumper import _build_clang_header_command

    cmd = _build_clang_header_command(
        "clang++", "gnu", [], Path("a.hpp"),
        gcc_option_tokens=("-include", "some header.h", "-std=gnu++23"),
        force_cpp=True, force_cpp20=True,
    )
    i = cmd.index("-include")
    assert cmd[i + 1] == "some header.h"        # spaced value stays one arg
    assert "-std=gnu++23" in cmd and "-std=gnu++20" not in cmd
