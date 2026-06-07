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

"""SYCL runtime and plugin metadata extraction.

Extracts metadata from SYCL distributions for ABI compatibility checking:

- Plugin inventory (which backend plugins are shipped)
- Entry points per plugin (exported ``pi*`` or ``ur*`` symbols)
- Interface version detection (from symbol heuristics)
- Plugin search path configuration

Supports two plugin interface generations:

- **PI (Plugin Interface)**: ``libpi_*.so``, entry point ``piPluginInit``,
  symbols prefixed ``pi``.  Used by DPC++ through ~2024.
- **UR (Unified Runtime)**: ``libur_adapter_*.so``, entry point
  ``urAdapterGet``, symbols prefixed ``ur``.  Successor to PI in newer
  DPC++ releases.

Both interfaces use the same detection approach: glob for plugin libraries,
parse ``.dynsym`` via pyelftools, match symbol patterns.  No SYCL compiler
or runtime needed — pure static analysis of ELF binaries.

See ADR-020b for design rationale.
"""
from __future__ import annotations

import logging
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Plugin interface patterns
#
# PI (Plugin Interface): DPC++ legacy — piCamelCase symbols, libpi_*.so
# UR (Unified Runtime):  DPC++ current — urCamelCase symbols, libur_adapter_*.so
# ---------------------------------------------------------------------------
_PI_SYMBOL_RE = re.compile(r"^pi[A-Z]\w+$")
_UR_SYMBOL_RE = re.compile(r"^ur[A-Z]\w+$")

# Library name → (plugin_name, interface_type)
_PI_PLUGIN_NAME_RE = re.compile(r"^libpi_(\w+)\.so")
_UR_PLUGIN_NAME_RE = re.compile(r"^libur_adapter_(\w+)\.so")

# Well-known PI entry points that must be present for a valid PI plugin.
PI_REQUIRED_ENTRYPOINTS: frozenset[str] = frozenset({
    "piPluginInit",
    "piPlatformsGet",
    "piPlatformGetInfo",
    "piDevicesGet",
    "piDeviceGetInfo",
    "piContextCreate",
    "piContextRelease",
    "piQueueCreate",
    "piQueueRelease",
    "piMemBufferCreate",
    "piMemRelease",
    "piProgramCreate",
    "piProgramBuild",
    "piProgramRelease",
    "piKernelCreate",
    "piKernelRelease",
    "piEnqueueKernelLaunch",
    "piEventsWait",
    "piEventRelease",
})

# Well-known UR entry points that must be present for a valid UR adapter.
UR_REQUIRED_ENTRYPOINTS: frozenset[str] = frozenset({
    "urAdapterGet",
    "urAdapterRelease",
    "urPlatformGet",
    "urPlatformGetInfo",
    "urDeviceGet",
    "urDeviceGetInfo",
    "urContextCreate",
    "urContextRelease",
    "urQueueCreate",
    "urQueueRelease",
    "urMemBufferCreate",
    "urMemRelease",
    "urProgramCreateWithIL",
    "urProgramBuild",
    "urProgramRelease",
    "urKernelCreate",
    "urKernelRelease",
    "urEnqueueKernelLaunch",
    "urEventWait",
    "urEventRelease",
})

# Backend type detection from plugin library name.
_BACKEND_MAP: dict[str, str] = {
    "level_zero": "level_zero",
    "opencl": "opencl",
    "cuda": "cuda",
    "hip": "hip",
    "esimd_emulator": "esimd_emulator",
    "native_cpu": "native_cpu",
    "unified_runtime": "unified_runtime",
}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class SyclPluginInfo:
    """Metadata for a single backend plugin (PI or UR)."""

    name: str                           # e.g. "level_zero", "opencl", "cuda"
    library: str                        # e.g. "libpi_level_zero.so"
    interface_type: str = "pi"          # "pi" (Plugin Interface) or "ur" (Unified Runtime)
    pi_version: str = ""                # interface version (if detectable)
    entry_points: list[str] = field(default_factory=list)  # exported pi*/ur* symbols
    backend_type: str = ""              # "level_zero" | "opencl" | "cuda" | ...
    min_driver_version: str | None = None  # minimum backend driver version


@dataclass
class SyclMetadata:
    """SYCL runtime + plugin interface metadata for one distribution."""

    implementation: str = ""            # "dpcpp" | "adaptivecpp" | "computecpp"
    runtime_version: str = ""           # e.g. "2025.2.0"
    pi_version: str = ""                # PI interface version of the runtime
    plugins: list[SyclPluginInfo] = field(default_factory=list)
    plugin_search_paths: list[str] = field(default_factory=list)

    @property
    def plugin_map(self) -> dict[tuple[str, str], SyclPluginInfo]:
        """(interface_type, name) -> SyclPluginInfo lookup.

        Keyed by ``(p.interface_type, p.name)`` so PI and UR plugins
        with the same backend name (e.g. both ``level_zero``) are
        treated as distinct entries.
        """
        return {(p.interface_type, p.name): p for p in self.plugins}


# ---------------------------------------------------------------------------
# Static extraction — uses pyelftools, no SYCL runtime needed
# ---------------------------------------------------------------------------

