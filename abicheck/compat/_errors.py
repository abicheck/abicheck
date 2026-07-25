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

"""Error classification helpers for the ABICC compat layer.

Split from ``abicheck/compat/cli.py`` to keep that module under the
AI-readiness file-size soft cap. The exit-code contract is documented in
``abicheck/compat/CLAUDE.md`` and the project-level ``CLAUDE.md``.
"""
from __future__ import annotations

import errno
import sys
from typing import NoReturn

import click

from ..errors import ProfileMismatchError, ScopeMismatchError


def _looks_like_tool_missing(msg: str, ctx: str) -> bool:
    """Return True when the error indicates missing external tooling."""
    tool_missing_msg = any(
        key in msg
        for key in (
            "not found in path",
            "command not found",
            "executable file not found",
        )
    )
    tool_missing_ctx = any(
        key in ctx for key in ("castxml", "compiler tool", "external tool")
    )
    return tool_missing_msg or tool_missing_ctx


def _is_descriptor_or_suppression_context(ctx: str) -> bool:
    """Return True when context points to malformed config/descriptor input."""
    return any(
        key in ctx
        for key in (
            "descriptor",
            "skip-symbols",
            "symbols-list",
            "skip-internal",
            "suppression",
            "logging",
        )
    )


def _classify_compat_error_exit_code(exc: BaseException, *, context: str = "") -> int:
    """Classify compat-mode failures into ABICC-style extended exit codes.

    Codes used:
      3  - missing external command/tooling
      4  - cannot access input files
      5  - header compilation/parsing failed
      6  - invalid descriptor/config/suppression inputs
      7  - failed to write report/output artifacts
      8  - dump/analysis pipeline failure
      9  - not_comparable (ADR-050 D2): OLD/NEW were not extracted under a
          comparable profile/scope contract, so no verdict was produced. The
          one code the 3-11 range documented no meaning for before this.
          Deliberately distinct from native ``compare``'s own ``16`` — the
          two commands maintain independent, non-overlapping exit-code
          schemes.
      10 - generic internal/tool error fallback
      11 - interrupted run
    """
    if isinstance(exc, KeyboardInterrupt):
        return 11
    if isinstance(exc, (ProfileMismatchError, ScopeMismatchError)):
        return 9

    msg = str(exc).lower()
    ctx = context.lower()
    tool_missing = _looks_like_tool_missing(msg, ctx)

    fs_code = _classify_fs_error(exc, ctx, tool_missing)
    if fs_code is not None:
        return fs_code
    if _is_compile_failure(msg):
        return 5
    if _looks_like_missing_path_message(msg):
        return 3
    if _is_descriptor_or_suppression_context(ctx):
        return 6
    if "report" in ctx or "output" in ctx:
        return 7
    if "dump" in ctx:
        return 8
    return 10


def _classify_fs_error(exc: BaseException, ctx: str, tool_missing: bool) -> int | None:
    """Classify filesystem/OS-level failures, or return None if not matched."""
    if isinstance(exc, FileNotFoundError):
        return 3 if tool_missing or _missing_filename_looks_like_command(exc, ctx) else 4
    if isinstance(exc, PermissionError):
        return 4
    if not isinstance(exc, OSError):
        return None
    if exc.errno in (errno.ENOENT,):
        return 3 if tool_missing else 4
    if exc.errno in (errno.EACCES, errno.EPERM):
        return 4
    if "report" in ctx or "output" in ctx:
        return 7
    return None


def _missing_filename_looks_like_command(exc: BaseException, ctx: str) -> bool:
    """Return True when ``exc.filename`` looks like a bare command name during a dump.

    ``FileNotFoundError`` from a missing executable (``subprocess`` couldn't
    spawn castxml/gcc) carries the bare command in ``exc.filename`` with no
    path separator, while a missing user input file is typically an absolute
    or relative path. This heuristic only kicks in inside a "during dump"
    context, so generic missing-input failures still classify as rc=4.
    """
    if "during dump" not in ctx:
        return False
    missing_name = (getattr(exc, "filename", "") or "").strip()
    return bool(missing_name) and "/" not in missing_name and "\\" not in missing_name


def _is_compile_failure(msg: str) -> bool:
    """Return True when message indicates compilation/parsing failures."""
    return any(
        token in msg
        for token in ("castxml failed", "cannot compile", "compilation terminated")
    )


def _looks_like_missing_path_message(msg: str) -> bool:
    """Return True when message indicates missing command/path."""
    return any(
        token in msg
        for token in ("not found in path", "command not found", "no such file or directory")
    )


def _compat_fail(context: str, exc: BaseException) -> NoReturn:
    """Print compat-mode error and exit with ABICC-style code."""
    click.echo(f"Error {context}: {exc}", err=True)
    sys.exit(_classify_compat_error_exit_code(exc, context=context))
