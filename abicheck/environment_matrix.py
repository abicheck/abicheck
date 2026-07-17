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

"""Environment matrix — declared deployment constraints for parameterized ABI checks.

When checking ABI compatibility for heterogeneous stacks (SYCL, CUDA), the
result depends on the deployment environment: which GPU architectures, driver
versions, and backend plugins are targeted.

The ``EnvironmentMatrix`` dataclass captures these constraints as explicit
inputs, converting "catch everything" into a checkable contract.

Usage::

    matrix = EnvironmentMatrix.from_yaml("env-matrix.yaml")
    result = compare(old, new, env_matrix=matrix)

YAML format::

    target_os: linux
    target_arch: x86_64

    compilers:
      - gcc-13
      - clang-17
    abi_version: "18"
    libstdcxx_dual_abi: cxx11

    sycl:
      implementation: dpcpp
      backends:
        - level_zero
        - opencl

    cuda:
      gpu_architectures:
        - sm_80
        - sm_90
      driver_range: ["525.0", "580.0"]
      toolkit_version: "12.4"

See ADR-020b for design rationale.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .diff_versioning import _parse_dotted_numeric_version

log = logging.getLogger(__name__)


@dataclass
class SyclConstraints:
    """SYCL-specific deployment constraints."""

    implementation: str = ""              # "dpcpp" | "adaptivecpp"
    backends: list[str] = field(default_factory=list)  # ["level_zero", "opencl"]
    min_pi_version: str = ""              # minimum PI version required


@dataclass
class CudaConstraints:
    """CUDA-specific deployment constraints (placeholder for future use)."""

    gpu_architectures: list[str] = field(default_factory=list)  # ["sm_80", "sm_90"]
    driver_range: tuple[str, str] | None = None   # (min_version, max_version)
    toolkit_version: str = ""
    require_ptx: bool = False              # require PTX for forward-compat


#: Top-level keys :meth:`EnvironmentMatrix.from_dict` understands; anything
#: else is ignored with a warning.
_KNOWN_KEYS = frozenset({
    "compilers", "abi_version", "libstdcxx_dual_abi",
    "sycl", "cuda", "target_os", "target_arch", "runtime_floors",
})


def _warn_unknown_keys(data: dict[str, Any]) -> None:
    """Log a warning for top-level keys ``from_dict`` does not understand."""
    unknown = set(data) - _KNOWN_KEYS
    if unknown:
        log.warning("EnvironmentMatrix: unknown keys ignored: %s", unknown)


def _section_dict(data: dict[str, Any], key: str) -> dict[str, Any]:
    """Return the *key* sub-dict of *data* (default empty), validating its type."""
    section = data.get(key, {})
    if not isinstance(section, dict):
        raise ValueError(f"'{key}' must be a dict, got {type(section).__name__}")
    return section


def _parse_sycl_constraints(sycl_data: dict[str, Any]) -> SyclConstraints:
    """Parse the validated ``sycl`` section into :class:`SyclConstraints`."""
    backends = sycl_data.get("backends", [])
    if not isinstance(backends, list):
        raise ValueError(
            f"'sycl.backends' must be a list, got {type(backends).__name__}"
        )
    return SyclConstraints(
        implementation=str(sycl_data.get("implementation", "")),
        backends=[str(b) for b in backends],
        min_pi_version=str(sycl_data.get("min_pi_version", "")),
    )


def _parse_cuda_constraints(cuda_data: dict[str, Any]) -> CudaConstraints:
    """Parse the validated ``cuda`` section into :class:`CudaConstraints`."""
    gpu_archs = cuda_data.get("gpu_architectures", [])
    if not isinstance(gpu_archs, list):
        raise ValueError(
            f"'cuda.gpu_architectures' must be a list, got {type(gpu_archs).__name__}"
        )

    driver_range_raw = cuda_data.get("driver_range")
    driver_range = None
    if isinstance(driver_range_raw, (list, tuple)) and len(driver_range_raw) == 2:
        driver_range = (str(driver_range_raw[0]), str(driver_range_raw[1]))
    elif driver_range_raw is not None:
        raise ValueError(
            f"'cuda.driver_range' must be a 2-element list [min, max], "
            f"got {driver_range_raw!r}"
        )

    require_ptx = cuda_data.get("require_ptx", False)
    if not isinstance(require_ptx, bool):
        raise ValueError(
            f"'cuda.require_ptx' must be a bool, got {type(require_ptx).__name__}"
        )

    return CudaConstraints(
        gpu_architectures=[str(a) for a in gpu_archs],
        driver_range=driver_range,
        toolkit_version=str(cuda_data.get("toolkit_version", "")),
        require_ptx=require_ptx,
    )


#: runtime_floors keys whose value is not a dotted-numeric version — they
#: declare a presence flag (MUSLLINUX, WHEEL_CONTEXT) or a non-version token
#: (WHEEL_ARCH, e.g. "x86_64") rather than a floor, so the dotted-numeric
#: validation below doesn't apply to them (Codex review #583: WHEEL_ARCH
#: was unreachable via --env-matrix/from_dict entirely — every value was
#: rejected before check_wheel_tag_architecture_mismatch ever ran, since
#: only the direct-constructor path bypassing from_dict's validation could
#: set a non-numeric runtime_floors value at all).
_NON_NUMERIC_RUNTIME_FLOOR_KEYS = frozenset(
    {"WHEEL_ARCH", "MUSLLINUX", "WHEEL_CONTEXT"}
)


def _parse_runtime_floors(floors_raw: object) -> dict[str, str]:
    """Parse and validate the ``runtime_floors`` prefix → version mapping."""
    if not isinstance(floors_raw, dict):
        raise ValueError(
            f"'runtime_floors' must be a dict of version-node prefix → "
            f"version (e.g. {{GLIBC: '2.28'}}), got {type(floors_raw).__name__}"
        )
    runtime_floors: dict[str, str] = {}
    for key, value in floors_raw.items():
        if isinstance(value, float):
            # An unquoted YAML floor has already been lossily parsed:
            # `GLIBC: 2.40` reaches us as the float 2.4, which would
            # silently declare a *lower* floor than the user wrote.
            # Reject rather than guess (Codex review #510).
            raise ValueError(
                f"'runtime_floors.{key}' must be a quoted string version: "
                f"unquoted YAML floats lose trailing zeros "
                f"(2.40 parses as 2.4). Write {key}: \"{value}\" "
                f"with the intended digits."
            )
        floor = str(value)
        if str(key).upper() not in _NON_NUMERIC_RUNTIME_FLOOR_KEYS:
            # Every dot-separated component must be purely numeric: the floor
            # contract parses with int() per component, so a "2.28-1" or "2.x"
            # would silently truncate to (2,) and flip verdicts. Reject
            # malformed text here instead (Codex review #510).
            if _parse_dotted_numeric_version(floor) is None:
                raise ValueError(
                    f"'runtime_floors.{key}' must be a dotted numeric version "
                    f"(digits and dots only, e.g. '2.28'), with each component "
                    f"at most 9 digits, got {value!r}"
                )
        runtime_floors[str(key).upper()] = floor
    return runtime_floors


@dataclass
class EnvironmentMatrix:
    """Declared deployment constraints — shared across SYCL, CUDA, etc.

    When constraints are unspecified (empty), detectors emit conditional
    results (e.g., "breaking if backend X is required").
    """

    # Host toolchain
    compilers: list[str] = field(default_factory=list)
    abi_version: str | None = None                    # -fabi-version value
    libstdcxx_dual_abi: str | None = None             # "cxx11" | "old"

    # Declared deployment runtime floors, keyed by ELF version-node prefix
    # (case-insensitive; normalized to upper): {"GLIBC": "2.28",
    # "GLIBCXX": "3.4.30", "CXXABI": "1.3.13"}. When set, a new symbol-version
    # requirement at or below the floor is COMPATIBLE (every declared target
    # already ships it) and one above the floor is BREAKING (a declared target
    # can no longer load the binary); unspecified prefixes keep the default
    # RISK classification.
    runtime_floors: dict[str, str] = field(default_factory=dict)

    # Heterogeneous stack constraints
    sycl: SyclConstraints = field(default_factory=SyclConstraints)
    cuda: CudaConstraints = field(default_factory=CudaConstraints)

    # Target platform — None means unspecified (no assumption).
    target_os: str | None = None
    target_arch: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EnvironmentMatrix:
        """Parse from a dictionary (e.g., loaded from YAML).

        Raises:
            TypeError: If *data* is not a dict.
            ValueError: If field types are wrong.
        """
        if not isinstance(data, dict):
            raise TypeError(
                f"EnvironmentMatrix expects a dict, got {type(data).__name__}"
            )

        _warn_unknown_keys(data)

        sycl_data = _section_dict(data, "sycl")
        cuda_data = _section_dict(data, "cuda")

        compilers = data.get("compilers", [])
        if not isinstance(compilers, list):
            raise ValueError(f"'compilers' must be a list, got {type(compilers).__name__}")

        sycl = _parse_sycl_constraints(sycl_data)
        cuda = _parse_cuda_constraints(cuda_data)
        runtime_floors = _parse_runtime_floors(data.get("runtime_floors", {}))

        return cls(
            compilers=compilers,
            abi_version=data.get("abi_version"),
            libstdcxx_dual_abi=data.get("libstdcxx_dual_abi"),
            runtime_floors=runtime_floors,
            sycl=sycl,
            cuda=cuda,
            target_os=data.get("target_os"),
            target_arch=data.get("target_arch"),
        )

    @classmethod
    def from_yaml(cls, path: Path) -> EnvironmentMatrix:
        """Load from a YAML file.

        Malformed YAML raises :class:`ValueError` (like the shape errors from
        :meth:`from_dict`), so callers need not depend on the ``yaml`` package
        for their error handling.
        """
        import yaml

        with open(path) as f:
            try:
                data = yaml.safe_load(f) or {}
            except yaml.YAMLError as exc:
                raise ValueError(f"malformed YAML: {exc}") from exc
        return cls.from_dict(data)
