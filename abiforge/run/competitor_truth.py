"""Independent expected answers for the tool comparison (``run.competitors``).

Two things are expected per pair, each from a source no tool under test
produced:

* the **decision** a maintainer needs (ABI break, source-only break,
  compatible, unchanged), from the operator's declared language-rule
  expectation (``EditOp.base()`` plus its first matching config ``Rule``).
  The observability floor of ``EditOp.expect`` is *not* applied: it encodes
  what abicheck's evidence model can see, which would tie the expectation to
  one of the tools being compared.
* the **explanation details** a report should carry, each with its source:
  the edited entity and the changed members come from the operator's header
  text diff; ``sizeof`` and ``offsetof`` come from a compiler probe built
  against each side's header with the library's own compile command; the
  affected public functions are the header's declarations that name the
  edited entity.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field, replace
from pathlib import Path

from abiforge.model.config import Config
from abiforge.model.expect import Expectation
from abiforge.model.seeds import Seed
from abiforge.operators import EditOp
from abiforge.run.harness import Library, compile_cmd

#: Expected decision classes, from the verdict bounds of the expectation.
CLASSES = ("abi_break", "source_break", "risk", "compatible", "unchanged")


def language_expectation(op: EditOp, config: Config) -> Expectation:
    """The edit's declared ground truth for *config* (base + first matching rule)."""
    exp = op.base()
    for rule in op.rules:
        if rule.when(config):
            return rule.apply(exp)
    return exp


def expected_class(exp: Expectation) -> str:
    lo, hi = exp.verdict_min, exp.verdict_max
    if lo == "BREAKING":
        return "abi_break"
    if lo == "API_BREAK":
        return "source_break"
    if hi == "NO_CHANGE":
        return "unchanged"
    if lo == "COMPATIBLE_WITH_RISK":
        return "risk"
    return "compatible"


# ------------------------------------------------------------ header model

_RECORD = re.compile(r"\b(struct|union|class)\s+(\w+)\s*(?::[^{]*)?\{(.*?)\}\s*;", re.S)
_ENUM = re.compile(r"\benum\s+(?:class\s+|struct\s+)?(\w+)")
_TYPEDEF = re.compile(r"\btypedef\b[^;]*?\b(\w+)\s*;")
_USING = re.compile(r"\busing\s+(\w+)\s*=")
_FUNC = re.compile(r"(?:^|[\s*&])(\w+)\s*\([^;{}]*\)\s*(?:const\s*)?(?:noexcept\s*)?;")
_VAR = re.compile(r"\bextern\b[^;(]*?\b(\w+)\s*(?:\[[^\]]*\])?\s*;")


def members(body: str) -> dict[str, str]:
    """``name -> declaration text`` of a simple record body (data members only)."""
    out: dict[str, str] = {}
    for decl in body.split(";"):
        d = " ".join(decl.split())
        if not d or "(" in d or d.startswith(("public", "private", "protected")):
            d = (
                d.split(":", 1)[1].strip()
                if d.startswith(("public:", "private:", "protected:"))
                else d
            )
            if not d or "(" in d:
                continue
        m = re.search(r"(\w+)\s*(?:\[[^\]]*\])*\s*(?::\s*\d+)?$", d)
        if m and not d.startswith(("static ", "typedef ", "using ", "friend ")):
            out[m.group(1)] = d
    return out


def records(text: str) -> dict[str, dict[str, str]]:
    return {m.group(2): members(m.group(3)) for m in _RECORD.finditer(text)}


def _decls(text: str) -> set[str]:
    """The declarations of a role text, comments dropped, whitespace normalized."""
    text = re.sub(r"/\*.*?\*/|//[^\n]*", "", text, flags=re.S)
    return {" ".join(d.split()) + ";" for d in text.split(";") if d.strip()}


def entities(text: str) -> set[str]:
    """Names a declaration text declares: records, enums, typedefs, functions, objects."""
    names = set(records(text))
    for rx in (_ENUM, _TYPEDEF, _USING, _VAR):
        names |= set(rx.findall(text))
    if not _RECORD.search(text):
        names |= {
            n for n in _FUNC.findall(text) if n not in ("sizeof", "requires", "if")
        }
    return names - {"int", "void", "char", "long", "double", "float"}


def public_functions(header: str) -> dict[str, str]:
    """``function name -> its declaration line`` for the header's prototypes."""
    out: dict[str, str] = {}
    for line in header.splitlines():
        s = line.strip()
        if s.startswith(("#", "//", "template", "typedef", "using")) or "{" in s:
            continue
        if m := _FUNC.search(s):
            out[m.group(1)] = s
    return out


@dataclass
class DetailTruth:
    """Expected explanation details, each with the source of the expected value."""

    entities: set[str] = field(default_factory=set)
    fields: set[str] = field(default_factory=set)
    size: tuple[int, int] | None = None  # (old, new) bytes of the edited record
    offsets_moved: set[str] = field(default_factory=set)
    functions: set[str] = field(default_factory=set)
    record: str | None = None
    sources: dict[str, str] = field(default_factory=dict)

    @property
    def size_changed(self) -> bool | None:
        return None if self.size is None else self.size[0] != self.size[1]

    def to_json(self) -> dict[str, object]:
        return {
            "entities": sorted(self.entities),
            "record": self.record,
            "fields": sorted(self.fields),
            "size": list(self.size) if self.size else None,
            "offsets_moved": sorted(self.offsets_moved),
            "functions": sorted(self.functions),
            "sources": self.sources,
        }


