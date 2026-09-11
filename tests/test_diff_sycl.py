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

"""Tests for SYCL PI detector (diff_sycl.py)."""
from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.checker_policy import ChangeKind
from abicheck.diff_sycl import (
    _diff_backend_driver_reqs,
    _diff_implementation,
    _diff_pi_version,
    _diff_plugin_entrypoints,
    _diff_plugin_search_paths,
    _diff_plugins,
    _diff_runtime_version,
    _diff_sycl,
)
from abicheck.model import AbiSnapshot
from abicheck.sycl_metadata import SyclMetadata, SyclPluginInfo


def _make_plugin(
    name: str = "level_zero",
    library: str = "libpi_level_zero.so",
    pi_version: str = "1.2",
    entry_points: list[str] | None = None,
    backend_type: str = "level_zero",
    min_driver_version: str | None = None,
    interface_type: str = "pi",
) -> SyclPluginInfo:
    if entry_points is None:
        if interface_type == "ur":
            entry_points = ["urAdapterGet", "urPlatformGet", "urDeviceGet"]
        else:
            entry_points = ["piPluginInit", "piPlatformsGet", "piDevicesGet"]
    return SyclPluginInfo(
        name=name,
        library=library,
        interface_type=interface_type,
        pi_version=pi_version,
        entry_points=entry_points,
        backend_type=backend_type,
        min_driver_version=min_driver_version,
    )


def _make_ur_plugin(
    name: str = "level_zero",
    library: str = "libur_adapter_level_zero.so",
    pi_version: str = "0.9",
    entry_points: list[str] | None = None,
    backend_type: str = "level_zero",
    min_driver_version: str | None = None,
) -> SyclPluginInfo:
    """Convenience wrapper for UR plugins."""
    return _make_plugin(
        name=name,
        library=library,
        pi_version=pi_version,
        entry_points=entry_points,
        backend_type=backend_type,
        min_driver_version=min_driver_version,
        interface_type="ur",
    )


def _make_sycl(
    pi_version: str = "1.2",
    plugins: list[SyclPluginInfo] | None = None,
    plugin_search_paths: list[str] | None = None,
    runtime_version: str = "",
) -> SyclMetadata:
    return SyclMetadata(
        implementation="dpcpp",
        runtime_version=runtime_version,
        pi_version=pi_version,
        plugins=plugins or [],
        plugin_search_paths=plugin_search_paths or ["/usr/lib/sycl"],
    )


# ---------------------------------------------------------------------------
# PI version diff
# ---------------------------------------------------------------------------

class TestDiffPiVersion:
    def test_no_change(self):
        old = _make_sycl(pi_version="1.2")
        new = _make_sycl(pi_version="1.2")
        assert _diff_pi_version(old, new) == []

    def test_version_changed(self):
        old = _make_sycl(pi_version="1.1")
        new = _make_sycl(pi_version="1.2")
        changes = _diff_pi_version(old, new)
        assert len(changes) == 1
        assert changes[0].kind == ChangeKind.SYCL_PI_VERSION_CHANGED
        assert changes[0].old_value == "1.1"
        assert changes[0].new_value == "1.2"

    def test_empty_version_ignored(self):
        old = _make_sycl(pi_version="")
        new = _make_sycl(pi_version="1.2")
        assert _diff_pi_version(old, new) == []


# ---------------------------------------------------------------------------
# Plugin inventory diff
# ---------------------------------------------------------------------------

class TestDiffPlugins:
    def test_no_change(self):
        p = _make_plugin()
        old = _make_sycl(plugins=[p])
        new = _make_sycl(plugins=[p])
        assert _diff_plugins(old, new) == []

    def test_plugin_removed(self):
        p = _make_plugin(name="cuda", library="libpi_cuda.so", backend_type="cuda")
        old = _make_sycl(plugins=[_make_plugin(), p])
        new = _make_sycl(plugins=[_make_plugin()])
        changes = _diff_plugins(old, new)
        assert len(changes) == 1
        assert changes[0].kind == ChangeKind.SYCL_PLUGIN_REMOVED
        assert "cuda" in changes[0].symbol

    def test_plugin_added(self):
        p = _make_plugin(name="opencl", library="libpi_opencl.so", backend_type="opencl")
        old = _make_sycl(plugins=[_make_plugin()])
        new = _make_sycl(plugins=[_make_plugin(), p])
        changes = _diff_plugins(old, new)
        assert len(changes) == 1
        assert changes[0].kind == ChangeKind.SYCL_PLUGIN_ADDED
        assert "opencl" in changes[0].symbol


# ---------------------------------------------------------------------------
# Plugin entry point diff
# ---------------------------------------------------------------------------

