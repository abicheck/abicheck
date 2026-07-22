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

"""Small filesystem and AST-toolchain identity helpers for :mod:`dumper`."""

from __future__ import annotations

import hashlib
import os
import shutil
import signal
import stat as stat_module
import subprocess
import threading
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
def _tool_version_output(selected_path: str, digest: str) -> str:
    """Return bounded ``--version`` output for one exact executable revision."""
    del digest
    limit = 64 * 1024
    raw = bytearray()
    try:
        # Avoid preexec_fn: dumps can originate from threaded service/MCP
        # paths, where Python documents it as unsafe. A parent-side reader caps
        # output and kills a noisy process without buffering an unbounded pipe.
        process = subprocess.Popen(
            [selected_path, "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=os.name == "posix",
        )
        assert process.stdout is not None
        stdout = process.stdout

        def _kill() -> None:
            try:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
            except OSError:
                pass

        def _read_capped() -> None:
            while chunk := stdout.read(8192):
                remaining = limit + 1 - len(raw)
                if remaining > 0:
                    raw.extend(chunk[:remaining])
                if len(raw) > limit:
                    _kill()
                    break

        reader = threading.Thread(target=_read_capped, daemon=True)
        reader.start()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _kill()
            process.wait()
            raise
        finally:
            reader.join(timeout=1)
            stdout.close()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"unavailable:{type(exc).__name__}:{exc}"
    truncated = len(raw) > limit
    text = raw[:limit].decode("utf-8", errors="replace")
    if truncated:
        text += "\n[truncated]"
    return "\n".join(line.rstrip() for line in text.splitlines() if line.strip())


def _resolved_tool(executable: str) -> tuple[str, Path, os.stat_result, str]:
    selected = shutil.which(executable)
    if selected is None:
        separators = tuple(sep for sep in (os.sep, os.altsep) if sep)
        if not Path(executable).is_absolute() and not any(
            sep in executable for sep in separators
        ):
            raise FileNotFoundError(f"tool not found on PATH: {executable}")
        selected = executable
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


def _resolve_selected_tool(executable: str) -> str:
    """Return the exact executable selected now, rejecting missing bare names."""
    return _resolved_tool(executable)[0]


def _tool_identity(executable: str) -> str:
    """Identify the executable selected by PATH, including content SHA256."""
    selected = shutil.which(executable) or executable
    try:
        selected, real, stat, digest = _resolved_tool(executable)
    except OSError as exc:
        return f"selected={selected};unavailable={type(exc).__name__}:{exc}"
    version = _tool_version_output(selected, digest)
    return (
        f"selected={selected};realpath={real};mtime_ns={stat.st_mtime_ns};"
        f"size={stat.st_size};sha256={digest};version={version}"
    )


def _tool_identity_metadata(executable: str) -> dict[str, str]:
    """Machine-readable subset of :func:`_tool_identity` for provenance."""
    selected = shutil.which(executable) or executable
    try:
        selected, real, stat, digest = _resolved_tool(executable)
        version = _tool_version_output(selected, digest)
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


def _allow_unsupported_castxml_enabled() -> bool:
    """Explicit opt-in override for the CastXML version gate
    (``castxml_policy``). Same convention as ``_ast_fallback_enabled`` — a
    hard failure by default, degraded only on deliberate request."""
    return os.environ.get("ABICHECK_ALLOW_UNSUPPORTED_CASTXML", "").strip().lower() in {
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


def _parser_ast_supported(parser: Any) -> bool | None:
    value = getattr(parser, "_abicheck_ast_supported", None)
    return bool(value) if value is not None else None


def _parser_ast_unsupported_reasons(parser: Any) -> list[str]:
    return list(getattr(parser, "_abicheck_ast_unsupported_reasons", []) or [])
