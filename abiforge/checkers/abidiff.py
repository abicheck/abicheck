"""libabigail ``abidiff`` adapter: run it on a library pair and read its answer.

``abidiff`` reads DWARF. Its exit status is a bit field (libabigail manual,
"abidiff return values"): bit 0 (1) an error, bit 1 (2) a usage error,
bit 2 (4) an ABI change, bit 3 (8) an ABI change that is *incompatible* (a
removed function or variable, a changed symbol). A record layout change sets
only bit 2: abidiff leaves the compatibility judgement of a type change to
the reader. So the decision this adapter reports needs the report text as
well as the status: :func:`decision` reads both.

With public headers, ``--headers-dir1/2`` restrict the report to types those
headers declare (abidiff matches a type's DWARF declaring file against the
header directory). Without DWARF (a stripped library) abidiff can only read
the ELF symbol table; :func:`run` records that, it does not hide it.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from abiforge.checkers.tools import ToolRun, missing, run_tool

STATUS_BITS = {1: "error", 2: "usage_error", 4: "abi_change", 8: "incompatible_change"}


def decode_status(code: int | None) -> dict[str, bool]:
    """The named bits of an ``abidiff`` exit status."""
    return {
        name: bool(code is not None and code & bit) for bit, name in STATUS_BITS.items()
    }


def run(
    old_so: Path,
    new_so: Path,
    out: Path,
    *,
    headers_dirs: tuple[Path, Path] | None = None,
    timeout: float = 300.0,
    stem: str = "abidiff",
) -> ToolRun:
    """``abidiff [--headers-dir1 D1 --headers-dir2 D2] OLD NEW`` into ``out/<stem>.txt``."""
    tr = ToolRun(mode="abidiff")
    if why := missing("abidiff"):
        tr.unavailable = why
        return tr
    argv = ["abidiff"]
    if headers_dirs is not None:
        argv += [
            "--headers-dir1",
            str(headers_dirs[0]),
            "--headers-dir2",
            str(headers_dirs[1]),
        ]
    argv += [str(old_so), str(new_so)]
    step = run_tool(argv, out / f"{stem}.txt", timeout=timeout)
    tr.steps.append(step)
    tr.reports["report"] = step.stdout
    return tr


@dataclass
class TypeChange:
    size: tuple[int, int] | None = None
    added: set[str] = field(default_factory=set)
    removed: set[str] = field(default_factory=set)
    retyped: set[str] = field(default_factory=set)
    moved: set[str] = field(default_factory=set)
    enumerators: bool = False


@dataclass
class Report:
    removed_functions: set[str] = field(default_factory=set)
    added_functions: set[str] = field(default_factory=set)
    changed_functions: set[str] = field(default_factory=set)
    removed_variables: set[str] = field(default_factory=set)
    added_variables: set[str] = field(default_factory=set)
    changed_variables: set[str] = field(default_factory=set)
    types: dict[str, TypeChange] = field(
        default_factory=lambda: defaultdict(TypeChange)
    )
    elf_symbol_changes: bool = False
    soname_changed: bool = False
    other_lines: int = 0

    @property
    def empty(self) -> bool:
        return not (
            self.removed_functions
            or self.added_functions
            or self.changed_functions
            or self.removed_variables
            or self.added_variables
            or self.changed_variables
            or self.types
            or self.elf_symbol_changes
            or self.soname_changed
        )


_ENTRY = re.compile(
    r"^\s*\[(?P<tag>[ACD])\] '(?:(?P<what>function|method|variable) )?(?P<decl>[^']*?)'"
)
#: An ELF symbol listed without debug info: ``  [A] add3`` (no quotes).
_ELF_ENTRY = re.compile(r"^\s*\[(?P<tag>[ACD])\] (?P<name>[\w.@]+)\s*$")
_TYPE = re.compile(
    r"(?:type|in pointed to type|in referenced type|underlying type) '(?P<t>(?:struct|union|enum|class) [\w:]+|[\w:]+)'"
)
_MEMBER = re.compile(r"'(?P<decl>[^']*?)(?P<name>\w+)(?:\[\d*\])*'")
_SIZE = re.compile(r"type size changed from (\d+) to (\d+)")


def _decl_name(decl: str) -> str:
    """``void fill_rec(rec*, int)`` -> ``fill_rec``; ``int g_counter`` -> ``g_counter``."""
    head = decl.split("(", 1)[0].strip()
    return re.split(r"[\s*&]+", head)[-1].split("::")[-1]


def parse(text: str) -> Report:
    """Read an ``abidiff`` text report into named changes (generic, any library)."""
    rep = Report()
    cur: TypeChange | None = None
    mode: str | None = None
    last: str | None = None
    section: str | None = None
    for line in text.splitlines():
        s = line.strip()
        if m := _ENTRY.match(line):
            name = _decl_name(m["decl"])
            var = m["what"] == "variable" or (
                m["what"] is None and "(" not in m["decl"]
            )
            target = {
                ("A", False): rep.added_functions,
                ("D", False): rep.removed_functions,
                ("C", False): rep.changed_functions,
                ("A", True): rep.added_variables,
                ("D", True): rep.removed_variables,
                ("C", True): rep.changed_variables,
            }[(m["tag"], var)]
            target.add(name)
            cur, mode, last = None, None, None
            continue
        if (m := _ELF_ENTRY.match(line)) and section is not None:
            target = {
                ("A", "function"): rep.added_functions,
                ("D", "function"): rep.removed_functions,
                ("A", "variable"): rep.added_variables,
                ("D", "variable"): rep.removed_variables,
            }.get((m["tag"], section))
            if target is not None:
                target.add(m["name"].split("@")[0])
                continue
        if hdr := re.match(
            r"\d+ (?:Added|Removed) (function|variable) symbols? not referenced by debug info",
            s,
        ):
            section = hdr.group(1)
            continue
        if "ELF SONAME changed" in s:
            rep.soname_changed = True
        if re.search(
            r"ELF symbols? (not referenced by debug info|changes)|Unreferenced symbols|symbol .* (version|binding)",
            s,
        ):
            rep.elf_symbol_changes = True
        if (m := _TYPE.search(s)) and ("changed" in s or s.endswith(":")):
            name = m["t"].split()[-1]
            cur, mode, last = rep.types[name], None, None
            continue
        if cur is None:
            continue
        if (m := _SIZE.search(s)) and mode is None and last is None:
            cur.size = (int(m[1]), int(m[2]))
        elif re.match(r"\d+ data member deletions?:", s):
            mode = "removed"
        elif re.match(r"\d+ data member insertions?:", s):
            mode = "added"
        elif re.match(r"\d+ data member changes?", s):
            mode = "changed"
        elif re.match(r"\d+ enumerator (insertion|deletion|change)", s):
            cur.enumerators = True
        elif mode in ("removed", "added") and (m := _MEMBER.match(s)):
            (cur.removed if mode == "removed" else cur.added).add(m["name"])
        elif s.startswith("type of ") and (m := _MEMBER.search(s)):
            last = m["name"]
            cur.retyped.add(last)
        elif s.startswith("and offset changed") and last is not None:
            cur.moved.add(last)
        elif "offset changed from" in s and (m := _MEMBER.match(s)):
            cur.moved.add(m["name"])
            last = m["name"]
        elif s.startswith("'") and "renamed" not in s and (m := _MEMBER.match(s)):
            last = m["name"]
    rep.types = dict(rep.types)
    return rep


def decision(status: int | None, rep: Report) -> str:
    """abidiff's own answer as a maintainer decision.

    ``BREAKING``: bit 3 (abidiff itself proved an incompatible change).
    ``ADVISORY``: bit 2 only, and the report names a changed or removed entity
    (abidiff shows a change and leaves the verdict to the reader).
    ``COMPATIBLE``: bit 2 only and every named change is an addition.
    ``NO_CHANGE``: status 0. Bits 0/1 are errors, decided by the caller.
    """
    bits = decode_status(status)
    if bits["incompatible_change"]:
        return "BREAKING"
    if not bits["abi_change"]:
        return "NO_CHANGE"
    only_added = (rep.added_functions or rep.added_variables) and not (
        rep.removed_functions
        or rep.changed_functions
        or rep.removed_variables
        or rep.changed_variables
        or rep.types
        or rep.elf_symbol_changes
        or rep.soname_changed
    )
    return "COMPATIBLE" if only_added else "ADVISORY"
