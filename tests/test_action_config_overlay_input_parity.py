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

"""Parity contract for the Action inputs that synthesize a ``.abicheck.yml``
overlay.

Two composite Actions write a ``--config`` overlay from their own inputs:
the root ``action.yml`` (``action/run.sh``'s ``add_release_topology_config_
flags``) and ``actions/check-target/action.yml``'s "Generate assurance-overlay
config" step. Each overlay input must stay reachable, tri-state and named
consistently, or a job silently loses an override it asked for:

P1 reachability. Every boolean config key an overlay writes is exposed by a
   sub-action input (``OVERLAY_INPUTS``), or listed in
   ``UNEXPOSED_OVERLAY_KEYS`` with a reason. The registry is also checked the
   other way, so it cannot claim a key an overlay never writes.
P2 tri-state. An overlay input declares no ``default:``. A declared default
   makes "omitted" indistinguishable from an explicit value, which silently
   discards an override (``dso-only``'s own description explains why).
P3 name parity. A config key exposed by more than one sub-action uses one
   input name everywhere.
P4 wiring. Each registered input reaches the step that writes the overlay.
P5 completeness. An input whose description names a boolean config key is
   registered, so a new overlay input cannot ship unregistered.
P6 forwarding. ``check-project.yml`` forwards ``''`` (inherit the config) to
   a cell that declares no assurance, never ``'false'``.

The behavioural both-direction tests live in
``tests/test_action_assurance_overlay_direction.py``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import _yaml_fast
import pytest

from abicheck.buildsource.build_config import BOOL_SUBKEYS

_REPO = Path(__file__).resolve().parents[1]
ROOT_ACTION = _REPO / "action.yml"
ROOT_RUN_SH = _REPO / "action" / "run.sh"
CHECK_TARGET_ACTION = _REPO / "actions" / "check-target" / "action.yml"
CHECK_PROJECT = _REPO / ".github" / "workflows" / "check-project.yml"

BOOL_CONFIG_KEYS = frozenset(
    f"{section}.{key}" for section, keys in BOOL_SUBKEYS.items() for key in keys
)

#: sub-action (repo-relative action.yml path) -> {input name: config key}.
#: The single registry the parity checks read.
OVERLAY_INPUTS: dict[str, dict[str, str]] = {
    "action.yml": {
        "dso-only": "release.dso_only",
        "include-private-dso": "release.include_private_dso",
        "fail-on-removed-library": "gate.fail_on_removed_library",
        "analysis-assurance-complete": "assurance.require_complete",
    },
    "actions/check-target/action.yml": {
        "analysis-assurance-complete": "assurance.require_complete",
    },
}

#: Config keys an overlay writes that no sub-action input exposes, with the
#: reason. This is a ratchet: it must stay empty. A new entry is a visible gap.
UNEXPOSED_OVERLAY_KEYS: dict[str, str] = {}

#: Inputs that cite a boolean config key but are deliberately not overlay
#: inputs. Ratchet, empty for the same reason as UNEXPOSED_OVERLAY_KEYS.
SUB_ACTION_ONLY_INPUTS: dict[str, str] = {}

_OVERLAY_SOURCES = {
    "action.yml": ROOT_RUN_SH,
    "actions/check-target/action.yml": CHECK_TARGET_ACTION,
}

#: Step ids (``_steps_forwarding`` reports ids): the root run step and the
#: check-target overlay step.
_OVERLAY_WRITER_STEP = {
    "action.yml": "run-abicheck",
    "actions/check-target/action.yml": "assurance_overlay",
}

_CITES_CONFIG_KEY = re.compile(r"\b(release|gate|assurance)\.([a-z_]+)\b")
_FORWARD_EXPR = re.compile(
    r"^\$\{\{\s*matrix\.analysis_assurance\s*==\s*'([a-z]+)'\s*&&\s*'([a-z]*)'"
    r"\s*\|\|\s*'([a-z]*)'\s*\}\}$"
)


def _load(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        return _yaml_fast.safe_load(fh)


def _written_config_keys(sub_action: str) -> set[str]:
    """Boolean config keys the sub-action's overlay code writes.

    Derived from the overlay code itself, never from ``OVERLAY_INPUTS``, so
    the registry is checked against what the Action really writes."""
    text = _OVERLAY_SOURCES[sub_action].read_text(encoding="utf-8")
    written: set[str] = {
        f"release.{key}" for key in re.findall(r'release_blk\["([a-z_]+)"\]\s*=', text)
    }
    written |= {
        f"{section}.{key}"
        for section, key in re.findall(
            r'doc\["(gate|assurance)"\]\s*=\s*\{"([a-z_]+)"', text
        )
    }
    written |= {
        f"assurance.{key}"
        for key in re.findall(r'\bassurance\["([a-z_]+)"\]\s*=', text)
    }
    return written & BOOL_CONFIG_KEYS


def _exposed_config_keys() -> set[str]:
    return {key for registry in OVERLAY_INPUTS.values() for key in registry.values()}


def _input_cases() -> list[tuple[str, str]]:
    return [
        (sub_action, name)
        for sub_action, registry in OVERLAY_INPUTS.items()
        for name in registry
    ]


class TestOverlayKeyReachability:
    """P1: the overlay code and the input registry agree, in both directions."""

    def test_every_written_bool_key_is_exposed_or_excepted(self) -> None:
        written: set[str] = set()
        for sub_action in _OVERLAY_SOURCES:
            written |= _written_config_keys(sub_action)
        unexposed = written - _exposed_config_keys() - set(UNEXPOSED_OVERLAY_KEYS)
        assert not unexposed, (
            f"the Action overlay code writes {sorted(unexposed)} but no sub-action "
            "input exposes them: a job cannot override that key from its workflow. "
            "Register an input in OVERLAY_INPUTS, or record a reason in "
            "UNEXPOSED_OVERLAY_KEYS."
        )

    @pytest.mark.parametrize("sub_action", sorted(OVERLAY_INPUTS))
    def test_every_registered_key_is_written_by_its_own_overlay(
        self, sub_action: str
    ) -> None:
        registered = set(OVERLAY_INPUTS[sub_action].values())
        never_written = registered - _written_config_keys(sub_action)
        assert not never_written, (
            f"{sub_action} registers {sorted(never_written)} but its overlay code "
            "never writes it"
        )

    def test_registered_keys_are_real_schema_booleans(self) -> None:
        assert _exposed_config_keys() <= BOOL_CONFIG_KEYS

    def test_unexposed_ratchet_is_empty(self) -> None:
        assert UNEXPOSED_OVERLAY_KEYS == {}


class TestOverlayInputsAreTriState:
    """P2: an overlay input has no declared default."""

    @pytest.mark.parametrize(("sub_action", "name"), _input_cases())
    def test_overlay_input_declares_no_default(
        self, sub_action: str, name: str
    ) -> None:
        inputs = _load(_REPO / sub_action)["inputs"]
        assert name in inputs, f"{sub_action} does not declare {name!r}"
        assert "default" not in inputs[name], (
            f"{name!r} declares default {inputs[name].get('default')!r}: an omitted "
            "input must stay distinguishable from an explicit value"
        )

    @pytest.mark.parametrize("sub_action", sorted(OVERLAY_INPUTS))
    def test_retired_tombstone_is_not_reused_as_an_overlay_input(
        self, sub_action: str
    ) -> None:
        assert "require-complete-analysis" not in OVERLAY_INPUTS[sub_action]


class TestSharedConfigKeyUsesOneInputName:
    """P3: one config key, one input name, across every sub-action."""

    def test_names_agree_per_config_key(self) -> None:
        names_by_key: dict[str, set[str]] = {}
        for registry in OVERLAY_INPUTS.values():
            for name, key in registry.items():
                names_by_key.setdefault(key, set()).add(name)
        divergent = {
            key: sorted(names) for key, names in names_by_key.items() if len(names) > 1
        }
        assert divergent == {}


def _steps_forwarding(sub_action: str, input_name: str) -> set[str]:
    """Ids (or names) of the steps whose ``env:`` forwards the input."""
    expr = f"${{{{ inputs.{input_name} }}}}"
    steps = _load(_REPO / sub_action)["runs"]["steps"]
    found: set[str] = set()
    for step in steps:
        if expr in (step.get("env") or {}).values():
            found.add(step.get("id") or step.get("name") or "")
    return found


class TestOverlayInputsReachTheWriter:
    """P4: every registered input is forwarded to the step that writes it."""

    @pytest.mark.parametrize(("sub_action", "name"), _input_cases())
    def test_input_is_forwarded_to_the_overlay_writer(
        self, sub_action: str, name: str
    ) -> None:
        forwarding = _steps_forwarding(sub_action, name)
        writer = _OVERLAY_WRITER_STEP[sub_action]
        assert writer in forwarding, (
            f"{name!r} is not forwarded into the {writer!r} step, which writes the "
            f"overlay (forwarded to: {sorted(forwarding) or 'nothing'})"
        )


class TestCompleteSurfaceRegistration:
    """P5: an input naming a boolean config key is registered."""

    @pytest.mark.parametrize("sub_action", sorted(OVERLAY_INPUTS))
    def test_every_input_citing_a_bool_key_is_registered(self, sub_action: str) -> None:
        inputs = _load(_REPO / sub_action)["inputs"]
        registered = set(OVERLAY_INPUTS[sub_action]) | set(SUB_ACTION_ONLY_INPUTS)
        unregistered: list[str] = []
        for name, spec in inputs.items():
            description = spec.get("description") or ""
            if "RETIRED" in description:
                continue
            cited = {
                f"{section}.{key}"
                for section, key in _CITES_CONFIG_KEY.findall(description)
            } & BOOL_CONFIG_KEYS
            if cited and name not in registered:
                unregistered.append(f"{name} (cites {sorted(cited)})")
        assert unregistered == [], (
            f"{sub_action} declares inputs that name a boolean config key but are not "
            f"registered in OVERLAY_INPUTS: {unregistered}"
        )


class TestCheckProjectForwardsInheritNotFalse:
    """P6: an undeclared check-project cell forwards '' (inherit), not 'false'.

    ``'false'`` is an explicit override, so forwarding it would silently drop a
    ``.abicheck.yml`` ``assurance.require_complete: true``."""

    def test_forwarded_expression_yields_inherit_for_undeclared_cells(self) -> None:
        steps = _load(CHECK_PROJECT)["jobs"]["check"]["steps"]
        run_step = next(s for s in steps if s.get("name") == "Run check-target")
        expression = run_step["with"]["analysis-assurance-complete"]
        match = _FORWARD_EXPR.match(expression.strip())
        assert match, f"unexpected forwarding expression: {expression!r}"
        declared, if_declared, if_undeclared = match.groups()
        assert (declared, if_declared) == ("complete", "true")
        assert if_undeclared == "", (
            f"an undeclared cell forwards {if_undeclared!r}; it must forward '' so "
            "the config is inherited rather than overridden"
        )
