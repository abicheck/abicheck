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

    def test_double_underscore_prefix_rejected_by_default(self):
        """Codex review, fresh evidence: a literal ELF export coincidentally
        named like Mach-O-prefixed Itanium mangling (e.g. a hand-written
        assembler alias) must not be silently demangled by a caller that
        never opted into Mach-O-prefix recognition -- `demangle()` is also
        used for correctness-critical matching (`debian_symbols.py`'s
        Debian `.symbols` file generation, `dwarf_snapshot.py`), not only
        report display, and those callers must keep the old, strict,
        unambiguous behavior."""
        mock_cxxfilt = MagicMock()
        mock_cxxfilt.demangle.side_effect = lambda s: f"demangled:{s}"
        with patch.dict("sys.modules", {"cxxfilt": mock_cxxfilt}):
            assert _mod.demangle("__ZN3foo3barEv") is None
        mock_cxxfilt.demangle.assert_not_called()

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

    def test_cppfilt_no_strip_underscore_fallback(self):
        """Darwin c++filt may strip the leading underscore unless told not to."""
        mock_cxxfilt = MagicMock()
        mock_cxxfilt.demangle.side_effect = RuntimeError("no")
        with patch.dict("sys.modules", {"cxxfilt": mock_cxxfilt}):
            with patch("subprocess.run") as mock_run:
                mock_run.side_effect = [
                    subprocess.CompletedProcess(
                        args=["c++filt"],
                        returncode=0,
                        stdout="_ZN3foo3barEv\n",
                        stderr="",
                    ),
                    subprocess.CompletedProcess(
                        args=["c++filt", "--no-strip-underscore"],
                        returncode=0,
                        stdout="foo::bar()\n",
                        stderr="",
                    ),
                ]
                result = _mod.demangle("_ZN3foo3barEv")
        assert result == "foo::bar()"

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

    def test_cppfilt_file_not_found_is_remembered_across_calls(self):
        """Codex review, fresh evidence: once a subprocess.run() call proves
        the c++filt binary itself isn't installed, a later demangle() call
        for a *different* symbol must not re-attempt the same doomed
        subprocess launch -- a large HTML report with no demangler installed
        would otherwise re-launch a fresh subprocess pair per row."""
        mock_cxxfilt = MagicMock()
        mock_cxxfilt.demangle.side_effect = RuntimeError("no")
        with patch.dict("sys.modules", {"cxxfilt": mock_cxxfilt}):
            with patch("subprocess.run", side_effect=FileNotFoundError) as mock_run:
                assert _mod.demangle("_ZN3foo3barEv") is None
                first_call_count = mock_run.call_count
                assert first_call_count > 0
                _mod.demangle.cache_clear()  # bypass the lru_cache, not the fix
                assert _mod.demangle("_ZN3baz4quxEv") is None
        # No new subprocess.run() calls for the second, different symbol.
        assert mock_run.call_count == first_call_count

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

    def test_macho_double_underscore_prefix_via_cxxfilt(self):
        """Codex review, fresh evidence: clang's own `mangledName` carries the
        Mach-O global-symbol prefix on macOS (`__ZN3foo3barEv`, not the plain
        ELF `_ZN3foo3barEv`) -- demangle() must recognize it and strip the
        extra leading underscore before handing it to cxxfilt, which only
        speaks the canonical `_Z...` spelling."""
        mock_cxxfilt = MagicMock()
        mock_cxxfilt.demangle.side_effect = lambda s: f"demangled:{s}"
        with patch.dict("sys.modules", {"cxxfilt": mock_cxxfilt}):
            result = _mod.demangle("__ZN3foo3barEv", accept_macho_prefix=True)
        assert result == "demangled:_ZN3foo3barEv"
        mock_cxxfilt.demangle.assert_called_once_with("_ZN3foo3barEv")

    def test_macho_double_underscore_prefix_via_cppfilt(self):
        mock_cxxfilt = MagicMock()
        mock_cxxfilt.demangle.side_effect = RuntimeError("no")
        with patch.dict("sys.modules", {"cxxfilt": mock_cxxfilt}):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = subprocess.CompletedProcess(
                    args=["c++filt"], returncode=0,
                    stdout="foo::bar()\n", stderr="",
                )
                result = _mod.demangle("__ZN3foo3barEv", accept_macho_prefix=True)
        assert result == "foo::bar()"
        # The canonical (single-underscore) form must reach the subprocess,
        # not the raw Mach-O `__Z...` spelling.
        called_args = mock_run.call_args[0][0]
        assert "_ZN3foo3barEv" in called_args
        assert "__ZN3foo3barEv" not in called_args

    def test_macho_prefixed_malformed_name_via_cppfilt_is_not_demangled(self):
        """Codex review, fresh evidence: c++filt exits 0 and simply echoes
        back its input for a name it can't demangle. Comparing that echo
        against the *original* (double-underscore) symbol instead of the
        canonical (single-underscore) input it was actually given made a
        malformed `__Z...` token that isn't real Itanium mangling silently
        succeed -- the echoed `_ZNOTVALID` never equals the original
        `__ZNOTVALID`, so it read as a real demangling result."""
        mock_cxxfilt = MagicMock()
        mock_cxxfilt.demangle.side_effect = RuntimeError("no")
        with patch.dict("sys.modules", {"cxxfilt": mock_cxxfilt}):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = subprocess.CompletedProcess(
                    args=["c++filt"], returncode=0,
                    stdout="_ZNOTVALID\n", stderr="",
                )
                result = _mod.demangle("__ZNOTVALID", accept_macho_prefix=True)
        assert result is None

    def test_macho_prefixed_malformed_name_via_cxxfilt_is_not_demangled(self):
        """Codex review, fresh evidence: some cxxfilt/__cxa_demangle
        versions return the input unchanged on failure rather than raising
        -- this direct cxxfilt path returned unconditionally, with no
        comparison against `canonical` at all, unlike the batch cxxfilt
        path which already guards this identically."""
        mock_cxxfilt = MagicMock()
        mock_cxxfilt.demangle.side_effect = lambda s: s  # echo back unchanged
        with patch.dict("sys.modules", {"cxxfilt": mock_cxxfilt}):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = subprocess.CompletedProcess(
                    args=["c++filt"], returncode=0,
                    stdout="_ZNOTVALID\n", stderr="",
                )
                result = _mod.demangle("__ZNOTVALID", accept_macho_prefix=True)
        assert result is None


