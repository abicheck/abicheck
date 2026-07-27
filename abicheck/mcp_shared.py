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

"""Leaf module shared by :mod:`abicheck.mcp_server` and its sibling tool
modules (e.g. :mod:`abicheck.mcp_server_project`) — the ``FastMCP`` server
instance every ``@mcp.tool()`` registers against, the mutable
timeout/file-size/log-format configuration every tool must honor (ADR-021b
D2/D3/D4), and the handful of pure, stateless path/error helpers more than
one tool module needs.

This module (not :mod:`abicheck.mcp_server`) is the single source of truth
for ``MCP_TIMEOUT``/``MCP_MAX_FILE_SIZE``/``_structured_logging`` so that
*every* tool — regardless of which module defines it — observes the same
``--timeout``/``--max-file-size``/``--log-format`` override a running
server was launched with. A module that only did ``from .mcp_shared import
MCP_TIMEOUT`` would bind a stale snapshot (Python's ``from X import Y``
copies the value at import time; a later ``mcp_shared.MCP_TIMEOUT = ...``
would not update that copy) — so every reader accesses these three names as
``mcp_shared.NAME`` (module-qualified), never as a bare imported name, and
:func:`abicheck.mcp_server.main` mutates them the same way
(``mcp_shared.MCP_TIMEOUT = args.timeout``, not ``global``).

This module is imported by :mod:`abicheck.mcp_server` (for the shared state
above) and by sibling tool modules like
:mod:`abicheck.mcp_server_project` — but never imports either of them back,
so pulling the mutable config in here (rather than leaving it split across
whichever module happens to define a given tool) doesn't recreate the exact
import cycle splitting the tools out was meant to avoid; see
``abicheck/mcp_server_project.py``'s own module docstring. ``_safe_write_path``
stays in :mod:`abicheck.mcp_server` — no tool outside that module writes an
output file, so it has no reason to be shared state.
"""

from __future__ import annotations

import concurrent.futures as _futures
import logging
import os as _os
from collections.abc import Callable
from pathlib import Path
from typing import ParamSpec, TypeVar

from .errors import AbicheckError

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as _exc:
    _msg = (
        "MCP support requires the 'mcp' package. "
        "Install it with: pip install abicheck[mcp]"
    )
    raise ImportError(_msg) from _exc
except Exception as _exc:  # noqa: BLE001
    # Guard against partial installs or other init-time failures from mcp internals
    raise ImportError(
        f"Failed to initialise MCP support: {_exc}. "
        "Try: pip install --upgrade 'abicheck[mcp]'"
    ) from _exc

_logger = logging.getLogger("abicheck.mcp")

_P = ParamSpec("_P")
_R = TypeVar("_R")


def _call_with_timeout(
    fn: Callable[_P, _R], /, *args: _P.args, **kwargs: _P.kwargs
) -> _R:
    """Run ``fn(*args, **kwargs)`` in a thread bounded by ``MCP_TIMEOUT``.

    ADR-021b D2: every tool invocation must have a configurable timeout
    rather than blocking the MCP stdio server indefinitely. Raises
    ``concurrent.futures.TimeoutError`` on expiry; any exception *fn* itself
    raises propagates unchanged (re-raised by ``future.result()``) so callers
    can catch their own domain exceptions the same way they would a direct
    call.

    Uses an explicit ``pool.shutdown(wait=False)`` in a ``finally`` rather
    than ``with ThreadPoolExecutor(...) as pool:`` — the ``with`` form calls
    ``shutdown(wait=True)`` on exit (including on a ``return`` from inside
    the block, which still runs ``__exit__`` before the function actually
    returns control to its caller), which blocks until the still-running
    worker finishes even after ``future.result(timeout=...)`` has already
    raised ``TimeoutError``, defeating the point of the timeout for a
    genuinely stuck call. Originally implemented only in
    ``mcp_server_project.py`` for the four project tools; moved here and
    also wired into ``mcp_server.py``'s ``abi_dump``/``abi_compare``/
    ``abi_audit`` after a review found those three still used the blocking
    ``with`` form, so a stuck resolve/compare there could exceed the
    advertised timeout indefinitely (Codex review) — this module's own
    ``MCP_TIMEOUT`` is referenced directly (not via a qualified import),
    since a function defined in this module always sees its own module's
    live global, unlike a caller in another module importing the bare name.
    """
    pool = _futures.ThreadPoolExecutor(max_workers=1)
    try:
        future = pool.submit(fn, *args, **kwargs)
        return future.result(timeout=MCP_TIMEOUT)
    finally:
        pool.shutdown(wait=False)


