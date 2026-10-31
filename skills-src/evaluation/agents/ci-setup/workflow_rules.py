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

"""Pure text rules the ci-setup grader applies to a workflow's `run:` text.

Kept out of `grader.py` so that file stays within its size record: how an
`abicheck compare|dump` argv maps onto the Action's input names, how an
abicheck install is pinned, and whether a release-asset write overwrites a
published baseline. Every function here is a pure function of its text.
"""

from __future__ import annotations

import re
from typing import Any

_SIDE = re.compile(r"^(old|new)=")


def parse_cli(mode: str, argv: list[str]) -> dict[str, Any]:
    """Map one `abicheck compare|dump` argv onto the Action's input names,
    so every check judges a CLI call and an Action step by the same rule."""
    inputs: dict[str, Any] = {"mode": mode}
    positional: list[str] = []
    headers: dict[str, list[str]] = {"header": [], "old-header": [], "new-header": []}
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("&&", "||", ";", "|") or arg.startswith(("#", ">")):
            break
        value = None
        if "=" in arg and arg.startswith("--"):
            arg, value = arg.split("=", 1)
        if arg in ("-H", "--header", "-o", "--output", "--config", "-I", "--include", "--version",
                   "--policy", "--suppress", "--severity-preset", "--depth", "--contract",
                   "--used-by", "--sources", "--build-info", "--debug-info", "--set"):
            if value is None:
                i += 1
                value = argv[i] if i < len(argv) else ""
            if arg in ("-H", "--header"):
                side = _SIDE.match(value)
                key = f"{side.group(1)}-header" if side else "header"
                headers[key].append(_SIDE.sub("", value))
            elif arg in ("-o", "--output"):
                inputs["output-file"] = value.split("=", 1)[-1] if re.match(r"^[a-z]+=", value) else value
        elif not arg.startswith("-"):
            positional.append(arg)
        i += 1
    for key, vals in headers.items():
        if vals:
            inputs[key] = " ".join(vals)
    if mode == "compare":
        if len(positional) >= 2:
            inputs["old-library"], inputs["new-library"] = positional[0], positional[1]
        elif positional:
            inputs["new-library"] = positional[0]
    elif positional:
        inputs["new-library"] = positional[0]
    return inputs


_PIP_PIN = re.compile(r"(pip3?|pipx|uv pip|uv tool)\s+install[^\n]*?\babicheck(?P<spec>[=<>~!]=?[\w.]+)?")


def cli_pin(text: str) -> str | None:
    """`==X.Y.Z` when the workflow installs an exact abicheck, `""` when it
    installs an unpinned one, `None` when it installs none this way."""
    found = None
    for m in _PIP_PIN.finditer(text):
        spec = m.group("spec") or ""
        if spec.startswith("==") and re.match(r"^==\d+\.\d+\.\d+$", spec):
            found = found if found == "" else spec
        else:
            found = ""
    return found


#: A release baseline is write-once (docs/reference/publish-baseline.md): an
#: upload with --clobber, or a run that deletes the asset, lets a re-run silently
#: replace the snapshot consumers already resolved. Matches a command, not prose.
_OVERWRITE_RE = re.compile(
    r"gh release upload\b[^\n]*--clobber\b|^\s*(?:-\s*)?(?:run:\s*)?gh release delete-asset\b",
    re.MULTILINE,
)


def overwrites_release_asset(workflow_text: str) -> bool:
    return _OVERWRITE_RE.search(workflow_text) is not None
