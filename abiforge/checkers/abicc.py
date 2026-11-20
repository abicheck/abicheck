"""ABI Compliance Checker (ABICC) adapters: two workflows, one report reader.

ABICC has two inputs of different nature, kept apart here:

* **dump workflow** (``abi-dumper`` -> ``abi-compliance-checker``): each
  library is dumped from its DWARF by ``abi-dumper``; the checker compares
  the two dumps. Without DWARF there is nothing to dump.
* **descriptor workflow**: each side is described by an *input* XML
  descriptor (``<version>``, ``<headers>``, ``<libs>``, ``<gcc_options>``);
  ABICC compiles the public headers itself (GCC) and reads the library's
  symbol table, so it works on a stripped library.

The *output* report format (``-report-format xml``) is a different XML: one
``<report kind="binary">`` and, when both are produced, a
``<report kind="source">`` under ``<reports>``. :func:`parse` reads each kind
separately; binary and source results are never merged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape

from defusedxml import ElementTree as ET

from abiforge.checkers.tools import ToolRun, missing, run_tool

#: Version labels; ABICC reads a bare ``0`` as unset, so ``1.0``/``2.0``.
OLD_VER, NEW_VER = "1.0", "2.0"


def dump(
    lib: Path,
    out_file: Path,
    version: str,
    *,
    public_headers: Path | None,
    timeout: float,
):  # type: ignore[no-untyped-def]
    argv = ["abi-dumper", str(lib), "-o", str(out_file), "-lver", version]
    if public_headers is not None:
        argv += ["-public-headers", str(public_headers)]
    return run_tool(argv, out_file.with_suffix(".dump.log"), timeout=timeout)


def run_dumps(
    old_so: Path,
    new_so: Path,
    out: Path,
    *,
    name: str = "edit",
    binary_only: bool = False,
    public_headers: tuple[Path, Path] | None = None,
    timeout: float = 300.0,
    stem: str = "abicc_dump",
    report: Path | None = None,
    versions: tuple[str, str] = (OLD_VER, NEW_VER),
) -> ToolRun:
    """``abi-dumper`` on each side, then ``abi-compliance-checker`` on the dumps."""
    tr = ToolRun(mode="abicc-dump")
    if why := missing("abi-dumper", "abi-compliance-checker"):
        tr.unavailable = why
        return tr
    dumps = []
    for side, lib, ver, hdr in (
        ("old", old_so, versions[0], public_headers[0] if public_headers else None),
        ("new", new_so, versions[1], public_headers[1] if public_headers else None),
    ):
        d = out / f"{stem}_{side}.dump"
        step = dump(lib, d, ver, public_headers=hdr, timeout=timeout)
        tr.steps.append(step)
        if step.exit != 0 or not d.exists():
            return tr
        dumps.append(d)
    rep = report or out / f"{stem}.xml"
    argv = ["abi-compliance-checker", "-l", name]
    if binary_only:
        argv.append("-binary")
    argv += ["-report-format", "xml", "-old", str(dumps[0]), "-new", str(dumps[1])]
    argv += ["-report-path", str(rep)]
    tr.steps.append(run_tool(argv, out / f"{stem}.log", cwd=out, timeout=timeout))
    if rep.exists():
        tr.reports["report"] = rep.name
    return tr


def descriptor(
    version: str, headers: list[Path], libs: list[Path], gcc_options: str
) -> str:
    """An ABICC *input* descriptor for one side."""
    return (
        f"<version>{escape(version)}</version>\n"
        f"<headers>\n{chr(10).join(escape(str(h)) for h in headers)}\n</headers>\n"
        f"<libs>\n{chr(10).join(escape(str(lib)) for lib in libs)}\n</libs>\n"
        f"<gcc_options>\n{escape(gcc_options)}\n</gcc_options>\n"
    )


def run_descriptors(
    old: tuple[Path, list[Path], str],
    new: tuple[Path, list[Path], str],
    out: Path,
    *,
    lang: str,
    name: str = "edit",
    timeout: float = 300.0,
    stem: str = "abicc_xml",
) -> ToolRun:
    """ABICC on two input descriptors: (library, public headers, gcc options) per side.

    One invocation produces both the binary and the source report.
    """
    tr = ToolRun(mode="abicc-xml")
    if why := missing("abi-compliance-checker", "gcc"):
        tr.unavailable = why
        return tr
    files = []
    for side, (lib, headers, opts), ver in (
        ("old", old, OLD_VER),
        ("new", new, NEW_VER),
    ):
        f = out / f"{stem}_{side}.desc.xml"
        f.write_text(descriptor(ver, headers, [lib], opts), encoding="utf-8")
        files.append(f)
    tr.reports["descriptor_old"], tr.reports["descriptor_new"] = (
        files[0].name,
        files[1].name,
    )
    rep = out / f"{stem}.xml"
    argv = [
        "abi-compliance-checker",
        "-l",
        name,
        "-lang",
        "C++" if lang == "c++" else "C",
        "-old",
        str(files[0]),
        "-new",
        str(files[1]),
        "-report-format",
        "xml",
        "-report-path",
        str(rep),
    ]
    tr.steps.append(run_tool(argv, out / f"{stem}.log", cwd=out, timeout=timeout))
    if rep.exists():
        tr.reports["report"] = rep.name
    return tr


# ------------------------------------------------------------------ reader


@dataclass
class TypeProblems:
    problems: list[tuple[str, str, str]] = field(
        default_factory=list
    )  # (severity, id, target)
    size: tuple[int, int] | None = None
    affected: set[str] = field(default_factory=set)

    def ids(self) -> set[str]:
        return {p[1] for p in self.problems}

    def targets(self, *prefixes: str) -> set[str]:
        return {t for _sev, pid, t in self.problems if t and pid.startswith(prefixes)}


@dataclass
class Side:
    """One report kind (``binary`` or ``source``)."""

    kind: str
    verdict: str
    counts: dict[str, int]
    added_symbols: set[str]
    removed_symbols: set[str]
    types: dict[str, TypeProblems]
    symbol_problems: dict[str, list[tuple[str, str]]]  # symbol -> [(severity, id)]
    headers_checked: list[str]
    libs_checked: list[str]
    symbols_checked: int

    def severe(self) -> int:
        return self.counts.get("high", 0) + self.counts.get("medium", 0)


def _int(node, path: str) -> int:  # type: ignore[no-untyped-def]
    el = node.find(path)
    return (
        int(el.text) if el is not None and el.text and el.text.strip().isdigit() else 0
    )


def _symbol_name(el) -> str:  # type: ignore[no-untyped-def]
    raw = el.get("name") or (el.text or "")
    return re.split(r"[(\s]", raw.strip(), maxsplit=1)[0]


def _side(rep) -> Side:  # type: ignore[no-untyped-def]
    if rep.find("problem_summary") is None:
        raise ValueError("ABICC report without <problem_summary>")
    ps = rep.find("problem_summary")
    counts = {
        sev: _int(ps, f"problems_with_types/{sev}")
        + _int(ps, f"problems_with_symbols/{sev}")
        for sev in ("high", "medium", "low", "safe")
    }
    counts["removed_symbols"] = _int(ps, "removed_symbols")
    counts["added_symbols"] = _int(ps, "added_symbols")
    counts["constants_low"] = _int(ps, "problems_with_constants/low")
    added = {
        _symbol_name(n)
        for sec in rep.findall("added_symbols")
        for n in sec.iter("name")
    }
    removed = {
        _symbol_name(n)
        for sec in rep.findall("removed_symbols")
        for n in sec.iter("name")
    }
    types: dict[str, TypeProblems] = {}
    for sec in rep.findall("problems_with_types"):
        sev = (sec.get("severity") or "").lower()
        for t in sec.iter("type"):
            name = (t.get("name") or "").split()[-1] if t.get("name") else ""
            tp = types.setdefault(name, TypeProblems())
            for p in t.findall("problem"):
                ch = p.find("change")
                target = ch.get("target", "") if ch is not None else ""
                tp.problems.append((sev, p.get("id") or "", target))
                if p.get("id") == "DataType_Size" and ch is not None:
                    tp.size = (int(ch.get("old_size", 0)), int(ch.get("new_size", 0)))
            aff = t.find("affected")
            if aff is not None:
                tp.affected |= {_symbol_name(s) for s in aff.findall("symbol")}
    sym: dict[str, list[tuple[str, str]]] = {}
    for sec in rep.findall("problems_with_symbols"):
        sev = (sec.get("severity") or "").lower()
        for s in sec.iter("symbol"):
            for p in s.findall("problem"):
                sym.setdefault(_symbol_name(s), []).append((sev, p.get("id") or ""))
    tr = rep.find("test_results")
    return Side(
        kind=rep.get("kind") or "binary",
        verdict=(rep.findtext("test_results/verdict") or "").strip(),
        counts=counts,
        added_symbols=added,
        removed_symbols=removed,
        types=types,
        symbol_problems=sym,
        headers_checked=[n.text or "" for n in tr.findall("headers/name")]
        if tr is not None
        else [],
        libs_checked=[n.text or "" for n in tr.findall("libs/name")]
        if tr is not None
        else [],
        symbols_checked=_int(rep, "test_results/symbols"),
    )


def parse(xml_text: str) -> dict[str, Side]:
    """Each report kind in an ABICC XML *output* report, by kind."""
    root = ET.fromstring(xml_text)
    reports = (
        [root]
        if root.tag == "report"
        else root.findall("report")
        if root.tag == "reports"
        else []
    )
    if not reports:
        raise ValueError(f"not an ABICC report (root <{root.tag}>)")
    return {s.kind: s for s in map(_side, reports)}


def side_decision(side: Side) -> str:
    """One report kind as a decision: ``INCOMPATIBLE`` (ABICC's own verdict, or
    a High/Medium problem, or a removed symbol), ``ADVISORY`` (Low problems or
    constant changes only), ``COMPATIBLE`` (only additions or Safe notes),
    ``NO_CHANGE``."""
    c = side.counts
    if side.verdict == "incompatible" or side.severe() or c["removed_symbols"]:
        return "INCOMPATIBLE"
    if c["low"] or c["constants_low"]:
        return "ADVISORY"
    if c["added_symbols"] or c["safe"]:
        return "COMPATIBLE"
    return "NO_CHANGE"


def decision(sides: dict[str, Side]) -> str:
    """The maintainer decision from the two kinds: binary first, then source."""
    b = side_decision(sides["binary"]) if "binary" in sides else None
    s = side_decision(sides["source"]) if "source" in sides else None
    if b == "INCOMPATIBLE":
        return "BREAKING"
    if s == "INCOMPATIBLE":
        return "API_BREAK"
    seen = [d for d in (b, s) if d]
    for d in ("ADVISORY", "COMPATIBLE"):
        if d in seen:
            return d
    return "NO_CHANGE"
