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

"""Config-fold decisions for the typed compare pipeline.

Pure functions over already-parsed values. The CLI reads what the user typed
(``click`` parameter sources) and passes the answer in as a plain ``bool``.
"""

from __future__ import annotations

from pathlib import Path


def resolve_required_symbol_policy(
    policy: str,
    *,
    required_symbols: tuple[str, ...],
    policy_from_commandline: bool,
    required_symbols_from_file: tuple[str, ...],
    required_symbols_path: str | None,
    required_symbols_sha: str | None,
) -> tuple[str, str | None, Path | None, str | None]:
    """Pick ``policy`` for a ``--required-symbol`` contract, and say what picked it.

    Required-symbol contracts default to the plugin-oriented policy unless the
    user explicitly picked one -- an explicit ``--policy`` always wins (ADR-043).
    Recorded, not just applied: the ADR-049 receipt has to name whatever really
    selected ``policy.base``, and this value was chosen by a typed
    ``--required-symbol`` rather than by ``--policy`` or a built-in default
    (Codex review, fresh evidence -- the receipt claimed ``strict_abi`` for a run
    that used ``plugin_abi``).

    Which spelling actually *contributed* decides what the receipt names -- not
    merely which was passed. ADR-068 D5 / plan Phase 7h folded the old, separate
    ``--required-symbols FILE`` flag into ``--required-symbol @FILE``, so both
    forms now share one option name for the receipt -- but a file that parsed
    to nothing still selected nothing (Codex review, two rounds, generalized):
    the ``@FILE`` form wins the receipt's path/digest only when it actually
    contributed a symbol.

    *policy_from_commandline* is whether ``--policy`` was typed on the command
    line (not defaulted); the caller reads it from its own argument parser.

    Returns ``(policy, selected_by, selected_path, selected_sha)``.
    """
    if not required_symbols or policy_from_commandline:
        return policy, None, None, None
    if required_symbols_from_file:
        return (
            "plugin_abi",
            "--required-symbol",
            Path(required_symbols_path) if required_symbols_path else None,
            required_symbols_sha,
        )
    return "plugin_abi", "--required-symbol", None, None