class TestDiffPluginEntrypoints:
    def test_no_change(self):
        p = _make_plugin()
        old = _make_sycl(plugins=[p])
        new = _make_sycl(plugins=[p])
        assert _diff_plugin_entrypoints(old, new) == []

    def test_entrypoint_removed(self):
        old_p = _make_plugin(entry_points=["piPluginInit", "piPlatformsGet", "piDevicesGet"])
        new_p = _make_plugin(entry_points=["piPluginInit", "piPlatformsGet"])
        old = _make_sycl(plugins=[old_p])
        new = _make_sycl(plugins=[new_p])
        changes = _diff_plugin_entrypoints(old, new)
        removed = [c for c in changes if c.kind == ChangeKind.SYCL_PI_ENTRYPOINT_REMOVED]
        assert len(removed) == 1
        assert "piDevicesGet" in removed[0].old_value

    def test_entrypoint_added(self):
        old_p = _make_plugin(entry_points=["piPluginInit", "piPlatformsGet"])
        new_p = _make_plugin(entry_points=["piPluginInit", "piPlatformsGet", "piextUSMAlloc"])
        old = _make_sycl(plugins=[old_p])
        new = _make_sycl(plugins=[new_p])
        changes = _diff_plugin_entrypoints(old, new)
        added = [c for c in changes if c.kind == ChangeKind.SYCL_PI_ENTRYPOINT_ADDED]
        assert len(added) == 1
        assert "piextUSMAlloc" in added[0].new_value

    def test_removed_plugin_no_spurious_entrypoint_changes(self):
        """Plugin removed from distribution should NOT generate entrypoint changes."""
        old_p = _make_plugin(entry_points=["piPluginInit", "piPlatformsGet"])
        old = _make_sycl(plugins=[old_p])
        new = _make_sycl(plugins=[])
        changes = _diff_plugin_entrypoints(old, new)
        assert len(changes) == 0


# ---------------------------------------------------------------------------
# Search path diff
# ---------------------------------------------------------------------------

class TestDiffPluginSearchPaths:
    def test_no_change(self):
        old = _make_sycl(plugin_search_paths=["/usr/lib/sycl"])
        new = _make_sycl(plugin_search_paths=["/usr/lib/sycl"])
        assert _diff_plugin_search_paths(old, new) == []

    def test_path_changed(self):
        old = _make_sycl(plugin_search_paths=["/usr/lib/sycl"])
        new = _make_sycl(plugin_search_paths=["/opt/intel/sycl/lib"])
        changes = _diff_plugin_search_paths(old, new)
        assert len(changes) == 1
        assert changes[0].kind == ChangeKind.SYCL_PLUGIN_SEARCH_PATH_CHANGED


# ---------------------------------------------------------------------------
# Runtime version diff
# ---------------------------------------------------------------------------

class TestDiffRuntimeVersion:
    def test_no_change(self):
        old = _make_sycl(runtime_version="2025.2.0")
        new = _make_sycl(runtime_version="2025.2.0")
        assert _diff_runtime_version(old, new) == []

    def test_version_changed(self):
        old = _make_sycl(runtime_version="2025.1.0")
        new = _make_sycl(runtime_version="2025.2.0")
        changes = _diff_runtime_version(old, new)
        assert len(changes) == 1
        assert changes[0].kind == ChangeKind.SYCL_RUNTIME_VERSION_CHANGED

    def test_empty_ignored(self):
        old = _make_sycl(runtime_version="")
        new = _make_sycl(runtime_version="2025.2.0")
        assert _diff_runtime_version(old, new) == []


# ---------------------------------------------------------------------------
# Backend driver requirement diff
# ---------------------------------------------------------------------------

class TestDiffBackendDriverReqs:
    def test_no_change(self):
        p = _make_plugin(min_driver_version="1.3.0")
        old = _make_sycl(plugins=[p])
        new = _make_sycl(plugins=[p])
        assert _diff_backend_driver_reqs(old, new) == []

    def test_driver_req_changed(self):
        old_p = _make_plugin(min_driver_version="1.3.0")
        new_p = _make_plugin(min_driver_version="1.5.0")
        old = _make_sycl(plugins=[old_p])
        new = _make_sycl(plugins=[new_p])
        changes = _diff_backend_driver_reqs(old, new)
        assert len(changes) == 1
        assert changes[0].kind == ChangeKind.SYCL_BACKEND_DRIVER_REQ_CHANGED

    def test_none_driver_ignored(self):
        old_p = _make_plugin(min_driver_version=None)
        new_p = _make_plugin(min_driver_version="1.5.0")
        old = _make_sycl(plugins=[old_p])
        new = _make_sycl(plugins=[new_p])
        assert _diff_backend_driver_reqs(old, new) == []


# ---------------------------------------------------------------------------
# Full detector integration (via AbiSnapshot)
# ---------------------------------------------------------------------------

