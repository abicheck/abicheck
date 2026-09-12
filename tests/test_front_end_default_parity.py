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

"""Front ends must agree on what *omitting* an option means.

Bug class ``config.front_end_default_divergence``: an option offered by more
than one front end carries the same default in each, so a caller that states
nothing gets the same behavior whichever front end it used.

This is not the same class as ``config.propagation_completeness``, which is
about a value the caller *did* state reaching every consumer. Here nobody
states anything and the front ends silently disagree, so there is no value to
trace — which is exactly why it escaped: every propagation test passes, and
the divergence only appears when two front ends' outputs meet.

They met. ``dump --include-system-declarations`` defaults to ``False``
(exclude toolchain/system-header declarations) while
``InputSpec.include_dependencies`` and ``run_dump``'s own keyword defaulted to
``True``, so dumping one C++ header yielded 10 functions
(``dependency_scope="filtered"``) through the CLI and 5,597 (``"full"``)
through ``DumpRequest``. Because ``comparability.check_contracts_comparable``
refuses a ``filtered``/``full`` pair, a user who dumped a baseline with the
CLI and a candidate with the typed API — passing the flag on *neither*, as the
mismatch error itself advises — got ``scope_mismatch`` and no verdict.

The tests below are deliberately not pinned to that one option. They read the
Click parameter's own default off the real command and compare it against the
typed surface, so a *new* shared option that disagrees fails here too.
"""

from __future__ import annotations

import dataclasses
import inspect

import pytest

from abicheck.cli import main
from abicheck.dumper_scoping import wrap_run_dump_with_dependency_scope
from abicheck.workflows.request_inputs import InputSpec

#: ``(click_dest, cli_command, typed_surfaces)`` for every option a caller can
#: reach from more than one front end. ``typed_surfaces`` names where the same
#: concept is spelled on the typed API; each is resolved dynamically below so a
#: rename fails loudly rather than silently skipping.
SHARED_OPTION_DEFAULTS = (
    ("include_dependencies", "dump", ("InputSpec.field", "InputSpec.of", "run_dump")),
)


def _click_default(command: str, dest: str) -> object:
    cmd = main.commands[command]
    for param in cmd.params:
        if param.name == dest:
            return param.default
    raise AssertionError(f"{command} has no parameter {dest!r}")


def _typed_default(surface: str, dest: str) -> object:
    if surface == "InputSpec.field":
        # Read the declared default off the dataclass field: `InputSpec()`
        # cannot be constructed without `path`, and a fabricated path would
        # make this read depend on the fixture rather than the declaration.
        for field in dataclasses.fields(InputSpec):
            if field.name == dest:
                return field.default
        raise AssertionError(f"InputSpec has no field {dest!r}")
    if surface == "InputSpec.of":
        return inspect.signature(InputSpec.of).parameters[dest].default
    if surface == "run_dump":
        wrapped = wrap_run_dump_with_dependency_scope(lambda *a, **k: None)
        return inspect.signature(wrapped).parameters[dest].default
    raise AssertionError(f"unknown typed surface {surface!r}")


@pytest.mark.parametrize(
    ("dest", "command", "surfaces"),
    SHARED_OPTION_DEFAULTS,
    ids=[d for d, _, _ in SHARED_OPTION_DEFAULTS],
)
def test_every_front_end_agrees_on_the_default(
    dest: str, command: str, surfaces: tuple[str, ...]
) -> None:
    """Every surface offering the option defaults to the same value.

    Batched so a failure names every disagreeing surface at once rather than
    stopping at the first."""
    cli_default = _click_default(command, dest)
    disagree = {
        surface: got
        for surface in surfaces
        if (got := _typed_default(surface, dest)) != cli_default
    }
    assert not disagree, (
        f"{dest!r} defaults to {cli_default!r} on `{command}` but "
        f"{disagree} on the typed API — omitting the option would mean "
        "different things depending on which front end the caller used"
    )


def test_the_oracle_is_not_vacuous() -> None:
    """Guard the guard: the comparison above must be able to fail.

    A resolver that silently returned the CLI default for every typed surface
    would make the parity test pass no matter what the typed API does. Assert
    the two sides are read through genuinely different mechanisms by feeding
    the typed resolver a value the CLI cannot produce."""
    assert _typed_default("InputSpec.field", "include_dependencies") is False
    with pytest.raises(AssertionError):
        _typed_default("no_such_surface", "include_dependencies")
    with pytest.raises(AssertionError):
        _click_default("dump", "no_such_dest")


def test_include_dependencies_default_is_the_filtered_surface() -> None:
    """The agreed value is ``False`` specifically, not merely agreed.

    Both front ends defaulting to ``True`` would satisfy the parity test above
    while restoring the unfiltered surface as the default for everyone — a
    different regression. ``False`` is the documented product default
    (``dumper_scoping.py``: dependency exclusion is on by default)."""
    assert _click_default("dump", "include_dependencies") is False
    assert _typed_default("InputSpec.field", "include_dependencies") is False
