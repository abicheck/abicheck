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


def _make_completed_process(
    returncode: int = 0, stderr: str = ""
) -> subprocess.CompletedProcess:
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
            patch("abicheck.dumper._resolve_selected_tool", return_value="castxml"),
            patch(
                "abicheck.dumper.deadline.run_bounded",
                return_value=_make_completed_process(
                    returncode=1, stderr="error: no such file"
                ),
            ),
            patch(
                "abicheck.dumper._cache_path",
                return_value=tmp_path / "nonexistent_cache.xml",
            ),
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
            patch("abicheck.dumper._resolve_selected_tool", return_value="castxml"),
            patch(
                "abicheck.dumper.deadline.run_bounded",
                return_value=_make_completed_process(returncode=2, stderr=stderr_text),
            ),
            patch(
                "abicheck.dumper._cache_path",
                return_value=tmp_path / "nonexistent_cache.xml",
            ),
        ):
            from abicheck.dumper import _castxml_dump

            header = tmp_path / "test.hpp"
            header.write_text("// empty", encoding="utf-8")
            with pytest.raises(RuntimeError, match="No such file"):
                _castxml_dump([header], [])

    def test_nonzero_exit_includes_exit_code(self, tmp_path: Path) -> None:
        """Error message should include the exit code."""
        with (
            patch("abicheck.dumper._resolve_selected_tool", return_value="castxml"),
            patch(
                "abicheck.dumper.deadline.run_bounded",
                return_value=_make_completed_process(returncode=127, stderr=""),
            ),
            patch(
                "abicheck.dumper._cache_path",
                return_value=tmp_path / "nonexistent_cache.xml",
            ),
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
            patch("abicheck.dumper._resolve_selected_tool", return_value="castxml"),
            patch("abicheck.dumper.deadline.run_bounded", side_effect=fake_run),
            patch(
                "abicheck.dumper._cache_path",
                return_value=tmp_path / "nonexistent_cache.xml",
            ),
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
            patch("abicheck.dumper._resolve_selected_tool", return_value="castxml"),
            patch("abicheck.dumper.deadline.run_bounded", side_effect=fake_run),
            patch(
                "abicheck.dumper._cache_path",
                return_value=tmp_path / "nonexistent_cache.xml",
            ),
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
            patch("abicheck.dumper._resolve_selected_tool", return_value="castxml"),
            patch("abicheck.dumper.deadline.run_bounded", side_effect=fake_run),
            patch(
                "abicheck.dumper._cache_path",
                return_value=tmp_path / "nonexistent_cache.xml",
            ),
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
            return _make_completed_process(
                returncode=0, stderr="warning: unused variable"
            )

        with (
            patch("abicheck.dumper._resolve_selected_tool", return_value="castxml"),
            patch("abicheck.dumper.deadline.run_bounded", side_effect=fake_run),
            patch(
                "abicheck.dumper._cache_path",
                return_value=tmp_path / "nonexistent_cache.xml",
            ),
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
            patch("abicheck.dumper._resolve_selected_tool", return_value="castxml"),
            patch("abicheck.dumper.deadline.run_bounded", side_effect=fake_run),
            patch(
                "abicheck.dumper._cache_path",
                return_value=tmp_path / "nonexistent_cache.xml",
            ),
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
            patch("abicheck.dumper._resolve_selected_tool", return_value="castxml"),
            patch("abicheck.dumper.deadline.run_bounded", side_effect=fake_run),
            patch(
                "abicheck.dumper._cache_path",
                return_value=tmp_path / "nonexistent_cache.xml",
            ),
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
            patch("abicheck.dumper._resolve_selected_tool", return_value="castxml"),
            patch("abicheck.dumper.deadline.run_bounded", side_effect=fake_run),
            patch(
                "abicheck.dumper._cache_path",
                return_value=tmp_path / "nonexistent_cache.xml",
            ),
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
            patch(
                "abicheck.dumper._resolve_selected_tool", side_effect=FileNotFoundError
            ),
            patch(
                "abicheck.dumper._cache_path",
                return_value=tmp_path / "nonexistent_cache.xml",
            ),
        ):
            from abicheck.dumper import _castxml_dump

            header = tmp_path / "test.hpp"
            header.write_text("// empty", encoding="utf-8")
            with pytest.raises(RuntimeError, match="castxml not found"):
                _castxml_dump([header], [])

    def test_hostile_cwd_castxml_is_not_executed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from abicheck.dumper import _castxml_dump

        fake = tmp_path / "castxml"
        fake.write_text("not an executable", encoding="utf-8")
        header = tmp_path / "test.hpp"
        header.write_text("// empty", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("PATH", str(tmp_path / "empty-path"))

        with (
            patch("abicheck.dumper.deadline.run_bounded") as run,
            pytest.raises(RuntimeError, match="castxml not found"),
        ):
            _castxml_dump([header], [])
        run.assert_not_called()


def test_build_castxml_command_gcc_option_tokens_verbatim(tmp_path):
    """G21.5: repeatable --gcc-option tokens reach castxml as literal argv
    elements (no shlex split), so a flag value with whitespace survives intact
    and identically across platforms — the cross-platform-correct design that
    the string-quoting approach could not provide on Windows."""
    from pathlib import Path

    from abicheck.dumper import _build_castxml_command

    cmd = _build_castxml_command(
        "gcc",
        "gnu",
        [],
        Path("out.xml"),
        Path("agg.hpp"),
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
    from abicheck._compiler_options import has_explicit_std

    assert has_explicit_std("-O2 -std=gnu++23", ()) is True
    assert has_explicit_std(None, ("-std=gnu++23",)) is True
    assert has_explicit_std(None, ("/std:c++latest",)) is True
    assert has_explicit_std("-O2", ("-Wall",)) is False
    assert has_explicit_std(None, ()) is False


def test_has_explicit_cpp_std_distinguishes_c_and_cpp_dialects():
    """Compile-DB C++ standards are language evidence, unlike C standards."""
    from abicheck._compiler_options import has_explicit_cpp_std

    assert has_explicit_cpp_std("-O2 -std=gnu++17", ()) is True
    assert has_explicit_cpp_std(None, ("-std=c++20",)) is True
    assert has_explicit_cpp_std(None, ("/std:c++latest",)) is True
    assert has_explicit_cpp_std("-O2 -std=gnu17", ()) is False
    assert has_explicit_cpp_std(None, ("-std=c23",)) is False


def test_castxml_cpp_std_selects_cpp_mode_for_c_compatible_dot_h(tmp_path, monkeypatch):
    """Cases 66/69: compile-DB -std=gnu++17 beats .h/content heuristics."""
    from xml.etree.ElementTree import Element

    from abicheck import dumper

    header = tmp_path / "public.h"
    header.write_text('extern "C" int api(void);\n', encoding="utf-8")
    captured: dict[str, bool] = {}

    def fake_run(*args, force_cpp: bool, **kwargs):
        captured["force_cpp"] = force_cpp
        return Element("GCC_XML")

    monkeypatch.setattr(dumper, "_resolve_selected_tool", lambda _name: "castxml")
    monkeypatch.setattr(dumper, "_cache_path", lambda key: tmp_path / "cache.xml")
    monkeypatch.setattr(
        dumper, "_resolve_compiler_binary", lambda *args: ("g++", "gnu")
    )
    monkeypatch.setattr(dumper, "_run_castxml_attempt", fake_run)

    dumper._castxml_dump([header], [], gcc_option_tokens=("-std=gnu++17",), lang=None)

    assert captured["force_cpp"] is True


def test_castxml_cpp20_only_syntax_selects_cpp_mode(tmp_path, monkeypatch):
    """Regression (Codex review): a header whose *only* C++ signal is
    abbreviated constrained-parameter syntax (``void f(std::integral auto
    x);`` — no class/namespace/template keyword at all) must still resolve
    to C++ mode. ``_detect_cpp_headers`` alone doesn't recognize this as
    C++, and ``force_cpp20`` was gated on ``force_cpp`` already being
    True — so without also consulting ``_detect_cpp20_headers`` when
    deciding ``force_cpp`` itself, castxml was invoked in C mode and
    failed before producing a snapshot."""
    from xml.etree.ElementTree import Element

    from abicheck import dumper

    header = tmp_path / "public.h"
    header.write_text(
        "#include <concepts>\nvoid f(std::integral auto x);\n", encoding="utf-8"
    )
    captured: dict[str, bool] = {}

    def fake_run(*args, force_cpp: bool, **kwargs):
        captured["force_cpp"] = force_cpp
        return Element("GCC_XML")

    monkeypatch.setattr(dumper, "_resolve_selected_tool", lambda _name: "castxml")
    monkeypatch.setattr(dumper, "_cache_path", lambda key: tmp_path / "cache.xml")
    monkeypatch.setattr(
        dumper, "_resolve_compiler_binary", lambda *args: ("g++", "gnu")
    )
    monkeypatch.setattr(dumper, "_run_castxml_attempt", fake_run)

    dumper._castxml_dump([header], [], lang=None)

    assert captured["force_cpp"] is True


def test_explicit_c_language_overrides_forwarded_cpp_standard(tmp_path):
    """An explicit --lang c remains authoritative despite conflicting flags."""
    from abicheck.dumper import _resolve_clang_langmode

    header = tmp_path / "public.h"
    header.write_text("int api(void);\n", encoding="utf-8")

    force_cpp, _, explicit_c, _ = _resolve_clang_langmode(
        "C", [header], "clang", gcc_option_tokens=("-std=gnu++17",)
    )

    assert force_cpp is False
    assert explicit_c is True


def test_clang_langmode_cpp20_only_syntax_selects_cpp_mode(tmp_path):
    """Companion to the castxml-path regression above, for the clang
    frontend's identical auto-detection: abbreviated constrained-parameter
    syntax alone must select C++ mode (and consequently force_cpp20 too,
    since it was gated on force_cpp)."""
    from abicheck.dumper import _resolve_clang_langmode

    header = tmp_path / "public.h"
    header.write_text(
        "#include <concepts>\nvoid f(std::integral auto x);\n", encoding="utf-8"
    )

    force_cpp, force_cpp20, explicit_c, _ = _resolve_clang_langmode(
        None, [header], "clang"
    )

    assert force_cpp is True
    assert force_cpp20 is True
    assert explicit_c is False


def test_castxml_command_user_std_token_not_overridden(tmp_path):
    """A -std passed via --gcc-option suppresses the automatic C++20 bump, so the
    user's dialect is the last (winning) standard flag (Codex review)."""
    from pathlib import Path

    from abicheck.dumper import _build_castxml_command

    cmd = _build_castxml_command(
        "g++",
        "gnu",
        [],
        Path("o.xml"),
        Path("a.hpp"),
        gcc_option_tokens=("-std=gnu++23",),
        force_cpp=True,
        force_cpp20=True,
    )
    assert "-std=gnu++23" in cmd
    assert "-std=gnu++20" not in cmd  # abicheck did not append its own after


def test_clang_header_command_carries_gcc_option_tokens(tmp_path):
    """The clang L2 backend honours --gcc-option too (verbatim argv + std guard)."""
    from pathlib import Path

    from abicheck.dumper import _build_clang_header_command

    cmd = _build_clang_header_command(
        "clang++",
        "gnu",
        [],
        Path("a.hpp"),
        gcc_option_tokens=("-include", "some header.h", "-std=gnu++23"),
        force_cpp=True,
        force_cpp20=True,
    )
    i = cmd.index("-include")
    assert cmd[i + 1] == "some header.h"  # spaced value stays one arg
    assert "-std=gnu++23" in cmd and "-std=gnu++20" not in cmd


def test_castxml_c_mode_user_std_token_not_overridden():
    """C-mode castxml must not append -std=gnu11 after a user -std token, so a
    C dialect chosen via --gcc-option actually takes effect (Codex review)."""
    from pathlib import Path

    from abicheck.dumper import _build_castxml_command

    cmd = _build_castxml_command(
        "gcc",
        "gnu",
        [],
        Path("o.xml"),
        Path("a.h"),
        gcc_option_tokens=("-std=gnu17",),
        force_cpp=False,
    )
    assert "-std=gnu17" in cmd
    assert "-std=gnu11" not in cmd
    assert "-x" in cmd and "c" in cmd  # C language mode still forced
    cc_index = cmd.index("--castxml-cc-gnu-c")
    assert cmd[cc_index + 1 : cc_index + 6] == ["(", "gcc", "-x", "c", ")"]


def test_castxml_c_mode_forces_explicit_cpp_driver_probe_to_c():
    """Keep an explicit g++ executable while probing its builtins as C."""
    from pathlib import Path

    from abicheck.dumper import _build_castxml_command

    cmd = _build_castxml_command(
        "/opt/cross/bin/g++",
        "gnu",
        [],
        Path("o.xml"),
        Path("a.h"),
        force_cpp=False,
    )
    cc_index = cmd.index("--castxml-cc-gnu-c")
    assert cmd[cc_index + 1 : cc_index + 6] == [
        "(",
        "/opt/cross/bin/g++",
        "-x",
        "c",
        ")",
    ]


def test_castxml_cpp_mode_keeps_gnu_emulation_id():
    """The ``gnu-c`` workaround must be scoped to C parsing only."""
    from pathlib import Path

    from abicheck.dumper import _build_castxml_command

    cmd = _build_castxml_command(
        "g++", "gnu", [], Path("o.xml"), Path("a.hpp"), force_cpp=True
    )
    assert "--castxml-cc-gnu" in cmd
    assert "--castxml-cc-gnu-c" not in cmd


def _ast_parser_kwargs(tmp_path):
    """Common keyword args for _header_ast_parser in the G16 fallback tests."""
    return dict(
        compiler="c++",
        gcc_path=None,
        gcc_prefix=None,
        gcc_options=None,
        gcc_option_tokens=(),
        sysroot=None,
        nostdinc=False,
        lang=None,
        exported_dynamic=set(),
        exported_static=set(),
        public_header_paths=[],
        public_dir_paths=[],
    )


def test_header_ast_parser_falls_back_to_clang_on_toolchain_failure(
    tmp_path, monkeypatch
):
    """G16: an auto-selected castxml that fails with a toolchain-version error
    (bundled Clang too old) falls back to the clang backend instead of aborting."""
    from abicheck import dumper
    from abicheck.dumper import _ClangAstParser, _header_ast_parser
    from abicheck.errors import SnapshotError

    def _boom(*a, **k):
        raise SnapshotError("castxml failed: error: unknown type name '_Float128'")

    sentinel = object()
    monkeypatch.setattr(dumper, "_resolve_header_backend", lambda b: "castxml")
    monkeypatch.setattr(dumper, "_castxml_dump", _boom)
    monkeypatch.setattr(dumper, "_resolve_clang_bin", lambda *a, **k: "clang")
    monkeypatch.setattr(dumper, "_clang_header_dump", lambda *a, **k: sentinel)
    monkeypatch.delenv("ABICHECK_AST_FRONTEND", raising=False)

    parser = _header_ast_parser(
        [Path("a.h")], [], backend="auto", **_ast_parser_kwargs(tmp_path)
    )
    assert isinstance(parser, _ClangAstParser)


def test_header_ast_parser_falls_back_to_clang_on_guard_error(tmp_path, monkeypatch):
    """An auto-selected castxml that fails on a direct-include #error guard (a
    -H include-dir swept in an internal/detail header) falls back to clang, whose
    path can granularly exclude the offending header and re-parse the rest."""
    from abicheck import dumper
    from abicheck.dumper import _ClangAstParser, _header_ast_parser
    from abicheck.errors import SnapshotError

    def _boom(*a, **k):
        raise SnapshotError(
            "castxml failed (exit 1):\n"
            "/x/_detail.h:21:6: error: do not #include this internal header directly"
        )

    sentinel = object()
    monkeypatch.setattr(dumper, "_resolve_header_backend", lambda b: "castxml")
    monkeypatch.setattr(dumper, "_castxml_dump", _boom)
    monkeypatch.setattr(dumper, "_resolve_clang_bin", lambda *a, **k: "clang")
    monkeypatch.setattr(dumper, "_clang_header_dump", lambda *a, **k: sentinel)
    monkeypatch.delenv("ABICHECK_AST_FRONTEND", raising=False)

    parser = _header_ast_parser(
        [Path("a.h")], [], backend="auto", **_ast_parser_kwargs(tmp_path)
    )
    assert isinstance(parser, _ClangAstParser)


def test_header_ast_parser_no_fallback_when_castxml_explicit(tmp_path, monkeypatch):
    """An explicit --ast-frontend castxml is honored verbatim — the toolchain
    error surfaces unchanged rather than silently switching to clang."""
    from abicheck import dumper
    from abicheck.dumper import _header_ast_parser
    from abicheck.errors import SnapshotError

    def _boom(*a, **k):
        raise SnapshotError("castxml failed: error: unknown type name '_Float128'")

    monkeypatch.setattr(dumper, "_resolve_header_backend", lambda b: "castxml")
    monkeypatch.setattr(dumper, "_castxml_dump", _boom)
    monkeypatch.setattr(dumper, "_resolve_clang_bin", lambda *a, **k: "clang")
    monkeypatch.delenv("ABICHECK_AST_FRONTEND", raising=False)

    with pytest.raises(SnapshotError):
        _header_ast_parser(
            [Path("a.h")], [], backend="castxml", **_ast_parser_kwargs(tmp_path)
        )


def test_header_ast_parser_no_fallback_on_non_toolchain_failure(tmp_path, monkeypatch):
    """A castxml failure that is NOT a toolchain-version signature (e.g. a bad
    header) re-raises — fallback is reserved for the recoverable case."""
    from abicheck import dumper
    from abicheck.dumper import _header_ast_parser
    from abicheck.errors import SnapshotError

    def _boom(*a, **k):
        raise SnapshotError("castxml failed: fatal error: 'missing.h' file not found")

    monkeypatch.setattr(dumper, "_resolve_header_backend", lambda b: "castxml")
    monkeypatch.setattr(dumper, "_castxml_dump", _boom)
    monkeypatch.setattr(dumper, "_resolve_clang_bin", lambda *a, **k: "clang")
    monkeypatch.delenv("ABICHECK_AST_FRONTEND", raising=False)

    with pytest.raises(SnapshotError):
        _header_ast_parser(
            [Path("a.h")], [], backend="auto", **_ast_parser_kwargs(tmp_path)
        )


def test_header_ast_parser_clang_backend_returns_clang_parser(tmp_path, monkeypatch):
    """When the resolved backend is clang, the clang parser is returned directly."""
    from abicheck import dumper
    from abicheck.dumper import _ClangAstParser, _header_ast_parser

    monkeypatch.setattr(dumper, "_resolve_header_backend", lambda b: "clang")
    monkeypatch.setattr(dumper, "_clang_header_dump", lambda *a, **k: {})

    parser = _header_ast_parser(
        [Path("a.h")], [], backend="clang", **_ast_parser_kwargs(tmp_path)
    )
    assert isinstance(parser, _ClangAstParser)


def test_header_ast_parser_castxml_success_returns_castxml_parser(
    tmp_path, monkeypatch
):
    """A successful castxml dump returns the castxml parser (no fallback)."""
    from xml.etree.ElementTree import Element

    from abicheck import dumper
    from abicheck.dumper import _CastxmlParser, _header_ast_parser

    monkeypatch.setattr(dumper, "_resolve_header_backend", lambda b: "castxml")
    monkeypatch.setattr(dumper, "_castxml_dump", lambda *a, **k: Element("GCC_XML"))

    parser = _header_ast_parser(
        [Path("a.h")], [], backend="auto", **_ast_parser_kwargs(tmp_path)
    )
    assert isinstance(parser, _CastxmlParser)