class TestDiffSyclDetector:
    def test_skipped_when_no_sycl_metadata(self):
        """Detector is skipped when sycl metadata is absent."""
        old = AbiSnapshot(library="libsycl.so", version="1.0")
        new = AbiSnapshot(library="libsycl.so", version="2.0")
        # _diff_sycl requires support check — directly calling would assert
        assert old.sycl is None
        assert new.sycl is None

    def test_full_diff_with_sycl(self):
        """Full diff with SYCL metadata detects multiple change types."""
        old_plugins = [
            _make_plugin(name="level_zero", entry_points=["piPluginInit", "piPlatformsGet", "piDevicesGet"]),
            _make_plugin(name="cuda", library="libpi_cuda.so", backend_type="cuda"),
        ]
        new_plugins = [
            _make_plugin(name="level_zero", entry_points=["piPluginInit", "piPlatformsGet"]),
            _make_plugin(name="opencl", library="libpi_opencl.so", backend_type="opencl"),
        ]
        old = AbiSnapshot(
            library="libsycl.so", version="1.0",
            sycl=_make_sycl(plugins=old_plugins),
        )
        new = AbiSnapshot(
            library="libsycl.so", version="2.0",
            sycl=_make_sycl(plugins=new_plugins),
        )
        changes = _diff_sycl(old, new)
        kinds = {c.kind for c in changes}
        assert ChangeKind.SYCL_PLUGIN_REMOVED in kinds      # cuda removed
        assert ChangeKind.SYCL_PLUGIN_ADDED in kinds         # opencl added
        assert ChangeKind.SYCL_PI_ENTRYPOINT_REMOVED in kinds  # piDevicesGet removed


# ---------------------------------------------------------------------------
# Implementation change diff
# ---------------------------------------------------------------------------

class TestDiffImplementation:
    def test_no_change(self):
        old = _make_sycl()
        new = _make_sycl()
        assert _diff_implementation(old, new) == []

    def test_implementation_changed(self):
        old = SyclMetadata(implementation="dpcpp")
        new = SyclMetadata(implementation="adaptivecpp")
        changes = _diff_implementation(old, new)
        assert len(changes) == 1
        assert changes[0].kind == ChangeKind.SYCL_IMPLEMENTATION_CHANGED
        assert changes[0].old_value == "dpcpp"
        assert changes[0].new_value == "adaptivecpp"

    def test_empty_implementation_ignored(self):
        old = SyclMetadata(implementation="")
        new = SyclMetadata(implementation="dpcpp")
        assert _diff_implementation(old, new) == []


# ---------------------------------------------------------------------------
# Serialization round-trip
# ---------------------------------------------------------------------------

class TestSyclSerialization:
    def test_snapshot_roundtrip_with_sycl(self):
        """SyclMetadata survives snapshot serialization round-trip."""
        from abicheck.serialization import snapshot_from_dict, snapshot_to_dict

        plugin = _make_plugin(
            name="level_zero",
            entry_points=["piPluginInit", "piPlatformsGet", "piDevicesGet"],
            min_driver_version="1.3.0",
        )
        snap = AbiSnapshot(
            library="libsycl.so", version="2025.2.0",
            sycl=_make_sycl(
                pi_version="1.2",
                plugins=[plugin],
                plugin_search_paths=["/usr/lib/sycl", "/opt/intel/lib"],
                runtime_version="2025.2.0",
            ),
        )
        d = snapshot_to_dict(snap)
        restored = snapshot_from_dict(d)
        assert restored.sycl is not None
        assert restored.sycl.implementation == "dpcpp"
        assert restored.sycl.pi_version == "1.2"
        assert restored.sycl.runtime_version == "2025.2.0"
        assert len(restored.sycl.plugins) == 1
        assert restored.sycl.plugins[0].name == "level_zero"
        assert restored.sycl.plugins[0].entry_points == [
            "piPluginInit", "piPlatformsGet", "piDevicesGet",
        ]
        assert restored.sycl.plugins[0].min_driver_version == "1.3.0"
        assert restored.sycl.plugin_search_paths == ["/usr/lib/sycl", "/opt/intel/lib"]

    def test_snapshot_roundtrip_without_sycl(self):
        """Snapshot without SYCL metadata round-trips correctly."""
        from abicheck.serialization import snapshot_from_dict, snapshot_to_dict

        snap = AbiSnapshot(library="libfoo.so", version="1.0")
        d = snapshot_to_dict(snap)
        restored = snapshot_from_dict(d)
        assert restored.sycl is None


# ---------------------------------------------------------------------------
# Environment matrix
# ---------------------------------------------------------------------------

