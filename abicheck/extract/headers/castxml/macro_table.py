"""Compiler-resolved object-like macros that spell a calling convention.

CastXML drops GNU x86-64 conventions (``ms_abi``/``sysv_abi``) from its
``attributes`` string, so :mod:`.calling_convention` recovers them from the
declaration's own text. Real APIs rarely spell the attribute literally; they
write ``CALL int f(void)`` with ``CALL`` defined in some configuration header,
often under ``#ifdef``. Reading the text alone then misses a switch made
through the macro (``sysv_abi`` -> ``ms_abi`` read as ``NO_CHANGE``) and
invents one when a literal attribute is replaced by an equivalent macro
(``ms_abi`` -> ``(default)``).

The macro definitions are therefore taken from the compiler, not from the
header text: the same castxml command is re-run with ``-E -dM`` (preprocess
only, dump the final macro table), which resolves every ``#ifdef`` and
``#include`` the real parse saw. Only object-like macros whose full expansion
contains a calling-convention token are kept, and they are stored in the
CastXML document itself (one ``<AbicheckMacro>`` per macro) so a cached AST
carries them too. When the preprocess run fails, the document is left without
a table and :mod:`.calling_convention` keeps its literal-text behaviour.
"""

from __future__ import annotations

import logging
import re
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement

log = logging.getLogger(__name__)

#: Tag of the element carrying one resolved macro inside the CastXML root.
MACRO_TAG = "AbicheckMacro"
#: Marks a document whose macro table was produced (possibly empty), so an
#: empty table is distinguishable from "no table could be produced".
MACRO_TABLE_TAG = "AbicheckMacroTable"

_DEFINE_RE = re.compile(r"^#define\s+(?P<name>[A-Za-z_]\w*)(?P<fn>\()?\s?(?P<body>.*)$")
_IDENT_RE = re.compile(r"[A-Za-z_]\w*")
#: Bound on nested expansion; a deeper chain is left partially expanded.
_MAX_EXPANSION_DEPTH = 16


def macro_dump_command(castxml_cmd: list[str], out_path: Path) -> list[str] | None:
    """*castxml_cmd* turned into a ``-E -dM`` run writing *out_path*.

    ``None`` when the command does not have the shape
    ``dumper_ast_config._build_castxml_command`` produces.
    """
    if "--castxml-output=1" not in castxml_cmd or "-o" not in castxml_cmd:
        return None
    cmd = [tok for tok in castxml_cmd if tok != "--castxml-output=1"]
    o_at = len(cmd) - 1 - cmd[::-1].index("-o")
    if o_at + 1 >= len(cmd):
        return None
    cmd[o_at + 1] = str(out_path)
    # Before the trailing input file, so the tool reads the flags first.
    return [*cmd[:-1], "-E", "-dM", cmd[-1]]


def parse_object_macros(text: str) -> dict[str, str]:
    """Object-like ``#define`` bodies in ``-dM`` output (function-like skipped)."""
    defs: dict[str, str] = {}
    for line in text.splitlines():
        m = _DEFINE_RE.match(line)
        if m and not m.group("fn"):
            defs[m.group("name")] = m.group("body").strip()
    return defs


def expand(text: str, defs: Mapping[str, str]) -> str:
    """*text* with every object-like macro in *defs* expanded (no rescans of
    a macro inside its own expansion, as the preprocessor does)."""

    def _expand(s: str, active: frozenset[str], depth: int) -> str:
        if depth > _MAX_EXPANSION_DEPTH:
            return s

        def _sub(m: re.Match[str]) -> str:
            name = m.group(0)
            if name in active or name not in defs:
                return name
            return _expand(defs[name], active | {name}, depth + 1)

        return _IDENT_RE.sub(_sub, s)

    return _expand(text, frozenset(), 0)


def calling_convention_macros(
    defs: Mapping[str, str], has_cc: Callable[[str], bool]
) -> dict[str, str]:
    """The macros of *defs* whose full expansion spells a calling convention,
    mapped to that expansion."""
    out: dict[str, str] = {}
    for name in defs:
        expanded = expand(name, defs)
        if expanded != name and has_cc(expanded):
            out[name] = expanded
    return out


def target_default_convention(defs: Mapping[str, str]) -> str:
    """The convention a plain declaration already has on the target the
    macro table describes: on x86-64 an explicit ``sysv_abi`` (SysV targets)
    or ``ms_abi`` (Windows) is the default and changes nothing, which is how
    the clang backend reads it too. Empty elsewhere."""
    if "__x86_64__" not in defs:
        return ""
    return "ms_abi" if "_WIN32" in defs else "sysv_abi"


class MacroTable:
    """Calling-convention macros plus the target's default convention."""

    def __init__(self, macros: Mapping[str, str], default_cc: str = "") -> None:
        self.macros = dict(macros)
        self.default_cc = default_cc


def attach_macro_table(root: Element, table: MacroTable) -> None:
    """Record *table* in the CastXML *root* (see the module docstring)."""
    macros = table.macros
    SubElement(root, MACRO_TABLE_TAG, default_cc=table.default_cc)
    for name in sorted(macros):
        SubElement(root, MACRO_TAG, name=name, expansion=macros[name])


def read_macro_table(root: Element) -> MacroTable | None:
    """The table :func:`attach_macro_table` stored in *root*, or ``None`` when
    the document has none."""
    marker = root.find(MACRO_TABLE_TAG)
    if marker is None:
        return None
    macros = {
        el.get("name", ""): el.get("expansion", "")
        for el in root.iter(MACRO_TAG)
        if el.get("name")
    }
    return MacroTable(macros, marker.get("default_cc", ""))


def resolve_macro_table(
    castxml_cmd: list[str],
    work_dir: Path,
    run: Callable[..., subprocess.CompletedProcess[str]],
    has_cc: Callable[[str], bool],
) -> MacroTable | None:
    """Run the ``-E -dM`` sibling of *castxml_cmd*; ``None`` on any failure."""
    out_path = work_dir / "macros.txt"
    cmd = macro_dump_command(castxml_cmd, out_path)
    if cmd is None:
        return None
    try:
        result = run(cmd, timeout=120)
        if result.returncode != 0 or not out_path.exists():
            return None
        text = out_path.read_text(encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("castxml macro dump failed: %s", exc)
        return None
    defs = parse_object_macros(text)
    return MacroTable(
        calling_convention_macros(defs, has_cc), target_default_convention(defs)
    )
