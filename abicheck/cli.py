# Copyright 2026 Nikolay Petrov
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

"""CLI root: command registration and delegation. No product logic.

ADR-061 Phase 4. This file declares the Click root group and nothing else --
every command body, every shared runtime helper and every process-exit
decision lives in :mod:`abicheck.frontends.cli`. What remains here is the
group, its `--version`/SIGTERM wiring, and the lazy command registry.

``abicheck.cli`` was for a long time the documented import path for ~80
private helpers that had physically moved to sibling modules, kept resolvable
by a lazy ``__getattr__`` (PEP 562) over a name-to-owner table, plus a custom
module class that raised on assignment to any of those names -- since a
``monkeypatch.setattr`` against a lazily-resolved alias froze it and silently
defeated every later patch of the real owner. Every caller now imports from
the owner directly, so the table, the resolver and the assignment guard are
all gone. Import from the owner; patch the owner.
"""

from __future__ import annotations

import sys

import click

# rich-click renders the (large) option lists in named panels for progressive
# disclosure (G21.8 / collapse M1). We keep the plain ``click`` API (so the
# module type-checks against click's stubs) and only base the root group on
# ``RichGroup`` -- that alone makes ``cls=_AbicheckGroup`` render the rich
# panels (and RichGroup.command produces RichCommand subcommands). Fall back to
# plain click.Group if rich-click is somehow unavailable so the CLI never
# hard-fails.
from . import deadline
from .frontends.cli import help as cli_help
from .frontends.cli.registry import (
    ROOT_COMMANDS as _ROOT_COMMANDS,
    LazyGroup as _LazyGroup,
)
from .model.tool_version import build_version as _build_version

__all__ = ["main"]

_ABICHECK_VERSION: str = _build_version()  # release number + checkout commit

cli_help.configure_rich_help()  # register --help option-group panels (G21.8 / M1)


@click.group(cls=_LazyGroup, command_specs=_ROOT_COMMANDS)
@click.version_option(
    version=_ABICHECK_VERSION,
    prog_name="abicheck",
    message="%(prog)s %(version)s (abicheck/abicheck)",
)
def main() -> None:
    """abicheck — ABI compatibility checker for C/C++ shared libraries."""
    # The plain CLI/CI path has no outer watchdog analogous to the MCP path's
    # dry_run_estimate._kill_process_tree; without this, an external SIGTERM
    # (job-scheduler cancellation, a CI step's own timeout) can orphan a
    # detached clang/castxml process group started by deadline.run_bounded
    # (Codex review, PR #591).
    deadline.install_sigterm_cleanup()


# Keep command registration on this group when invoked as python -m abicheck.cli.
if __name__ == "__main__":
    sys.modules.setdefault("abicheck.cli", sys.modules[__name__])

if __name__ == "__main__":
    main()
