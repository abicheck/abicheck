"""Unit tests for abicheck.demangle — targeting ≥80% coverage."""
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

# Clear the LRU cache before each test to avoid cross-test contamination
import abicheck.demangle as _mod


@pytest.fixture(autouse=True)
def _clear_caches():
    _mod.demangle.cache_clear()
    _mod._reset_demangle_batch_cache()
    _mod._warned_no_demangler = False
    yield
    _mod.demangle.cache_clear()
    _mod._reset_demangle_batch_cache()
    _mod._warned_no_demangler = False


# ── demangle() ──────────────────────────────────────────────────────────────


class TestDemangle:
    """Tests for the single-symbol demangle() function."""

    def test_empty_string_returns_none(self):
        assert _mod.demangle("") is None

    def test_non_cpp_symbol_returns_none(self):
        assert _mod.demangle("printf") is None

    def test_non_z_prefix_returns_none(self):
        assert _mod.demangle("myFunction") is None

    def test_cxxfilt_available(self):
        """When cxxfilt is importable and works, we get a demangled string."""
        mock_cxxfilt = MagicMock()
        mock_cxxfilt.demangle.return_value = "foo::bar()"
        with patch.dict("sys.modules", {"cxxfilt": mock_cxxfilt}):
            result = _mod.demangle("_ZN3foo3barEv")
        assert result == "foo::bar()"

    def test_cxxfilt_raises_falls_through_to_cppfilt(self):
        """When cxxfilt raises, fall back to c++filt subprocess."""
        mock_cxxfilt = MagicMock()
        mock_cxxfilt.demangle.side_effect = RuntimeError("boom")
        with patch.dict("sys.modules", {"cxxfilt": mock_cxxfilt}):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = subprocess.CompletedProcess(
                    args=["c++filt", "_ZN3foo3barEv"],
                    returncode=0,
                    stdout="foo::bar()\n",
                    stderr="",
                )
                result = _mod.demangle("_ZN3foo3barEv")
        assert result == "foo::bar()"

    def test_cppfilt_non_zero_return_code(self):
        """When c++filt returns non-zero, we get None (after cxxfilt also fails)."""
        mock_cxxfilt = MagicMock()
        mock_cxxfilt.demangle.side_effect = RuntimeError("no")
        with patch.dict("sys.modules", {"cxxfilt": mock_cxxfilt}):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = subprocess.CompletedProcess(
                    args=["c++filt"], returncode=1, stdout="", stderr="error",
                )
                result = _mod.demangle("_ZN3foo3barEv")
        assert result is None

    def test_cppfilt_output_same_as_input(self):
        """If c++filt outputs the same symbol, treat as failed demangling."""
        mock_cxxfilt = MagicMock()
        mock_cxxfilt.demangle.side_effect = RuntimeError("no")
        with patch.dict("sys.modules", {"cxxfilt": mock_cxxfilt}):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = subprocess.CompletedProcess(
                    args=["c++filt"],
                    returncode=0,
                    stdout="_ZN3foo3barEv\n",
                    stderr="",
                )
                result = _mod.demangle("_ZN3foo3barEv")
        assert result is None

    def test_cppfilt_empty_output(self):
        """If c++filt returns empty stdout, treat as failed."""
        mock_cxxfilt = MagicMock()
        mock_cxxfilt.demangle.side_effect = RuntimeError("no")
        with patch.dict("sys.modules", {"cxxfilt": mock_cxxfilt}):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = subprocess.CompletedProcess(
                    args=["c++filt"], returncode=0, stdout="", stderr="",
                )
                result = _mod.demangle("_ZN3foo3barEv")
        assert result is None

    def test_cppfilt_file_not_found(self):
        """When c++filt binary is missing, return None."""
        mock_cxxfilt = MagicMock()
        mock_cxxfilt.demangle.side_effect = RuntimeError("no")
        with patch.dict("sys.modules", {"cxxfilt": mock_cxxfilt}):
            with patch("subprocess.run", side_effect=FileNotFoundError):
                result = _mod.demangle("_ZN3foo3barEv")
        assert result is None

    def test_cppfilt_timeout(self):
        """When c++filt times out, return None."""
        mock_cxxfilt = MagicMock()
        mock_cxxfilt.demangle.side_effect = RuntimeError("no")
        with patch.dict("sys.modules", {"cxxfilt": mock_cxxfilt}):
            with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("c++filt", 5)):
                result = _mod.demangle("_ZN3foo3barEv")
        assert result is None

    def test_warning_emitted_once(self):
        """The 'demangling unavailable' warning fires only once."""
        mock_cxxfilt = MagicMock()
        mock_cxxfilt.demangle.side_effect = RuntimeError("no")
        with patch.dict("sys.modules", {"cxxfilt": mock_cxxfilt}):
            with patch("subprocess.run", side_effect=FileNotFoundError):
                _mod.demangle("_ZN3foo3barEv")
                _mod.demangle.cache_clear()
                _mod.demangle("_ZN3foo3bazEv")
        assert _mod._warned_no_demangler is True


# ── demangle_batch() ────────────────────────────────────────────────────────