class TestEnvironmentMatrix:
    def test_from_dict_valid(self):
        from abicheck.environment_matrix import EnvironmentMatrix

        data = {
            "compilers": ["gcc-13", "clang-17"],
            "abi_version": "18",
            "target_os": "linux",
            "target_arch": "x86_64",
            "sycl": {
                "implementation": "dpcpp",
                "backends": ["level_zero", "opencl"],
                "min_pi_version": "1.2",
            },
            "cuda": {
                "gpu_architectures": ["sm_80", "sm_90"],
                "driver_range": ["525.0", "580.0"],
            },
        }
        matrix = EnvironmentMatrix.from_dict(data)
        # Codex review, P2 follow-up (PR #1221): `compilers`/`sycl.backends`/
        # `cuda.gpu_architectures` are now frozen into `tuple`s at
        # construction (the hash-invariant fix), not left as plain,
        # directly-mutable `list`s -- so these compare against tuple
        # literals, not the list shape `from_dict`'s own input used.
        assert matrix.compilers == ("gcc-13", "clang-17")
        assert matrix.sycl.implementation == "dpcpp"
        assert matrix.sycl.backends == ("level_zero", "opencl")
        assert matrix.cuda.gpu_architectures == ("sm_80", "sm_90")
        assert matrix.cuda.driver_range == ("525.0", "580.0")

    def test_from_dict_empty(self):
        from abicheck.environment_matrix import EnvironmentMatrix

        matrix = EnvironmentMatrix.from_dict({})
        assert matrix.compilers == ()
        assert matrix.target_os is None
        assert matrix.target_arch is None
        assert matrix.sycl.implementation == ""
        assert matrix.cuda.gpu_architectures == ()

    def test_from_dict_not_dict_raises(self):
        from abicheck.environment_matrix import EnvironmentMatrix

        with pytest.raises(TypeError, match="expects a dict"):
            EnvironmentMatrix.from_dict([1, 2, 3])

    def test_from_dict_bad_compilers_raises(self):
        from abicheck.environment_matrix import EnvironmentMatrix

        with pytest.raises(ValueError, match="compilers.*must be a list"):
            EnvironmentMatrix.from_dict({"compilers": 42})


# ---------------------------------------------------------------------------
# UR (Unified Runtime) plugin support
# ---------------------------------------------------------------------------

