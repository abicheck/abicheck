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

"""One repeatable export request: ``-o FORMAT=DESTINATION`` (plan slice 7m).

``one-comparison-product.md`` §6's Phase 7l adopted this as the replacement
for **four** parallel mechanisms answering the same question -- *which
artifacts does this analysis produce?*

===========================  =================================================
Retired                      Replaced by
===========================  =================================================
``--format FMT``             ``-o FMT=-``
``-o PATH`` (path-only)      ``-o FMT=PATH``
``--write FMT=PATH``         ``-o FMT=PATH``, repeatable
``--output-dir DIR``         ``-o json=DIR/`` (a *directory* destination)
``--max-findings-per-...``   nothing: it is a consequence, see below
===========================  =================================================

Phase 7k had declined the ``--write``/``--format`` merge for one stated
reason -- "``--format`` with no ``-o`` renders to *stdout*, which ``--write``
cannot express at all (its grammar requires a PATH)". ``-`` is what dissolves
that: a destination of ``-`` **is** the stdout spelling, so there is one
grammar rather than two, and the declined merge's own objection ("a
path-less ``--write`` value would be one flag carrying two grammars") no
longer applies.

Why the other two retire as *consequences* rather than by deletion
--------------------------------------------------------------------

Plan §Non-goals forbids shortening the CLI by hiding behaviour or ignoring
supplied input, so neither flag may simply vanish:

* ``--output-dir``'s per-component fan-out survives as a **directory
  destination**. ``-o json=reports/`` writes exactly what ``--output-dir
  reports`` wrote -- one complete, uncapped per-library report plus
  ``summary.json`` -- and it is now part of the same export set as every
  other artifact, so collision detection covers it too.
* ``--max-findings-per-library``'s summary cap survives as an *automatic*
  bound rather than a decision: a **machine** export (``json``/``sarif``/
  ``junit``) is never truncated, and a **human** summary
  (``markdown``/``review``/``html``/``oneline``) is bounded at
  :data:`~abicheck.report.release_display_limits.
  MAX_RELEASE_FINDINGS_PER_LIBRARY` with the truncation disclosed exactly as
  before. The user therefore no longer *decides* anything: asking for
  complete data is asking for a machine format, which is what the flag's own
  help text already told them to do.

Grammar
-------

``FORMAT=DESTINATION``, where ``DESTINATION`` is

``-``
    standard output. At most one export may name it (two documents
    interleaved on one stream is not an artifact), and it is the default
    when no ``-o`` is given at all.
``a path ending in ``/`` (or ``os.sep``), or an existing directory``
    a directory export -- the per-component fan-out. Only commands that
    *have* a fan-out accept one (``supports_directory``), and only in the
    formats that fan-out actually produces.
``anything else``
    a file, validated through Click's own ``click.Path(dir_okay=False)`` at
    parse time so naming an existing directory is a usage error *before* the
    analysis runs rather than after it (the same reasoning the retired
    ``--write`` operand's own path validation recorded).

The operand is split on the **first** ``=`` only, which is what makes a
Windows destination work unchanged: ``-o json=C:\\out\\report.json`` splits
into ``json`` and ``C:\\out\\report.json`` -- a drive letter carries a colon,
never an equals sign -- and a destination containing further ``=``
characters is preserved verbatim.

Collision detection is part of the grammar, not a downstream concern: two
exports resolving to one destination would mean the second silently
overwrites the first, so it is a usage error, checked across *every* target
(file and directory alike) rather than only within one flag's own repeats,
which is all the retired ``--write`` callback could see.

This module is deliberately a **leaf** (``click`` and the standard library,
nothing first-party), for the same cycle-avoidance reason
``secondary_output.py`` -- the ``--write`` module it replaces -- recorded:
``cli_options.py`` re-exports it, and modules that sit on an import path
*back into* ``cli_options`` need the parser half without acquiring that
edge.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

import click

F = TypeVar("F", bound=Callable[..., object])

#: The destination spelling that means standard output.
STDOUT_DESTINATION = "-"

#: The metavar every command's ``-o`` shows, so the grammar is legible from
#: ``--help`` without reading prose.
EXPORT_METAVAR = "FORMAT=DESTINATION"

#: Destination validator for a *file* target. ``dir_okay=False`` is the point:
#: a destination that is an existing directory must not be silently treated as
#: a file target (it becomes a directory export above, or a usage error on a
#: command with no fan-out).
_FILE_DESTINATION = click.Path(dir_okay=False, path_type=Path)


@dataclass(frozen=True, slots=True)
class ExportTarget:
    """One artifact this run was asked to produce."""

    #: The renderer to project the one completed evaluation through.
    fmt: str
    #: Where it goes. ``None`` means standard output.
    destination: Path | None
    #: Whether *destination* names a directory receiving a per-component
    #: fan-out rather than one file receiving one document.
    is_directory: bool = False
    #: The operand exactly as the user typed it, for diagnostics.
    spelling: str = ""

    @property
    def to_stdout(self) -> bool:
        """Whether this export goes to standard output."""
        return self.destination is None

    @property
    def is_document(self) -> bool:
        """Whether this export is one rendered document (not a fan-out)."""
        return not self.is_directory


@dataclass(frozen=True, slots=True)
class ExportSet:
    """Every artifact one invocation produces, in the order requested.

    ``targets`` always holds at least one *document* target: when a command
    is given only directory exports (or no ``-o`` at all), the command's own
    default document export is prepended, which is what keeps ``-o
    json=reports/`` behaving exactly like the retired ``--output-dir
    reports`` -- per-library reports in the directory, the summary on stdout.
    """

    targets: tuple[ExportTarget, ...]
    #: Whether the user actually typed ``-o``. ``False`` means every target
    #: here is the command's built-in default, which is what lets a caller
    #: distinguish "asked for stdout" from "asked for nothing".
    explicit: bool = False

    @property
    def documents(self) -> tuple[ExportTarget, ...]:
        """The rendered-document targets, in request order."""
        return tuple(t for t in self.targets if t.is_document)

    @property
    def directories(self) -> tuple[ExportTarget, ...]:
        """The per-component fan-out targets, in request order."""
        return tuple(t for t in self.targets if t.is_directory)

    @property
    def primary(self) -> ExportTarget:
        """The document target the surrounding machinery treats as "the" report.

        Not a user-visible concept and deliberately not a second grammar:
        every document target renders from the *same* envelope with the same
        options (plan §7 F-19), so which one is "primary" can change nothing
        about any document's bytes. It exists only because the exit-code
        fold, the abort renderers and the release fan-out all still take one
        ``fmt``/``output`` pair, and something has to be handed to them. A
        stdout target wins when present, matching the retired
        ``--format``/``-o`` pair's own shape (the terminal report was the
        primary one); otherwise the first requested document is it.
        """
        docs = self.documents
        for target in docs:
            if target.to_stdout:
                return target
        return docs[0]

    @property
    def secondaries(self) -> tuple[ExportTarget, ...]:
        """Every document target other than :attr:`primary`."""
        primary = self.primary
        return tuple(t for t in self.documents if t is not primary)

    @property
    def formats(self) -> tuple[str, ...]:
        """Every requested format, document and directory alike, in order."""
        return tuple(t.fmt for t in self.targets)

    @property
    def file_targets(self) -> tuple[ExportTarget, ...]:
        """Every target that writes to the filesystem (not stdout)."""
        return tuple(t for t in self.targets if not t.to_stdout)

    @property
    def destinations(self) -> tuple[Path, ...]:
        """Every filesystem destination, in request order."""
        return tuple(t.destination for t in self.file_targets if t.destination)


def _grammar_error(value: str, formats: Sequence[str], detail: str) -> str:
    example = formats[0] if formats else "json"
    return (
        f"{value!r}: {detail} -o takes {EXPORT_METAVAR} and is repeatable "
        f"(e.g. -o {example}=report.{example}, or -o {example}=- for stdout). "
        "The path-only -o, --format, --write and --output-dir were retired "
        "in favour of this one export request."
    )


def parse_export_operand(
    value: str,
    formats: Sequence[str],
    *,
    param: click.Parameter | None = None,
    supports_directory: bool = False,
    directory_formats: Sequence[str] = (),
) -> ExportTarget:
    """Parse one ``FORMAT=DESTINATION`` operand into an :class:`ExportTarget`.

    Raises ``click.BadParameter`` (exit 64 through ``_AbicheckGroup``) for
    every malformed operand, including the retired path-only spelling -- a
    bare ``-o report.json`` has no ``=`` and is reported as the grammar
    error it now is, never silently accepted as either half of the old pair.
    """
    fmt, sep, raw_destination = value.partition("=")
    if not sep:
        raise click.BadParameter(
            _grammar_error(
                value, formats, "missing '=' between FORMAT and DESTINATION."
            ),
            param=param,
        )
    if not fmt:
        raise click.BadParameter(
            _grammar_error(value, formats, "no FORMAT before '='."), param=param
        )
    if not raw_destination:
        raise click.BadParameter(
            _grammar_error(value, formats, "no DESTINATION after '='."), param=param
        )
    if fmt not in formats:
        raise click.BadParameter(
            f"{fmt!r}: not a renderable format here "
            f"(choose from {', '.join(formats)}).",
            param=param,
        )

    if raw_destination == STDOUT_DESTINATION:
        return ExportTarget(fmt=fmt, destination=None, spelling=value)

    looks_like_directory = (
        raw_destination.endswith(("/", os.sep)) or Path(raw_destination).is_dir()
    )
    if looks_like_directory:
        if not supports_directory:
            raise click.BadParameter(
                f"{value!r}: {raw_destination!r} names a directory, and this "
                "command produces one document per export. Name a file, or "
                f"'-' for stdout.",
                param=param,
            )
        if directory_formats and fmt not in directory_formats:
            raise click.BadParameter(
                f"{value!r}: a directory destination receives the "
                "per-component fan-out, which is produced in "
                f"{'/'.join(directory_formats)} only (got {fmt!r}). Name a "
                "file for this format instead.",
                param=param,
            )
        return ExportTarget(
            fmt=fmt,
            destination=Path(raw_destination),
            is_directory=True,
            spelling=value,
        )

    # Through Click's own path validator rather than a bare ``Path``: naming
    # an existing directory (without a trailing separator, on a command with
    # no fan-out) must fail at parse time, before the analysis runs, not
    # after it has already produced a report with nowhere to put it.
    converted = _FILE_DESTINATION.convert(raw_destination, param, None)
    assert isinstance(converted, Path)
    return ExportTarget(fmt=fmt, destination=converted, spelling=value)


def build_export_set(
    values: Sequence[str],
    formats: Sequence[str],
    *,
    default_format: str,
    param: click.Parameter | None = None,
    supports_directory: bool = False,
    directory_formats: Sequence[str] = (),
) -> ExportSet:
    """Parse a whole ``-o`` request, with collision and exclusivity checks."""
    targets = [
        parse_export_operand(
            raw,
            formats,
            param=param,
            supports_directory=supports_directory,
            directory_formats=directory_formats,
        )
        for raw in values
    ]

    stdout_targets = [t for t in targets if t.to_stdout]
    if len(stdout_targets) > 1:
        raise click.BadParameter(
            "two exports name '-': standard output can carry one document, so "
            "interleaving "
            f"{stdout_targets[0].fmt} and {stdout_targets[1].fmt} on it would "
            "produce neither. Send one of them to a file.",
            param=param,
        )

    directory_targets = [t for t in targets if t.is_directory]
    if len(directory_targets) > 1:
        raise click.BadParameter(
            f"{directory_targets[1].spelling!r}: one per-component export at a "
            f"time -- {directory_targets[0].spelling!r} already names the "
            "directory every component's report is written to.",
            param=param,
        )

    seen: dict[Path, ExportTarget] = {}
    for target in targets:
        if target.destination is None:
            continue
        # Resolved, so two spellings of one location (``a/../b.json`` and
        # ``b.json``) collide the way the filesystem will, not the way the
        # strings look. ``strict=False`` throughout: a destination that does
        # not exist yet is the normal case.
        resolved = target.destination.resolve()
        previous = seen.get(resolved)
        if previous is not None:
            raise click.BadParameter(
                f"{target.spelling!r} and {previous.spelling!r} both export to "
                f"{resolved}: the second would silently overwrite the first. "
                "Give each export its own destination.",
                param=param,
            )
        seen[resolved] = target

    # An exact-path match is not the whole collision class (Codex review, P1,
    # reproduced): a directory export *owns* everything the fan-out generates
    # underneath it -- one report per component plus ``summary.json`` -- and
    # those names are not knowable here, since they come from the operand's
    # own inventory, resolved long after parse time. So the rule is
    # containment, not name prediction: any other export writing anywhere
    # inside that tree races with generated content, and the observed failure
    # (``-o json=reports/ -o markdown=reports/libfoo.json`` exiting 0 after
    # the Markdown document overwrote the per-library JSON) is one member of
    # it, not the shape of it. Checked in both directions for the same
    # reason a single directory export is allowed at all -- which target was
    # spelled first says nothing about which one clobbers the other.
    for directory in directory_targets:
        assert directory.destination is not None
        root = directory.destination.resolve()
        for target in targets:
            if target is directory or target.destination is None:
                continue
            if root not in target.destination.resolve().parents:
                continue
            raise click.BadParameter(
                f"{target.spelling!r} writes inside {root}, which "
                f"{directory.spelling!r} already claims for its per-component "
                "reports: the fan-out generates one report per component plus "
                "summary.json there, so either export could overwrite the "
                "other's document. Send it somewhere outside that directory.",
                param=param,
            )

    if not any(t.is_document for t in targets):
        # Only directory exports were requested. The fan-out's own summary
        # document still needs somewhere to go, and the retired --output-dir
        # put it on stdout in the default format -- keep exactly that, rather
        # than either silently dropping the summary or making the user
        # restate a default they never changed.
        targets.insert(
            0, ExportTarget(fmt=default_format, destination=None, spelling="")
        )

    return ExportSet(targets=tuple(targets), explicit=bool(values))


def export_options(
    formats: Sequence[str],
    *,
    default_format: str = "markdown",
    supports_directory: bool = False,
    directory_formats: Sequence[str] = (),
    help_extra: str = "",
) -> Callable[[F], F]:
    """Factory for the one ``-o/--output`` export request.

    A factory rather than a bare decorator for the same reason the retired
    ``output_options``/``secondary_output_options`` pair were: the *set* of
    producible formats legitimately differs per command. What no longer
    differs is the grammar, the flag, the collision rules, or the stdout
    spelling -- all of which live here once.

    *supports_directory* is what a command with a per-component fan-out
    (``compare`` over a directory/package operand) opts into;
    *directory_formats* narrows which formats that fan-out actually produces.
    """
    allowed = list(formats)
    if default_format not in allowed:  # pragma: no cover - programming error
        raise ValueError(f"default_format {default_format!r} is not one of {allowed!r}")
    dir_formats = list(directory_formats)

    def _callback(
        ctx: click.Context, param: click.Parameter, value: tuple[str, ...]
    ) -> ExportSet:
        return build_export_set(
            value,
            allowed,
            default_format=default_format,
            param=param,
            supports_directory=supports_directory,
            directory_formats=dir_formats,
        )

    directory_help = (
        " A DESTINATION ending in '/' (or naming an existing directory) is a "
        "per-component export: every component's own complete report is "
        f"written there ({'/'.join(dir_formats) or 'json'} only)."
        if supports_directory
        else ""
    )

    def deco(func: F) -> F:
        option = click.option(
            "-o",
            "--output",
            "exports",
            metavar=EXPORT_METAVAR,
            multiple=True,
            callback=_callback,
            help=(
                f"Export this run's report as FORMAT to DESTINATION. FORMAT is "
                f"one of {'/'.join(allowed)}; DESTINATION is a file path, or "
                f"'-' for stdout. Repeatable: every export renders the same "
                f"completed analysis, so the result never depends on which (or "
                f"how many) you ask for. "
                f"Default: {default_format}=-." + directory_help + help_extra
            ),
        )
        func = option(func)
        # The FORMAT half is no longer a `click.Choice`, so it is no longer
        # introspectable the way `--format` was -- and `scripts/
        # gen_action_cli_surface.py` derives the composite Action's
        # pre-install validation from exactly that introspection (ADR-070
        # D3: a derived fact, never a hand-maintained mirror). Publishing
        # the set on the parameter keeps that derivation real rather than
        # turning it into the mirror the generator's own docstring rules
        # out.
        params = getattr(func, "__click_params__", None)
        if params:
            params[-1].export_formats = tuple(allowed)  # type: ignore[attr-defined]
        elif isinstance(func, click.Command):
            func.params[-1].export_formats = tuple(allowed)  # type: ignore[attr-defined]
        return func

    return deco


def expand_export_kwargs(kwargs: dict[str, object]) -> ExportSet:
    """Expand ``kwargs["exports"]`` into the dest names ``compare`` threads.

    The one place the new grammar meets the existing compare stack.
    ``run_compare``, the release fan-out, the abort renderers and the exit
    fold each still take one ``fmt``/``output`` pair plus a list of extra
    ``(fmt, path)`` writes -- shapes that predate this slice and mean exactly
    what they always did -- so the export set is expanded here, at the
    callback boundary, rather than threaded as a new parameter through every
    one of them.

    That the expansion designates a "primary" changes nothing a user can
    observe: :attr:`ExportSet.primary` documents why, and
    ``cli_compare_helpers._report_compare_result`` renders every target from
    the same result under the same options, so the pair and the list are two
    slices of one uniform emission loop rather than two behaviours.

    The directory export becomes ``output_dir``, the release fan-out's own
    per-component destination -- the retired ``--output-dir``'s parameter,
    unchanged, now fed by a destination shape instead of a fourth flag.
    """
    exports = kwargs.pop("exports")
    assert isinstance(exports, ExportSet)
    primary = exports.primary
    directories = exports.directories
    kwargs["fmt"] = primary.fmt
    kwargs["output"] = primary.destination
    kwargs["secondary_writes"] = tuple(
        (t.fmt, t.destination) for t in exports.secondaries if t.destination is not None
    )
    kwargs["output_dir"] = directories[0].destination if directories else None
    return exports


def reject_dry_run_with_exports(dry_run: bool, exports: ExportSet) -> None:
    """A dry run may not write files -- it never ran the analysis.

    Replaces ``dry_run.reject_dry_run_with_output`` and the retired
    ``reject_incoherent_secondary_writes``' first half with one check over
    the whole export set: a stdout export is fine (the dry-run preview goes
    there anyway), a file or directory export is a usage error.
    """
    if not dry_run:
        return
    writing = exports.file_targets
    if not writing:
        return
    named = ", ".join(t.spelling or f"{t.fmt}=-" for t in writing)
    raise click.UsageError(
        f"--dry-run writes no report, so it cannot be combined with an export "
        f"to a file or directory ({named}). Drop the export, or drop "
        "--dry-run."
    )
