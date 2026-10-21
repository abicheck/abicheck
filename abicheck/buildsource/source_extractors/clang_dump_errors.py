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
"""How the L4 clang extractor reports one TU's failed AST dump.

:class:`~..clang_ast_run.TuAstDump` says *what* went wrong (its ``stage``); this
module says it the way L4 always has, as the :class:`SourceExtractionError` a
TU's partial-coverage record carries. Split out of ``clang.py``, which is over
its size baseline.
"""

from __future__ import annotations

import re
from typing import Any

from ..build_evidence import CompileUnit
from ..clang_ast_run import (
    STAGE_DEADLINE_AFTER_PARSE,
    STAGE_DEADLINE_BEFORE_PARSE,
    STAGE_EMPTY,
    STAGE_JSON,
    TuAstDump,
)
from .base import SourceExtractionError

_GENERATED_HEADER_RE = re.compile(
    r"fatal error:\s*['\"]?([^'\":\n]+\.(?:inc|def|h|hpp|hxx))['\"]?"
    r"\s*(?:file not found|: No such file or directory)",
    re.IGNORECASE,
)
_LIKELY_GENERATED_RE = re.compile(
    r"\.(inc|def)$|config\.h$|\.pb\.h$|moc_|\.generated\.", re.I
)


def _missing_generated_header_hint(stderr: str) -> str:
    """P19: turn a bare clang 'file not found' into an actionable build hint.

    L4 replay parses each TU under its real flags, but a *configure-only* tree has
    not produced its generated headers (TableGen ``*.inc``, ``config.h``, protobuf
    ``*.pb.h``…), so clang fails with a generic include error. Detect that shape and
    point the user at building the target first, rather than reporting an opaque
    parse failure. Returns ``""`` when the stderr is not a missing-header failure.
    """
    m = _GENERATED_HEADER_RE.search(stderr or "")
    if not m:
        return ""
    header = m.group(1)
    generated = bool(_LIKELY_GENERATED_RE.search(header))
    what = "generated header" if generated else "header"
    return (
        f"\n  hint: missing {what} '{header}'. L4 source replay needs the target's "
        "generated headers to exist — build the target (or its codegen step) first, "
        "then re-run; configure-only trees do not produce them."
    )


def ast_or_raise(compile_unit: CompileUnit, dump: TuAstDump) -> dict[str, Any]:
    """The dump's tree, or the L4 failure its stage stands for."""
    if dump.raised is not None:
        raise dump.raised
    source = compile_unit.source
    if dump.stage == STAGE_EMPTY:
        raise SourceExtractionError(
            f"clang produced no AST for {source} "
            f"(exit {dump.returncode}): {dump.stderr[:1000]}"
            + _missing_generated_header_hint(dump.stderr)
        )
    if dump.stage == STAGE_JSON:
        hint = ""
        if dump.json_extra_data:
            # "Extra data" means a second top-level JSON value follows the
            # first — some other flag not already collapsed to a single pass
            # (`_clang_context_args`'s `-fsycl-host-only` insertion) made this
            # compile emit more than one document to the same stdout stream
            # (e.g. an OpenMP/CUDA offload target flag, or a non-Intel
            # SYCL-capable driver this codebase doesn't yet special-case) —
            # name the likely cause here rather than a bare byte-offset,
            # mirroring the identical hint `dumper_clang_errors` gives the L2
            # header-AST backend for the same failure shape.
            hint = (
                " -- this looks like more than one JSON document on "
                "stdout, which happens when a compiler flag makes "
                "clang run multiple -cc1 passes for one compile (e.g. "
                "an unpinned SYCL/OpenMP/CUDA offload flag); pin a "
                "single compilation pass (e.g. -fsycl-host-only) in "
                "the build's own recorded flags"
            )
        raise SourceExtractionError(
            f"clang AST for {source} was not valid JSON: {dump.error}{hint}"
        )
    if dump.stage == STAGE_DEADLINE_BEFORE_PARSE:
        raise SourceExtractionError(
            f"scan deadline exceeded before loading AST for {source}"
        )
    if dump.stage == STAGE_DEADLINE_AFTER_PARSE:
        raise SourceExtractionError(
            f"scan deadline exceeded after loading AST for {source}"
        )
    if dump.stage is not None or dump.ast is None:
        raise SourceExtractionError(dump.error or f"clang failed on {source}")
    return dump.ast