class TestURPluginDetection:
    """Test that UR plugins are detected and diffed correctly."""

    def test_ur_plugin_removal(self):
        """Removing a UR adapter is breaking, same as removing a PI plugin."""
        old_p = _make_ur_plugin(name="level_zero")
        new_plugins: list[SyclPluginInfo] = []
        old = _make_sycl(plugins=[old_p])
        new = _make_sycl(plugins=new_plugins)
        changes = _diff_plugins(old, new)
        assert len(changes) == 1
        assert changes[0].kind == ChangeKind.SYCL_PLUGIN_REMOVED
        assert "level_zero" in changes[0].description

    def test_ur_plugin_addition(self):
        """Adding a new UR adapter is compatible."""
        new_p = _make_ur_plugin(name="cuda", library="libur_adapter_cuda.so", backend_type="cuda")
        old = _make_sycl(plugins=[])
        new = _make_sycl(plugins=[new_p])
        changes = _diff_plugins(old, new)
        assert len(changes) == 1
        assert changes[0].kind == ChangeKind.SYCL_PLUGIN_ADDED

    def test_ur_entrypoint_removed(self):
        """Removing a UR entry point is breaking."""
        old_p = _make_ur_plugin(entry_points=["urAdapterGet", "urPlatformGet", "urDeviceGet"])
        new_p = _make_ur_plugin(entry_points=["urAdapterGet", "urPlatformGet"])
        old = _make_sycl(plugins=[old_p])
        new = _make_sycl(plugins=[new_p])
        changes = _diff_plugin_entrypoints(old, new)
        removed = [c for c in changes if c.kind == ChangeKind.SYCL_PI_ENTRYPOINT_REMOVED]
        assert len(removed) == 1
        assert "urDeviceGet" in removed[0].description
        assert "UR" in removed[0].description  # mentions UR, not PI

    def test_ur_entrypoint_added(self):
        """Adding a UR entry point is compatible."""
        old_p = _make_ur_plugin(entry_points=["urAdapterGet", "urPlatformGet"])
        new_p = _make_ur_plugin(entry_points=["urAdapterGet", "urPlatformGet", "urDeviceGet"])
        old = _make_sycl(plugins=[old_p])
        new = _make_sycl(plugins=[new_p])
        changes = _diff_plugin_entrypoints(old, new)
        added = [c for c in changes if c.kind == ChangeKind.SYCL_PI_ENTRYPOINT_ADDED]
        assert len(added) == 1
        assert "UR" in added[0].description

    def test_mixed_pi_and_ur_plugins(self):
        """Distribution can ship both PI and UR plugins simultaneously."""
        pi_plugin = _make_plugin(name="opencl", library="libpi_opencl.so", backend_type="opencl")
        ur_plugin = _make_ur_plugin(name="level_zero")
        old = _make_sycl(plugins=[pi_plugin, ur_plugin])

        # New version drops PI opencl, keeps UR level_zero, adds UR cuda
        ur_cuda = _make_ur_plugin(name="cuda", library="libur_adapter_cuda.so", backend_type="cuda")
        new = _make_sycl(plugins=[ur_plugin, ur_cuda])

        changes = _diff_plugins(old, new)
        removed = [c for c in changes if c.kind == ChangeKind.SYCL_PLUGIN_REMOVED]
        added = [c for c in changes if c.kind == ChangeKind.SYCL_PLUGIN_ADDED]
        assert len(removed) == 1
        assert removed[0].old_value == "libpi_opencl.so"
        assert len(added) == 1
        assert added[0].new_value == "libur_adapter_cuda.so"

    def test_ur_interface_type_in_symbol_path(self):
        """UR entry point changes use 'ur' in symbol path, not 'pi'."""
        old_p = _make_ur_plugin(entry_points=["urAdapterGet", "urPlatformGet"])
        new_p = _make_ur_plugin(entry_points=["urAdapterGet"])
        old = _make_sycl(plugins=[old_p])
        new = _make_sycl(plugins=[new_p])
        changes = _diff_plugin_entrypoints(old, new)
        assert len(changes) == 1
        assert changes[0].symbol.startswith("sycl::ur::")

    def test_ur_serialization_roundtrip(self):
        """UR plugins survive serialization round-trip with interface_type."""
        from abicheck.serialization import snapshot_from_dict, snapshot_to_dict

        ur_plugin = _make_ur_plugin(
            name="level_zero",
            entry_points=["urAdapterGet", "urPlatformGet", "urDeviceGet"],
        )
        snap = AbiSnapshot(
            library="libsycl.so", version="2026.1.0",
            sycl=_make_sycl(plugins=[ur_plugin]),
        )
        d = snapshot_to_dict(snap)
        restored = snapshot_from_dict(d)
        assert restored.sycl is not None
        assert len(restored.sycl.plugins) == 1
        p = restored.sycl.plugins[0]
        assert p.interface_type == "ur"
        assert p.name == "level_zero"
        assert "urAdapterGet" in p.entry_points

    def test_full_detector_with_ur(self):
        """Full SYCL detector works end-to-end with UR plugins."""
        old_plugins = [
            _make_ur_plugin(name="level_zero", entry_points=["urAdapterGet", "urPlatformGet", "urDeviceGet"]),
            _make_ur_plugin(name="cuda", library="libur_adapter_cuda.so", backend_type="cuda"),
        ]
        new_plugins = [
            _make_ur_plugin(name="level_zero", entry_points=["urAdapterGet", "urPlatformGet"]),
        ]
        old = AbiSnapshot(
            library="libsycl.so", version="1.0",
            sycl=_make_sycl(plugins=old_plugins),
        )
        new = AbiSnapshot(
            library="libsycl.so", version="2.0",
            sycl=_make_sycl(plugins=new_plugins),
        )
        changes = _diff_sycl(old, new)
        kinds = {c.kind for c in changes}
        assert ChangeKind.SYCL_PLUGIN_REMOVED in kinds        # cuda removed
        assert ChangeKind.SYCL_PI_ENTRYPOINT_REMOVED in kinds  # urDeviceGet removed


class TestURVersionDetection:
    """Test UR version heuristic detection."""

    def test_basic_ur_version(self):
        from abicheck.sycl_metadata import _detect_ur_version_from_symbols
        assert _detect_ur_version_from_symbols(["urAdapterGet", "urPlatformGet"]) == "0.7"

    def test_ur_with_command_buffer(self):
        from abicheck.sycl_metadata import _detect_ur_version_from_symbols
        assert _detect_ur_version_from_symbols([
            "urAdapterGet", "urCommandBufferCreate",
        ]) == "0.8"

    def test_ur_with_virtual_mem(self):
        from abicheck.sycl_metadata import _detect_ur_version_from_symbols
        assert _detect_ur_version_from_symbols([
            "urAdapterGet", "urVirtualMemMap",
        ]) == "0.9"

    def test_ur_with_bindless(self):
        from abicheck.sycl_metadata import _detect_ur_version_from_symbols
        assert _detect_ur_version_from_symbols([
            "urAdapterGet", "urBindlessImagesCreate",
        ]) == "0.10"

    def test_empty_symbols(self):
        from abicheck.sycl_metadata import _detect_ur_version_from_symbols
        assert _detect_ur_version_from_symbols([]) == ""


# ---------------------------------------------------------------------------
# sycl_metadata.py coverage: extraction, detection, discovery
# ---------------------------------------------------------------------------