_HIDDEN_VISIBILITIES = frozenset({"STV_HIDDEN", "STV_INTERNAL"})


def _extract_plugin_symbols(so_path: Path, symbol_re: re.Pattern[str]) -> list[str]:
    """Extract exported symbols matching *symbol_re* from a plugin ``.so``.

    Uses ``.dynsym`` only (not ``.symtab``) to match ``elf_metadata.py``
    behaviour. Filters out hidden/internal symbols. Uses ``os.fstat()``
    after open to prevent TOCTOU attacks (symlink to FIFO/device).

    Returns an empty list on parse errors (logged as WARNING).
    """
    from elftools.common.exceptions import ELFError
    from elftools.elf.elffile import ELFFile
    from elftools.elf.sections import SymbolTableSection

    symbols: list[str] = []
    try:
        with open(so_path, "rb") as f:
            # TOCTOU protection: verify fd is a regular file after open.
            st = os.fstat(f.fileno())
            if not stat.S_ISREG(st.st_mode):
                log.warning("_extract_plugin_symbols: not a regular file: %s", so_path)
                return []
            elf = ELFFile(f)
            for section in elf.iter_sections():
                if not isinstance(section, SymbolTableSection):
                    continue
                # Only process .dynsym — .symtab may contain internal
                # static functions that are not part of the public surface.
                if section.name != ".dynsym":
                    continue
                for sym in section.iter_symbols():
                    name = sym.name
                    if not name:
                        continue
                    info_bind = sym.entry["st_info"]["bind"]
                    shndx = sym.entry["st_shndx"]
                    # Filter: exported (GLOBAL/WEAK), defined (not UND),
                    # and not hidden/internal visibility.
                    if info_bind not in ("STB_GLOBAL", "STB_WEAK"):
                        continue
                    if shndx == "SHN_UNDEF":
                        continue
                    vis = sym.entry["st_other"]["visibility"]
                    if vis in _HIDDEN_VISIBILITIES:
                        continue
                    if symbol_re.match(name):
                        symbols.append(name)
    except (ELFError, OSError, ValueError) as exc:
        log.warning("Failed to extract symbols from %s: %s", so_path, exc)
    return sorted(symbols)


def _detect_backend_type(plugin_name: str) -> str:
    """Map plugin name to backend type string."""
    return _BACKEND_MAP.get(plugin_name, plugin_name)


def _detect_pi_version_from_symbols(symbols: list[str]) -> str:
    """Heuristic PI version detection from exported symbol set.

    PI versions add new entry points over time. This uses landmark
    symbols to estimate the minimum PI version.

    Returns empty string if version cannot be determined.
    """
    # PI 1.2+ added piextUSM* and piextQueue* families
    has_usm = any(s.startswith("piextUSM") for s in symbols)
    has_queue_ext = any(s.startswith("piextQueue") for s in symbols)
    # PI 1.1+ added piextDevice* family
    has_device_ext = any(s.startswith("piextDevice") for s in symbols)

    if has_usm and has_queue_ext:
        return "1.2"
    if has_device_ext:
        return "1.1"
    if "piPluginInit" in symbols:
        return "1.0"
    return ""


def _detect_ur_version_from_symbols(symbols: list[str]) -> str:
    """Heuristic UR version detection from exported symbol set.

    UR versions are detected by presence of landmark entry points
    added in each release.

    Returns empty string if version cannot be determined.
    """
    # UR 0.10+ added urKernelSetArgSampler, urBindlessImages*
    has_bindless = any(s.startswith("urBindlessImages") for s in symbols)
    # UR 0.9+ added urVirtualMem*, urPhysicalMem*
    has_virtual_mem = any(s.startswith("urVirtualMem") for s in symbols)
    # UR 0.8+ added urCommandBuffer*
    has_cmd_buffer = any(s.startswith("urCommandBuffer") for s in symbols)

    if has_bindless:
        return "0.10"
    if has_virtual_mem:
        return "0.9"
    if has_cmd_buffer:
        return "0.8"
    if "urAdapterGet" in symbols:
        return "0.7"
    return ""


def parse_sycl_plugin(so_path: Path) -> SyclPluginInfo | None:
    """Parse a single SYCL plugin .so (PI or UR) and extract metadata.

    Tries PI pattern first (``libpi_*.so``), then UR (``libur_adapter_*.so``).
    Returns None if the file matches neither pattern or lacks the required
    init entry point.
    """
    # Try PI pattern: libpi_<backend>.so
    pi_match = _PI_PLUGIN_NAME_RE.match(so_path.name)
    if pi_match:
        plugin_name = pi_match.group(1)
        entry_points = _extract_plugin_symbols(so_path, _PI_SYMBOL_RE)
        if "piPluginInit" not in entry_points:
            log.warning("Plugin %s missing piPluginInit — not a valid PI plugin", so_path)
            return None
        return SyclPluginInfo(
            name=plugin_name,
            library=so_path.name,
            interface_type="pi",
            pi_version=_detect_pi_version_from_symbols(entry_points),
            entry_points=entry_points,
            backend_type=_detect_backend_type(plugin_name),
        )

    # Try UR pattern: libur_adapter_<backend>.so
    ur_match = _UR_PLUGIN_NAME_RE.match(so_path.name)
    if ur_match:
        plugin_name = ur_match.group(1)
        entry_points = _extract_plugin_symbols(so_path, _UR_SYMBOL_RE)
        if "urAdapterGet" not in entry_points:
            log.warning("Plugin %s missing urAdapterGet — not a valid UR adapter", so_path)
            return None
        return SyclPluginInfo(
            name=plugin_name,
            library=so_path.name,
            interface_type="ur",
            pi_version=_detect_ur_version_from_symbols(entry_points),
            entry_points=entry_points,
            backend_type=_detect_backend_type(plugin_name),
        )

    log.debug("Not a SYCL plugin (name mismatch): %s", so_path.name)
    return None


