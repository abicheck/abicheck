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

"""Drive a composite Action's ``env:`` mapping the way a runner does.

A composite Action has a boundary nothing else in this suite exercised: a
declared input reaches its script *only* if some step maps it into ``env:``.
A test that sets ``INPUT_FOO`` itself has already crossed that boundary by
hand, so it passes identically whether or not ``action.yml`` forwards the
input -- which is exactly how ``actions/report`` came to declare
``source-run-id``/``source-run-attempt`` while its ``Render and publish``
step never mapped them, leaving ``run.sh`` silently falling back to the
*publisher's* ambient run coordinates.

So this module computes the step environment from the real, unmodified
``action.yml``: declared input defaults, overridden by a caller's ``with:``,
evaluated through :mod:`tests._gha_expr` (the same real expression evaluator
the workflow-wiring tests use). Nothing here restates a mapping; a mapping
that is absent from ``action.yml`` is absent from the result, which is what
makes the regression fail when the fix is reverted.

Not a ``test_`` module: shared machinery only, per this directory's
convention for ``_workflow_exec.py`` and ``_action_run_sh_harness.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from tests._gha_expr import eval_gha_expression


class CompositeActionError(ValueError):
    """A composite Action shape this helper deliberately refuses to guess at."""


def load_action(action_dir: Path) -> dict[str, Any]:
    """Parse ``<action_dir>/action.yml``."""
    return yaml.safe_load((action_dir / "action.yml").read_text(encoding="utf-8"))


def declared_inputs(action: dict[str, Any]) -> dict[str, str]:
    """Every declared input at its declared default, as a runner sees it.

    A runner exposes an input with no default as the empty string, so an
    absent ``default:`` is modelled as ``""`` rather than omitted -- an
    omitted key would raise in the evaluator and hide the real distinction
    between "not forwarded" and "forwarded as empty".
    """
    inputs: dict[str, str] = {}
    for name, spec in (action.get("inputs") or {}).items():
        default = (spec or {}).get("default", "")
        inputs[str(name)] = "" if default is None else str(default)
    return inputs


def find_step(action: dict[str, Any], name: str) -> dict[str, Any]:
    matches = [s for s in action["runs"]["steps"] if s.get("name") == name]
    if len(matches) != 1:
        raise CompositeActionError(
            f"expected exactly one step named {name!r}, found {len(matches)}"
        )
    return matches[0]


def _evaluate(value: Any, inputs: dict[str, str], github: dict[str, Any]) -> str:
    text = "" if value is None else str(value)
    if "${{" not in text:
        return text
    return str(eval_gha_expression(text, inputs=inputs, github=github))


def step_env(
    action_dir: Path,
    step_name: str,
    *,
    with_inputs: dict[str, str] | None = None,
    github: dict[str, Any] | None = None,
) -> dict[str, str]:
    """The environment ``step_name`` receives for a caller's ``with:`` block.

    Raises when ``with_inputs`` names something the Action does not declare:
    a workflow passing an undeclared input gets a runner warning and no
    value, so silently honouring one here would let a test assert a wiring
    that cannot exist.
    """
    action = load_action(action_dir)
    inputs = declared_inputs(action)
    for key, value in (with_inputs or {}).items():
        if key not in inputs:
            raise CompositeActionError(
                f"{action_dir.name}: input {key!r} is not declared by this Action"
            )
        inputs[key] = value
    step = find_step(action, step_name)
    return {
        str(k): _evaluate(v, inputs, github or {})
        for k, v in (step.get("env") or {}).items()
    }


def forwarded_input_names(action: dict[str, Any]) -> set[str]:
    """Input names any step references, in ``env:``, ``with:``, ``run:`` or ``if:``.

    Deliberately a reference scan over the whole executable surface rather
    than ``env:`` alone: an input consumed by a nested ``uses:`` step's
    ``with:`` (``python-version``) or named directly inside a ``run:`` body
    is genuinely forwarded, and a rule that only understood ``env:`` would
    report those as unforwarded and be relaxed away.
    """
    seen: set[str] = set()
    declared = set(declared_inputs(action))

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)
        elif node is not None:
            text = str(node)
            for name in declared:
                if f"inputs.{name}" in text or f"inputs['{name}']" in text:
                    seen.add(name)

    visit(action["runs"]["steps"])
    return seen
