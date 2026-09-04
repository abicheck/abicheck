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
group, its `--version`/SIGTERM wiring, the registration imports whose only
purpose is the ``@main.command(...)`` side effect, and one lazy
compatibility shim.

That shim is the reason this file can be short without breaking anyone.
``abicheck.cli`` has been the documented import path for a long list of
private helpers -- sibling ``cli_*`` modules and the test suite both reach for
them there -- so every one stays resolvable at its historical name through a
module-level ``__getattr__`` (PEP 562). It resolves through
``importlib.import_module`` at *access* time, which is a runtime call rather
than a static import edge, so this module never grows a top-level dependency
on the packages that import back into it. New code should import from the
owner named in ``_MOVED`` below.
"""

from __future__ import annotations

import sys
from typing import Any

import click

# rich-click renders the (large) option lists in named panels for progressive
# disclosure (G21.8 / collapse M1). We keep the plain ``click`` API (so the
# module type-checks against click's stubs) and only base the root group on
# ``RichGroup`` -- that alone makes ``cls=_AbicheckGroup`` render the rich
# panels (and RichGroup.command produces RichCommand subcommands). Fall back to
# plain click.Group if rich-click is somehow unavailable so the CLI never
# hard-fails.
from . import __version__ as _abicheck_version, deadline
from .compat.cli import compat_group
from .frontends.cli import help as cli_help
from .frontends.cli.moved import MOVED, install_facade_guard
from .frontends.cli.runtime import _AbicheckGroup

__all__ = ["main"]

cli_help.configure_rich_help()  # register --help option-group panels (G21.8 / M1)


@click.group(cls=_AbicheckGroup)
@click.version_option(
    version=_abicheck_version,
    prog_name="abicheck",
    message="%(prog)s %(version)s (abicheck/abicheck)",
)
def main() -> None:
    """abicheck — ABI compatibility checker for C/C++ shared libraries."""
    # The plain CLI/CI path has no outer watchdog analogous to the MCP path's
    # service_scan._kill_process_tree; without this, an external SIGTERM
    # (job-scheduler cancellation, a CI step's own timeout) can orphan a
    # detached clang/castxml process group started by deadline.run_bounded
    # (Codex review, PR #591).
    deadline.install_sigterm_cleanup()


# ABICC-compatible subcommands. Eagerly imported above, deliberately: every
# consumer of `abicheck.cli` must get them registered.
main.add_command(compat_group)


def __getattr__(name: str) -> Any:
    """Resolve a name this module used to define, from its current owner.

    See :mod:`abicheck.frontends.cli.moved` for the map and why it is lazy.
    """
    owner = MOVED.get(name)
    if owner is not None:
        import importlib

        return getattr(importlib.import_module(owner), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


install_facade_guard(sys.modules[__name__])


# ---------------------------------------------------------------------------
# Sub-command modules. Imported for side-effect so their @main.command(...)
# decorators register the commands on the Click group above.
#
# When this file is run directly (``python -m abicheck.cli``, distinct from
# the documented ``python -m abicheck`` entry point in __main__.py but still
# a common thing to type), Python executes it as the ``__main__`` module --
# under a DIFFERENT sys.modules key than ``abicheck.cli``. Every module below
# does ``from ...cli import main``, a fresh relative import that would
# otherwise re-execute this file a second time under the real ``abicheck.cli``
# key, producing a second, empty ``main`` Click group; every
# ``@main.command(...)`` decorator then attaches to that second group, not the
# one actually running, so `python -m abicheck.cli --help` silently listed only
# the handful of commands defined directly in this file and omitted every
# sibling-registered one (scan, deps, ...). Alias the already-running module
# under its real package name first, so the relative imports below reuse it
# (Codex review).
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    sys.modules.setdefault("abicheck.cli", sys.modules[__name__])

from . import (  # noqa: E402  — must run after `main` is defined
    cli_aggregate,  # noqa: F401  — registers aggregate
    cli_buildsource,  # noqa: F401  — buildsource internals (no command of its own)
    cli_project,  # noqa: F401  — registers project (validate, validate-build, plan)
    cli_scan,  # noqa: F401  — registers scan
    cli_stack,  # noqa: F401  — registers deps (tree, compare)
)
from .cli_options import variant_options  # noqa: E402
from .frontends.cli.commands import (  # noqa: E402
    compare as _compare_cmd,  # noqa: F401  — registers compare
    dump as _dump_cmd,  # noqa: F401  — registers dump
)
from .frontends.cli.commands.compare import compare_cmd  # noqa: E402

# ADR-062 A1.7: --old-variant/--new-variant, applied to the already-
# registered `compare` command from here rather than as a `@variant_options`
# decorator inside frontends/cli/commands/compare.py itself -- that file is
# capped at the 800-line production maximum, and `click.option(...)` (which
# `variant_options` is built from) supports attaching a param to an
# already-built `click.Command` exactly this way: it appends directly to
# `compare_cmd.params` (`click.decorators._param_memo`'s own `isinstance(f,
# Command)` branch), the identical mechanism a `@click.option` stacked
# above `@main.command(...)` uses, just invoked as a function call instead
# of decorator syntax. This module (a 131-line registration facade with no
# `architecture/debt.yaml` entry at all) is where the CLI's other
# post-registration wiring already lives, so `compare.py` needs neither a
# new import nor a new debt-ledger baseline for this flag family (Codex
# review: "the flag plumbing must not grow a previously-compliant capped
# module").
variant_options(compare_cmd)

if __name__ == "__main__":
    main()
