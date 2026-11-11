# SPDX-License-Identifier: Apache-2.0
"""Explicit, version-matched native GCC plugin discovery. Never builds on scan."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from ....deadline import PROBE_HANG_TIMEOUT_S, run_bounded
from ....errors import SnapshotError


def compiler_output(compiler: str, option: str) -> str:
    try:
        result = run_bounded(
            [compiler, option],
            timeout=PROBE_HANG_TIMEOUT_S,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SnapshotError(f"Cannot probe native GCC {compiler}: {exc}") from exc
    if result.returncode:
        raise SnapshotError(
            f"Native GCC probe failed: {compiler} {option}: {result.stderr.strip()}"
        )
    return str(result.stdout).strip()


def cache_directory(compiler: str) -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
    return (
        base
        / "abicheck/gcc"
        / f"{compiler_output(compiler, '-dumpmachine')}-{compiler_output(compiler, '-dumpfullversion')}"
    )


def resolve_compiler(compiler: str) -> str:
    resolved = shutil.which(compiler)
    if not resolved:
        raise SnapshotError(f"Native GCC compiler not found: {compiler}")
    version = compiler_output(resolved, "--version")
    if "clang" in version.lower() or not any(
        word in version.lower() for word in ("gcc", "g++", "free software foundation")
    ):
        raise SnapshotError(
            f"Native GCC backend requires a real GCC compiler, got {compiler}: {version.splitlines()[0]}"
        )
    return resolved


def find_plugin(compiler: str, language: str) -> Path:
    name = "gcc_cpp_facts.so" if language == "c++" else "gcc_facts.so"
    configured = os.environ.get("ABICHECK_GCC_PLUGIN")
    if configured:
        path = Path(configured).expanduser()
        if path.is_dir():
            path = path / name
        if not path.is_file():
            raise SnapshotError(
                f"ABICHECK_GCC_PLUGIN does not name a plugin file: {path}"
            )
        return path.resolve()
    path = cache_directory(compiler) / name
    if path.is_file():
        return path
    sdk = Path(compiler_output(compiler, "-print-file-name=plugin")) / "abicheck" / name
    if sdk.is_file():
        return sdk
    raise SnapshotError(
        f"Native GCC plugin is missing for {compiler} ({language}). Build it with "
        f"python -m abicheck.extract.headers.gcc.build --compiler {compiler}, using matching GCC plugin-dev headers, "
        "or set ABICHECK_GCC_PLUGIN to a matching plugin file/directory. No Clang fallback was attempted."
    )
