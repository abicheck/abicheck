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

"""The CLI boundary for stored-package operand resolution -- one translation.

The resolution itself -- turning a release operand that *is* a stored
`ProjectSnapshot` package directory into the canonical-key -> `Path` map the
fan-out already builds from a live directory of `.so` files (ADR-062 A1.7),
including the side-correct ``--variant`` remediation ADR-068 Phase 7j asks
for -- moved to :func:`abicheck.workflows.release_inputs.
resolve_release_package_side` (ADR-061 gap D, closed). It had to: it raised
a real ``click.UsageError`` from inside the resolution, which is one of the
two things that kept the release request/plan off ``abicheck.service``.

The remediation example moved *with* it rather than staying behind. That is
not a weakening of :class:`~abicheck.errors.AmbiguousVariantSelectionError`'s
own "the engine names no flag" rule -- the rule exists because a bare
``--variant ID`` applies to both sides and is unsafe advice when only one is
ambiguous, and the engine function now takes the ``side`` it needs to render
the safe form. What is left here is the single translation of the typed
:class:`~abicheck.errors.ReleaseOperandUsageError` into a
``click.UsageError``, so the CLI keeps exiting ``64`` with the same text.

It was split out of ``cli_compare_release_matrix.py`` (Codex review, PR
#1184), which sits at its `architecture/debt.yaml` ``no_growth`` baseline
with no headroom, and AGENTS.md is explicit that the way to shrink such an
entry is to move responsibility out to a properly-owned module. That is
still why the file exists; what it owns is now smaller.

It lives here, in ADR-061's ``frontends`` ring, rather than as a new flat
``cli_*`` root sibling -- ``scripts/check_architecture.py`` rejects the
latter outright (``[frozen-root-family]``/``[root-module]``), and it is
right to: this is a front end translating an engine error into a usage
error, which is exactly what ``frontends/`` owns. It sits beside
``release_dry_run.py``/``release_summary.py``, the release fan-out's other
front-end helpers.

A deliberate **import leaf**: it imports nothing from ``abicheck.cli`` or
any ``cli_*`` sibling, so the module that consumes it gains no edge into
the CLI-registration import cycle (``scripts/check_ai_readiness.py``'s
``IMPORT_CYCLE_ALLOWLIST``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


def _resolve_release_package_side(
    side_dir: Path,
    variant_id: str | None,
    make_temp_dir: Callable[[str], Path],
    *,
    side: str,
) -> dict[str, Path] | None:
    """The Click-translating wrapper over :func:`abicheck.workflows.
    release_inputs.resolve_release_package_side`.

    The resolution itself, including the side-correct ``--variant``
    remediation example, moved to that engine module so the release
    request/plan is resolvable with no Click context (ADR-061 gap D's
    "Remaining scope" note, which named this function's ``click.UsageError``
    raises as one of the two things blocking it). This wrapper exists only
    to keep a usage error a usage error for a CLI caller: same message,
    same exit ``64``.
    """
    from ...errors import ReleaseOperandError
    from ...workflows.release_inputs import resolve_release_package_side

    try:
        return resolve_release_package_side(
            side_dir, variant_id, make_temp_dir, side=side
        )
    except ReleaseOperandError as exc:
        raise click.UsageError(str(exc)) from exc
