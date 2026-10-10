# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
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

"""Which L2 header frontend a real run will use, and whether it can run.

``--dry-run`` used to list ``castxml``/``clang`` availability side by side
and estimate a runtime, while the real run resolved ``auto`` to castxml
(``extract.header_ast_backend._resolve_header_backend``) and failed with
"castxml not found in PATH" -- clang being present changed nothing, since
``auto`` only falls back to clang when ``ABICHECK_ALLOW_AST_FALLBACK`` opts
in. This module answers the question with the *same* resolution functions
the real dump calls, so the preview cannot disagree with the run.
"""

from __future__ import annotations

import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "HeaderFrontendPreflight",
    "operand_parses_headers",
    "preflight_header_frontend",
]


def operand_parses_headers(
    path: Path, effective_headers: Sequence[Path], dump_manifest: object | None
) -> bool:
    """Will the real run parse headers for this operand?

    The same two facts ``resolve_input`` acts on: a stored snapshot is loaded
    (``is_stored_snapshot_operand``), so its headers are never parsed whatever
    ``-H`` says; anything extracted live parses headers when it has an
    effective header list *or* a dump manifest (whose TUs carry their own).
    """
    from .input_resolution import is_stored_snapshot_operand

    if not effective_headers and dump_manifest is None:
        return False
    return not is_stored_snapshot_operand(path)


@dataclass(frozen=True)
class HeaderFrontendPreflight:
    """The resolved frontend, the tools it needs, and any missing one."""

    requested: str
    resolved: str
    required_tools: tuple[str, ...]
    missing_tools: tuple[str, ...]
    #: ``auto`` may fall back castxml -> clang at run time (opt-in only).
    clang_fallback_enabled: bool

    @property
    def runnable(self) -> bool:
        return not self.missing_tools

    def describe(self) -> str:
        line = f"header frontend: {self.resolved} (requested: {self.requested})"
        if self.clang_fallback_enabled:
            line += "; castxml->clang fallback enabled (ABICHECK_ALLOW_AST_FALLBACK)"
        return line

    def blocker(self) -> str | None:
        """The error the real run will raise, or ``None`` when it can run."""
        if self.runnable:
            return None
        missing = ", ".join(self.missing_tools)
        hint = ""
        if "castxml" in self.missing_tools:
            hint = (
                " Install castxml, or select the clang backend explicitly "
                "(--ast-frontend clang, compile.frontend: clang, or "
                "ABICHECK_AST_FRONTEND=clang)."
            )
        return (
            f"header frontend {self.resolved!r} needs {missing}, which is not "
            f"found on PATH; the real run would fail before parsing any header.{hint}"
        )


def preflight_header_frontend(
    requested: str,
    *,
    lang: str = "c++",
    gcc_path: str | None = None,
    gcc_prefix: str | None = None,
) -> HeaderFrontendPreflight:
    """Resolve *requested* exactly as the dump will, then check its tools.

    Tool presence is a ``PATH`` lookup only (no subprocess), matching the
    dump's own ``_resolve_selected_tool``/``_clang_available`` checks.
    """
    from ..dumper_clang import _resolve_clang_bin
    from ..errors import SnapshotError
    from ..extract.header_ast_backend import _resolve_header_backend
    from ..extract.headers.ast_config import _resolve_compiler_binary
    from ..extract.headers.toolchain import (
        _ast_fallback_enabled,
        _auto_ast_fallback_eligible,
    )

    resolved = _resolve_header_backend(requested)
    compiler = "cc" if lang == "c" else "c++"
    fallback = _auto_ast_fallback_eligible(requested) and _ast_fallback_enabled()

    def _clang_tool() -> tuple[str, bool]:
        try:
            return _resolve_clang_bin(compiler, gcc_path, gcc_prefix), True
        except SnapshotError:
            return ("clang" if lang == "c" else "clang++"), False

    required: list[str] = []
    missing: list[str] = []
    if resolved in ("castxml", "hybrid"):
        required.append("castxml")
        castxml_missing = [] if shutil.which("castxml") else ["castxml"]
        # castxml emulates a host compiler. An explicit gcc_path/gcc_prefix
        # selects it through the dump's own resolver (``dumper._castxml_dump``
        # passes *compiler* unchanged in that case), and castxml fails when
        # that binary is absent. The implicit default is not asserted here:
        # the dump picks gcc vs g++ from the parsed headers' content.
        if gcc_path or gcc_prefix:
            cc_bin, _ = _resolve_compiler_binary(compiler, gcc_path, gcc_prefix)
            required.append(cc_bin)
            if shutil.which(cc_bin) is None:
                castxml_missing.append(cc_bin)
        # An opted-in auto fallback reaches clang when castxml fails.
        if castxml_missing and not (fallback and _clang_tool()[1]):
            missing.extend(castxml_missing)
    if resolved in ("clang", "hybrid"):
        name, ok = _clang_tool()
        required.append(name)
        if not ok:
            missing.append(name)
    return HeaderFrontendPreflight(
        requested=requested,
        resolved=resolved,
        required_tools=tuple(required),
        missing_tools=tuple(missing),
        clang_fallback_enabled=fallback,
    )