def _is_plugin_candidate(filename: str) -> bool:
    """Check if a filename matches any known plugin naming pattern."""
    return bool(_PI_PLUGIN_NAME_RE.match(filename) or _UR_PLUGIN_NAME_RE.match(filename))


def discover_sycl_plugins(
    search_paths: list[Path],
) -> list[SyclPluginInfo]:
    """Discover and parse all SYCL plugins (PI and UR) in the given paths.

    Scans directories for files matching ``libpi_*.so`` (PI) or
    ``libur_adapter_*.so`` (UR) and extracts metadata from each.
    """
    plugins: list[SyclPluginInfo] = []
    seen: set[str] = set()

    for search_dir in search_paths:
        if not search_dir.is_dir():
            log.debug("SYCL plugin search path does not exist: %s", search_dir)
            continue
        for entry in sorted(search_dir.iterdir()):
            if not entry.is_file():
                continue
            if entry.name in seen:
                continue
            if not _is_plugin_candidate(entry.name):
                continue
            seen.add(entry.name)
            plugin = parse_sycl_plugin(entry)
            if plugin is not None:
                plugins.append(plugin)

    return plugins


def _detect_sycl_implementation(lib_dir: Path) -> str:
    """Heuristic to detect which SYCL implementation is present."""
    # DPC++ ships libsycl.so alongside libpi_*.so plugins
    if (lib_dir / "libsycl.so").exists() or any(
        lib_dir.glob("libsycl.so.*")
    ):
        return "dpcpp"
    # AdaptiveCpp uses libacpp-rt.so (or versioned libacpp-rt.so.*)
    if (lib_dir / "libacpp-rt.so").exists() or any(
        lib_dir.glob("libacpp-rt.so.*")
    ) or any(
        lib_dir.glob("libhipsycl-rt.so*")
    ):
        return "adaptivecpp"
    return ""


def _default_plugin_search_paths() -> list[Path]:
    """Return default DPC++ plugin search paths from environment.

    DPC++ looks for plugins in:
    1. ``SYCL_PI_PLUGINS_DIR`` — PI plugin directory (legacy)
    2. ``SYCL_UR_ADAPTERS_DIR`` — UR adapter directory (newer DPC++)
    3. ``<libdir>/sycl/`` — relative to ``libsycl.so`` (handled by caller)
    """
    paths: list[Path] = []
    for env_var in ("SYCL_PI_PLUGINS_DIR", "SYCL_UR_ADAPTERS_DIR"):
        val = os.environ.get(env_var)
        if val:
            paths.append(Path(val))
    return paths


def parse_sycl_metadata(
    lib_dir: Path,
    *,
    extra_plugin_paths: list[Path] | None = None,
) -> SyclMetadata | None:
    """Extract SYCL metadata from a distribution directory.

    Args:
        lib_dir: Directory containing libsycl.so and/or PI plugins.
        extra_plugin_paths: Additional directories to scan for plugins.

    Returns:
        SyclMetadata if SYCL artifacts detected, None otherwise.
    """
    implementation = _detect_sycl_implementation(lib_dir)
    if not implementation:
        return None

    # Build plugin search path list
    search_paths = [lib_dir]
    # DPC++ convention: plugins in <libdir>/ or <libdir>/sycl/
    sycl_subdir = lib_dir / "sycl"
    if sycl_subdir.is_dir():
        search_paths.append(sycl_subdir)
    if extra_plugin_paths:
        search_paths.extend(extra_plugin_paths)
    search_paths.extend(_default_plugin_search_paths())

    plugins = discover_sycl_plugins(search_paths)

    # Detect the dominant interface version from plugin versions.
    # Use tuple comparison to avoid lexicographic ordering issues
    # (e.g., "1.10" should be > "1.2").
    pi_versions = [p.pi_version for p in plugins if p.pi_version]
    if pi_versions:
        runtime_pi_version = max(
            pi_versions,
            key=lambda v: tuple(int(x) for x in v.split(".") if x.isdigit()),
        )
    else:
        runtime_pi_version = ""

    return SyclMetadata(
        implementation=implementation,
        pi_version=runtime_pi_version,
        plugins=plugins,
        plugin_search_paths=[str(p) for p in search_paths],
    )
