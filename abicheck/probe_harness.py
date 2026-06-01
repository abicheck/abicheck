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

"""Probe-harness: compile-and-snapshot driver for header-only libraries.

The premise: a header-only library has no ``.so`` to snapshot, but a
consumer that includes its headers and instantiates a few templates
produces an object file with mangled symbols, DWARF type info, and
vtables. We synthesize that consumer from a YAML *probe spec*, compile
it under one or more *configurations* (compiler × language standard ×
macro set), and feed the resulting ``.o`` files into the existing
snapshot pipeline.

Public API:

* :class:`ProbeSpec` — parsed YAML.
* :class:`ProbeConfiguration` — one (compiler, flags, defines) tuple.
* :class:`Probe` — one consumer TU snippet.
* :class:`ProbeResult` — ``(configuration_id, probe_id, AbiSnapshot)``.
* :class:`MatrixSnapshot` — set of ``ProbeResult`` for one version of
  a library; serialisable to/from JSON.
* :func:`load_probe_spec` — parse YAML into ``ProbeSpec``.
* :func:`run_probe_matrix` — compile each (configuration × probe) and
  return ``MatrixSnapshot``.

The actual compilation uses :mod:`subprocess` and the configured
compiler binary; if the binary is missing or returns non-zero, the
corresponding ``ProbeResult`` is recorded with ``snapshot=None`` and
the error captured in ``error``. Callers decide how to surface the
failure.

This module deliberately does NOT depend on the existing dumper /
checker code at import time; the dumper is imported lazily inside
:func:`_snapshot_object_file` so unit tests that only exercise YAML
parsing and matrix bookkeeping don't pay the import cost.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .model import AbiSnapshot


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProbeConfiguration:
    """A (compiler, flags, defines) tuple."""
    id: str
    compiler: str
    flags: tuple[str, ...] = ()
    defines: dict[str, str] = field(default_factory=dict)
    include_dirs: tuple[str, ...] = ()
    cxx_std: int | None = None  # 17, 20, 23 — parsed from -std=c++NN

    def as_command_args(self) -> list[str]:
        """Return the compiler invocation prefix (binary + flags + defines)."""
        out: list[str] = [self.compiler, *self.flags]
        for k, v in self.defines.items():
            out.append(f"-D{k}={v}" if v else f"-D{k}")
        for d in self.include_dirs:
            out.append(f"-I{d}")
        return out


@dataclass(frozen=True)
class Probe:
    """One consumer TU snippet."""
    name: str
    headers: tuple[str, ...]
    body: str

    def render(self) -> str:
        """Generate the full .cpp source the harness will compile."""
        lines = []
        for h in self.headers:
            # Bare angle/quote characters are accepted as-is; the
            # YAML author writes ``<acme/lib/algorithm>`` or
            # ``"my_header.h"`` exactly as they would in C++.
            if h.startswith("<") or h.startswith('"'):
                lines.append(f"#include {h}")
            else:
                lines.append(f"#include <{h}>")
        lines.append("")
        lines.append(self.body.rstrip())
        return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class ProbeSpec:
    """A parsed probe-harness YAML manifest."""
    name: str
    configurations: tuple[ProbeConfiguration, ...]
    probes: tuple[Probe, ...]
    defaults: dict[str, str] = field(default_factory=dict)


@dataclass
class ProbeResult:
    """Outcome of compiling one (configuration × probe) pair."""
    configuration_id: str
    probe_id: str
    object_path: str | None = None
    snapshot: AbiSnapshot | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "configuration_id": self.configuration_id,
            "probe_id": self.probe_id,
            "object_path": self.object_path,
            "error": self.error,
        }
        if self.snapshot is not None:
            from .serialization import snapshot_to_dict
            out["snapshot"] = snapshot_to_dict(self.snapshot)
        return out


@dataclass
class MatrixSnapshot:
    """A version-stamped set of ProbeResults — the matrix-aware analogue
    of ``AbiSnapshot``."""
    library: str
    version: str
    spec_name: str
    cxx_stds: dict[str, int | None] = field(default_factory=dict)
    defaults: dict[str, str] = field(default_factory=dict)
    results: list[ProbeResult] = field(default_factory=list)

    def by_configuration(self) -> dict[str, list[ProbeResult]]:
        out: dict[str, list[ProbeResult]] = {}
        for r in self.results:
            out.setdefault(r.configuration_id, []).append(r)
        return out

    def to_json(self) -> str:
        return json.dumps({
            "library": self.library,
            "version": self.version,
            "spec_name": self.spec_name,
            "cxx_stds": self.cxx_stds,
            "defaults": self.defaults,
            "results": [r.to_dict() for r in self.results],
        }, indent=2)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MatrixSnapshot:
        from .serialization import snapshot_from_dict
        results: list[ProbeResult] = []
        for r in data.get("results", []):
            snap = None
            if r.get("snapshot") is not None:
                snap = snapshot_from_dict(r["snapshot"])
            results.append(ProbeResult(
                configuration_id=r["configuration_id"],
                probe_id=r["probe_id"],
                object_path=r.get("object_path"),
                snapshot=snap,
                error=r.get("error"),
            ))
        return cls(
            library=data["library"],
            version=data["version"],
            spec_name=data["spec_name"],
            cxx_stds=data.get("cxx_stds", {}),
            defaults=data.get("defaults", {}),
            results=results,
        )


# ---------------------------------------------------------------------------
# YAML parsing
# ---------------------------------------------------------------------------


_CXX_STD_FLAG = "-std=c++"


def _parse_cxx_std(flags: list[str]) -> int | None:
    """Extract the C++ standard version from a flag list (``-std=c++20``)."""
    for f in flags:
        if f.startswith(_CXX_STD_FLAG):
            try:
                return int(f[len(_CXX_STD_FLAG):])
            except ValueError:
                return None
    return None


def load_probe_spec(path: str | Path) -> ProbeSpec:
    """Parse a YAML probe manifest. Accepts JSON too (a YAML subset)."""
    text = Path(path).read_text(encoding="utf-8")
    try:
        import yaml
        data = yaml.safe_load(text)
    except ImportError:
        # Fallback: PyYAML isn't required as a runtime dep for abicheck,
        # so we accept JSON-shaped files unchanged.
        data = json.loads(text)
    return parse_probe_spec(data)


def parse_probe_spec(data: dict[str, Any]) -> ProbeSpec:
    """Parse an already-loaded mapping into a ProbeSpec.

    Strict on the *shape* (every required field must be present) but
    forgiving on extras (unrecognized keys are silently ignored so the
    schema can grow without breaking existing manifests).
    """
    if not isinstance(data, dict):
        raise ValueError("probe spec must be a mapping at the top level")
    for required in ("name", "configurations", "probes"):
        if required not in data:
            raise ValueError(f"probe spec missing required key {required!r}")

    configs: list[ProbeConfiguration] = []
    for c in data["configurations"]:
        flags = tuple(c.get("flags", []))
        configs.append(ProbeConfiguration(
            id=c["id"],
            compiler=c["compiler"],
            flags=flags,
            defines=dict(c.get("defines", {})),
            include_dirs=tuple(c.get("include_dirs", [])),
            cxx_std=_parse_cxx_std(list(flags)),
        ))

    probes: list[Probe] = []
    for p in data["probes"]:
        probes.append(Probe(
            name=p["name"],
            headers=tuple(p.get("headers", [])),
            body=p["body"],
        ))

    return ProbeSpec(
        name=data["name"],
        configurations=tuple(configs),
        probes=tuple(probes),
        defaults=dict(data.get("defaults", {})),
    )


# ---------------------------------------------------------------------------
# Compilation
# ---------------------------------------------------------------------------


def _compile_probe(
    probe: Probe,
    cfg: ProbeConfiguration,
    work_dir: Path,
) -> tuple[Path | None, str | None]:
    """Compile one probe under one configuration. Returns (object_path, error)."""
    src_path = work_dir / f"{cfg.id}__{probe.name}.cpp"
    obj_path = work_dir / f"{cfg.id}__{probe.name}.o"
    src_path.write_text(probe.render(), encoding="utf-8")

    cmd = cfg.as_command_args() + [
        "-c",
        "-o", str(obj_path),
        str(src_path),
    ]

    if shutil.which(cfg.compiler) is None:
        return None, f"compiler {cfg.compiler!r} not found on PATH"

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60, check=False,
        )
    except subprocess.TimeoutExpired:
        return None, "compilation timed out (60s)"
    except OSError as e:
        return None, f"failed to invoke {cfg.compiler}: {e}"
    if proc.returncode != 0:
        return None, (
            f"compilation failed (rc={proc.returncode}): "
            f"{proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else ''}"
        )
    return obj_path, None


def _snapshot_object_file(obj_path: Path) -> AbiSnapshot:
    """Pass a .o file through the existing dumper to produce an AbiSnapshot.

    A standalone .o is dumped with no headers (the probe TU IS the
    consumer; we don't want to re-parse library headers separately).
    """
    # Lazy import — keeps unit tests that don't compile fast.
    from .dumper import dump
    return dump(obj_path, headers=[], dwarf_only=True)


# ---------------------------------------------------------------------------
# Matrix execution
# ---------------------------------------------------------------------------


def run_probe_matrix(
    spec: ProbeSpec,
    *,
    library_name: str,
    version: str,
    work_dir: str | Path | None = None,
    snapshot: bool = True,
) -> MatrixSnapshot:
    """Compile every (configuration × probe) and return a MatrixSnapshot.

    When ``snapshot=False`` the .o files are kept but not run through
    the dumper — useful in tests that exercise compilation routing
    without paying the dumper cost.

    Failures are captured per-result; an overall ``OSError`` is raised
    only if the work_dir cannot be created.
    """
    if work_dir is None:
        work_dir_path = Path(tempfile.mkdtemp(prefix="abicheck-probe-"))
        owns_dir = True
    else:
        work_dir_path = Path(work_dir)
        work_dir_path.mkdir(parents=True, exist_ok=True)
        owns_dir = False

    results: list[ProbeResult] = []
    cxx_stds: dict[str, int | None] = {}
    try:
        for cfg in spec.configurations:
            cxx_stds[cfg.id] = cfg.cxx_std
            for probe in spec.probes:
                obj_path, err = _compile_probe(probe, cfg, work_dir_path)
                snap = None
                if obj_path is not None and snapshot:
                    try:
                        snap = _snapshot_object_file(obj_path)
                    except (OSError, ValueError, RuntimeError) as e:  # pragma: no cover
                        # Dumper raises one of these on malformed objects;
                        # everything else propagates.
                        err = f"dumper failed on {obj_path.name}: {e}"
                results.append(ProbeResult(
                    configuration_id=cfg.id,
                    probe_id=probe.name,
                    object_path=str(obj_path) if obj_path else None,
                    snapshot=snap,
                    error=err,
                ))
    finally:
        # Don't tear down a user-supplied dir; ours is throwaway but
        # may be useful for debug, so leave it on disk and rely on the
        # OS tmpdir cleanup policy.
        _ = owns_dir

    return MatrixSnapshot(
        library=library_name,
        version=version,
        spec_name=spec.name,
        cxx_stds=cxx_stds,
        defaults=dict(spec.defaults),
        results=results,
    )


def write_matrix_snapshot(matrix: MatrixSnapshot, path: str | Path) -> None:
    Path(path).write_text(matrix.to_json(), encoding="utf-8")


def load_matrix_snapshot(path: str | Path) -> MatrixSnapshot:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return MatrixSnapshot.from_dict(data)


# ---------------------------------------------------------------------------
# Re-export helpers used by tests
# ---------------------------------------------------------------------------


__all__ = [
    "MatrixSnapshot",
    "Probe",
    "ProbeConfiguration",
    "ProbeResult",
    "ProbeSpec",
    "load_matrix_snapshot",
    "load_probe_spec",
    "parse_probe_spec",
    "run_probe_matrix",
    "write_matrix_snapshot",
]


# Keep `asdict` and `os` imported for downstream callers that re-export
# them from this module (the CLI uses both when serializing for human
# display). Re-export is explicit so the imports are not flagged
# unused by ruff while still serving as a stable surface.
_unused = (asdict, os)
