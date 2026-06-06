# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Tests for abicheck.sycl_metadata — SYCL plugin/runtime metadata extraction.

ELF parsing is exercised with mocked pyelftools (no real binaries needed).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from abicheck.sycl_metadata import (
    _PI_SYMBOL_RE,
    SyclMetadata,
    SyclPluginInfo,
    _default_plugin_search_paths,
    _detect_backend_type,
    _detect_pi_version_from_symbols,
    _detect_sycl_implementation,
    _detect_ur_version_from_symbols,
    _extract_plugin_symbols,
    _is_plugin_candidate,
    discover_sycl_plugins,
    parse_sycl_metadata,
    parse_sycl_plugin,
)

# ---------------------------------------------------------------------------
# ELF mocking helpers
# ---------------------------------------------------------------------------


def _make_sym(name, *, bind="STB_GLOBAL", shndx=1, vis="STV_DEFAULT"):
    sym = MagicMock()
    sym.name = name
    sym.entry = {
        "st_info": {"bind": bind},
        "st_shndx": shndx,
        "st_other": {"visibility": vis},
    }
    return sym


def _make_dynsym(symbols):
    from elftools.elf.sections import SymbolTableSection

    sec = MagicMock(spec=SymbolTableSection)
    sec.name = ".dynsym"
    sec.iter_symbols.return_value = symbols
    return sec


def _patch_elf(sections):
    elf = MagicMock()
    elf.iter_sections.return_value = sections
    return patch("elftools.elf.elffile.ELFFile", return_value=elf)


def _write_elf(tmp_path: Path, name: str) -> Path:
    p = tmp_path / name
    p.write_bytes(b"\x7fELF" + b"\x00" * 60)
    return p


# ---------------------------------------------------------------------------
# _extract_plugin_symbols
# ---------------------------------------------------------------------------


class TestExtractPluginSymbols:
    def test_collects_matching_symbols(self, tmp_path: Path) -> None:
        so = _write_elf(tmp_path, "libpi_cuda.so")
        syms = [_make_sym("piPluginInit"), _make_sym("piDevicesGet")]
        with _patch_elf([_make_dynsym(syms)]):
            result = _extract_plugin_symbols(so, _PI_SYMBOL_RE)
        assert result == ["piDevicesGet", "piPluginInit"]  # sorted

    def test_non_regular_file_returns_empty(self) -> None:
        # /dev/null is a char device — TOCTOU guard rejects it.
        import os

        if not os.path.exists("/dev/null"):
            pytest.skip("/dev/null missing")
        assert _extract_plugin_symbols(Path("/dev/null"), _PI_SYMBOL_RE) == []

    def test_skips_non_symbol_table_section(self, tmp_path: Path) -> None:
        so = _write_elf(tmp_path, "libpi_cuda.so")
        plain = MagicMock()  # not a SymbolTableSection
        with _patch_elf([plain]):
            assert _extract_plugin_symbols(so, _PI_SYMBOL_RE) == []

    def test_skips_non_dynsym_table(self, tmp_path: Path) -> None:
        from elftools.elf.sections import SymbolTableSection

        so = _write_elf(tmp_path, "libpi_cuda.so")
        sec = MagicMock(spec=SymbolTableSection)
        sec.name = ".symtab"
        sec.iter_symbols.return_value = [_make_sym("piPluginInit")]
        with _patch_elf([sec]):
            assert _extract_plugin_symbols(so, _PI_SYMBOL_RE) == []

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"bind": "STB_LOCAL"},
            {"shndx": "SHN_UNDEF"},
            {"vis": "STV_HIDDEN"},
            {"vis": "STV_INTERNAL"},
        ],
    )
    def test_filtered_symbols(self, tmp_path: Path, kwargs) -> None:
        so = _write_elf(tmp_path, "libpi_cuda.so")
        with _patch_elf([_make_dynsym([_make_sym("piPluginInit", **kwargs)])]):
            assert _extract_plugin_symbols(so, _PI_SYMBOL_RE) == []

    def test_empty_name_skipped(self, tmp_path: Path) -> None:
        so = _write_elf(tmp_path, "libpi_cuda.so")
        with _patch_elf([_make_dynsym([_make_sym("")])]):
            assert _extract_plugin_symbols(so, _PI_SYMBOL_RE) == []

    def test_non_matching_pattern_skipped(self, tmp_path: Path) -> None:
        so = _write_elf(tmp_path, "libpi_cuda.so")
        with _patch_elf([_make_dynsym([_make_sym("not_a_pi_symbol")])]):
            assert _extract_plugin_symbols(so, _PI_SYMBOL_RE) == []

    def test_parse_error_returns_empty(self, tmp_path: Path) -> None:
        from elftools.common.exceptions import ELFError

        so = _write_elf(tmp_path, "libpi_cuda.so")
        with patch("elftools.elf.elffile.ELFFile", side_effect=ELFError("bad")):
            assert _extract_plugin_symbols(so, _PI_SYMBOL_RE) == []


# ---------------------------------------------------------------------------
# version + backend detection helpers
# ---------------------------------------------------------------------------