# ── demangle_batch() ────────────────────────────────────────────────────────


class TestDemangleBatch:
    """Tests for the batch demangling function."""

    def test_empty_list(self):
        assert _mod.demangle_batch([]) == {}

    def test_double_underscore_prefix_rejected_by_default(self):
        """Same guard as demangle()'s own -- a caller that doesn't opt into
        Mach-O-prefix recognition must not have a `__Z...`-shaped symbol
        silently demangled."""
        mock_cxxfilt = MagicMock()
        mock_cxxfilt.demangle.side_effect = lambda s: f"demangled:{s}"
        with patch.dict("sys.modules", {"cxxfilt": mock_cxxfilt}):
            assert _mod.demangle_batch(["__ZN3foo3barEv"]) == {}
        mock_cxxfilt.demangle.assert_not_called()

    def test_permissive_cache_entry_does_not_leak_into_a_strict_call(self):
        """Codex review, fresh evidence: once an `accept_macho_prefix=True`
        caller (report rendering) has cached a `__Z...` symbol's demangled
        result, a later *strict* caller (e.g. debian_symbols.py) for the
        identical symbol must still get the old, safe answer -- the
        Itanium-mangled gate runs before any cache lookup, so a stricter
        caller never even consults an entry it wouldn't itself have
        produced."""
        mock_cxxfilt = MagicMock()
        mock_cxxfilt.demangle.side_effect = lambda s: f"demangled:{s}"
        with patch.dict("sys.modules", {"cxxfilt": mock_cxxfilt}):
            permissive = _mod.demangle_batch(
                ["__ZN3foo3barEv"], accept_macho_prefix=True
            )
            assert permissive == {"__ZN3foo3barEv": "demangled:_ZN3foo3barEv"}
            strict = _mod.demangle_batch(["__ZN3foo3barEv"])
        assert strict == {}

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

    def test_cppfilt_file_not_found_is_remembered_across_batch_calls(self):
        """Codex review, fresh evidence: once one demangle_batch() call proves
        c++filt itself isn't installed, a later demangle_batch() call for
        different symbols must not re-attempt the doomed subprocess launch."""
        with patch.dict("sys.modules", {"cxxfilt": None}):
            with patch("subprocess.run", side_effect=FileNotFoundError) as mock_run:
                assert _mod.demangle_batch(["_ZN3foo3barEv"]) == {}
                first_call_count = mock_run.call_count
                assert first_call_count > 0
                assert _mod.demangle_batch(["_ZN3baz4quxEv"]) == {}
        assert mock_run.call_count == first_call_count

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

    def test_cppfilt_batch_no_strip_underscore_fallback(self):
        """Batch demangling also retries with --no-strip-underscore for Darwin."""
        with patch.dict("sys.modules", {"cxxfilt": None}):
            with patch("subprocess.run") as mock_run:
                mock_run.side_effect = [
                    subprocess.CompletedProcess(
                        args=["c++filt"],
                        returncode=0,
                        stdout="_ZN3foo3barEv\n",
                        stderr="",
                    ),
                    subprocess.CompletedProcess(
                        args=["c++filt", "--no-strip-underscore"],
                        returncode=0,
                        stdout="foo::bar()\n",
                        stderr="",
                    ),
                ]
                result = _mod.demangle_batch(["_ZN3foo3barEv"])
        assert result == {"_ZN3foo3barEv": "foo::bar()"}

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

    def test_macho_double_underscore_prefix_via_cxxfilt(self):
        """Codex review, fresh evidence: a batch containing a Mach-O
        `__Z...`-prefixed symbol must be recognized, canonicalized before
        being handed to cxxfilt, and the result keyed by the *original*
        (double-underscore) symbol so callers can look it up unchanged."""
        mock_cxxfilt = MagicMock()
        mock_cxxfilt.demangle.side_effect = lambda s: f"demangled:{s}"
        with patch.dict("sys.modules", {"cxxfilt": mock_cxxfilt}):
            result = _mod.demangle_batch(["__ZN3foo3barEv"], accept_macho_prefix=True)
        assert result == {"__ZN3foo3barEv": "demangled:_ZN3foo3barEv"}
        mock_cxxfilt.demangle.assert_called_once_with("_ZN3foo3barEv")

    def test_macho_double_underscore_prefix_via_cppfilt(self):
        with patch.dict("sys.modules", {"cxxfilt": None}):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = subprocess.CompletedProcess(
                    args=["c++filt"], returncode=0,
                    stdout="foo::bar()\n", stderr="",
                )
                result = _mod.demangle_batch(["__ZN3foo3barEv"], accept_macho_prefix=True)
        assert result == {"__ZN3foo3barEv": "foo::bar()"}
        sent_input = mock_run.call_args[1]["input"]
        assert sent_input == "_ZN3foo3barEv"

    def test_macho_prefixed_malformed_name_is_not_demangled_via_cppfilt(self):
        """Codex review, fresh evidence: `demangle_batch(["__ZNOTVALID"])`
        must not silently succeed. c++filt exits 0 and echoes back its
        input (the *canonical* single-underscore form) for a name it can't
        demangle -- comparing that echo against the original double-
        underscore symbol instead of the canonical input it was actually
        given made this read as a real (and wrong) demangling."""
        with patch.dict("sys.modules", {"cxxfilt": None}):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = subprocess.CompletedProcess(
                    args=["c++filt"], returncode=0,
                    stdout="_ZNOTVALID\n", stderr="",
                )
                result = _mod.demangle_batch(["__ZNOTVALID"], accept_macho_prefix=True)
        assert result == {}

    def test_macho_prefixed_malformed_name_is_not_demangled_via_cxxfilt(self):
        """Same failure mode, one layer up: some cxxfilt/__cxa_demangle
        versions return the input unchanged on failure rather than raising."""
        mock_cxxfilt = MagicMock()
        mock_cxxfilt.demangle.side_effect = lambda s: s  # echo back unchanged
        with patch.dict("sys.modules", {"cxxfilt": mock_cxxfilt}):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = subprocess.CompletedProcess(
                    args=["c++filt"], returncode=0,
                    stdout="_ZNOTVALID\n", stderr="",
                )
                result = _mod.demangle_batch(["__ZNOTVALID"], accept_macho_prefix=True)
        assert result == {}


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


