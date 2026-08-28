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

"""The shared ``--write FORMAT=PATH`` option and its coherence validator
(Codex review, PR #748) -- a dependency-free leaf module, not a member of
``cli_options.py`` itself.

ADR-061 Phase 4: this is the first module to move under
``abicheck.frontends`` -- the "reusable Click-only option declaration into
``frontends/cli/options``" half of Phase 4 item 1. It qualified for a
same-session move for the identical reason ``artifact_plan.py`` did for
Phase 3: zero first-party imports, so the physical relocation cannot change
any import-cycle or dependency-direction fact about the rest of the
codebase. Its own cycle-avoidance role (below) is unaffected by *where* the
file lives, only by what it imports -- still nothing.

``--write`` replaces the ``--secondary-format``/``--secondary-output`` pair
it grew out of. The two flags only ever meant anything together -- half of
the pair was a usage error either direction -- so they were one option
spelled as two, and the format-and-destination operand says the same thing
in one place.

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


#: The validator the removed ``--secondary-output`` option used, kept as the
#: destination half of ``--write FORMAT=PATH``.
_WRITE_PATH = click.Path(dir_okay=False, path_type=Path)


def _parse_write_operand(
    value: str, formats: Sequence[str], param: click.Parameter | None
) -> tuple[str, Path]:
    """Split one ``FORMAT=PATH`` operand, or raise ``BadParameter``."""
    fmt, sep, raw_path = value.partition("=")
    if not sep or not fmt or not raw_path:
        raise click.BadParameter(
            f"{value!r}: expected FORMAT=PATH (e.g. "
            f"'{formats[0]}=report.{formats[0]}').",
            param=param,
        )
    if fmt not in formats:
        raise click.BadParameter(
            f"{fmt!r}: not a renderable format here "
            f"(choose from {', '.join(formats)}).",
            param=param,
        )
    # Through Click's own path validator, not a bare `Path`: the replaced
    # `--secondary-output` was a `click.Path(dir_okay=False)`, so naming an
    # existing directory was a parse-time usage error. Constructing the Path
    # directly let it through, and the failure moved to the secondary write
    # -- after the whole comparison and the primary render had already run,
    # leaving a partial report behind (Codex review).
    # `click.Path.convert` is typed as returning its generic value union;
    # `path_type=Path` is what actually makes it a `Path`, so narrow it here
    # rather than widening this function's own return type.
    converted = _WRITE_PATH.convert(raw_path, param, None)
    assert isinstance(converted, Path)
    return fmt, converted


def secondary_output_options(
    formats: Sequence[str],
    *,
    format_help: str = "Emit a second output format from this same run, to "
    "its own file, without re-running the analysis. FORMAT is one of "
    "{formats}; PATH must differ from --output/-o.",
) -> Callable[[F], F]:
    """Factory for the ``--write FORMAT=PATH`` option.

    A factory rather than a bare decorator, mirroring ``cli_options.
    output_options``, because the *set* of renderable secondary formats
    legitimately differs per command (``compare`` supports the full
    ``json``/``markdown``/``sarif``/``html``/``junit``/``review`` set;
    ``scan --against`` only ever produces ``text``/``json``) -- but the
    option's structure, flag spelling, and help text live here once.

    The parsed operand is published as the ``secondary_fmt`` /
    ``secondary_output`` pair the whole rendering path already threads, so
    only the user-facing spelling changed.
    """
    allowed = list(formats)

    def _callback(
        ctx: click.Context, param: click.Parameter, value: str | None
    ) -> str | None:
        if value is None:
            ctx.params["secondary_output"] = None
            return None
        fmt, path = _parse_write_operand(value, allowed, param)
        ctx.params["secondary_output"] = path
        return fmt

    def deco(func: F) -> F:
        func = click.option(
            "--write",
            "secondary_fmt",
            metavar="FORMAT=PATH",
            default=None,
            callback=_callback,
            help=format_help.format(formats="/".join(allowed)),
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
    """The two ``--write`` coherence checks every command carrying the
    :func:`secondary_output_options` option shares: a dry run asked to write
    a secondary report, and two reports aimed at the same file. Raised as
    ``UsageError`` (exit 64) up front, before any real work.

    The old ``--secondary-format``/``--secondary-output`` pair also needed a
    half-given check in each direction; ``--write``'s single ``FORMAT=PATH``
    operand cannot be half-given, so those two are gone rather than
    unreachable.

    Command-specific extra checks (``compare``'s ``--contract`` gate,
    ``scan``'s ``--artifact-set`` incompatibility) are NOT this function's
    job and stay in each command's own wrapper -- this covers only
    ``--write``'s own internal coherence, the part that was previously
    duplicated byte-for-byte between the two commands.
    """
    if dry_run and secondary_output is not None:
        raise click.UsageError(
            "--dry-run cannot be combined with --write: a dry run performs "
            "no analysis and writes nothing, so there is no secondary "
            "report to produce."
        )
    if (
        secondary_output is not None
        and output is not None
        and secondary_output.resolve() == output.resolve()
    ):
        raise click.UsageError(
            "--write's PATH must differ from --output/-o: writing both "
            "formats to the same file would silently overwrite the primary "
            "report with the secondary one."
        )
