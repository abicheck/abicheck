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

from abicheck import service
from abicheck.cli import main
from abicheck.dumper_scoping import wrap_run_dump_with_dependency_scope
from abicheck.service import InputSpec

#: Options a caller can reach from more than one front end, as
#: ``(click_dest, cli_command)``. The *typed* surfaces are not listed: they are
#: **derived** below from ``abicheck.service.__all__``, because a hand-written
#: list is exactly what let two of them be missed. The first revision of this
#: file named three surfaces (``InputSpec``, ``InputSpec.of``, ``run_dump``)
#: and recorded the enumeration gap as a ``KnownGap``; review then found
#: ``resolve_input`` and ``run_compare`` still defaulting the other way, with
#: ``run_compare`` writing its value into *both* ``InputSpec``s and so
#: overriding the field default this test did check. Deriving closes that.
SHARED_OPTIONS = (("include_dependencies", "dump"),)


def _click_default(command: str, dest: str) -> object:
    cmd = main.commands[command]
    for param in cmd.params:
        if param.name == dest:
            return param.default
    raise AssertionError(f"{command} has no parameter {dest!r}")


def _declared_defaults(dest: str) -> dict[str, object]:
    """Every public typed surface that accepts *dest*, mapped to its default.

    Derived, not listed: the dataclass field, ``InputSpec.of``, and every
    callable in ``abicheck.service.__all__`` whose signature accepts *dest* --
    so a new or renamed public entry point carrying the option is covered the
    moment it exists.
    """
    found: dict[str, object] = {}

    for field in dataclasses.fields(InputSpec):
        if field.name == dest:
            # `InputSpec()` needs `path`, and a fabricated one would make this
            # read depend on the fixture rather than the declaration.
            found["InputSpec.field"] = field.default

    for name in service.__all__:
        obj = getattr(service, name, None)
        if not callable(obj):
            continue
        try:
            sig = inspect.signature(obj)
        except (TypeError, ValueError):  # pragma: no cover - builtins
            continue
        param = sig.parameters.get(dest)
        if param is not None and param.default is not inspect.Parameter.empty:
            found[f"service.{name}"] = param.default

    of_param = inspect.signature(InputSpec.of).parameters.get(dest)
    if of_param is not None and of_param.default is not inspect.Parameter.empty:
        found["InputSpec.of"] = of_param.default

    wrapped = wrap_run_dump_with_dependency_scope(lambda *a, **k: None)
    sig_param = inspect.signature(wrapped).parameters.get(dest)
    if sig_param is not None and sig_param.default is not inspect.Parameter.empty:
        found["run_dump.__signature__"] = sig_param.default

    return found


@pytest.mark.parametrize(
    ("dest", "command"), SHARED_OPTIONS, ids=[d for d, _ in SHARED_OPTIONS]
)
def test_every_typed_surface_agrees_with_the_cli(dest: str, command: str) -> None:
    """Every public typed surface accepting the option defaults as the CLI does.

    Batched so a failure names every disagreeing surface at once rather than
    stopping at the first -- the two surfaces review found were both invisible
    to a check that stopped at the dataclass field."""
    cli_default = _click_default(command, dest)
    declared = _declared_defaults(dest)
    disagree = {k: v for k, v in declared.items() if v != cli_default}
    assert not disagree, (
        f"{dest!r} defaults to {cli_default!r} on `{command}` but "
        f"{disagree} on the typed API — omitting the option would mean "
        "different things depending on which entry point the caller used"
    )


@pytest.mark.parametrize(
    ("dest", "command"), SHARED_OPTIONS, ids=[d for d, _ in SHARED_OPTIONS]
)
def test_the_derivation_finds_the_surfaces_it_should(dest: str, command: str) -> None:
    """Guard the guard: a derivation that found *nothing* would make the parity
    test above pass vacuously, which is the original failure in a new place.

    Pin that the sweep reaches the dataclass field, both the request-building
    and the execution entry points, and the synthetic signature -- the four
    kinds of surface this option is spelled on."""
    declared = _declared_defaults(dest)
    assert "InputSpec.field" in declared
    assert "InputSpec.of" in declared
    assert "run_dump.__signature__" in declared
    service_surfaces = {k for k in declared if k.startswith("service.")}
    assert len(service_surfaces) >= 3, (
        f"derivation found only {service_surfaces} on `abicheck.service` — "
        "expected at least run_dump, resolve_input and run_compare"
    )


def test_the_oracle_is_not_vacuous() -> None:
    """The two sides must be read through genuinely different mechanisms."""
    assert _declared_defaults("include_dependencies")["InputSpec.field"] is False
    assert _declared_defaults("no_such_option_anywhere") == {}
    with pytest.raises(AssertionError):
        _click_default("dump", "no_such_dest")


def test_include_dependencies_default_is_the_filtered_surface() -> None:
    """The agreed value is ``False`` specifically, not merely agreed.

    Both front ends defaulting to ``True`` would satisfy the parity test above
    while restoring the unfiltered surface as the default for everyone — a
    different regression. ``False`` is the documented product default
    (``dumper_scoping.py``: dependency exclusion is on by default)."""
    assert _click_default("dump", "include_dependencies") is False
    assert _declared_defaults("include_dependencies")["InputSpec.field"] is False
