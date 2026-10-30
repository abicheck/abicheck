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

"""Renamed CLI options with a one-release hidden alias (ADR-091 D7).

``renamed_option`` declares the new spelling as a normal visible option and
each old spelling as a hidden twin. A twin uses click's own ``deprecated=``
mechanism, so passing it prints exactly one ``DeprecationWarning`` on stderr
naming the new spelling. Its value is routed into the new option, so the
command body sees the same parameter either way and keeps its existing dest.

Passing an old spelling together with the new one is a usage error (exit 64,
via ``runtime``'s mapping of click's exit code 2). Several old spellings may be
combined with each other, for a rename that split one flag into a per-side
pair (``--old-root``/``--new-root`` to ``--root old=``/``new=``). The aliases
are removed in the next minor release (ADR-091 Phase 6); until then they are
listed in ``architecture/option-vocabulary.yaml`` under ``deprecated_aliases``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import click
from click.core import ParameterSource

#: A click option decorator applied to a command function.
Decorator = Callable[[Any], Any]


@dataclass(frozen=True)
class Legacy:
    """One retired spelling of a renamed option.

    ``replacement`` is the new spelling the warning names, e.g.
    ``--root old=PATH``. ``translate`` maps one legacy raw value to the new
    spelling's raw form (identity when ``None``). ``multiple`` mirrors whether
    the legacy flag was repeatable.
    """

    flag: str
    replacement: str
    translate: Callable[[str], str] | None = None
    multiple: bool = False

    @property
    def dest(self) -> str:
        return "_legacy_" + self.flag.lstrip("-").replace("-", "_")


class _LegacyOption(click.Option):
    """A retired spelling: named by the flag the user typed, not its hidden dest."""

    @property
    def human_readable_name(self) -> str:
        return self.opts[0]


class RenamedOption(click.Option):
    """The new spelling of a renamed option; it reads its hidden legacy twins.

    When any twin was given, this option must not also have been given (usage
    error). Otherwise the twins' raw values are translated, in declaration
    order, into this option's raw form.
    """

    def __init__(self, *args: Any, legacy: Sequence[Legacy], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.legacy = tuple(legacy)

    def consume_value(
        self, ctx: click.Context, opts: Any
    ) -> tuple[Any, ParameterSource]:
        value, source = super().consume_value(ctx, opts)
        given = [spec for spec in self.legacy if spec.dest in opts]
        if not given:
            return value, source
        if source is ParameterSource.COMMANDLINE:
            raise click.UsageError(
                f"{given[0].flag} and {self.opts[0]} name the same option; "
                f"pass only {self.opts[0]}.",
                ctx=ctx,
            )
        raw: list[str] = []
        for spec in given:
            legacy_values = (
                tuple(opts[spec.dest]) if spec.multiple else (opts[spec.dest],)
            )
            raw.extend(
                spec.translate(v) if spec.translate is not None else v
                for v in legacy_values
            )
        return (tuple(raw) if self.multiple else raw[0]), ParameterSource.COMMANDLINE


def renamed_option(
    new_flag: str,
    dest: str,
    *,
    legacy: Sequence[Legacy],
    **attrs: Any,
) -> Decorator:
    """Declare ``new_flag`` (parameter ``dest``) with ``legacy`` as hidden aliases.

    ``attrs`` are click's option attributes for the new spelling. Each alias is
    declared hidden with ``expose_value=False``, so it never reaches the command
    callback; the new option receives its value.
    """

    def decorate(func: Any) -> Any:
        for spec in legacy:
            func = click.option(
                spec.flag,
                spec.dest,
                cls=_LegacyOption,
                hidden=True,
                expose_value=False,
                multiple=spec.multiple,
                deprecated=f"Use {spec.replacement} instead.",
            )(func)
        return click.option(
            new_flag,
            dest,
            cls=RenamedOption,
            legacy=legacy,
            **attrs,
        )(func)

    return decorate
