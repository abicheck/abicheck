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
"""The ``-p``/``--compile-db`` match, owned by the typed dump pipeline
(ADR-063 Phase 10, Phase 1 row).

A compile database named on the command line is matched against the dump's
public headers: the first compile unit that includes the first header, else
the union of every unit's flags (``build_context.build_context_for_header``/
``build_context_union_fallback``). Until this module, the CLI computed that
match three separate times (real run, ``--dry-run`` depth check, dry-run
preview) and handed the pipeline pre-derived tokens. Now the pipeline takes
the compile database itself (``DumpExecutionOptions.compile_db``) and every
caller -- CLI and typed API alike -- goes through :func:`match_compile_db`.

It is deliberately *not* merged into the L3->L2 fold
(``buildsource.header_compile_context``): the fold reads ``--build-info``
compile units and has no union fallback, while this match reads a raw
``compile_commands.json`` and does. Folding one into the other would change
what either caller gets; this module only gives the existing match one owner.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

__all__ = ["CompileDbMatch", "match_compile_db", "try_match_compile_db"]


@dataclass(frozen=True)
class CompileDbMatch:
    """The castxml/clang flags derived from a compile database, and whether a
    compile-DB entry genuinely backs them.

    ``matched`` is distinct from ``bool(tokens)``: a matched unit with no
    ABI-relevant flags is still real build-context evidence.
    """

    tokens: tuple[str, ...] = ()
    matched: bool = False
    entry_count: int = 0
    has_conflicts: bool = False


def match_compile_db(
    compile_db: Path, headers: Sequence[Path], source_filter: str | None = None
) -> CompileDbMatch:
    """Load *compile_db* and match it against *headers*.

    Raises :class:`~abicheck.errors.AbicheckError` (a missing header path, a
    malformed database) or ``OSError`` (an unreadable one).
    """
    from ...build_context import (
        build_context_for_header,
        build_context_union_fallback,
        load_compile_db,
    )
    from ...dry_run_estimate import expand_header_inputs

    entries = load_compile_db(compile_db)
    expanded = expand_header_inputs(list(headers)) if headers else []
    if expanded:
        ctx = build_context_for_header(
            entries, expanded[0], source_filter=source_filter
        )
    else:
        ctx = build_context_union_fallback(entries, source_filter=source_filter)
    return CompileDbMatch(
        tokens=tuple(ctx.to_castxml_flags()),
        matched=ctx.compile_db_path is not None,
        entry_count=len(entries),
        has_conflicts=ctx.has_conflicts,
    )


def try_match_compile_db(
    compile_db: Path | None, headers: Sequence[Path], source_filter: str | None = None
) -> CompileDbMatch | None:
    """:func:`match_compile_db` for a dry run: ``None`` when no compile
    database was given, and an unmatched :class:`CompileDbMatch` when loading
    or matching fails (the real run fails on the same input, so a dry run
    only needs to say it cannot succeed)."""
    if compile_db is None:
        return None
    from ...errors import AbicheckError

    try:
        return match_compile_db(compile_db, headers, source_filter)
    except (AbicheckError, OSError, ValueError):
        return CompileDbMatch()