class TestDemangleBatch:
    """Tests for the batch demangling function."""

    def test_empty_list(self):
        assert _mod.demangle_batch([]) == {}

    def test_no_cpp_symbols(self):
        assert _mod.demangle_batch(["printf", "strlen", ""]) == {}

    def test_cxxfilt_available_batch(self):
        mock_cxxfilt = MagicMock()
        mock_cxxfilt.demangle.side_effect = lambda s: f"demangled_{s}"
        with patch.dict("sys.modules", {"cxxfilt": mock_cxxfilt}):
            result = _mod.demangle_batch(["_ZN3foo3barEv", "_ZN3baz4quxEv"])
        assert result == {
            "_ZN3foo3barEv": "demangled__ZN3foo3barEv",
            "_ZN3baz4quxEv": "demangled__ZN3baz4quxEv",
        }

    def test_cxxfilt_partial_failure_falls_to_cppfilt(self):
        """When cxxfilt fails on some symbols, c++filt handles the rest."""
        mock_cxxfilt = MagicMock()
        call_count = 0

        def _side_effect(s):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return f"demangled_{s}"
            raise RuntimeError("fail")

        mock_cxxfilt.demangle.side_effect = _side_effect
        with patch.dict("sys.modules", {"cxxfilt": mock_cxxfilt}):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = subprocess.CompletedProcess(
                    args=["c++filt"], returncode=0,
                    stdout="baz::qux()\n", stderr="",
                )
                result = _mod.demangle_batch(["_ZN3foo3barEv", "_ZN3baz4quxEv"])
        assert "_ZN3foo3barEv" in result
        assert result["_ZN3baz4quxEv"] == "baz::qux()"

    def test_cxxfilt_import_error_falls_to_cppfilt(self):
        """When cxxfilt can't be imported, use c++filt for all."""
        with patch.dict("sys.modules", {"cxxfilt": None}):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = subprocess.CompletedProcess(
                    args=["c++filt"], returncode=0,
                    stdout="foo::bar()\n", stderr="",
                )
                result = _mod.demangle_batch(["_ZN3foo3barEv"])
        assert result == {"_ZN3foo3barEv": "foo::bar()"}

    def test_cppfilt_file_not_found_batch(self):
        """When c++filt is missing, batch returns empty for those symbols."""
        with patch.dict("sys.modules", {"cxxfilt": None}):
            with patch("subprocess.run", side_effect=FileNotFoundError):
                result = _mod.demangle_batch(["_ZN3foo3barEv"])
        assert result == {}

    def test_cppfilt_timeout_batch(self):
        with patch.dict("sys.modules", {"cxxfilt": None}):
            with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("c++filt", 30)):
                result = _mod.demangle_batch(["_ZN3foo3barEv"])
        assert result == {}

    def test_cppfilt_non_zero_return_batch(self):
        with patch.dict("sys.modules", {"cxxfilt": None}):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = subprocess.CompletedProcess(
                    args=["c++filt"], returncode=1, stdout="", stderr="err",
                )
                result = _mod.demangle_batch(["_ZN3foo3barEv"])
        assert result == {}

    def test_cppfilt_same_as_input_skipped(self):
        """Symbols that c++filt returns unchanged are excluded."""
        with patch.dict("sys.modules", {"cxxfilt": None}):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = subprocess.CompletedProcess(
                    args=["c++filt"], returncode=0,
                    stdout="_ZN3foo3barEv\n", stderr="",
                )
                result = _mod.demangle_batch(["_ZN3foo3barEv"])
        assert result == {}

    def test_cxxfilt_returns_same_as_input(self):
        """When cxxfilt.demangle returns the same string, push to remaining."""
        mock_cxxfilt = MagicMock()
        mock_cxxfilt.demangle.side_effect = lambda s: s  # return unchanged
        with patch.dict("sys.modules", {"cxxfilt": mock_cxxfilt}):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = subprocess.CompletedProcess(
                    args=["c++filt"], returncode=0,
                    stdout="foo::bar()\n", stderr="",
                )
                result = _mod.demangle_batch(["_ZN3foo3barEv"])
        assert result == {"_ZN3foo3barEv": "foo::bar()"}

    def test_mixed_cpp_and_non_cpp(self):
        """Non-C++ symbols are filtered out from the batch."""
        mock_cxxfilt = MagicMock()
        mock_cxxfilt.demangle.return_value = "foo::bar()"
        with patch.dict("sys.modules", {"cxxfilt": mock_cxxfilt}):
            result = _mod.demangle_batch(["printf", "_ZN3foo3barEv", "", "strlen"])
        assert list(result.keys()) == ["_ZN3foo3barEv"]


# ── base_name() ─────────────────────────────────────────────────────────────


class TestBaseName:
    """Tests for the base_name() helper."""

    def test_plain_c_name(self):
        assert _mod.base_name("add") == "add"

    def test_demangled_qualified(self):
        """When demangle returns a qualified name, extract the last part."""
        with patch.object(_mod, "demangle", return_value="Widget::getValue() const"):
            result = _mod.base_name("_ZNK6Widget8getValueEv")
        assert result == "getValue"

    def test_demangled_no_parens(self):
        with patch.object(_mod, "demangle", return_value="ns::Foo"):
            result = _mod.base_name("_ZN2ns3FooE")
        assert result == "Foo"

    def test_demangle_returns_none(self):
        """When demangle returns None, base_name uses the raw symbol."""
        with patch.object(_mod, "demangle", return_value=None):
            result = _mod.base_name("simple_func")
        assert result == "simple_func"

    def test_no_namespace(self):
        with patch.object(_mod, "demangle", return_value="getValue()"):
            result = _mod.base_name("_Z8getValuev")
        assert result == "getValue"
