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

"""Guard: every ``abicheck <cmd> … --flag`` shown in the user-guide examples is a
real option of that command.

The worked-example and scan-levels pages hand-write CLI invocations; nothing else
exercises them, so a future flag rename/removal would silently stale the docs.
This parses the ``abicheck`` commands out of those pages' fenced ``bash`` blocks
and asserts each long/short flag still resolves against the click command — the
test fails (prompting a doc fix) the moment the CLI surface drifts.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from abicheck.cli import compare_cmd, dump_cmd
from abicheck.cli_buildsource import collect_cmd
from abicheck.cli_scan import scan_cmd

_DOCS = Path(__file__).resolve().parent.parent / "docs" / "user-guide"
_DOC_FILES = ("real-world-example.md", "scan-levels.md", "cli-usage.md")

#: subcommand name → click command object whose options are authoritative.
_COMMANDS = {
    "compare": compare_cmd,
    "dump": dump_cmd,
    "scan": scan_cmd,
    "collect": collect_cmd,
}

_BASH_BLOCK = re.compile(r"```bash\n(.*?)```", re.DOTALL)
#: a CLI flag token: one or two leading dashes then a letter (excludes ``-D…``
#: define values and ``--`` alone). ``=value`` and any value are dropped.
_FLAG = re.compile(r"^--?[A-Za-z][A-Za-z0-9-]*$")


def _command_options(cmd: object) -> set[str]:
    """All option strings (long + short, incl. ``--no-x`` secondaries) of *cmd*."""
    out: set[str] = set()
    for param in getattr(cmd, "params", []):
        out.update(getattr(param, "opts", []))
        out.update(getattr(param, "secondary_opts", []))
    return out


def _logical_lines(block: str) -> list[str]:
    """Join ``\\``-continued shell lines into single logical commands."""
    lines: list[str] = []
    buf = ""
    for raw in block.splitlines():
        line = raw.rstrip()
        if line.endswith("\\"):
            buf += line[:-1] + " "
            continue
        buf += line
        if buf.strip():
            lines.append(buf)
        buf = ""
    if buf.strip():
        lines.append(buf)
    return lines


def _abicheck_invocations() -> list[tuple[str, str, str, list[str]]]:
    """Extract ``(file, command, raw_line, flags)`` for each abicheck example."""
    found: list[tuple[str, str, str, list[str]]] = []
    for name in _DOC_FILES:
        text = (_DOCS / name).read_text(encoding="utf-8")
        for block in _BASH_BLOCK.findall(text):
            for line in _logical_lines(block):
                if line.lstrip().startswith("#"):
                    continue
                # Drop quoted substrings so a value like "-std=c++20 -DFOO=1"
                # behind --gcc-options is not mistaken for flags.
                clean = re.sub(r"\"[^\"]*\"|'[^']*'", "", line)
                toks = clean.split()
                if "abicheck" not in toks:
                    continue
                sub = toks[toks.index("abicheck") + 1 :]
                if not sub or sub[0] not in _COMMANDS:
                    continue
                cmd = sub[0]
                flags = [t.split("=", 1)[0] for t in sub[1:] if t.startswith("-")]
                flags = [f for f in flags if _FLAG.match(f)]
                found.append((name, cmd, line, flags))
    return found


def test_docs_contain_abicheck_examples() -> None:
    """Sanity: the parser actually found the documented commands (no silent zero)."""
    invs = _abicheck_invocations()
    assert len(invs) >= 5, f"expected several abicheck examples, found {len(invs)}"
    assert {c for _, c, _, _ in invs} >= {"compare", "scan", "dump"}


@pytest.mark.parametrize("name,cmd,line,flags", _abicheck_invocations())
def test_doc_cli_flags_exist(
    name: str, cmd: str, line: str, flags: list[str]
) -> None:
    """Every flag in a documented ``abicheck`` example is a real option."""
    valid = _command_options(_COMMANDS[cmd])
    unknown = [f for f in flags if f not in valid]
    assert not unknown, (
        f"{name}: `abicheck {cmd}` example uses flag(s) {unknown} that no longer "
        f"exist on the command — update the docs or the example.\n  line: {line}"
    )