def exp_min_rank_is_change(op: EditOp) -> bool:
    """Details are owed only when the edit must be reported (``verdict_min`` above
    ``NO_CHANGE``): a report may legitimately stay silent on anything else."""
    return op.verdict_min != "NO_CHANGE"


def changed_roles(seed: Seed, op: EditOp) -> dict[str, tuple[str, str]]:
    """``role -> (old header text, new header text)`` for the roles the edit re-spells."""
    new = op.edit(seed.roles)
    return {
        r: (seed.roles.get(r, ("", ""))[0], t[0])
        for r, t in new.items()
        if not r.startswith("build.") and seed.roles.get(r, ("", ""))[0] != t[0]
    }


def detail_truth(
    seed: Seed, op: EditOp | None, old: Library, new: Library, work: Path
) -> DetailTruth:
    """The details a report should explain for this pair (empty for a control)."""
    dt = DetailTruth()
    if op is None:
        return dt
    roles = changed_roles(seed, op)
    if not roles:
        return dt
    src_diff = "operator header-text diff (abiforge.operators, role texts)"
    if not exp_min_rank_is_change(op):
        return dt
    for old_t, new_t in roles.values():
        changed = _decls(old_t) ^ _decls(new_t)
        ents: set[str] = (
            set().union(*(entities(d) for d in changed)) if changed else set()
        )
        ro, rn = records(old_t), records(new_t)
        for rec in ro.keys() & rn.keys():
            if list(ro[rec].items()) != list(rn[rec].items()):
                dt.record = rec
                mo, mn = ro[rec], rn[rec]
                dt.fields |= (mo.keys() ^ mn.keys()) | {
                    m for m in mo.keys() & mn.keys() if mo[m] != mn[m]
                }
                ents = {rec}
        dt.entities |= ents
    if dt.entities:
        dt.sources["entities"] = src_diff
    if dt.fields:
        dt.sources["fields"] = src_diff
    old_h = old.header.read_text(encoding="utf-8")
    new_h = new.header.read_text(encoding="utf-8")
    fo, fn = public_functions(old_h), public_functions(new_h)
    for name in fo.keys() | fn.keys():
        decl = fo.get(name, "") + " " + fn.get(name, "")
        if name in dt.entities or any(
            re.search(rf"\b{re.escape(e)}\b", decl) for e in dt.entities
        ):
            dt.functions.add(name)
    if dt.functions:
        dt.sources["functions"] = (
            "public prototypes in the old/new header that declare or name the edited entity"
        )
    if dt.record:
        po = probe_layout(
            old, dt.record, records(old_h).get(dt.record, {}), work / "probe_old"
        )
        pn = probe_layout(
            new, dt.record, records(new_h).get(dt.record, {}), work / "probe_new"
        )
        if po and pn:
            dt.size = (po[0], pn[0])
            dt.offsets_moved = {
                m for m in po[1].keys() & pn[1].keys() if po[1][m] != pn[1][m]
            }
            src = "compiler probe: sizeof/offsetof compiled against each side's header with the library's compile command"
            dt.sources["size"] = src
            dt.sources["offsets_moved"] = src
    return dt


def probe_layout(
    lib: Library, record: str, mems: dict[str, str], d: Path
) -> tuple[int, dict[str, int]] | None:
    """(sizeof, member -> offsetof) of *record* as the producer lays it out."""
    cfg = lib.config
    cxx = cfg.lang == "c++"
    tag = "" if cxx else "struct "
    names = [
        m for m, d in mems.items() if not re.search(r":\s*\d+$", d)
    ]  # no bit-fields
    body = [
        f'#include "{lib.header.name}"',
        "#include <stdio.h>",
        "#include <stddef.h>",
        "int main(void) {",
        f'  printf("sizeof %zu\\n", sizeof({tag}{record}));',
        *[f'  printf("{m} %zu\\n", offsetof({tag}{record}, {m}));' for m in names],
        "  return 0; }",
    ]
    d.mkdir(parents=True, exist_ok=True)
    src = d / f"probe_{record}.{'cpp' if cxx else 'c'}"
    src.write_text("\n".join(body) + "\n", encoding="utf-8")
    exe = d / f"probe_{record}"
    flags = replace(cfg.flags)
    cmd = [
        *compile_cmd(cfg.producer, cfg.std, flags, debug=False),
        "-w",
        "-I",
        str(lib.dir),
        str(src),
        "-o",
        str(exe),
    ]
    if subprocess.run(cmd, capture_output=True, check=False).returncode != 0:  # noqa: S603
        return None
    out = subprocess.run([str(exe)], capture_output=True, text=True, check=False).stdout  # noqa: S603
    vals = dict(line.split() for line in out.splitlines() if line.strip())
    return int(vals.pop("sizeof")), {k: int(v) for k, v in vals.items()}
