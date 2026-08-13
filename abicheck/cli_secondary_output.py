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

"""The shared ``--secondary-format``/``--secondary-output`` option pair and
its coherence validator (Codex review, PR #748) -- a dependency-free leaf
module, not a member of ``cli_options.py`` itself.

``compare`` (``cli.py``) and ``scan`` (``cli_scan.py``) both declared this
flag family, and validated it, with separately hand-copied help text and
checks that had already begun to drift. The natural home for the shared
half is ``cli_options.py`` (every other cross-command Click option group
lives there) -- but ``scan``'s own validator lives in ``cli_scan_helpers.py``,
which sits on an existing import path back into ``cli_options.py``
(``cli_options -> cli_resolve -> service_scan -> scan_engine ->
cli_scan_helpers``), so a ``cli_scan_helpers -> cli_options`` edge closes a
real cycle the AI-readiness ``import-cycle-growth`` gate rejects. This module
is the leaf both sides can depend on without ever depending on each other:
``cli_options.py`` re-exports :func:`secondary_output_options` for the two
CLI modules that apply it as a decorator, and ``cli_scan_helpers.py`` /
``cli_compare_helpers.py`` import :func:`reject_incoherent_secondary_output`
directly from here rather than through ``cli_options``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TypeVar

import click

F = TypeVar("F", bound=Callable[..., object])


def secondary_output_options(
    formats: Sequence[str],
    *,
    format_help: str = "Emit a second output format from this same run, "
    "without re-running it a second time. Requires --secondary-output "
    "(writing two formats to the same stream would be ambiguous).",
) -> Callable[[F], F]:
    """Factory for the ``--secondary-format`` / ``--secondary-output`` pair.

    A factory rather than a bare decorator, mirroring ``cli_options.
    output_options``, because the *set* of renderable secondary formats
    legitimately differs per command (``compare`` supports the full
    ``json``/``markdown``/``sarif``/``html``/``junit``/``review`` set;
    ``scan --against`` only ever produces ``text``/``json``) -- but the
    option pair's structure, flag spellings, and ``--secondary-output``
    help text live here once.
    """

    def deco(func: F) -> F:
        func = click.option(
            "--secondary-output",
            "secondary_output",
            type=click.Path(dir_okay=False, path_type=Path),
            default=None,
            help="File path to write --secondary-format's output to. Must "
            "differ from --output/-o, or the secondary render would "
            "silently overwrite the primary report.",
        )(func)
        func = click.option(
            "--secondary-format",
            "secondary_fmt",
            type=click.Choice(list(formats)),
            default=None,
            help=format_help,
        )(func)
        return func

    return deco


def reject_incoherent_secondary_output(
    *,
    dry_run: bool,
    output: Path | None,
    secondary_fmt: str | None,
    secondary_output: Path | None,
) -> None:
    """The four ``--secondary-*`` coherence checks every command with the
    :func:`secondary_output_options` pair shares: a dry run asked to write
    a secondary report, a half-given ``--secondary-*`` pair either
    direction, and two reports aimed at the same file. Raised as
    ``UsageError`` (exit 64) up front, before any real work.

    Command-specific extra checks (``compare``'s ``--contract`` gate,
    ``scan``'s ``--artifact-set`` incompatibility) are NOT this function's
    job and stay in each command's own wrapper -- this covers only the
    ``--secondary-*`` pair's own internal coherence, the part that was
    previously duplicated byte-for-byte between the two commands.
    """
    if dry_run and secondary_output is not None:
        raise click.UsageError(
            "--dry-run cannot be combined with --secondary-output: a dry "
            "run performs no analysis and writes nothing, so there is no "
            "secondary report to produce."
        )
    if secondary_fmt is not None and secondary_output is None:
        raise click.UsageError(
            "--secondary-format requires --secondary-output: writing two "
            "output formats to the same stream would be ambiguous."
        )
    if secondary_output is not None and secondary_fmt is None:
        raise click.UsageError(
            "--secondary-output requires --secondary-format: with no "
            "format given there is nothing to render, and the path would "
            "be silently ignored."
        )
    if (
        secondary_output is not None
        and output is not None
        and secondary_output.resolve() == output.resolve()
    ):
        raise click.UsageError(
            "--secondary-output must differ from --output/-o: writing both "
            "formats to the same file would silently overwrite the primary "
            "report with the secondary one."
        )
