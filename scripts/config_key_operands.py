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

"""The `config-key-as-cli-operand` docs scan.

Split out of `scripts/check_docs_contract.py` the same way
`scripts/retired_surfaces.py` was, and for the same reason: that file sits
against the AI-readiness `file-size` soft limit, and CLAUDE.md's rule for a
file at its ceiling is to move responsibility to a properly-owned module
rather than trim it. This module owns one scan.

`check_docs_contract.py` keeps thin wrappers so the check name, the
`Findings` shape and the test entry points are unchanged.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

#: A ``key.subkey:`` token -- what a `.abicheck.yml` block's setting looks
#: like when it is written inline. Anchored to a whitespace/quote boundary so
#: it cannot match inside a URL, a Python attribute access, or a flag value.
_CONFIG_KEY_OPERAND_RE = re.compile(
    r"(?:^|[\s'\"])([a-z][a-z0-9_]*\.[a-z][a-z0-9_]*):(?=\s|$)"
)

#: The same failure without the trailing colon. A *list*-typed key
#: (``scope.public_symbols``) is spelled bare in a mechanical flag->key
#: rewrite -- ``--public-symbol my_asm_stub`` becomes
#: ``scope.public_symbols my_asm_stub`` -- so the colon-anchored pattern
#: above never sees it. That exact line shipped in
#: `docs/use/output-formats.md` and survived this check.
#:
#: A bare ``a.b`` token on a command line is usually a filename
#: (``libfoo.so``, ``foo.h``), so this variant is deliberately **not**
#: shape-based: it matches only against the real key set
#: ``BuildConfig`` validates (:func:`_known_config_keys`), which is the same
#: registry `scripts/gen_config_reference.py` renders. A key that stops
#: existing stops being flagged, and a new one is covered with no edit here.
_BARE_CONFIG_KEY_RE = re.compile(
    r"(?:^|[\s'\"])([a-z][a-z0-9_]*\.[a-z][a-z0-9_]*)(?=\s|$)"
)


def _known_config_keys() -> frozenset[str]:
    """Every ``block.subkey`` `.abicheck.yml` name ``BuildConfig`` validates.

    Empty (so the bare-token scan simply finds nothing) if the package is
    not importable, rather than failing this docs-only check on an import
    error.
    """
    try:
        from abicheck.buildsource.build_config import BuildConfig
    except Exception:  # pragma: no cover - defensive, import-environment only
        return frozenset()
    return frozenset(
        f"{block}.{sub}"
        for block, subs in BuildConfig._KNOWN_BLOCK_KEYS.items()
        for sub in subs
    )


#: A shell line invoking the tool, including a backslash-continued one. The
#: subcommand list is deliberately explicit: `abicheck` alone also appears in
#: prose like "abicheck reads .abicheck.yml", which is not a command line.
_ABICHECK_COMMAND_RE = re.compile(
    r"^\s*(?:\$\s*)?abicheck\s+"
    r"(?:compare|scan|dump|aggregate|compat|deps|project|appcompat)\b"
)

#: The Action input that forwards raw argv. Same rule applies to its value:
#: a config key written there reaches Click as a positional operand.
_EXTRA_ARGS_RE = re.compile(r"^\s*extra-args:\s*(.+)$")


def _shell_command_lines(text: str) -> list[tuple[int, str]]:
    """Every ``abicheck <subcommand> ...`` invocation in *text*, with its
    backslash continuations joined, as ``(line_number, command)``."""
    out: list[tuple[int, str]] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if _ABICHECK_COMMAND_RE.match(lines[i]):
            start = i + 1
            parts = [lines[i]]
            while parts[-1].rstrip().endswith("\\") and i + 1 < len(lines):
                i += 1
                parts.append(lines[i])
            out.append((start, " ".join(p.rstrip().rstrip("\\") for p in parts)))
        i += 1
    return out


def check_config_keys_as_cli_operands(
    f: Any,
    scan_targets: Iterable[tuple[Path, str]],
    *,
    rel: Callable[[Path], str],
) -> None:
    """Flag a documented command line that passes a config key as argv.

    The failure this exists for: when a hidden per-run flag is demoted to a
    ``.abicheck.yml``-only setting, a mechanical rewrite of every mention
    (``--severity-addition error`` -> ``severity.addition: error``) is correct
    in the *prose* naming the key and wrong in every *command line* that used
    to pass the flag -- Click sees two unexpected positional operands and the
    example exits 64. Eight such examples shipped across five pages before a
    reviewer read one of them (Codex review), because nothing distinguishes
    the two contexts by eye.

    Scoped to actual invocations (and the Action's ``extra-args``, which is
    raw argv by another name), so prose and YAML config blocks -- where the
    same token is exactly right -- are untouched. WARN-only, matching the
    retired-surface sweep: the fix is a human decision about which spelling
    the passage meant.
    """
    known_keys = _known_config_keys()
    for path, _rel_unused in scan_targets:
        text = path.read_text(encoding="utf-8")
        for line_no, command in _shell_command_lines(text):
            seen: set[int] = set()
            for m in _CONFIG_KEY_OPERAND_RE.finditer(command):
                seen.add(m.start(1))
                f.warn(
                    "config-key-as-cli-operand",
                    f"{rel(path)}:{line_no}: {m.group(1)!r} is a "
                    ".abicheck.yml key, not a CLI operand -- this command "
                    "exits 64 (Click reads it as unexpected positional "
                    "arguments). Show a config file, or the flag that "
                    "really exists.",
                )
            for m in _BARE_CONFIG_KEY_RE.finditer(command):
                if m.start(1) in seen or m.group(1) not in known_keys:
                    continue
                f.warn(
                    "config-key-as-cli-operand",
                    f"{rel(path)}:{line_no}: {m.group(1)!r} is a "
                    ".abicheck.yml key, not a CLI operand -- this command "
                    "exits 64 (Click reads it as unexpected positional "
                    "arguments). A list-typed key is spelled without a "
                    "colon, which is why it reads like an argument. Show a "
                    "config file, or the flag that really exists.",
                )
        for i, line in enumerate(text.splitlines(), start=1):
            em = _EXTRA_ARGS_RE.match(line)
            if em is None:
                continue
            for m in _CONFIG_KEY_OPERAND_RE.finditer(em.group(1)):
                f.warn(
                    "config-key-as-cli-operand",
                    f"{rel(path)}:{i}: {m.group(1)!r} is a .abicheck.yml "
                    "key, but `extra-args` is raw argv -- it reaches Click "
                    "as unexpected positional arguments. Put it in the "
                    "repository's .abicheck.yml instead.",
                )