# ── PR #256 review findings ──────────────────────────────────────────────────


class TestFindingA_Phase2BroadExcept:
    """Finding A: _batch_phase2_cxxfilt must catch non-ImportError exceptions
    from the outer 'import cxxfilt' and fall through to phase 3 without
    crashing demangle_batch."""

    def test_non_import_error_from_cxxfilt_import_falls_through_to_phase3(self):
        """A RuntimeError raised at import time must not propagate; phase 3
        (c++filt) must still be reached and return a result."""
        # Simulate an unusual module whose import raises RuntimeError.
        bad_module = MagicMock()
        bad_module.__spec__ = None
        # Patch the *import* of cxxfilt inside the module by making sys.modules
        # hold a broken sentinel; we must also make the import statement itself
        # raise — easiest is to pass a module object whose attribute access
        # raises, which happens when cxxfilt.demangle is called after import.
        # A cleaner approach: patch builtins.__import__ just for 'cxxfilt'.
        import builtins

        real_import = builtins.__import__

        def _bad_import(name, *args, **kwargs):
            if name == "cxxfilt":
                raise RuntimeError("cxxfilt C extension failed to load")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_bad_import):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = subprocess.CompletedProcess(
                    args=["c++filt"], returncode=0,
                    stdout="foo::bar()\n", stderr="",
                )
                # Must not raise; must reach phase 3 and return the c++filt result.
                result = _mod.demangle_batch(["_ZN3foo3barEv"])
        assert result == {"_ZN3foo3barEv": "foo::bar()"}

    def test_non_import_error_does_not_poison_fail_cache(self):
        """After a RuntimeError at cxxfilt import, the symbol must NOT be in
        the FAIL cache — phase 3 handles it and may succeed."""
        import builtins

        real_import = builtins.__import__

        def _bad_import(name, *args, **kwargs):
            if name == "cxxfilt":
                raise RuntimeError("boom")
            return real_import(name, *args, **kwargs)

        sym = "_ZN3foo3barEv"
        with patch("builtins.__import__", side_effect=_bad_import):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = subprocess.CompletedProcess(
                    args=["c++filt"], returncode=0,
                    stdout="foo::bar()\n", stderr="",
                )
                _mod.demangle_batch([sym])

        # Symbol should be in OK cache (phase 3 succeeded), not FAIL cache.
        assert sym not in _mod._BATCH_CACHE_FAIL
        assert sym in _mod._BATCH_CACHE_OK


