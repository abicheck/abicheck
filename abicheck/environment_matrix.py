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

        _KNOWN_KEYS = {
            "compilers", "abi_version", "libstdcxx_dual_abi",
            "sycl", "cuda", "target_os", "target_arch",
        }
        unknown = set(data) - _KNOWN_KEYS
        if unknown:
            log.warning("EnvironmentMatrix: unknown keys ignored: %s", unknown)

        sycl_data = data.get("sycl", {})
        if not isinstance(sycl_data, dict):
            raise ValueError(f"'sycl' must be a dict, got {type(sycl_data).__name__}")
        cuda_data = data.get("cuda", {})
        if not isinstance(cuda_data, dict):
            raise ValueError(f"'cuda' must be a dict, got {type(cuda_data).__name__}")

        compilers = data.get("compilers", [])
        if not isinstance(compilers, list):
            raise ValueError(f"'compilers' must be a list, got {type(compilers).__name__}")

        backends = sycl_data.get("backends", [])
        if not isinstance(backends, list):
            raise ValueError(
                f"'sycl.backends' must be a list, got {type(backends).__name__}"
            )

        sycl = SyclConstraints(
            implementation=str(sycl_data.get("implementation", "")),
            backends=[str(b) for b in backends],
            min_pi_version=str(sycl_data.get("min_pi_version", "")),
        )

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

        cuda = CudaConstraints(
            gpu_architectures=[str(a) for a in gpu_archs],
            driver_range=driver_range,
            toolkit_version=str(cuda_data.get("toolkit_version", "")),
            require_ptx=require_ptx,
        )

        return cls(
            compilers=compilers,
            abi_version=data.get("abi_version"),
            libstdcxx_dual_abi=data.get("libstdcxx_dual_abi"),
            sycl=sycl,
            cuda=cuda,
            target_os=data.get("target_os"),
            target_arch=data.get("target_arch"),
        )

    @classmethod
    def from_yaml(cls, path: Path) -> EnvironmentMatrix:
        """Load from a YAML file."""
        import yaml

        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return cls.from_dict(data)
