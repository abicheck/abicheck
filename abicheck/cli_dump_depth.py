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

"""``dump``'s ``--depth`` dial resolution -- split out of ``cli_dump_helpers.py``
purely to stay under the AI-readiness 2000-line hard cap.

A genuine leaf: only ``.buildsource.scan_levels`` and ``click``. Reachable
under its original names (``cli_dump_helpers.resolve_dump_depth``/
``resolve_dump_collect_context``) via that module's own lazy ``__getattr__``
shim (PEP 562), mirroring the identical pattern at the tail of
``cli_buildsource.py`` -- so every pre-existing ``from .cli_dump_helpers
import resolve_dump_depth`` (``cli_compare_helpers.py``) and ``from
.cli_dump_helpers import resolve_dump_collect_context`` (``cli.py``) keeps
working unchanged.
"""

from __future__ import annotations

from pathlib import Path

import click


def resolve_dump_depth(
    depth: str | None,
    default_mode: str,
) -> str:
    """Resolve the ``--depth`` dial into the internal collect-mode value.

    ``--depth`` is the friendly evidence-depth dial (same vocabulary as
    ``scan --depth``: binary/headers/build/source); it expands to the
    underlying ADR-033 collect mode via the shared ``scan_levels`` mapping so the
    commands stay consistent. When no depth preset is supplied, the command's
    *default_mode* is returned (``dump`` embeds at ``source-target``;
    ``compare`` reads at ``off``).
    """
    from .buildsource.scan_levels import (
        EvidenceDepth,
        SourceScope,
        depth_to_method,
        level_to_collect_mode,
    )

    if depth is None:
        return default_mode
    # Lowercased before the EvidenceDepth lookup (CodeRabbit review): the real
    # `dump` CLI always hands this an already-lowercased value (Click's own
    # `DepthParam.convert()`), but this function is also called directly
    # (tests, other typed-API callers) bypassing that normalization -- an
    # un-lowercased value raised a bare ValueError instead of resolving, and
    # this function's own deliberately-duplicated leaf mirror,
    # `service_compare_evidence._resolve_depth_collect_mode`, already
    # lowercases here.
    evidence_depth = EvidenceDepth(depth.lower())
    method = depth_to_method(evidence_depth)
    if method is None:
        # headers/binary depth reaches no source method (L2 is intrinsic) --
        # collect nothing.
        return "off"
    # dump/compare always resolve --depth source at target scope (ADR-043 D3):
    # the fix for the zero-TU defect where an explicit deep depth without a
    # change seed silently selected no translation units.
    return level_to_collect_mode(
        method, evidence_depth, source_scope=SourceScope.TARGET
    )


def resolve_dump_collect_context(
    depth: str | None,
    resolved_collect_mode: str | None,
    sources: Path | None,
    build_info: Path | None,
    headers: tuple[Path, ...],
    inputs_pack: Path | None = None,
) -> tuple[str, tuple[Path, ...]]:
    """Resolve the --depth preset into the internal collect mode for a dump.

    Returns the ``(collect_mode, headers)`` pair the caller should proceed
    with — ``--depth binary`` suppresses the L2 header AST (and, with it, the
    compile database the caller derives from ``--build-info``), and an
    explicitly-requested deep depth without a source tree / build context
    warns loudly (G21.7-style fail-loud).
    """
    # Resolve the --depth preset into the internal collect mode before any dump
    # path runs, so every branch (source-only / PE-Mach-O / ELF) embeds the same
    # evidence depth (G21.1). With no preset, dump embeds at "source-target".
    # ``compare``'s inline source-tree embed already resolved the mode and hands
    # it over via the private _resolved_collect_mode hook so we don't re-derive a
    # different default here (Codex review).
    if (
        resolved_collect_mode is not None
    ):  # pragma: no cover - only via compare's inline embed (integration)
        collect_mode = resolved_collect_mode
    else:
        collect_mode = resolve_dump_depth(depth, "source-target")
    # --depth binary suppresses the L2 header AST (symbols-only dump, ADR-037 D5).
    # A compile DB only feeds the header parse, and the caller derives it from
    # these headers, so dropping them drops it too. Compared case-insensitively
    # (CodeRabbit review): the real `dump` CLI always hands this function an
    # already-lowercased `depth` (Click's own `DepthParam.convert()`), but this
    # function is also called directly (tests, and any other typed-API caller)
    # bypassing that normalization -- an exact-case comparison there could
    # silently disagree with `resolve_dump_request_evidence`'s own `.lower()`
    # and report headers a real `--depth BINARY` invocation would suppress.
    if depth is not None and depth.lower() == "binary":
        headers = ()

    # An *explicitly* requested deep evidence depth (--depth) collects nothing
    # without a source tree / build context: _write_snapshot_output only embeds
    # when --sources/--build-info is given. Warn loudly rather than silently
    # writing an L0-L2 snapshot for an explicitly-requested deep depth (Codex
    # review). The bare default (collect_mode "source-target" with no flag) stays
    # silent -- embedding is a no-op there by design. G21.7-style fail-loud (a
    # warning, not an error).
    depth_requested = depth is not None
    if (
        depth_requested
        and collect_mode != "off"
        and sources is None
        and build_info is None
        and inputs_pack is None
    ):
        click.echo(
            f"Warning: evidence depth '{collect_mode}' was requested but no "
            "--sources/--build-info/--inputs was given; the snapshot will carry "
            "only L0-L2 data (no build/source/graph facts). Pass --sources, "
            "--build-info, or --inputs, or use --depth headers for an L2-only dump.",
            err=True,
        )
    return collect_mode, headers
