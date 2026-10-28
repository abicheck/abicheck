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

"""Terminal warnings for compare inputs a single-pair run cannot honour.

``compare`` accepts the header and include options only when an operand is a
binary or a source tree; when both operands are snapshots they have no effect,
and this warning says so on stderr. A development package given through
``-H/--header`` is read only by the release fan-out, and is named here when a
single-pair run drops it. These are the CLI's only terminal-writing helpers for
that path -- the rest of the compare/dump helper logic is owned by
:mod:`abicheck.workflows`.
"""

from __future__ import annotations

from pathlib import Path

import click


def _warn_ignored_flags(
    old_is_binary: bool,
    new_is_binary: bool,
    headers: tuple[Path, ...],
    includes: tuple[Path, ...],
    old_headers_only: tuple[Path, ...],
    new_headers_only: tuple[Path, ...],
    old_includes_only: tuple[Path, ...],
    new_includes_only: tuple[Path, ...],
) -> None:
    """Warn if dump-only options are provided but not used (both inputs are snapshots)."""
    if old_is_binary or new_is_binary:
        return
    flag_pairs: list[tuple[tuple[Path, ...], str]] = [
        (headers, "-H/--header"),
        (old_headers_only, "--header old="),
        (new_headers_only, "--header new="),
        (includes, "-I/--include"),
        (old_includes_only, "--include old="),
        (new_includes_only, "--include new="),
    ]
    ignored_flags = [label for value, label in flag_pairs if value]
    if ignored_flags:
        click.echo(
            f"Warning: {', '.join(ignored_flags)} ignored when both inputs are snapshots.",
            err=True,
        )


def _warn_ignored_development_packages(
    devel_pkg1: Path | None, devel_pkg2: Path | None
) -> None:
    """Name each development package a single-pair compare was given and cannot read.

    A ``-H/--header`` operand that is a development package is consumed only by
    the release fan-out. A single-pair compare has no extraction for it, so the
    package would otherwise drop out with nothing but a "no headers provided"
    note per library. One warning per distinct package, naming the side(s) it
    was given for.
    """
    sides_by_package: dict[Path, list[str]] = {}
    for side, package in (("old", devel_pkg1), ("new", devel_pkg2)):
        if package is not None:
            sides_by_package.setdefault(package, []).append(side)
    for package, sides in sides_by_package.items():
        scope = "both sides" if len(sides) == 2 else f"{sides[0]} side"
        click.echo(
            f"Warning: development package '{package}' ignored for the {scope}: a "
            "single-pair compare does not read headers from a development "
            "package. Extract it and pass its include directory with -H, or "
            "compare the release directories.",
            err=True,
        )
