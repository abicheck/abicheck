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

"""The one bounded ``clang -ast-dump=json`` run per translation unit.

Both the L4 source replay (:class:`~.source_extractors.clang.ClangSourceExtractor`)
and the L5 AST passes (:mod:`l5_ast_pass`) read a TU's clang AST. They share
this module's runner and the argv from
:func:`~.source_extractors.clang_command.build_clang_command`, so a TU that both
tiers read is dumped once: :func:`dump_tu_ast` runs the process and returns a
:class:`TuAstDump`, and each consumer maps that one result onto its own
diagnostics.

The dump is spilled to a temp file rather than captured in memory, so N
concurrent workers keep their (multi-GiB) payloads on disk until each parses.
Every failure point degrades to a :class:`TuAstDump` with a ``stage`` -- nothing
here raises for a bad TU; a consumer decides whether that is a diagnostic (L5,
advisory) or an L4 partial-coverage record.
"""

from __future__ import annotations

import json
import os
import subprocess  # noqa: S404 - AST extraction shells out to clang (never shell=True)
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import deadline
from .build_evidence import CompileUnit
from .source_extractors._argv import unredact_home
from .source_extractors._deadline_bound import run_bounded_for_extraction
from .source_extractors.base import SourceExtractionError
from .source_extractors.clang_command import (
    ast_clang_binary,
    build_clang_command,
    compile_unit_invocation,
)

#: The L5 passes' per-TU ceiling for one AST dump, folded against the scan's
#: own remaining ``--budget``. The L4 replay passes its own (larger) per-TU
#: timeout instead when the dump is shared (see ``ClangSourceExtractor``).
LOCAL_CAP_S = 120.0

#: Failure stages :class:`TuAstDump` reports. ``None`` means a tree was read.
STAGE_RUN = "run"
STAGE_EMPTY = "empty"
STAGE_JSON = "json"
STAGE_DEADLINE_BEFORE_PARSE = "deadline_before_parse"
STAGE_DEADLINE_AFTER_PARSE = "deadline_after_parse"


@dataclass(eq=False)
class TuAstDump:
    """One TU's bounded clang AST dump, before any consumer reads it.

    ``ast`` is the tree exactly as clang wrote it when ``stage`` is ``None``,
    else ``None``. The L4 reader and the L5 passes read locations differently,
    so each consumer decides its own view of the one tree (see
    :func:`~.l5_ast_pass.consume_tu_dump`). ``returncode`` is clang's exit
    status (``None`` when the process never ran to completion) and ``stderr``
    its decoded stderr. ``error`` is the failure's own text; ``raised`` is the
    exception behind a ``run`` failure, so a consumer that must propagate it
    (L4 has always let a missing ``clang`` surface) can re-raise it.
    ``json_extra_data`` marks the "more than one JSON document" shape (a SYCL or
    offload flag that made clang run two ``-cc1`` passes).

    The tree is the one large object here: consumers read it in turn, and the
    last one :meth:`release`\\ s it before any further subprocess runs.
    """

    ast: dict[str, Any] | None
    returncode: int | None
    stderr: str = ""
    stage: str | None = None
    error: str = ""
    json_extra_data: bool = False
    raised: BaseException | None = None

    def release(self) -> None:
        """Drop the tree so its memory is reclaimed before the next subprocess."""
        self.ast = None


def _check_deadline() -> str | None:
    """``None`` while the scan budget holds, else the failure text."""
    try:
        deadline.check()
    except deadline.DeadlineExceeded as exc:
        return str(exc)
    return None


def dump_tu_ast(
    cmd: list[str],
    *,
    cwd: str | None,
    timeout: float,
    unit_label: str,
) -> TuAstDump:
    """Run one AST dump command and read its tree, degrading on every failure.

    *cmd* is the complete command from
    :func:`~.source_extractors.clang_command.build_clang_command` (it already
    carries the fixed ``-Xclang -ast-dump=json -fsyntax-only`` tail). The run is
    bounded by ``min(timeout, remaining --budget)`` and is process-group-safe on
    timeout, via :func:`run_bounded_for_extraction`.

    A non-zero clang exit is *not* a failure: clang still prints the partial,
    error-recovered tree it built from the necessarily approximate replayed
    flags, and salvaging edges from it is deliberate. The caller records it
    (``returncode``) so coverage never counts that TU as cleanly parsed.
    """
    fd, name = tempfile.mkstemp(prefix="abicheck-tu-ast-", suffix=".json")
    path = Path(name)
    try:
        with os.fdopen(fd, "wb") as out:
            try:
                proc = run_bounded_for_extraction(
                    cmd,
                    timeout=timeout,
                    tool_label="clang",
                    unit_label=unit_label,
                    stdout=out,
                    stderr=subprocess.PIPE,
                    cwd=cwd,
                )
            except (OSError, SourceExtractionError) as exc:
                return TuAstDump(
                    None, None, "", stage=STAGE_RUN, error=str(exc), raised=exc
                )
        stderr = proc.stderr.decode("utf-8", "replace") if proc.stderr else ""
        if path.stat().st_size == 0:
            return TuAstDump(None, proc.returncode, stderr, stage=STAGE_EMPTY)
        late = _check_deadline()
        if late is not None:
            return TuAstDump(
                None,
                proc.returncode,
                stderr,
                stage=STAGE_DEADLINE_BEFORE_PARSE,
                error=late,
            )
        try:
            # Bytes, not text: json detects the encoding. Both json.loads and the
            # consumers' recursive walks can hit the recursion limit on a
            # pathologically deep TU; that degrades to a stage, not an abort.
            with open(path, "rb") as fh:
                ast: dict[str, Any] = json.load(fh)
        except (ValueError, RecursionError) as exc:
            return TuAstDump(
                None,
                proc.returncode,
                stderr,
                stage=STAGE_JSON,
                error=str(exc),
                # json.JSONDecodeError's message opens with its reason.
                json_extra_data=str(exc).startswith("Extra data"),
            )
        # The JSON load itself can consume the rest of the budget on a huge AST.
        late = _check_deadline()
        if late is not None:
            return TuAstDump(
                None,
                proc.returncode,
                stderr,
                stage=STAGE_DEADLINE_AFTER_PARSE,
                error=late,
            )
        return TuAstDump(ast, proc.returncode, stderr)
    finally:
        path.unlink(missing_ok=True)


def dump_compile_unit(
    compile_unit: CompileUnit,
    *,
    clang_bin: str,
    compiler_binary: str | None = None,
    timeout: float = LOCAL_CAP_S,
) -> TuAstDump:
    """The one AST dump of *compile_unit*, argv and cwd from the shared builders.

    Every token is un-redacted, macro values included (a home path inside a
    ``-D`` value must expand or clang parses a different TU), and the cwd is the
    effective directory the real build compiled from.
    """
    source, run_directory = compile_unit_invocation(compile_unit)
    cmd = build_clang_command(
        compile_unit,
        source,
        clang_bin=ast_clang_binary(clang_bin),
        compiler_binary=compiler_binary,
    )
    return dump_tu_ast(
        [unredact_home(tok) for tok in cmd],
        cwd=run_directory or None,
        timeout=timeout,
        unit_label=compile_unit.source,
    )