class TestVersionDetection:
    def test_pi_1_2(self) -> None:
        assert (
            _detect_pi_version_from_symbols(["piextUSMHostAlloc", "piextQueueCreate"])
            == "1.2"
        )

    def test_pi_1_1(self) -> None:
        assert _detect_pi_version_from_symbols(["piextDeviceSelect"]) == "1.1"

    def test_pi_1_0(self) -> None:
        assert _detect_pi_version_from_symbols(["piPluginInit"]) == "1.0"

    def test_pi_unknown(self) -> None:
        assert _detect_pi_version_from_symbols(["piRandom"]) == ""

    def test_ur_0_10(self) -> None:
        assert _detect_ur_version_from_symbols(["urBindlessImagesCreate"]) == "0.10"

    def test_ur_0_9(self) -> None:
        assert _detect_ur_version_from_symbols(["urVirtualMemReserve"]) == "0.9"

    def test_ur_0_8(self) -> None:
        assert _detect_ur_version_from_symbols(["urCommandBufferCreateExp"]) == "0.8"

    def test_ur_0_7(self) -> None:
        assert _detect_ur_version_from_symbols(["urAdapterGet"]) == "0.7"

    def test_ur_unknown(self) -> None:
        assert _detect_ur_version_from_symbols(["urRandom"]) == ""

    def test_backend_known(self) -> None:
        assert _detect_backend_type("cuda") == "cuda"

    def test_backend_unknown_passthrough(self) -> None:
        assert _detect_backend_type("weird") == "weird"


# ---------------------------------------------------------------------------
# parse_sycl_plugin
# ---------------------------------------------------------------------------


class TestParseSyclPlugin:
    def test_pi_plugin(self, tmp_path: Path) -> None:
        so = _write_elf(tmp_path, "libpi_cuda.so")
        with patch(
            "abicheck.sycl_metadata._extract_plugin_symbols",
            return_value=["piPluginInit", "piextUSMHostAlloc", "piextQueueCreate"],
        ):
            plugin = parse_sycl_plugin(so)
        assert plugin is not None
        assert plugin.name == "cuda"
        assert plugin.interface_type == "pi"
        assert plugin.pi_version == "1.2"
        assert plugin.backend_type == "cuda"

    def test_pi_plugin_missing_init(self, tmp_path: Path) -> None:
        so = _write_elf(tmp_path, "libpi_cuda.so")
        with patch(
            "abicheck.sycl_metadata._extract_plugin_symbols", return_value=["piFoo"]
        ):
            assert parse_sycl_plugin(so) is None

    def test_ur_plugin(self, tmp_path: Path) -> None:
        so = _write_elf(tmp_path, "libur_adapter_level_zero.so")
        with patch(
            "abicheck.sycl_metadata._extract_plugin_symbols",
            return_value=["urAdapterGet", "urVirtualMemReserve"],
        ):
            plugin = parse_sycl_plugin(so)
        assert plugin is not None
        assert plugin.name == "level_zero"
        assert plugin.interface_type == "ur"
        assert plugin.pi_version == "0.9"

    def test_ur_plugin_missing_adapter_get(self, tmp_path: Path) -> None:
        so = _write_elf(tmp_path, "libur_adapter_opencl.so")
        with patch(
            "abicheck.sycl_metadata._extract_plugin_symbols", return_value=["urFoo"]
        ):
            assert parse_sycl_plugin(so) is None

    def test_non_plugin_name(self, tmp_path: Path) -> None:
        so = _write_elf(tmp_path, "libsomething.so")
        assert parse_sycl_plugin(so) is None


# ---------------------------------------------------------------------------
# discovery / candidate matching
# ---------------------------------------------------------------------------


class TestPluginCandidate:
    @pytest.mark.parametrize(
        "name,expected",
        [
            ("libpi_cuda.so", True),
            ("libur_adapter_opencl.so", True),
            ("libsycl.so", False),
            ("random.txt", False),
        ],
    )
    def test_is_candidate(self, name, expected) -> None:
        assert _is_plugin_candidate(name) is expected


class TestDiscoverSyclPlugins:
    def test_discovers_and_dedups(self, tmp_path: Path) -> None:
        d1 = tmp_path / "a"
        d2 = tmp_path / "b"
        d1.mkdir()
        d2.mkdir()
        (d1 / "libpi_cuda.so").write_bytes(b"\x7fELF")
        (d2 / "libpi_cuda.so").write_bytes(b"\x7fELF")  # duplicate name
        (d1 / "notaplugin.so").write_bytes(b"x")

        fake = SyclPluginInfo(name="cuda", library="libpi_cuda.so")
        with patch(
            "abicheck.sycl_metadata.parse_sycl_plugin", return_value=fake
        ) as mock_parse:
            plugins = discover_sycl_plugins([d1, d2])
        # Deduplicated by filename → parsed only once.
        assert len(plugins) == 1
        assert mock_parse.call_count == 1

    def test_skips_missing_dir(self, tmp_path: Path) -> None:
        assert discover_sycl_plugins([tmp_path / "nope"]) == []

    def test_skips_subdirectories(self, tmp_path: Path) -> None:
        # An entry that is a directory (not a file) is skipped.
        (tmp_path / "libpi_cuda.so").mkdir()
        assert discover_sycl_plugins([tmp_path]) == []

    def test_parse_returns_none_excluded(self, tmp_path: Path) -> None:
        (tmp_path / "libpi_cuda.so").write_bytes(b"\x7fELF")
        with patch("abicheck.sycl_metadata.parse_sycl_plugin", return_value=None):
            assert discover_sycl_plugins([tmp_path]) == []


