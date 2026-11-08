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

"""``delta-on-missing-base: build`` -- the dump of a base commit built in the PR job.

Plan: ``docs/contribute/plans/pr-delta-vs-main-snapshot.md`` (§ Outcomes,
``build``). When no stored snapshot describes the PR's base commit, the main
Action checks that commit out beside the PR checkout, runs the user's build
command there, and dumps the result as the delta's old side.

Evidence parity is the rule this module enforces: the base is dumped with
the **new side's** evidence inputs of the gate command -- the same headers,
include directories, debug info, build/source evidence, depth and compile
context -- each path moved from the PR checkout into the base checkout. A
path that cannot be moved (an absolute path outside the PR checkout) would
describe the PR build, not the base, so it is refused rather than reused:
a delta measured with the PR's headers on both sides would hide exactly the
header changes it exists to show. The one exception is an include directory
outside the checkout (a system or toolchain directory), which is the same
for both builds and is kept as given.

Pure: no I/O beyond path arithmetic, so every rule is unit-tested without a
build.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Sequence
from dataclasses import dataclass

__all__ = ["BaseDumpRefused", "base_dump_argv", "map_into_base"]


class BaseDumpRefused(ValueError):
    """The gate's new side cannot be re-pointed at the base checkout."""


#: Gate flag -> the ``dump`` flag carrying the same evidence. Each takes a
#: path, optionally sided (``old=``/``new=``) on ``compare``.
_PATH_FLAGS = {
    "-H": "-H",
    "--header": "-H",
    "-I": "-I",
    "--include": "-I",
    "--debug-info": "--debug-info",
    "--sources": "--sources",
    "--build-info": "--build-info",
}
#: Paths that may legitimately sit outside the checkout (toolchain/system).
_MAY_STAY_OUTSIDE = {"-I"}
#: Kept verbatim with their value: they shape extraction, not one side.
_VALUE_FLAGS = {
    "--depth",
    "--config",
    "-D",
    "--define",
    "--exclude-header",
    "--search-path",
    "--ld-library-path",
}
_BARE_FLAGS = {"--follow-deps", "--no-follow-deps"}
#: ``--set`` keys that describe how a side is extracted. Report, gate,
#: policy and consumer keys belong to the comparison, not to a dump.
_SET_PREFIXES = ("compile.", "build.")


def map_into_base(
    path: str,
    *,
    cwd: str,
    checkout: str,
    base_root: str,
    may_stay_outside: bool = False,
) -> str:
    """*path* (as the gate saw it, relative to *cwd*) inside *base_root*.

    *checkout* is the PR checkout's top-level directory; *cwd* must be inside
    it. A path inside the checkout keeps its position relative to the
    checkout root. Anything else raises :class:`BaseDumpRefused`, unless
    *may_stay_outside*, in which case it is returned unchanged.
    """
    if not path:
        raise BaseDumpRefused("empty path")
    absolute = posixpath.normpath(posixpath.join(cwd, path))
    top = posixpath.normpath(checkout)
    if absolute == top or absolute.startswith(top.rstrip("/") + "/"):
        rel = posixpath.relpath(absolute, top)
        return posixpath.normpath(posixpath.join(base_root, rel))
    if may_stay_outside:
        return path
    raise BaseDumpRefused(
        f"{path!r} is outside the checkout ({top}), so the base build cannot "
        "provide it; point the input at a path inside the repository"
    )


@dataclass(frozen=True)
class _Flag:
    name: str
    value: str
    #: The token(s) consumed, for the "skip unknown" path.
    width: int


#: Click lets boolean ``-v`` cluster in front of a short option (``-vH x``).
_CLUSTER = re.compile(r"^-v+([HID].*)$")


def _unclustered(tok: str) -> str:
    match = _CLUSTER.match(tok)
    return "-" + match.group(1) if match else tok


def _split_path_flag(argv: Sequence[str], i: int) -> _Flag | None:
    tok = _unclustered(argv[i])
    if tok in _PATH_FLAGS:
        if i + 1 >= len(argv):
            raise BaseDumpRefused(f"{tok} has no value")
        return _Flag(tok, argv[i + 1], 2)
    for flag in _PATH_FLAGS:
        if flag.startswith("--") and tok.startswith(flag + "="):
            return _Flag(flag, tok[len(flag) + 1 :], 1)
        if not flag.startswith("--") and tok.startswith(flag) and len(tok) > len(flag):
            return _Flag(flag, tok[len(flag) :], 1)
    return None


def base_dump_argv(
    gate_tail: Sequence[str],
    *,
    new_library: str,
    cwd: str,
    checkout: str,
    base_root: str,
    output: str,
    value_options: frozenset[str] = frozenset(),
) -> list[str]:
    """``dump`` arguments (after the program name) for the built base.

    *gate_tail* is the gate ``compare`` argv after its two operands.
    *value_options* names every other option of the gate command that takes
    a separate value, so that value is skipped with its option rather than
    read as a flag. Old-side evidence (``old=``) is dropped: it describes
    the release baseline, not the base commit.
    """
    argv = [
        "dump",
        map_into_base(new_library, cwd=cwd, checkout=checkout, base_root=base_root),
    ]
    i = 0
    while i < len(gate_tail):
        tok = _unclustered(gate_tail[i])
        path_flag = _split_path_flag(gate_tail, i)
        if path_flag is not None:
            i += path_flag.width
            side, sep, rest = path_flag.value.partition("=")
            if sep and side == "old":
                continue
            value = rest if sep and side == "new" else path_flag.value
            dump_flag = _PATH_FLAGS[path_flag.name]
            argv += [
                dump_flag,
                map_into_base(
                    value,
                    cwd=cwd,
                    checkout=checkout,
                    base_root=base_root,
                    may_stay_outside=dump_flag in _MAY_STAY_OUTSIDE,
                ),
            ]
            continue
        name, eq, inline = tok.partition("=")
        if name == "--set":
            value = (
                inline if eq else (gate_tail[i + 1] if i + 1 < len(gate_tail) else "")
            )
            i += 1 if eq else 2
            if value.startswith(_SET_PREFIXES):
                argv += ["--set", value]
            continue
        if name in _VALUE_FLAGS:
            value = (
                inline if eq else (gate_tail[i + 1] if i + 1 < len(gate_tail) else "")
            )
            i += 1 if eq else 2
            argv += [name, value]
            continue
        if tok.startswith("-D") and len(tok) > 2 and not tok.startswith("--"):
            argv.append(tok)  # -DNAME[=VALUE]
            i += 1
            continue
        if tok in _BARE_FLAGS:
            argv.append(tok)
            i += 1
            continue
        i += 2 if (not eq and tok in value_options) else 1
    argv += ["-o", output]
    return argv
