# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
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

"""SYCL plugin-interface facts as data.

The dataclasses ``abicheck.sycl_metadata`` reads a SYCL runtime into. Holds no
parsing logic (ADR-061 Phase 5)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SyclPluginInfo:
    """Metadata for a single backend plugin (PI or UR)."""

    name: str  # e.g. "level_zero", "opencl", "cuda"
    library: str  # e.g. "libpi_level_zero.so"
    interface_type: str = "pi"  # "pi" (Plugin Interface) or "ur" (Unified Runtime)
    pi_version: str = ""  # interface version (if detectable)
    entry_points: list[str] = field(default_factory=list)  # exported pi*/ur* symbols
    backend_type: str = ""  # "level_zero" | "opencl" | "cuda" | ...
    min_driver_version: str | None = None  # minimum backend driver version


@dataclass
class SyclMetadata:
    """SYCL runtime + plugin interface metadata for one distribution."""

    implementation: str = ""  # "dpcpp" | "adaptivecpp" | "computecpp"
    runtime_version: str = ""  # e.g. "2025.2.0"
    pi_version: str = ""  # PI interface version of the runtime
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