# ---------------------------------------------------------------------------
# _detect_sycl_implementation / _default_plugin_search_paths
# ---------------------------------------------------------------------------


class TestDetectImplementation:
    def test_dpcpp_libsycl(self, tmp_path: Path) -> None:
        (tmp_path / "libsycl.so").write_bytes(b"x")
        assert _detect_sycl_implementation(tmp_path) == "dpcpp"

    def test_dpcpp_versioned(self, tmp_path: Path) -> None:
        (tmp_path / "libsycl.so.7").write_bytes(b"x")
        assert _detect_sycl_implementation(tmp_path) == "dpcpp"

    def test_adaptivecpp(self, tmp_path: Path) -> None:
        (tmp_path / "libacpp-rt.so").write_bytes(b"x")
        assert _detect_sycl_implementation(tmp_path) == "adaptivecpp"

    def test_adaptivecpp_hipsycl(self, tmp_path: Path) -> None:
        (tmp_path / "libhipsycl-rt.so.1").write_bytes(b"x")
        assert _detect_sycl_implementation(tmp_path) == "adaptivecpp"

    def test_none(self, tmp_path: Path) -> None:
        assert _detect_sycl_implementation(tmp_path) == ""


class TestDefaultPluginSearchPaths:
    def test_from_env(self, monkeypatch) -> None:
        monkeypatch.setenv("SYCL_PI_PLUGINS_DIR", "/pi/dir")
        monkeypatch.setenv("SYCL_UR_ADAPTERS_DIR", "/ur/dir")
        paths = _default_plugin_search_paths()
        assert Path("/pi/dir") in paths
        assert Path("/ur/dir") in paths

    def test_empty_when_unset(self, monkeypatch) -> None:
        monkeypatch.delenv("SYCL_PI_PLUGINS_DIR", raising=False)
        monkeypatch.delenv("SYCL_UR_ADAPTERS_DIR", raising=False)
        assert _default_plugin_search_paths() == []


# ---------------------------------------------------------------------------
# parse_sycl_metadata (top-level)
# ---------------------------------------------------------------------------


class TestParseSyclMetadata:
    def test_no_implementation_returns_none(self, tmp_path: Path) -> None:
        assert parse_sycl_metadata(tmp_path) is None

    def test_full_metadata(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.delenv("SYCL_PI_PLUGINS_DIR", raising=False)
        monkeypatch.delenv("SYCL_UR_ADAPTERS_DIR", raising=False)
        (tmp_path / "libsycl.so").write_bytes(b"x")
        sycl_sub = tmp_path / "sycl"
        sycl_sub.mkdir()
        extra = tmp_path / "extra"
        extra.mkdir()

        plugins = [
            SyclPluginInfo(name="cuda", library="libpi_cuda.so", pi_version="1.2"),
            SyclPluginInfo(name="opencl", library="libpi_opencl.so", pi_version="1.10"),
        ]
        with patch(
            "abicheck.sycl_metadata.discover_sycl_plugins", return_value=plugins
        ):
            meta = parse_sycl_metadata(tmp_path, extra_plugin_paths=[extra])
        assert meta is not None
        assert meta.implementation == "dpcpp"
        # Tuple-based max: "1.10" > "1.2".
        assert meta.pi_version == "1.10"
        assert str(sycl_sub) in meta.plugin_search_paths
        assert str(extra) in meta.plugin_search_paths

    def test_no_versions(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.delenv("SYCL_PI_PLUGINS_DIR", raising=False)
        monkeypatch.delenv("SYCL_UR_ADAPTERS_DIR", raising=False)
        (tmp_path / "libsycl.so").write_bytes(b"x")
        plugins = [SyclPluginInfo(name="cuda", library="libpi_cuda.so", pi_version="")]
        with patch(
            "abicheck.sycl_metadata.discover_sycl_plugins", return_value=plugins
        ):
            meta = parse_sycl_metadata(tmp_path)
        assert meta is not None
        assert meta.pi_version == ""


class TestSyclMetadataModel:
    def test_plugin_map_keys(self) -> None:
        pi = SyclPluginInfo(name="cuda", library="libpi_cuda.so", interface_type="pi")
        ur = SyclPluginInfo(
            name="cuda", library="libur_adapter_cuda.so", interface_type="ur"
        )
        meta = SyclMetadata(plugins=[pi, ur])
        m = meta.plugin_map
        assert m[("pi", "cuda")] is pi
        assert m[("ur", "cuda")] is ur
