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

"""``compare``'s two operand-shape diagnostics: the application-instead-of-
library refusal, and the set-only-flags-on-a-single-file warning.

Moved out of ``commands/compare.py`` (AGENTS.md: "the way to shrink a debt
entry is to move responsibility out to a properly-owned module") when
ADR-068 Phase 2c/2d needed room there and that module was at the 800-line
production cap. Neither function touches the command object -- both take
plain resolved values and either raise ``click.UsageError`` or warn -- so a
leaf module is their natural home, and it also removes the reason
``cli_compare_helpers`` had to import them from a command module behind a
``# cycle`` comment. ``commands/compare.py`` re-exports both names, so that
import path (and ``frontends/cli/moved.py``'s mapping) is unchanged.
"""

from __future__ import annotations

from pathlib import Path

import click


def _reject_application_operand(
    old_input: Path, new_input: Path, old_kind: str, new_kind: str
) -> None:
    """Error when a `compare` operand is an application/executable, not a library."""
    which = old_input if old_kind == "app" else new_input
    raise click.UsageError(
        f"'{which}' looks like an application/executable, not a shared library, "
        "so `compare` cannot pair it as a library ABI. To check whether an "
        "application is still satisfied by a library, use "
        "`abicheck compare <old-lib> <new-lib> --used-by <app>`. If this file "
        "really is a shared library with an unusual ET_DYN/PIE layout, dump it "
        "first with `abicheck dump` and compare the resulting snapshots."
    )


def _warn_unused_set_flags(
    *,
    dso_only: bool,
    output_dir: Path | None,
    select: tuple[str, ...] = (),
    select_required: tuple[str, ...] = (),
    max_findings_per_library: int | None = None,
) -> None:
    """Warn that the set-input fan-out flags do not apply to single-file inputs."""
    used = []
    if dso_only:
        # Phase 7d demoted --dso-only to .abicheck.yml's release.dso_only --
        # there is no CLI flag left to name here, only the config key.
        used.append("release.dso_only")
    if output_dir is not None:
        used.append("--output-dir")
    if select:
        used.append("--select")
    if select_required:
        used.append("--select-required")
    if max_findings_per_library is not None:
        # Codex/CodeRabbit review: a single-pair `compare` has no release
        # summary to cap, and this option previously reached the single-pair
        # path silently -- the given value was neither applied nor reported,
        # the exact "dropped flag" defect this warning mechanism exists to
        # prevent for its siblings above.
        used.append("--max-findings-per-library")
    if used:
        click.echo(
            "Warning: " + ", ".join(used) + " only apply to directory/package "
            "(set) inputs; ignoring them for this single-file comparison.",
            err=True,
        )
