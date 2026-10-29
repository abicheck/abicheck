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

"""The CLI's one translation of :class:`~abicheck.workflows.run_input_errors.RunInputError`.

``USAGE`` becomes a ``click.UsageError`` and ``PARAMETER`` a
``click.BadParameter`` (both exit 64 under the root group's remap);
``OPERATIONAL`` becomes a plain ``click.ClickException`` (exit 1). The
message is never rewritten.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import click

from ...workflows.run_input_errors import InputRefusal, RunInputError

__all__ = ["as_click_error", "click_input_errors"]


def as_click_error(exc: RunInputError) -> click.ClickException:
    """The Click exception that reports *exc* with its message unchanged."""
    message = str(exc)
    if exc.refusal is InputRefusal.USAGE:
        return click.UsageError(message)
    if exc.refusal is InputRefusal.PARAMETER:
        return click.BadParameter(message, param_hint=exc.param_hint)
    return click.ClickException(message)


@contextmanager
def click_input_errors() -> Iterator[None]:
    """Re-raise a :class:`RunInputError` from the body as its Click exception."""
    try:
        yield
    except RunInputError as exc:
        raise as_click_error(exc) from exc
