# Copyright 2026 Nikolay Petrov
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Small filesystem and AST-toolchain identity helpers for :mod:`dumper`."""

from __future__ import annotations

import hashlib
import os
import shutil
import signal
import stat as stat_module
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any


def _safe_mtime(path: Path) -> tuple[float | None, bool]:
    """Return (mtime, SOURCE_DATE_EPOCH substitution), or (None, False)."""
    source_date_epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if source_date_epoch:
        try:
            return float(int(source_date_epoch.strip())), True
        except (ValueError, OverflowError):
            pass
    try:
        return path.stat().st_mtime, False
    except OSError:
        return None, False


def _safe_size(path: Path) -> int | None:
    """Return path's byte size, or None when it cannot be stat'd."""
    try:
        return path.stat().st_size
    except OSError:
        return None


def _castxml_available() -> bool:
    return shutil.which("castxml") is not None


@lru_cache(maxsize=64)
def _executable_sha256(
    real_path: str,
    device: int,
    inode: int,
    mtime_ns: int,
    ctime_ns: int,
    size: int,
) -> str:
    """Hash one exact executable revision (stat fields invalidate memoization)."""
    del device, inode, mtime_ns, ctime_ns, size
    digest = hashlib.sha256()
    with Path(real_path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@lru_cache(maxsize=64)
def _tool_version_output(real_path: str, digest: str) -> str:
    """Return stable ``--version`` output for one exact executable revision."""
    del digest
    try:
        # Keep hostile/broken tools from filling memory and tolerate arbitrary
        # bytes in diagnostics.  The file also combines stdout/stderr in actual
        # emission order, unlike concatenating two independently captured pipes.
        with tempfile.TemporaryFile() as output:
            popen_kwargs: dict[str, Any] = {}
            if os.name == "posix":
                import resource

                def _limit_output() -> None:
                    resource.setrlimit(resource.RLIMIT_FSIZE, (128 * 1024, 128 * 1024))

                popen_kwargs.update(start_new_session=True, preexec_fn=_limit_output)
            process = subprocess.Popen(
                [real_path, "--version"],
                stdout=output,
                stderr=subprocess.STDOUT,
                **popen_kwargs,
            )
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
                process.wait()
                raise
            output.seek(0)
            raw = output.read(64 * 1024 + 1)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"unavailable:{type(exc).__name__}:{exc}"
    truncated = len(raw) > 64 * 1024
    text = raw[: 64 * 1024].decode("utf-8", errors="replace")
    if truncated:
        text += "\n[truncated]"
    return "\n".join(
        line.rstrip()
        for line in text.splitlines()
        if line.strip()
    )


def _resolved_tool(executable: str) -> tuple[str, Path, os.stat_result, str]:
    selected = shutil.which(executable) or executable
    real = Path(selected).resolve(strict=True)
    stat = real.stat()
    if not stat_module.S_ISREG(stat.st_mode):
        raise OSError(f"resolved tool is not a regular file: {real}")
    digest = _executable_sha256(
        str(real),
        stat.st_dev,
        stat.st_ino,
        stat.st_mtime_ns,
        stat.st_ctime_ns,
        stat.st_size,
    )
    return selected, real, stat, digest


def _tool_identity(executable: str) -> str:
    """Identify the executable selected by PATH, including content SHA256."""
    selected = shutil.which(executable) or executable
    try:
        selected, real, stat, digest = _resolved_tool(executable)
    except OSError as exc:
        return f"selected={selected};unavailable={type(exc).__name__}:{exc}"
    version = _tool_version_output(str(real), digest)
    return (
        f"selected={selected};realpath={real};mtime_ns={stat.st_mtime_ns};"
        f"size={stat.st_size};sha256={digest};version={version}"
    )


def _tool_identity_metadata(executable: str) -> dict[str, str]:
    """Machine-readable subset of :func:`_tool_identity` for provenance."""
    selected = shutil.which(executable) or executable
    try:
        selected, real, stat, digest = _resolved_tool(executable)
        version = _tool_version_output(str(real), digest)
    except OSError as exc:
        return {"selected": selected, "error": f"{type(exc).__name__}: {exc}"}
    return {
        "selected": selected,
        "realpath": str(real),
        "mtime_ns": str(stat.st_mtime_ns),
        "size": str(stat.st_size),
        "sha256": digest,
        "version": version,
    }


def _ast_fallback_enabled() -> bool:
    return os.environ.get("ABICHECK_ALLOW_AST_FALLBACK", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _auto_ast_fallback_eligible(backend: str) -> bool:
    """Whether this request is genuinely unpinned ``auto`` selection."""
    choice = (backend or "auto").strip().lower()
    env_pin = os.environ.get("ABICHECK_AST_FRONTEND", "").strip().lower()
    return choice == "auto" and env_pin not in {"castxml", "clang", "hybrid"}


def _parser_ast_toolchain(parser: Any) -> dict[str, str]:
    return dict(getattr(parser, "_abicheck_ast_toolchain", {}))


def _parser_ast_fallback_reason(parser: Any) -> str | None:
    value = getattr(parser, "_abicheck_ast_fallback_reason", None)
    return str(value) if value else None