class TestFindingB_Phase3NoPoisonOnFailure:
    """Finding B: _batch_phase3_cppfilt must NOT record FAIL cache entries
    when c++filt is unavailable, times out, raises OSError, or returns
    non-zero — so a later call can retry."""

    def _sym(self) -> str:
        return "_ZN3foo3barEv"

    def test_file_not_found_does_not_cache_fail(self):
        """Missing c++filt binary: FAIL cache stays empty."""
        sym = self._sym()
        with patch.dict("sys.modules", {"cxxfilt": None}):
            with patch("subprocess.run", side_effect=FileNotFoundError):
                _mod.demangle_batch([sym])
        assert sym not in _mod._BATCH_CACHE_FAIL

    def test_timeout_does_not_cache_fail(self):
        """Timed-out c++filt: FAIL cache stays empty."""
        sym = self._sym()
        with patch.dict("sys.modules", {"cxxfilt": None}):
            with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("c++filt", 30)):
                _mod.demangle_batch([sym])
        assert sym not in _mod._BATCH_CACHE_FAIL

    def test_oserror_does_not_cache_fail(self):
        """OSError from subprocess: FAIL cache stays empty."""
        sym = self._sym()
        with patch.dict("sys.modules", {"cxxfilt": None}):
            with patch("subprocess.run", side_effect=OSError("permission denied")):
                _mod.demangle_batch([sym])
        assert sym not in _mod._BATCH_CACHE_FAIL

    def test_nonzero_returncode_does_not_cache_fail(self):
        """Non-zero returncode: c++filt ran but failed; FAIL cache stays empty."""
        sym = self._sym()
        with patch.dict("sys.modules", {"cxxfilt": None}):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = subprocess.CompletedProcess(
                    args=["c++filt"], returncode=1, stdout="", stderr="error",
                )
                _mod.demangle_batch([sym])
        assert sym not in _mod._BATCH_CACHE_FAIL

    def test_nonzero_returncode_retry_succeeds(self):
        """After a non-zero returncode (no FAIL cache), a second call with a
        working c++filt must succeed."""
        sym = self._sym()
        with patch.dict("sys.modules", {"cxxfilt": None}):
            # First call: c++filt returns non-zero.
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = subprocess.CompletedProcess(
                    args=["c++filt"], returncode=1, stdout="", stderr="error",
                )
                first = _mod.demangle_batch([sym])
            assert first == {}
            assert sym not in _mod._BATCH_CACHE_FAIL

            # Second call: c++filt now works.
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = subprocess.CompletedProcess(
                    args=["c++filt"], returncode=0,
                    stdout="foo::bar()\n", stderr="",
                )
                second = _mod.demangle_batch([sym])
        assert second == {sym: "foo::bar()"}

    def test_success_still_caches_fail_for_unresolved(self):
        """When c++filt succeeds (rc=0) but a symbol is unchanged/blank, it
        IS recorded as FAIL so we don't spawn c++filt for it again."""
        sym = self._sym()
        with patch.dict("sys.modules", {"cxxfilt": None}):
            with patch("subprocess.run") as mock_run:
                # returncode=0 but output equals the mangled name → not demangled.
                mock_run.return_value = subprocess.CompletedProcess(
                    args=["c++filt"], returncode=0,
                    stdout=f"{sym}\n", stderr="",
                )
                _mod.demangle_batch([sym])
        # c++filt ran successfully but couldn't demangle → FAIL cache entry is correct.
        assert sym in _mod._BATCH_CACHE_FAIL


