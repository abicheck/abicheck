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

"""Reusable Click option groups.

Stacked-decorator helpers that bundle related ``compare`` options so the large
``cli.py`` stays under the AI-readiness file-size cap. Imported at the top of
``cli.py`` and applied to ``compare_cmd``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

import click

F = TypeVar("F", bound=Callable[..., object])


def adr027_compare_options(func: F) -> F:
    """Add the ADR-027 API-surface-intelligence options to ``compare``.

    ``--pattern-verdicts`` / ``--explain-patterns`` (A4 modulation) and
    ``--surface-metrics`` (A1/D1.2 metric drift). Decorators apply bottom-up, so
    they are listed here in reverse of their displayed order.
    """
    func = click.option(
        "--surface-metrics",
        "surface_metrics",
        is_flag=True,
        default=False,
        help="Emit aggregate public-surface metric drift (ADR-027): "
        "public_surface_grew/shrank, undocumented_export_ratio_increased. "
        "Informational (COMPATIBLE).",
    )(func)
    func = click.option(
        "--explain-patterns",
        "explain_patterns",
        is_flag=True,
        default=False,
        help="Print idiom evidence behind each modulation (implies "
        "--pattern-verdicts).",
    )(func)
    func = click.option(
        "--pattern-verdicts/--no-pattern-verdicts",
        "pattern_verdicts",
        default=False,
        help="Modulate verdicts with idiom/anti-pattern evidence (ADR-027): "
        "demote opaque-pointer/PIMPL-hidden layout changes (header-aware only) "
        "and raise breaks when an opacity/handle guarantee is lost. Disclosed in "
        "the pattern_modulations ledger; reversible.",
    )(func)
    return func


def build_source_dump_options(func: F) -> F:
    """Add the ``--build-info`` / ``--sources`` embed options to ``dump``.

    Source-tree-centric inputs (ADR-028..033 amendment): ``--sources`` is a
    source checkout — L4 source ABI replay and the L5 graph are run inline and
    embedded; ``--build-info`` is an optional build dir / ``compile_commands.json``
    / pre-captured pack supplying L3 (auto-discovered inside the source tree when
    omitted). A path that is itself a pack directory from ``abicheck collect``
    is loaded as that pack instead. Embedding makes the ``.abi.json``
    self-contained, so a later ``compare old.json new.json`` carries the facts
    with no out-of-band directories. Applied bottom-up, so listed in reverse of
    display.
    """
    from pathlib import Path

    func = click.option(
        "--collect-mode", "collect_mode",
        type=click.Choice(["off", "build", "source-changed", "source-target", "graph-summary", "graph-full"]),
        default="source-target", show_default=True,
        help="ADR-033 D2 CI evidence mode selecting which layers to collect from "
        "--sources/--build-info: 'build' captures L3 build context only (no source "
        "replay), 'source-*'/'graph-*' collect L3+L4+L5 at the matching replay "
        "scope, 'off' embeds nothing.",
    )(func)
    func = click.option(
        "--allow-build-query", "allow_build_query", is_flag=True, default=False,
        help="Permit running the configured `build.query` command to emit a "
        "compile DB / exports (ADR-032 D5 query_build_system). Off by default: "
        "only existing build outputs are inspected — a full project build is "
        "never run.",
    )(func)
    func = click.option(
        "--build-config", "build_config",
        type=click.Path(exists=True, dir_okay=False, path_type=Path),
        default=None,
        help="Path to an `.abicheck.yml` build config (build system, query "
        "command, compile-DB location). Defaults to `.abicheck.yml` at the "
        "--sources tree root.",
    )(func)
    func = click.option(
        "--sources", "sources",
        type=click.Path(exists=True, path_type=Path),
        default=None,
        help="Source checkout to run L4 source ABI replay + the L5 graph over "
        "and embed inline. (A pack directory from `abicheck collect` is loaded "
        "as that pack instead.)",
    )(func)
    func = click.option(
        "--build-info", "build_info",
        type=click.Path(exists=True, path_type=Path),
        default=None,
        help="Optional L3 build context: a build dir, a compile_commands.json, "
        "or a pre-captured pack. Auto-discovered inside the --sources tree when "
        "omitted.",
    )(func)
    return func


def build_source_compare_options(func: F) -> F:
    """Add the build-info / sources compare options.

    By default ``compare old.json new.json`` reads build-info + source facts
    **embedded** in each snapshot (single-artifact UX). The optional
    ``--old-build-info`` / ``--new-build-info`` and ``--old-sources`` /
    ``--new-sources`` point at out-of-band pack directories to supply or
    override those facts per side; ``--collect-mode`` selects the inline
    collection mode (ADR-033 D2). All folded into the verdict as ordinary
    findings, never overriding artifact-backed ABI verdicts (ADR-028 D3).
    Applied bottom-up, so listed in reverse of displayed order.
    """
    from pathlib import Path

    pack_dir = click.Path(exists=True, file_okay=False, path_type=Path)
    func = click.option(
        "--collect-mode", "collect_mode",
        type=click.Choice(["off", "build", "source-changed", "source-target", "graph-summary", "graph-full"]),
        default="off", show_default=True,
        help="Inline collection mode (ADR-033 D2). 'off' uses embedded facts and "
        "any explicitly-provided pack directories. Other modes are recognized "
        "and reported in the coverage table but not yet collected inline.",
    )(func)
    func = click.option(
        "--new-sources", "new_sources", type=pack_dir, default=None,
        help="Out-of-band L4/L5 source pack for the new side (overrides embedded).",
    )(func)
    func = click.option(
        "--old-sources", "old_sources", type=pack_dir, default=None,
        help="Out-of-band L4/L5 source pack for the old side (overrides embedded).",
    )(func)
    func = click.option(
        "--new-build-info", "new_build_info", type=pack_dir, default=None,
        help="Out-of-band L3 build-info pack for the new side (overrides embedded).",
    )(func)
    func = click.option(
        "--old-build-info", "old_build_info", type=pack_dir, default=None,
        help="Out-of-band L3 build-info pack for the old side (overrides embedded).",
    )(func)
    return func
