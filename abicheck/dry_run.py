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

"""Shared ``--dry-run`` model and renderer (ADR-043 D4).

A dry run resolves and validates an invocation -- classifies inputs, resolves
config/CLI precedence, checks tool availability, counts candidate translation
units when cheap to do so -- and prints exactly what would run, without
performing the actual analysis (no compiler/frontend invocation, no build
query, no network access, no output file / cache / report written).

One shared :class:`DryRunResult` model + renderer is used by ``dump``,
``compare``, ``scan``, and ``deps tree``/``deps compare`` so a dry run reads
the same way across every command instead of each Click callback hand-building
its own ad hoc strings.

Exit codes (never a compatibility verdict code -- ``2``/``4`` are reserved for
a real comparison):

- ``0`` -- the invocation is valid and can run (warnings may still be shown).
- ``1`` -- the requested analysis cannot be satisfied operationally (a
  ``blocker``, e.g. an explicit depth with no usable evidence for it).
- ``64`` -- invalid invocation or configuration (usage error; raised directly
  as :class:`click.UsageError`, not encoded here).
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field

#: Dry run never returns a compatibility verdict code (2/4); only these three.
EXIT_OK = 0
EXIT_BLOCKED = 1
EXIT_USAGE = 64

#: The canonical section ordering every command's dry-run report follows.
SECTION_ORDER: tuple[str, ...] = (
    "Command",
    "Inputs",
    "Resolved depth and source scope",
    "Headers and compile context",
    "Build/source inputs",
    "Tools and frontends",
    "Configuration and value origins",
    "Consumer/contract scoping",
    "Output and exit-code behavior",
)


@dataclass
class DryRunResult:
    """A deterministic, side-effect-free description of what a command would do."""

    command: str
    sections: dict[str, list[str]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)

    def add(self, section: str, *lines: str | None) -> None:
        """Append non-empty *lines* to *section*, creating it if needed."""
        clean = [ln for ln in lines if ln]
        if not clean:
            return
        self.sections.setdefault(section, []).extend(clean)

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def block(self, message: str) -> None:
        """Record a blocker: the requested analysis cannot be satisfied (exit 1)."""
        self.blockers.append(message)

    @property
    def exit_code(self) -> int:
        return EXIT_BLOCKED if self.blockers else EXIT_OK

    def render(self) -> str:
        out = [f"Command: {self.command}"]
        for title in SECTION_ORDER:
            if title == "Command":
                continue
            lines = self.sections.get(title)
            if not lines:
                continue
            out.append("")
            out.append(f"{title}:")
            out.extend(f"  {ln}" for ln in lines)
        # Any section not in the canonical ordering (command-specific extras)
        # still renders, after the canonical ones.
        for title, lines in self.sections.items():
            if title in SECTION_ORDER or not lines:
                continue
            out.append("")
            out.append(f"{title}:")
            out.extend(f"  {ln}" for ln in lines)
        if self.warnings:
            out.append("")
            out.append("Warnings/blockers:")
            out.extend(f"  warning: {w}" for w in self.warnings)
        if self.blockers:
            if not self.warnings:
                out.append("")
                out.append("Warnings/blockers:")
            out.extend(f"  blocker: {b}" for b in self.blockers)
        out.append("")
        out.append(
            f"Dry run only -- no analysis performed, nothing written. "
            f"Exit code: {self.exit_code}"
        )
        return "\n".join(out)


def reject_dry_run_with_output(dry_run: bool, output: object) -> None:
    """A dry run promises no output-file side effect; reject ``--output`` with it."""
    if dry_run and output is not None:
        import click

        raise click.UsageError(
            "--dry-run cannot be combined with -o/--output: a dry run performs no "
            "analysis and writes nothing, so there is no output to produce."
        )


def tool_status(*names: str) -> list[str]:
    """Cheap, read-only ``PATH`` lookup for each tool name (no subprocess run)."""
    lines = []
    for name in names:
        found = shutil.which(name)
        lines.append(f"{name}: {'found at ' + found if found else 'not found on PATH'}")
    return lines


def emit_dry_run(result: DryRunResult) -> None:
    """Print *result* to stdout and exit with its resolved exit code."""
    import click

    click.echo(result.render())
    raise SystemExit(result.exit_code)