def _env_int(name: str, default: str) -> int:
    """Parse an integer environment variable with a clear error on bad input."""
    raw = _os.environ.get(name, default)
    try:
        return int(raw)
    except ValueError:
        raise ValueError(
            f"Environment variable {name}={raw!r} is not a valid integer"
        ) from None


#: Maximum seconds for a single tool invocation. Mutable — read this as
#: ``mcp_shared.MCP_TIMEOUT`` at call time, never via a bare imported name
#: (see module docstring).
MCP_TIMEOUT: int = _env_int("ABICHECK_MCP_TIMEOUT", "120")

#: Maximum input file size in bytes (default 500 MB). Mutable — same rule.
MCP_MAX_FILE_SIZE: int = _env_int("ABICHECK_MCP_MAX_FILE_SIZE", str(500 * 1024 * 1024))

#: Structured JSON log format flag (set via --log-format json). Mutable — same rule.
_structured_logging: bool = False

try:
    mcp = FastMCP(
        "abicheck",
        instructions=(
            "ABI compatibility checker for C/C++ shared libraries. "
            "Detects breaking changes in .so/.dll/.dylib files before they reach production. "
            "Use abi_compare to diff two library versions, abi_dump to extract ABI snapshots, "
            "abi_audit/abi_scan for single-build hygiene and source-intelligence scans, "
            "abi_deps to resolve a binary's dependency stack, abi_aggregate to fold per-target "
            "reports into one gate decision, abi_project_validate/abi_project_plan for project "
            "config validation and run-plan generation, abi_list_changes to browse change kinds, "
            "and abi_explain_change for detailed explanations."
        ),
    )
except Exception as _exc:  # noqa: BLE001
    raise ImportError(
        f"Failed to initialise MCP support: {_exc}. "
        "Try: pip install --upgrade 'abicheck[mcp]'"
    ) from _exc


def _check_file_size(path: Path, *, label: str = "input") -> None:
    """Raise ValueError if *path* exceeds ``MCP_MAX_FILE_SIZE`` (ADR-021b D3)."""
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return  # let downstream handle missing files
    except OSError as exc:
        # str(exc) on an OSError embeds the full filename (e.g. "[Errno 13]
        # Permission denied: '/home/user/private/x.yml'") -- ValueError
        # messages are surfaced verbatim by _sanitize_error below, so use
        # only the strerror portion to avoid leaking the path (Codex review).
        raise ValueError(
            f"Cannot check {label} file size: {exc.strerror or exc}"
        ) from exc
    if size > MCP_MAX_FILE_SIZE:
        raise ValueError(
            f"{label} is {size / (1024 * 1024):.1f} MB, "
            f"exceeds limit of {MCP_MAX_FILE_SIZE / (1024 * 1024):.0f} MB"
        )


def _audit_log(
    tool: str,
    inputs: dict[str, str],
    duration_s: float,
    status: str,
    verdict: str | None = None,
) -> None:
    """Log a tool invocation for audit purposes (ADR-021b D4)."""
    record = {
        "tool": tool,
        "inputs": inputs,
        "duration_s": round(duration_s, 3),
        "status": status,
    }
    if verdict is not None:
        record["verdict"] = verdict
    if _structured_logging:
        import json as _json

        _logger.info(_json.dumps(record))
    else:
        parts = [f"tool={tool}"]
        for k, v in inputs.items():
            parts.append(f"{k}={v}")
        parts.append(f"duration={duration_s:.3f}s")
        parts.append(f"status={status}")
        if verdict is not None:
            parts.append(f"verdict={verdict}")
        _logger.info(" ".join(parts))


def _safe_read_path(raw: str, *, label: str = "path") -> Path:
    """Resolve and validate a path for reading.

    - Resolves symlinks and `..` components.
    - Does NOT restrict to a specific directory (read paths are user-specified).
    - Returns the resolved Path.

    Raises ValueError with a generic message on obviously bad input.
    """
    if not raw or raw.strip() == "":
        raise ValueError(f"Empty {label} is not allowed")
    try:
        return Path(raw).resolve()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {label}: {exc!s}") from exc


def _sanitize_error(exc: Exception, *, context: str = "operation") -> str:
    """Return a safe error message that does not leak filesystem paths or internals."""
    # Known domain errors: safe to surface as-is
    if isinstance(exc, AbicheckError):
        return str(exc)
    if isinstance(exc, (ValueError, KeyError)):
        return str(exc)
    # OS/IO errors: return generic message, log details internally
    if isinstance(exc, (OSError, FileNotFoundError, PermissionError)):
        _logger.debug("OS error in %s: %s", context, exc, exc_info=True)
        return f"{context} failed: file system error (check logs for details)"
    # All others: generic
    _logger.debug("Unexpected error in %s: %s", context, exc, exc_info=True)
    return f"{context} failed: unexpected error"
