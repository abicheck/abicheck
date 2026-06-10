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


def evidence_dump_option(func: F) -> F:
    """Add the ADR-028 ``--evidence`` attach option to ``dump``."""
    from pathlib import Path

    func = click.option(
        "--evidence", "evidence_dir",
        type=click.Path(exists=True, file_okay=False, path_type=Path),
        default=None,
        help="Attach an existing EvidencePack directory (from `abicheck "
        "collect-evidence`) to the snapshot. Stores a lightweight, "
        "content-addressed reference; the pack itself stays out-of-band.",
    )(func)
    return func


def evidence_compare_options(func: F) -> F:
    """Add the ADR-028/ADR-029 evidence options to ``compare``.

    ``--old-evidence`` / ``--new-evidence`` attach packs whose build evidence is
    diffed into the verdict (never overriding artifact-backed findings, ADR-028
    D3); ``--evidence-mode`` selects the inline collection mode (ADR-033 D2).
    Applied bottom-up, so listed in reverse of displayed order.
    """
    from pathlib import Path

    func = click.option(
        "--evidence-mode", "evidence_mode",
        type=click.Choice(["off", "build", "source-changed", "source-target", "graph-summary", "graph-full"]),
        default="off", show_default=True,
        help="Inline evidence collection mode (ADR-033 D2). 'off' uses only "
        "explicitly-provided --old/--new-evidence packs. Other modes are "
        "recognized and reported in the coverage table but not yet collected "
        "inline in this release.",
    )(func)
    func = click.option(
        "--new-evidence", "new_evidence",
        type=click.Path(exists=True, file_okay=False, path_type=Path), default=None,
        help="EvidencePack directory for the new side.",
    )(func)
    func = click.option(
        "--old-evidence", "old_evidence",
        type=click.Path(exists=True, file_okay=False, path_type=Path), default=None,
        help="EvidencePack directory for the old side (from `abicheck "
        "collect-evidence`). Adds L3 build-context findings and an "
        "evidence-coverage table; never overrides artifact-backed ABI "
        "verdicts (ADR-028 D3).",
    )(func)
    return func
