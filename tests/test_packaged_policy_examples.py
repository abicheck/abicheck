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

"""Every ``abicheck`` flag a packaged policy example shows must still exist.

Each built-in policy document opens with a copy-pasteable ``abicheck compare
... --policy <name>`` line. When ``--policy-file`` was folded into
``--policy``, all seven still advertised the removed spelling, so copying a
shipped example exited with an unknown-option error instead of loading the
policy (Codex review).

`scripts/check_docs_contract.py`'s `_RETIRED_SURFACES` registry does not reach
this tree -- it scans `docs/` -- and extending it would only re-check a
hand-maintained list of *known-dead* spellings. This asserts the stronger
property against the live CLI instead: every flag these examples name is a
flag the command actually has, so the next removal fails here without anyone
remembering to register it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from abicheck.cli import main

POLICIES = Path(__file__).resolve().parents[1] / "abicheck" / "policies"

#: `abicheck <command> ... --flag` inside a comment line.
_INVOCATION = re.compile(r"^#.*\babicheck\s+([a-z-]+)\b(.*)$")
_FLAG = re.compile(r"(?<![\w-])(--[a-z][a-z0-9-]*)")


def _documents() -> list[Path]:
    return sorted(POLICIES.glob("*.yaml"))


def _flags_of(command_name: str) -> set[str]:
    command = main.commands[command_name]
    return {
        opt
        for p in command.params
        if getattr(p, "param_type_name", None) == "option"
        for opt in (*p.opts, *p.secondary_opts)
    }


def test_there_are_packaged_policies_to_check() -> None:
    # Without this the parametrized test below passes vacuously if the tree
    # is ever moved or renamed.
    assert _documents(), f"no packaged policy documents under {POLICIES}"


@pytest.mark.parametrize("document", _documents(), ids=lambda p: p.name)
def test_example_invocations_name_only_live_flags(document: Path) -> None:
    checked = 0
    for line in document.read_text(encoding="utf-8").splitlines():
        m = _INVOCATION.match(line.strip())
        if m is None:
            continue
        command_name, rest = m.group(1), m.group(2)
        if command_name not in main.commands:
            continue
        live = _flags_of(command_name)
        for flag in _FLAG.findall(rest):
            checked += 1
            assert flag in live, (
                f"{document.name} shows `abicheck {command_name} ... {flag}`, "
                f"which {command_name} no longer accepts — copying this "
                "example would exit with an unknown-option error."
            )
    assert checked, f"{document.name} has no example invocation to check"


def test_the_package_docstring_names_a_live_flag() -> None:
    """`policies/__init__.py`'s own prose advertises the same spelling."""
    import abicheck.policies as pkg

    doc = pkg.__doc__ or ""
    for flag in _FLAG.findall(doc):
        assert flag in _flags_of("compare"), flag