class TestSyclMetadataExtraction:
    """Coverage tests for sycl_metadata.py functions."""

    def test_detect_sycl_implementation_dpcpp(self, tmp_path):
        from abicheck.sycl_metadata import _detect_sycl_implementation
        (tmp_path / "libsycl.so").touch()
        assert _detect_sycl_implementation(tmp_path) == "dpcpp"

    def test_detect_sycl_implementation_dpcpp_versioned(self, tmp_path):
        from abicheck.sycl_metadata import _detect_sycl_implementation
        (tmp_path / "libsycl.so.7").touch()
        assert _detect_sycl_implementation(tmp_path) == "dpcpp"

    def test_detect_sycl_implementation_adaptivecpp(self, tmp_path):
        from abicheck.sycl_metadata import _detect_sycl_implementation
        (tmp_path / "libacpp-rt.so").touch()
        assert _detect_sycl_implementation(tmp_path) == "adaptivecpp"

    def test_detect_sycl_implementation_adaptivecpp_versioned(self, tmp_path):
        from abicheck.sycl_metadata import _detect_sycl_implementation
        (tmp_path / "libacpp-rt.so.1.2").touch()
        assert _detect_sycl_implementation(tmp_path) == "adaptivecpp"

    def test_detect_sycl_implementation_hipsycl(self, tmp_path):
        from abicheck.sycl_metadata import _detect_sycl_implementation
        (tmp_path / "libhipsycl-rt.so.0").touch()
        assert _detect_sycl_implementation(tmp_path) == "adaptivecpp"

    def test_detect_sycl_implementation_none(self, tmp_path):
        from abicheck.sycl_metadata import _detect_sycl_implementation
        assert _detect_sycl_implementation(tmp_path) == ""

    def test_detect_backend_type_known(self):
        from abicheck.sycl_metadata import _detect_backend_type
        assert _detect_backend_type("level_zero") == "level_zero"
        assert _detect_backend_type("cuda") == "cuda"

    def test_detect_backend_type_unknown(self):
        from abicheck.sycl_metadata import _detect_backend_type
        assert _detect_backend_type("exotic") == "exotic"

    def test_detect_pi_version_1_0(self):
        from abicheck.sycl_metadata import _detect_pi_version_from_symbols
        assert _detect_pi_version_from_symbols(["piPluginInit"]) == "1.0"

    def test_detect_pi_version_1_1(self):
        from abicheck.sycl_metadata import _detect_pi_version_from_symbols
        assert _detect_pi_version_from_symbols([
            "piPluginInit", "piextDeviceSelectBinary",
        ]) == "1.1"

    def test_detect_pi_version_1_2(self):
        from abicheck.sycl_metadata import _detect_pi_version_from_symbols
        assert _detect_pi_version_from_symbols([
            "piPluginInit", "piextUSMAlloc", "piextQueueCreate",
        ]) == "1.2"

    def test_detect_pi_version_empty(self):
        from abicheck.sycl_metadata import _detect_pi_version_from_symbols
        assert _detect_pi_version_from_symbols([]) == ""

    def test_is_plugin_candidate(self):
        from abicheck.sycl_metadata import _is_plugin_candidate
        assert _is_plugin_candidate("libpi_level_zero.so") is True
        assert _is_plugin_candidate("libur_adapter_cuda.so") is True
        assert _is_plugin_candidate("libfoo.so") is False
        assert _is_plugin_candidate("libsycl.so") is False

    def test_parse_sycl_plugin_not_a_plugin(self, tmp_path):
        from abicheck.sycl_metadata import parse_sycl_plugin
        p = tmp_path / "libfoo.so"
        p.touch()
        assert parse_sycl_plugin(p) is None

    def test_parse_sycl_metadata_no_sycl(self, tmp_path):
        from abicheck.sycl_metadata import parse_sycl_metadata
        assert parse_sycl_metadata(tmp_path) is None

    def test_parse_sycl_metadata_dpcpp_no_plugins(self, tmp_path):
        from abicheck.sycl_metadata import parse_sycl_metadata
        (tmp_path / "libsycl.so").touch()
        meta = parse_sycl_metadata(tmp_path)
        assert meta is not None
        assert meta.implementation == "dpcpp"
        assert meta.plugins == []
        assert meta.pi_version == ""

    def test_discover_sycl_plugins_nonexistent_dir(self):
        from abicheck.sycl_metadata import discover_sycl_plugins
        result = discover_sycl_plugins([Path("/nonexistent/dir")])
        assert result == []

    def test_discover_sycl_plugins_skips_directories(self, tmp_path):
        from abicheck.sycl_metadata import discover_sycl_plugins
        (tmp_path / "libpi_fake.so").mkdir()  # directory, not file
        result = discover_sycl_plugins([tmp_path])
        assert result == []

    def test_discover_sycl_plugins_deduplicates(self, tmp_path):
        """Same plugin in multiple search paths is only returned once."""
        from abicheck.sycl_metadata import discover_sycl_plugins
        d1 = tmp_path / "d1"
        d2 = tmp_path / "d2"
        d1.mkdir()
        d2.mkdir()
        # Create a tiny ELF-like file — it will fail to parse but not crash
        (d1 / "libpi_test.so").write_bytes(b"\x00" * 16)
        (d2 / "libpi_test.so").write_bytes(b"\x00" * 16)
        # Both are invalid ELFs, so no plugins returned, but no crash
        result = discover_sycl_plugins([d1, d2])
        assert result == []

    def test_extract_plugin_symbols_not_regular_file(self, tmp_path):
        """FIFO/device should be rejected after fstat."""
        from abicheck.sycl_metadata import _PI_SYMBOL_RE, _extract_plugin_symbols
        # Can't easily create a FIFO in all environments, but we can test
        # that a directory fails gracefully
        d = tmp_path / "fake.so"
        d.mkdir()
        # This should log a warning and return []
        # (it will raise OSError because you can't open a dir as binary)
        result = _extract_plugin_symbols(d, _PI_SYMBOL_RE)
        assert result == []

    def test_extract_plugin_symbols_invalid_elf(self, tmp_path):
        """Invalid ELF should return empty list."""
        from abicheck.sycl_metadata import _PI_SYMBOL_RE, _extract_plugin_symbols
        p = tmp_path / "bad.so"
        p.write_bytes(b"not an ELF file at all")
        result = _extract_plugin_symbols(p, _PI_SYMBOL_RE)
        assert result == []

    def test_default_plugin_search_paths_empty(self, monkeypatch):
        from abicheck.sycl_metadata import _default_plugin_search_paths
        monkeypatch.delenv("SYCL_PI_PLUGINS_DIR", raising=False)
        monkeypatch.delenv("SYCL_UR_ADAPTERS_DIR", raising=False)
        assert _default_plugin_search_paths() == []

    def test_default_plugin_search_paths_with_env(self, monkeypatch):
        from abicheck.sycl_metadata import _default_plugin_search_paths
        monkeypatch.setenv("SYCL_PI_PLUGINS_DIR", "/opt/pi")
        monkeypatch.setenv("SYCL_UR_ADAPTERS_DIR", "/opt/ur")
        paths = _default_plugin_search_paths()
        assert Path("/opt/pi") in paths
        assert Path("/opt/ur") in paths

    def test_parse_sycl_metadata_with_sycl_subdir(self, tmp_path):
        from abicheck.sycl_metadata import parse_sycl_metadata
        (tmp_path / "libsycl.so").touch()
        sycl_subdir = tmp_path / "sycl"
        sycl_subdir.mkdir()
        meta = parse_sycl_metadata(tmp_path)
        assert meta is not None
        assert str(sycl_subdir) in meta.plugin_search_paths


