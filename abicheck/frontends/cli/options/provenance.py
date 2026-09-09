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

"""``dump --provenance`` -- ADR-068 D5 / one-comparison-product.md Sec 4.2,
Phase 7f: one repeatable ``KEY=VALUE`` option collapsing ``--git-tag``,
``--build-id`` and ``--no-git``.

All three were spellings of the same thing -- *how this snapshot is stamped
with where it came from* -- and Sec 4.2 classifies them MERGE for exactly that
reason. The collapse is the same shape ``--view`` applied to the four
rendering flags in Phase 5, and the same shape ``--header old=``/``new=``
applies to a side-scoped input: one flag, a small typed key vocabulary,
repeatable.

Grammar (each ``--provenance TOKEN`` occurrence contributes one of):

* ``git-tag=<value>`` -- the git tag to embed (``--git-tag``'s old
  spelling). An empty value is a usage error, not a silent unset.
* ``build-id=<value>`` -- the opaque build identifier (``--build-id``'s old
  spelling). Same empty-value rule.
* ``git=auto`` / ``git=off`` -- whether to auto-detect the git commit SHA.
  ``git=off`` is ``--no-git``'s old spelling; ``auto`` is the default and is
  accepted so a caller assembling the flag programmatically can state it.

A repeated key is last-one-wins, matching ``--view``'s own report-mode and
demangle tokens rather than inventing a second collision rule.

Pure parsing over already-typed strings, like every other module under
``frontends/cli/options/`` -- it reaches no snapshot, no service, and no
Click context of its own.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

import click

#: Same shape every sibling decorator module under this package uses.
F = TypeVar("F", bound=Callable[..., Any])

#: The complete key vocabulary, in the order ``--help`` documents it.
PROVENANCE_KEYS: tuple[str, ...] = ("git-tag", "build-id", "git")

#: Accepted values for the ``git`` key.
GIT_VALUES: tuple[str, ...] = ("auto", "off")


@dataclass(frozen=True)
class DumpProvenance:
    """The three values the retired flags produced, unchanged in meaning."""

    git_tag: str | None = None
    build_id: str | None = None
    no_git: bool = False


def parse_provenance_tokens(tokens: tuple[str, ...]) -> DumpProvenance:
    """Parse ``--provenance`` tokens into :class:`DumpProvenance`.

    Raises ``ValueError`` on an unknown key, a missing ``=``, an empty
    value, or an unrecognized ``git=`` value -- callers translate that into
    a ``click.BadParameter``/``click.UsageError``. Nothing here is
    silently ignored: a stamp the caller asked for and this build cannot
    honor is a usage error, never a dropped value (ADR-068 D5's own
    "never shorten the CLI by ignoring supplied input").
    """
    git_tag: str | None = None
    build_id: str | None = None
    no_git = False
    for raw in tokens:
        token = raw.strip()
        key, sep, value = token.partition("=")
        key = key.strip()
        value = value.strip()
        if not sep:
            raise ValueError(
                f"--provenance needs KEY=VALUE, got {raw!r}. Expected one of "
                f"{', '.join(k + '=' for k in PROVENANCE_KEYS)}."
            )
        if key not in PROVENANCE_KEYS:
            raise ValueError(
                f"Unknown --provenance key: {key!r}. Expected one of "
                f"{', '.join(PROVENANCE_KEYS)}."
            )
        if not value:
            raise ValueError(
                f"--provenance {key}= needs a value (e.g. "
                f"--provenance {_example_for(key)})."
            )
        if key == "git-tag":
            git_tag = value
        elif key == "build-id":
            build_id = value
        else:  # key == "git"
            if value not in GIT_VALUES:
                raise ValueError(
                    f"--provenance git={value!r} is not valid. Expected one "
                    f"of {', '.join(GIT_VALUES)}."
                )
            no_git = value == "off"
    return DumpProvenance(git_tag=git_tag, build_id=build_id, no_git=no_git)


def _example_for(key: str) -> str:
    """One realistic example per key, for the empty-value error above."""
    return {
        "git-tag": "git-tag=v2.0.0",
        "build-id": "build-id=ci-1234",
        "git": "git=off",
    }[key]


def validate_provenance(
    ctx: click.Context, param: click.Parameter, value: tuple[str, ...]
) -> tuple[str, ...]:
    """Eagerly validate ``--provenance`` tokens -- the exact shape
    ``frontends.cli.runtime._validate_view`` uses for ``compare --view``, so
    a malformed ``KEY=VALUE``, an unknown key, or a bad ``git=`` value
    surfaces as a usage error before any extraction work runs. Returns the
    raw tuple (Click needs a value on the dest); ``dump_cmd`` calls
    :func:`parse_provenance_tokens` once more, cheaply, so exactly one
    function ever turns tokens into ``git_tag``/``build_id``/``no_git``."""
    try:
        parse_provenance_tokens(value)
    except ValueError as exc:
        raise click.BadParameter(str(exc)) from exc
    return value


def dump_provenance_option(func: F) -> F:
    """Attach ``dump --provenance KEY=VALUE`` (repeatable)."""
    func = click.option(
        "--provenance",
        "provenance",
        multiple=True,
        callback=validate_provenance,
        metavar="KEY=VALUE",
        help="Repeatable provenance stamp for the snapshot (ADR-068 D5). "
        "KEY=VALUE, one of: 'git-tag=<tag>' (e.g. git-tag=v2.0.0), "
        "'build-id=<id>' (CI run ID, build number, ...), or 'git=auto'/"
        "'git=off' ('off' skips commit-SHA auto-detection). Replaces "
        "--git-tag/--build-id/--no-git. A repeated key is last-one-wins. "
        "Example: --provenance git-tag=v2.0.0 --provenance build-id=ci-1234.",
    )(func)
    return func
