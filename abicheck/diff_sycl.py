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

"""SYCL plugin ABI change detector (PI and UR).

Compares SYCL metadata between two snapshots to detect:
- Plugin interface version incompatibilities (PI or UR)
- Missing/added entry points in backend plugins
- Removed/added backend plugins
- Plugin search path drift
- Backend driver requirement changes

Works with both PI (Plugin Interface, ``libpi_*.so``) and UR (Unified
Runtime, ``libur_adapter_*.so``) plugins using the same change kinds.

Registered via ``@registry.detector("sycl")`` and automatically skipped when
``SyclMetadata`` is absent from either snapshot.

See ADR-020b for design rationale.
"""
from __future__ import annotations

from .checker_policy import ChangeKind
from .checker_types import Change
from .detector_registry import registry
from .diff_helpers import make_change
from .model import AbiSnapshot
from .sycl_metadata import SyclMetadata


def _diff_implementation(old: SyclMetadata, new: SyclMetadata) -> list[Change]:
    """Detect SYCL implementation changes (e.g., DPC++ -> AdaptiveCpp)."""
    changes: list[Change] = []
    if (
        old.implementation
        and new.implementation
        and old.implementation != new.implementation
    ):
        changes.append(make_change(
            ChangeKind.SYCL_IMPLEMENTATION_CHANGED,
            symbol="sycl::implementation",
            old=old.implementation,
            new=new.implementation,
        ))
    return changes


def _diff_pi_version(old: SyclMetadata, new: SyclMetadata) -> list[Change]:
    """Detect PI interface version changes at the runtime level."""
    changes: list[Change] = []
    if old.pi_version and new.pi_version and old.pi_version != new.pi_version:
        changes.append(make_change(
            ChangeKind.SYCL_PI_VERSION_CHANGED,
            symbol="sycl::pi",
            old=old.pi_version,
            new=new.pi_version,
        ))
    return changes


def _diff_plugins(old: SyclMetadata, new: SyclMetadata) -> list[Change]:
    """Detect added/removed backend plugins.

    Plugins are keyed by ``(interface_type, name)`` so that a PI and UR
    plugin sharing the same backend name are treated as distinct entries.
    """
    changes: list[Change] = []
    old_keys = {(p.interface_type, p.name) for p in old.plugins}
    new_keys = {(p.interface_type, p.name) for p in new.plugins}

    for key in sorted(old_keys - new_keys):
        iface, name = key
        old_plugin = old.plugin_map[key]
        changes.append(make_change(
            ChangeKind.SYCL_PLUGIN_REMOVED,
            symbol=f"sycl::{iface}::{name}",
            name=old_plugin.library,
            detail=name,
            old=old_plugin.backend_type,
            old_value=old_plugin.library,
            new_value=None,
        ))

    for key in sorted(new_keys - old_keys):
        iface, name = key
        new_plugin = new.plugin_map[key]
        changes.append(make_change(
            ChangeKind.SYCL_PLUGIN_ADDED,
            symbol=f"sycl::{iface}::{name}",
            name=new_plugin.library,
            detail=name,
            new=new_plugin.backend_type,
            old_value=None,
            new_value=new_plugin.library,
        ))

    return changes


def _diff_plugin_entrypoints(
    old: SyclMetadata, new: SyclMetadata,
) -> list[Change]:
    """Detect added/removed entry points within plugins that exist in both."""
    changes: list[Change] = []
    old_map = old.plugin_map
    new_map = new.plugin_map
    common = sorted(set(old_map) & set(new_map))

    for key in common:
        old_plugin = old_map[key]
        new_plugin = new_map[key]
        name = key[1]  # (interface_type, name)
        old_eps = set(old_plugin.entry_points)
        new_eps = set(new_plugin.entry_points)
        iface = new_plugin.interface_type.upper()  # "PI" or "UR"

        for ep in sorted(old_eps - new_eps):
            changes.append(make_change(
                ChangeKind.SYCL_PI_ENTRYPOINT_REMOVED,
                symbol=f"sycl::{new_plugin.interface_type}::{name}::{ep}",
                name=ep,
                detail=iface,
                old=old_plugin.library,
                old_value=ep,
                new_value=None,
            ))

        for ep in sorted(new_eps - old_eps):
            changes.append(make_change(
                ChangeKind.SYCL_PI_ENTRYPOINT_ADDED,
                symbol=f"sycl::{new_plugin.interface_type}::{name}::{ep}",
                name=ep,
                detail=iface,
                new=new_plugin.library,
                old_value=None,
                new_value=ep,
            ))

        # Per-plugin PI version changes are NOT emitted separately to
        # avoid duplicating the runtime-level SYCL_PI_VERSION_CHANGED
        # from _diff_pi_version() (runtime pi_version is derived from
        # plugin versions).

    return changes


def _diff_plugin_search_paths(
    old: SyclMetadata, new: SyclMetadata,
) -> list[Change]:
    """Detect plugin search path changes."""
    changes: list[Change] = []
    if old.plugin_search_paths != new.plugin_search_paths:
        changes.append(make_change(
            ChangeKind.SYCL_PLUGIN_SEARCH_PATH_CHANGED,
            symbol="sycl::pi::search_paths",
            old_value=", ".join(old.plugin_search_paths),
            new_value=", ".join(new.plugin_search_paths),
        ))
    return changes


def _diff_runtime_version(
    old: SyclMetadata, new: SyclMetadata,
) -> list[Change]:
    """Detect SYCL runtime version changes (informational)."""
    changes: list[Change] = []
    if (
        old.runtime_version
        and new.runtime_version
        and old.runtime_version != new.runtime_version
    ):
        changes.append(make_change(
            ChangeKind.SYCL_RUNTIME_VERSION_CHANGED,
            symbol="sycl::runtime",
            old=old.runtime_version,
            new=new.runtime_version,
        ))
    return changes


def _diff_backend_driver_reqs(
    old: SyclMetadata, new: SyclMetadata,
) -> list[Change]:
    """Detect backend driver requirement changes across plugins."""
    changes: list[Change] = []
    old_map = old.plugin_map
    new_map = new.plugin_map

    for key in sorted(set(old_map) & set(new_map)):
        name = key[1]  # (interface_type, name)
        old_drv = old_map[key].min_driver_version
        new_drv = new_map[key].min_driver_version
        if old_drv and new_drv and old_drv != new_drv:
            changes.append(make_change(
                ChangeKind.SYCL_BACKEND_DRIVER_REQ_CHANGED,
                symbol=f"sycl::pi::{name}::driver",
                name=name,
                old=old_drv,
                new=new_drv,
            ))

    return changes


# ---------------------------------------------------------------------------
# Detector registration
# ---------------------------------------------------------------------------

@registry.detector(
    "sycl",
    requires_support=lambda o, n: (
        o.sycl is not None and n.sycl is not None,
        "missing SYCL metadata",
    ),
)
def _diff_sycl(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """SYCL PI compatibility detector (ADR-020b)."""
    o = old.sycl
    n = new.sycl
    assert o is not None and n is not None  # guaranteed by requires_support

    changes: list[Change] = []
    changes.extend(_diff_implementation(o, n))
    changes.extend(_diff_pi_version(o, n))
    changes.extend(_diff_plugins(o, n))
    changes.extend(_diff_plugin_entrypoints(o, n))
    changes.extend(_diff_plugin_search_paths(o, n))
    changes.extend(_diff_runtime_version(o, n))
    changes.extend(_diff_backend_driver_reqs(o, n))
    return changes