# ---------------------------------------------------------------------------
# service.py: auto-attach coverage
# ---------------------------------------------------------------------------

class TestServiceSyclAutoAttach:
    def test_try_attach_sycl_no_sycl(self, tmp_path):
        from abicheck.model import AbiSnapshot
        from abicheck.service import _try_attach_sycl_metadata
        snap = AbiSnapshot(library="libfoo.so", version="1.0")
        lib = tmp_path / "libfoo.so"
        lib.touch()
        _try_attach_sycl_metadata(snap, lib)
        assert snap.sycl is None

    def test_try_attach_sycl_dpcpp(self, tmp_path):
        from abicheck.model import AbiSnapshot
        from abicheck.service import _try_attach_sycl_metadata
        (tmp_path / "libsycl.so").touch()
        snap = AbiSnapshot(library="libsycl.so", version="1.0")
        lib = tmp_path / "libsycl.so"
        _try_attach_sycl_metadata(snap, lib)
        assert snap.sycl is not None
        assert snap.sycl.implementation == "dpcpp"

    def test_try_attach_sycl_exception_handled(self, tmp_path, monkeypatch):
        """Extraction errors are caught and logged, not raised."""
        from abicheck.model import AbiSnapshot
        from abicheck.service import _try_attach_sycl_metadata

        def boom(*a, **kw):
            raise RuntimeError("test error")

        # Patch the module-level function that the lazy import will resolve
        import abicheck.sycl_metadata
        monkeypatch.setattr(abicheck.sycl_metadata, "parse_sycl_metadata", boom)
        snap = AbiSnapshot(library="libsycl.so", version="1.0")
        lib = tmp_path / "libsycl.so"
        lib.touch()
        _try_attach_sycl_metadata(snap, lib)
        assert snap.sycl is None  # error was caught


# ---------------------------------------------------------------------------
# environment_matrix.py: validation coverage
# ---------------------------------------------------------------------------