class TestDemangleText:
    """``demangle_text`` rewrites mangled tokens embedded in free-form text,
    leaving prose (and unresolvable tokens) untouched. Deterministic via a
    stubbed batch demangler so it passes where no c++filt/cxxfilt exists."""

    def test_replaces_known_tokens(self, monkeypatch):
        monkeypatch.setattr(
            _mod, "demangle_batch", lambda syms, **kw: {"_Z3foov": "foo()"}
        )
        out = _mod.demangle_text("New public function: _Z3foov; see also _Z3foov.")
        assert out == "New public function: foo(); see also foo()."

    def test_leaves_unresolved_tokens_unchanged(self, monkeypatch):
        monkeypatch.setattr(_mod, "demangle_batch", lambda syms, **kw: {})
        assert _mod.demangle_text("_ZUnresolved stays as-is") == "_ZUnresolved stays as-is"

    def test_noop_and_no_batch_call_without_tokens(self, monkeypatch):
        calls = {"n": 0}

        def _fake(syms, **kw):
            calls["n"] += 1
            return {}

        monkeypatch.setattr(_mod, "demangle_batch", _fake)
        assert _mod.demangle_text("just plain prose, no symbols") == "just plain prose, no symbols"
        assert calls["n"] == 0

    def test_real_demangler_when_available(self):
        # Integration with whatever demangler exists; skip if none is present.
        if _mod.demangle("_Z3foov") is None:
            pytest.skip("no c++filt/cxxfilt demangler available")
        assert "foo()" in _mod.demangle_text("call _Z3foov now")

    def test_macho_double_underscore_token_replaced_whole(self, monkeypatch):
        """Codex review, fresh evidence: matching only the `_Z...` suffix of
        a Mach-O `__Z...` token left the extra leading underscore glued onto
        the demangled text (`_Foo::bar()` instead of `Foo::bar()`). The whole
        `__Z...` span must be captured and replaced as one unit."""
        monkeypatch.setattr(
            _mod, "demangle_batch", lambda syms, **kw: {"__ZN3Foo3barEv": "Foo::bar()"}
        )
        out = _mod.demangle_text("removed: __ZN3Foo3barEv")
        assert out == "removed: Foo::bar()"
        assert "_Foo::bar()" not in out


