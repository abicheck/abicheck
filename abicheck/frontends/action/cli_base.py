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

"""The Action CLI group and the helpers its command modules share.

A leaf, deliberately: :mod:`abicheck.frontends.action.cli` imports its sibling
:mod:`~abicheck.frontends.action.cli_integration` to register that module's
commands, so anything both of them need has to live *below* both. Putting the
group in ``cli`` instead and having the sibling import it back would be a real
import cycle -- the kind AGENTS.md says to fix by moving the shared logic to a
leaf module rather than by extending the cycle allowlist.
"""

from __future__ import annotations

import json
from pathlib import Path

import click

#: Exit code for a refusal that is about a *boundary* -- a wrong run, an
#: unresolvable PR, a hostile artifact, a failed post, an unresolvable library
#: declaration, an aggregate document that describes nothing. It is
#: deliberately distinct from Click's own usage exit (2) and from every
#: compatibility exit abicheck uses, because the one thing this tooling must
#: never do is let such a failure be read as a compatibility result.
EXIT_REFUSED = 3


@click.group("abicheck-action")
def action_cli() -> None:
    """Internal helpers for abicheck's Action tooling.

    Not an ``abicheck`` subcommand (ADR-043 D1): this is Action/library
    tooling, invoked as ``python -m abicheck.frontends.action.cli``. It is a
    Click group purely for its argument parsing and ``--help``.
    """


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise click.ClickException(f"Cannot read {path}: {exc}") from exc


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