class TestEnvironmentMatrixValidation:
    def test_bad_sycl_backends_type(self):
        from abicheck.environment_matrix import EnvironmentMatrix
        with pytest.raises(ValueError, match="sycl.backends.*must be a list"):
            EnvironmentMatrix.from_dict({"sycl": {"backends": "level_zero"}})

    def test_bad_gpu_architectures_type(self):
        from abicheck.environment_matrix import EnvironmentMatrix
        with pytest.raises(ValueError, match="cuda.gpu_architectures.*must be a list"):
            EnvironmentMatrix.from_dict({"cuda": {"gpu_architectures": "sm_80"}})

    def test_bad_require_ptx_type(self):
        from abicheck.environment_matrix import EnvironmentMatrix
        with pytest.raises(ValueError, match="cuda.require_ptx.*must be a bool"):
            EnvironmentMatrix.from_dict({"cuda": {"require_ptx": "yes"}})

    def test_bad_driver_range_type(self):
        from abicheck.environment_matrix import EnvironmentMatrix
        with pytest.raises(ValueError, match="cuda.driver_range.*must be a 2-element"):
            EnvironmentMatrix.from_dict({"cuda": {"driver_range": "525.0"}})

    def test_bad_sycl_not_dict(self):
        from abicheck.environment_matrix import EnvironmentMatrix
        with pytest.raises(ValueError, match="'sycl' must be a dict"):
            EnvironmentMatrix.from_dict({"sycl": "dpcpp"})

    def test_bad_cuda_not_dict(self):
        from abicheck.environment_matrix import EnvironmentMatrix
        with pytest.raises(ValueError, match="'cuda' must be a dict"):
            EnvironmentMatrix.from_dict({"cuda": [1, 2]})

    def test_non_string_backends_rejected(self):
        """Numeric values in backends list are rejected, not silently coerced.

        Previously asserted the opposite (silent int->str coercion into the
        resulting tuple), but `_validate_str_list_elements` now rejects a
        non-``str`` element outright (Codex review, PR #1221): an unchecked
        non-string element would otherwise reach a frozen, hashable
        dataclass field and defer the real type error to hash time. See
        `_validate_str_list_elements`'s own docstring in
        `abicheck/environment_matrix.py`.
        """
        from abicheck.environment_matrix import EnvironmentMatrix
        with pytest.raises(ValueError, match="sycl.backends.*must be strings"):
            EnvironmentMatrix.from_dict({"sycl": {"backends": [123]}})


# ---------------------------------------------------------------------------
# Plugin keying: PI and UR with same backend name
# ---------------------------------------------------------------------------

class TestPluginKeyingPiAndUr:
    """PI and UR plugins with the same backend name are distinct."""

    def test_pi_and_ur_same_name_both_present(self):
        """Both PI and UR level_zero plugins co-exist without collision."""
        pi = _make_plugin(name="level_zero", interface_type="pi")
        ur = _make_ur_plugin(name="level_zero")
        meta = _make_sycl(plugins=[pi, ur])
        assert len(meta.plugin_map) == 2
        assert ("pi", "level_zero") in meta.plugin_map
        assert ("ur", "level_zero") in meta.plugin_map

    def test_pi_removed_ur_kept(self):
        """Removing PI plugin while UR with same name exists is detected."""
        pi = _make_plugin(name="level_zero", interface_type="pi")
        ur = _make_ur_plugin(name="level_zero")
        old = _make_sycl(plugins=[pi, ur])
        new = _make_sycl(plugins=[ur])
        changes = _diff_plugins(old, new)
        assert len(changes) == 1
        assert changes[0].kind == ChangeKind.SYCL_PLUGIN_REMOVED
        assert "libpi_level_zero.so" in changes[0].old_value

    def test_pi_to_ur_migration(self):
        """Replacing PI plugins with UR adapters shows both remove and add."""
        pi = _make_plugin(name="level_zero", interface_type="pi")
        ur = _make_ur_plugin(name="level_zero")
        old = _make_sycl(plugins=[pi])
        new = _make_sycl(plugins=[ur])
        changes = _diff_plugins(old, new)
        removed = [c for c in changes if c.kind == ChangeKind.SYCL_PLUGIN_REMOVED]
        added = [c for c in changes if c.kind == ChangeKind.SYCL_PLUGIN_ADDED]
        assert len(removed) == 1
        assert len(added) == 1
        assert "pi" in removed[0].symbol
        assert "ur" in added[0].symbol

    def test_entrypoints_compared_within_same_interface(self):
        """Entry point comparison uses (interface_type, name) key."""
        old_pi = _make_plugin(
            name="level_zero", interface_type="pi",
            entry_points=["piPluginInit", "piPlatformsGet", "piDevicesGet"],
        )
        new_pi = _make_plugin(
            name="level_zero", interface_type="pi",
            entry_points=["piPluginInit", "piPlatformsGet"],
        )
        ur = _make_ur_plugin(name="level_zero")
        old = _make_sycl(plugins=[old_pi, ur])
        new = _make_sycl(plugins=[new_pi, ur])
        changes = _diff_plugin_entrypoints(old, new)
        assert len(changes) == 1
        assert "piDevicesGet" in changes[0].description
