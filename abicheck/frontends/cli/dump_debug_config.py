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

"""The Click-bound remainder of ``dump``'s project-config resolution.

The resolution itself -- ``compile.lang``, ``build.compile_db_filter``,
``scope.exclude_headers`` and the ``debug:`` block, all read from one
selected file -- is ``workflows/dump/project_config.py``, shared with
``compare``'s inline evidence collection. What stays here needs the Click
context: the compile-context adapter and the stored-bundle ``lang`` rule
``compare``'s stored-bundle dispatch applies to its raw kwargs.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

if TYPE_CHECKING:
    from ...dry_run_estimate import CompileContext


def resolve_stored_bundle_lang(
    kwargs: dict[str, Any],
    *,
    config_explicit: bool,
    new_is_stored: bool,
    lang_default: str,
) -> tuple[str, bool]:
    """``compare``'s stored-bundle-facts dispatch (``old_is_stored``) own
    ``lang``/``lang_explicit`` resolution -- split out of ``compare.py``
    purely to keep that file under its architecture ``no_growth`` baseline
    (AGENTS.md: "move responsibility instead of raising the baseline"),
    the same reason this module exists at all.

    Phase 7 (one-comparison-product.md §4.1): `--lang` has no CLI spelling
    left on `compare` at all -- `ctx.get_parameter_source("lang")` can never
    report COMMANDLINE any more, and `kwargs["lang"]` is never populated by
    Click either, since it is not a declared parameter. `compile.lang` from
    the already-resolved project config (``kwargs["config"]``, set by
    ``compare_bundle_facts.resolve_dispatch_compile_context`` before this is
    called) is this dispatch path's only remaining source, mirroring
    ``run_compare``'s own ``resolved_cfg.compile_lang`` resolution.

    *new_is_stored* changes what "explicit" means, matching
    ``reject_explicit_compile_config_for_stored_pair``'s own explicit-vs-
    ambient distinction: stored/stored has no header-frontend extraction
    channel on either side, so (like every other ``compile:`` field in that
    situation) only a genuinely *explicit* ``--config``'s ``compile.lang``
    is a real, rejectable request -- an ambient project default stays
    harmlessly unused. Stored/live's NEW side genuinely extracts, so
    ambient and explicit ``compile.lang`` both apply normally, the same way
    ``compile.frontend``/``compile.include_dirs`` already do for that side.
    """
    from ...workflows.extraction import load_build_config

    project_cfg = None
    if kwargs.get("config") is not None:
        try:
            project_cfg = load_build_config(kwargs["config"])
        except ValueError:
            project_cfg = None
    compile_lang = project_cfg.compile_lang if project_cfg is not None else None
    lang = kwargs.get("lang") or compile_lang or lang_default
    if new_is_stored:
        lang_explicit = compile_lang is not None and config_explicit
    else:
        lang_explicit = compile_lang is not None
    return lang, lang_explicit


def resolve_dump_compile_context(
    *,
    gcc_options: str | None,
    sysroot: Path | None,
    nostdinc: bool,
    header_backend: str,
    includes: tuple[Path, ...],
    build_config: Path | None,
    sources: Path | None,
    frontend_context: str = "host",
    compiler_path: str | None = None,
    compiler_prefix: str | None = None,
    compiler_option_tokens: tuple[str, ...] = (),
    defines: tuple[str, ...] = (),
    config_explicit: bool | None = None,
) -> tuple[CompileContext, tuple[Path, ...]]:
    """Resolve the L2 compile context for a dump, folding the config compile: block.

    Returns ``(compile_context, includes)``. *defines* is ADR-074's
    ``-D/--define``, folded with ``compile.defines`` by macro name downstream."""
    from ...cli_options import resolve_compile_context

    return resolve_compile_context(
        click.get_current_context(),
        gcc_options=gcc_options,
        sysroot=sysroot,
        nostdinc=nostdinc,
        header_backend=header_backend,
        includes=includes,
        build_config=build_config,
        sources=sources,
        frontend_context=frontend_context,
        compiler_path=compiler_path,
        compiler_prefix=compiler_prefix,
        compiler_option_tokens=compiler_option_tokens,
        defines=defines,
        config_explicit=config_explicit,
    )


def resolve_and_check_dump_debug_format(
    so_path: Path | None,
    debug_format_opt: str | None,
) -> str | None:
    """Resolve the effective debug format and reject the usage error it implies.

    A genuine usage error in the real run (exit 64), raised here -- before the
    ``--dry-run`` branch -- so the dry run and the real run agree on it rather
    than the dry run downgrading it into an evidence blocker. Returns ``None``
    for a source-only dump, which has no binary to resolve a debug format
    against.
    """
    if so_path is None:
        return None
    from ...cli_dump_helpers import (
        check_dump_debug_format_error,
        resolve_dump_debug_format,
    )
    from ...workflows.extraction import normalize_binary_input as _peek_binary_format

    effective_debug_format = resolve_dump_debug_format(debug_format_opt)
    _, dry_run_binary_fmt = _peek_binary_format(so_path)
    debug_format_error = check_dump_debug_format_error(
        effective_debug_format, dry_run_binary_fmt
    )
    if debug_format_error is not None:
        raise click.BadParameter(debug_format_error)
    return effective_debug_format
