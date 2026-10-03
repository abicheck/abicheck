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

"""The per-run settings only ``.abicheck.yml`` can give ``compare``.

``--lang``, ``--debug-format``, ``--dwarf-only``, ``--debuginfod``/
``--debuginfod-url``, ``--pdb-path`` and ``--source-method`` were demoted off
``compare``'s CLI (ADR-040 Lever 2, ADR-068 Phase 7, one-comparison-product
§4.1); the resolved project config is their only source. Both ``compare``
shapes -- two operands, and ``--no-baseline`` -- resolve that config through
the same function, and :func:`config_run_settings` is the one place that
turns it into these settings, so the two cannot read it differently. Before
it existed, ``--no-baseline`` read none of them: a C project's
``compile.lang: c`` parsed the audit's headers as C++, and the ``debug:``
block never reached the candidate's resolution.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...cli_helpers_compare import ResolvedCompareConfig

__all__ = ["ConfigRunSettings", "config_run_settings"]


@dataclass(frozen=True)
class ConfigRunSettings:
    """What ``.abicheck.yml`` alone decides about one ``compare`` run."""

    #: ``compile.lang``, defaulting to ``LANG_DEFAULT`` (``"c++"``, the
    #: removed flag's default), and whether the config stated it.
    lang: str
    lang_explicit: bool
    #: ``debug.format`` as written (``None`` when unset); see
    #: :attr:`effective_debug_format` for the extraction layer's form.
    debug_format: str | None
    dwarf_only: bool
    debuginfod: bool
    debuginfod_url: str | None
    #: ``debug.pdb_path`` as written; two operands may reject it
    #: (``compare_pdb_config``), one candidate uses it as its PDB.
    pdb_path: str | None
    #: ``source.method``, one rung of the collect-mode precedence
    #: (``cli_compare_helpers._resolve_compare_collect_mode``).
    source_method: str | None

    @property
    def effective_debug_format(self) -> str | None:
        """``debug.format`` with an explicit ``auto`` read as auto-detection."""
        from ...cli_dump_helpers import resolve_dump_debug_format

        return resolve_dump_debug_format(self.debug_format)


def config_run_settings(resolved_cfg: ResolvedCompareConfig) -> ConfigRunSettings:
    """The config-only settings of *resolved_cfg*, for either ``compare``."""
    from ...cli_options import LANG_DEFAULT

    return ConfigRunSettings(
        lang=resolved_cfg.compile_lang or LANG_DEFAULT,
        lang_explicit=resolved_cfg.compile_lang is not None,
        debug_format=resolved_cfg.debug_format,
        dwarf_only=bool(resolved_cfg.dwarf_only),
        debuginfod=bool(resolved_cfg.debuginfod),
        debuginfod_url=resolved_cfg.debuginfod_url,
        pdb_path=resolved_cfg.pdb_path,
        source_method=resolved_cfg.source_method,
    )
