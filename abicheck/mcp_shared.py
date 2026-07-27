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
instance every ``@mcp.tool()`` registers against, plus the handful of pure,
stateless path/error helpers more than one tool module needs.

Deliberately excludes anything mutable (``MCP_TIMEOUT``/``MCP_MAX_FILE_SIZE``/
the structured-logging flag, the ``_check_file_size``/``_audit_log`` helpers
that read them, and ``_safe_write_path`` — no tool outside
:mod:`abicheck.mcp_server` itself writes an output file) — those stay owned
by :mod:`abicheck.mcp_server`, so this module can be imported by a sibling
tool module without that sibling being imported back by
:mod:`abicheck.mcp_server` (which would recreate the exact import cycle
splitting the tools out was meant to avoid; see
``abicheck/mcp_server_project.py``'s own module docstring).
"""

from __future__ import annotations

import logging
from pathlib import Path

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

try:
    mcp = FastMCP(
        "abicheck",
        instructions=(
            "ABI compatibility checker for C/C++ shared libraries. "
            "Detects breaking changes in .so/.dll/.dylib files before they reach production. "
            "Use abi_compare to diff two library versions, abi_dump to extract ABI snapshots, "
            "abi_list_changes to browse change kinds, and abi_explain_change for detailed explanations."
        ),
    )
except Exception as _exc:  # noqa: BLE001
    raise ImportError(
        f"Failed to initialise MCP support: {_exc}. "
        "Try: pip install --upgrade 'abicheck[mcp]'"
    ) from _exc


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