def test_demangle_reads_warmed_batch_cache(monkeypatch):
    """P11: a name resolved by demangle_batch is served from the shared batch
    cache without demangle() re-forking a subprocess."""
    import abicheck.demangle as dm

    dm.demangle.cache_clear()
    sym = "_ZN3FooEv_p11test"  # synthetic; need not be real Itanium
    dm._BATCH_CACHE_OK[sym] = "Foo::warmed()"

    # Any subprocess use here would be a regression — fail loudly if called.
    def _boom(*a, **k):
        raise AssertionError("demangle() spawned a subprocess despite a warm cache")
    monkeypatch.setattr(dm.subprocess, "run", _boom)

    try:
        assert dm.demangle(sym) == "Foo::warmed()"
    finally:
        dm._BATCH_CACHE_OK.pop(sym, None)
        dm.demangle.cache_clear()


def test_demangle_batch_cache_fail_short_circuits(monkeypatch):
    """A name proven non-demangleable by a batch run returns None without forking."""
    import abicheck.demangle as dm

    dm.demangle.cache_clear()
    sym = "_Znot_really_mangled_p11"
    dm._BATCH_CACHE_FAIL.add(sym)

    def _boom(*a, **k):
        raise AssertionError("demangle() spawned a subprocess for a known-fail name")
    monkeypatch.setattr(dm.subprocess, "run", _boom)

    try:
        assert dm.demangle(sym) is None
    finally:
        dm._BATCH_CACHE_FAIL.discard(sym)
        dm.demangle.cache_clear()


class TestPrewarmDemangleFromJsonValue:
    """`prewarm_demangle_from_json_value` -- ADR-061 Phase 2's HTML closure
    (Codex review, fresh evidence): `render_html_document` can now run
    standalone on a document built or deserialized in an earlier process,
    with no compute-side prewarm ever having populated the cache, so this
    primitive has to find every embeddable symbol *by walking the document's
    own JSON shape* rather than by name-listing fields -- the general
    invariant a single reported field would not have proven."""

    def test_batches_tokens_found_at_every_nesting_depth(self):
        """Tokens live at every JSON shape a real ReportDocument mixes:
        directly under a dict key, inside a list, inside a tuple, and nested
        several dicts deep -- one batched subprocess call must resolve all
        of them, not one per occurrence."""
        value = {
            "top": "_ZN3foo3barEv",
            "rows": [
                {"symbol": "_ZN3baz4quxEv", "old_value": "int"},
                {"affected_symbols": ("_ZN3abc3defEv",)},
            ],
            "nested": {"deeper": {"still": ["_ZN3ghi3jklEv"]}},
            "irrelevant": {"count": 3, "flag": True, "note": None},
        }
        expected = {
            "_ZN3foo3barEv": "foo::bar()",
            "_ZN3baz4quxEv": "baz::qux()",
            "_ZN3abc3defEv": "abc::def()",
            "_ZN3ghi3jklEv": "ghi::jkl()",
        }
        with patch.dict("sys.modules", {"cxxfilt": None}):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = subprocess.CompletedProcess(
                    args=["c++filt"],
                    returncode=0,
                    stdout="\n".join(expected[sym] for sym in sorted(expected)) + "\n",
                    stderr="",
                )
                _mod.prewarm_demangle_from_json_value(value)
                assert mock_run.call_count == 1, (
                    "expected one batched c++filt call for every token found "
                    f"across the tree, got {mock_run.call_count}"
                )

            # Every symbol is now a pure cache hit -- no further subprocess.
            with patch("subprocess.run") as mock_run_after:
                for sym, want in expected.items():
                    assert _mod.demangle(sym, accept_macho_prefix=True) == want
                mock_run_after.assert_not_called()

    def test_no_tokens_makes_no_call(self):
        value = {"a": ["b", "c"], "d": (1, 2, None, True), "e": {"f": "plain text"}}
        with patch.dict("sys.modules", {"cxxfilt": None}):
            with patch("subprocess.run") as mock_run:
                _mod.prewarm_demangle_from_json_value(value)
        mock_run.assert_not_called()

    def test_non_string_scalars_do_not_raise(self):
        """ints/floats/bools/None reach the walk unscathed -- a real document
        carries plenty of them (counts, exit codes, flags) -- and contribute
        no token, so the return value is the same "did nothing" `None` a
        function with no explicit `return` always gives."""
        result = _mod.prewarm_demangle_from_json_value(
            {"n": 3, "f": 1.5, "b": False, "none": None, "empty": {}, "l": []}
        )
        assert result is None
