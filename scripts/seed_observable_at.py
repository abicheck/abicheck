#!/usr/bin/env python3
"""Seed ``observable_at`` on the ChangeKind taxonomy (ADR-093, WS-1 slice 3).

``DECISIONS`` is the reviewable record of how each kind's observability was
derived. One row per kind: ``kind  CODE  # note``. A kind with no row, or with
code ``U``, stays ``UNCLASSIFIED``: when the evidence is ambiguous or absent
the sentinel is the answer, because a wrong value creates a false-clean
obligation that no run can discharge.

Evidence collected per kind (``--table`` renders it as Markdown):

* abiforge operators whose ``expected`` or ``any_of`` names the kind, with the
  operator's ``min_evidence`` (``fn`` = computed per config);
* the designed evidence tier of ``scripts/evidence_tiers.py``
  (``EVIDENCE_TIER_BY_KIND``, a chain assumption: a hint, not a rule);
* the modules outside the taxonomy that emit ``ChangeKind.<NAME>``.

Rules applied (plan ``abicheck-abiforge-completeness.md`` §1.2, §2):

* pure export-table facts (symbols, versions, SONAME, ELF/Mach-O/PE header
  facts) -> ``B``;
* layout or type facts that both DWARF and the public header carry ->
  ``{B,D}, {B,H}``;
* compiler-flag facts that headers never carry -> ``{B,D}, {B,BD}``;
* declaration-only facts (defaults, ``explicit``, ``noexcept``, deprecation,
  access, qualifiers, inline or template bodies, macros) -> ``{B,H}``;
* facts that need the source (``S``) or the graph (``G``) -> ``L4`` / ``L5``;
* platform-specific facts carry an ``ObservabilityCondition``.

Apply the decisions to the taxonomy modules (formatting afterwards with
``ruff format``)::

    python scripts/seed_observable_at.py --apply
    python scripts/seed_observable_at.py --baseline   # rewrite the U baseline
"""

from __future__ import annotations

import argparse
import glob
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
CATALOG = REPO / "abicheck" / "model" / "change_catalog"
BASELINE = REPO / "tests" / "data" / "change_kind_unclassified.json"
# architecture/modules.yaml `limits.production`.
PRODUCTION_LINE_CAP = 800

# Ordered form codes. ``form`` is (alternatives, rationale, platforms, const).
# ``const`` is the module-level name the taxonomy file defines and reuses.
FORMS: dict[str, tuple[tuple[tuple[str, ...], ...], str, tuple[str, ...], str]] = {
    "B": ((("B",),), "", (), "_OBS_B"),
    "B:ELF": ((("B",),), "", ("elf",), "_OBS_B_ELF"),
    "B:MACHO": ((("B",),), "", ("macho",), "_OBS_B_MACHO"),
    "B:PE": ((("B",),), "", ("pe",), "_OBS_B_PE"),
    "B:ELF_MACHO": ((("B",),), "", ("elf", "macho"), "_OBS_B_ELF_MACHO"),
    "BH": (
        (("B", "H"),),
        "declared in the public header and present in the export table",
        (),
        "_OBS_BH",
    ),
    "H": (
        (("B", "H"),),
        "declaration-only fact: visible in the public header",
        (),
        "_OBS_H",
    ),
    "DH:ELF": (
        (("B", "D"), ("B", "H")),
        "ELF binary-only mode emits the *_elf_only twin: needs DWARF or headers",
        ("elf",),
        "_OBS_DH_ELF",
    ),
    "DH": (
        (("B", "D"), ("B", "H")),
        "layout or type fact: DWARF and the public header both carry it",
        (),
        "_OBS_DH",
    ),
    "H:PURE": (
        (("B", "H"),),
        "a pure virtual has no body and no symbol: only the public header declares it, so a DWARF-only surface never holds it",
        (),
        "_OBS_H_PURE",
    ),
    "D:PRESENCE": (
        (("B", "D"),),
        "DWARF presence on the old side: only a debug-info read sees it",
        (),
        "_OBS_D_PRESENCE",
    ),
    "D1": (
        (("B", "D"),),
        "underlying size set by the compiler: only DWARF records it",
        (),
        "_OBS_D",
    ),
    "DBD": (
        (("B", "D"), ("B", "BD")),
        "compiler-flag fact: the header never carries it; DWARF and build data do",
        (),
        "_OBS_DBD",
    ),
    "BHSG": (
        (("B", "H", "S", "G"),),
        "call edges across translation units: needs sources and the graph",
        (),
        "_OBS_BHSG",
    ),
    "BHS": (
        (("B", "H", "S"),),
        "single-TU source fact: carried by the translation unit's sources",
        (),
        "_OBS_BHS",
    ),
    "BHS_TREE": (
        (("B", "H", "S"),),
        "source-tree fact: carried by the source replay of the tree",
        (),
        "_OBS_BHS_TREE",
    ),
    "BH_ALIGN": (
        (("B", "H"),),
        "record alignment is a header layout fact; DWARF records it only when explicit",
        (),
        "_OBS_BH_ALIGN",
    ),
    "DH:ELFPE": (
        (("B", "D"), ("B", "H")),
        "layout or type fact: DWARF and the public header both carry it",
        ("elf", "pe"),
        "_OBS_DH_ELFPE",
    ),
    "BH:PE": (
        (("B", "H"),),
        "declared in the public header and present in the export table",
        ("pe",),
        "_OBS_BH_PE",
    ),
    "BH:ELFPE": (
        (("B", "H"),),
        "declared in the public header and present in the export table",
        ("elf", "pe"),
        "_OBS_BH_ELFPE",
    ),
    "DH:C": (
        (("B", "D"), ("B", "H")),
        "a C parameter change keeps the symbol: DWARF or the header carries it",
        (),
        "_OBS_DH_C",
    ),
    "B:CXX": (
        (("B",),),
        "",
        (),
        "_OBS_B_CXX",
    ),
    "BH_BD_ACCESS": (
        (("B", "H"), ("B", "D")),
        "access level: the public header declares it, DWARF carries it as DW_AT_accessibility",
        (),
        "_OBS_H_OR_D",
    ),
    "L4": (
        (("B", "H", "BD", "S"),),
        "needs the source translation unit (S)",
        (),
        "_OBS_L4",
    ),
    "L5": (
        (("B", "H", "BD", "S", "G"),),
        "needs the source graph (G)",
        (),
        "_OBS_L5",
    ),
}

# kind  CODE  [# note].  Kinds absent here (or coded U) stay UNCLASSIFIED.
DECISIONS_FILE = Path(__file__).with_name("seed_observable_decisions.txt")
DECISIONS_TEXT = DECISIONS_FILE.read_text()

# Kinds that stay UNCLASSIFIED on purpose, with the reason. Kept here so the
# reviewer sees what was considered and not classified (not used by --apply).
UNCLASSIFIED_REASONS: dict[str, str] = {
    "func_virtual_became_pure": "API (B,H) or ABI (B,H,S) form: the kind does not say which",
    "func_became_inline": "export-table effect depends on the inline emission in the TU",
    "func_deleted_dwarf": "a DWARF-derived deletion: its positive evidence is unclear",
    "python_api_*": "Python binding surface: no evidence source named in the taxonomy",
    "python_abi3_*": "stable-ABI floor: depends on the producer's module init symbols",
    "bundle_*": "multi-library bundle facts: a pipeline fact, not one library's",
    "numpy_*": "NumPy C-API consumption: a producer-side policy fact",
    "wheel_*": "wheel packaging facts: outside the ELF/PE/Mach-O header set",
    "sycl_*": "SYCL plugin facts: runtime deployment, not a library surface",
    "declaration_*": "graph reconciliation identity: a pipeline fact (ADR-031)",
    "suppression_*": "policy disposition facts (ADR-067), not observation facts",
    "kabi_*": "kernel ABI symbol metadata: not covered by the ELF/PE/Mach-O sets",
}

FORM_CODES = frozenset(FORMS)

#: The language a form's condition is scoped to (``DH:C``, ``B:CXX``). Forms
#: absent here are language-free.
FORM_LANGUAGES: dict[str, tuple[str, ...]] = {"DH:C": ("c",), "B:CXX": ("cxx",)}


@dataclass(frozen=True, slots=True)
class Decision:
    """One kind's row. ``code`` is one form code, or several joined by ``+``:
    one (form, condition) pair per entry of the catalog's observable_at tuple."""

    kind: str
    code: str
    note: str

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(self.code.split("+"))


def form_observability(code: str) -> tuple[Any, ...]:
    """The catalog value one form code stands for: one ``Observability`` entry.

    Built with the same ``observable`` call the taxonomy uses, so the ledger and
    the catalog compare by value. Imported lazily: this module stays stdlib-only
    for the gates that import it without the package installed.
    """
    from abicheck.model.change_catalog.observability import (
        Language,
        ObservabilityCondition,
        Platform,
        observable,
    )

    alts, rationale, platforms, _const = FORMS[code]
    languages = FORM_LANGUAGES.get(code, ())
    condition = None
    if platforms or languages:
        condition = ObservabilityCondition(
            platform=frozenset(Platform(p) for p in platforms) if platforms else None,
            language=frozenset(Language(lang) for lang in languages)
            if languages
            else None,
        )
    return (observable(*alts, rationale=rationale, condition=condition),)


def decision_observability(code: str) -> tuple[Any, ...]:
    """The catalog value a (possibly multi-condition) ledger code stands for."""
    out: tuple[Any, ...] = ()
    for part in code.split("+"):
        out += form_observability(part)
    return out


def codes_of(value: tuple[Any, ...]) -> str:
    """The inverse: the ledger code whose conditions reproduce a catalog value."""
    codes: list[str] = []
    for obs in value:
        match = [c for c in FORMS if form_observability(c)[0] == obs]
        if len(match) != 1:
            raise ValueError(f"no single form for {obs!r}: {match}")
        codes.append(match[0])
    return "+".join(codes)


def parse_decisions(text: str = DECISIONS_TEXT) -> dict[str, Decision]:
    """Parse ``kind CODE [# note]`` rows; reject unknown codes and duplicates."""
    out: dict[str, Decision] = {}
    for raw in text.splitlines():
        line, _, note = raw.partition("#")
        parts = line.split()
        if not parts:
            continue
        if len(parts) != 2:
            raise ValueError(f"malformed decision row: {raw!r}")
        kind, code = parts
        if code == "U":
            continue
        for part in code.split("+"):
            if part not in FORM_CODES:
                raise ValueError(f"{kind}: unknown form code {part!r}")
        if kind in out:
            raise ValueError(f"{kind}: decided twice")
        out[kind] = Decision(kind, code, note.strip())
    return out


DECISIONS: dict[str, Decision] = parse_decisions()


# ── evidence ────────────────────────────────────────────────────────────────


def taxonomy_files() -> list[Path]:
    """Files under the change catalog that hold ``_E(...)`` entry rows."""
    found = []
    for path in sorted(CATALOG.glob("*.py")):
        if re.search(r"^    _E\($", path.read_text(), re.M):
            found.append(path)
    return found


def taxonomy_owner() -> dict[str, str]:
    owner: dict[str, str] = {}
    for path in taxonomy_files():
        for m in re.finditer(r'_E\(\s*\n\s*"([a-z0-9_]+)"', path.read_text()):
            owner[m.group(1)] = path.name
    return owner


def operator_evidence() -> dict[str, list[tuple[str, str]]]:
    """Kind -> [(operator name, evidence source)] over abiforge's operators."""
    sys.path.insert(0, str(REPO))
    from abiforge.operators import OPERATORS

    out: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for op in OPERATORS:
        kinds = set(op.expected)
        for group in op.any_of:
            kinds |= set(group)
        # The evidence floor is no longer a per-operator field: it is each
        # kind's observable_at, applied per configuration.
        me = "observable_at"
        for kind in kinds:
            out[kind.lower()].append((op.name, me))
    return out


def designed_tiers() -> dict[str, str]:
    sys.path.insert(0, str(REPO / "scripts"))
    import evidence_tiers

    return dict(evidence_tiers.EVIDENCE_TIER_BY_KIND)


def detector_modules() -> dict[str, set[str]]:
    out: dict[str, set[str]] = defaultdict(set)
    for path in glob.glob(str(REPO / "abicheck" / "**" / "*.py"), recursive=True):
        rel = Path(path).relative_to(REPO / "abicheck").as_posix()
        if rel.startswith("model/change_catalog/") or "/tests/" in rel:
            continue
        for m in re.finditer(r"ChangeKind\.([A-Z0-9_]+)\b", Path(path).read_text()):
            out[m.group(1).lower()].add(rel)
    return out


# ── rendering ───────────────────────────────────────────────────────────────


def _spell(alts: tuple[tuple[str, ...], ...]) -> str:
    return ", ".join("{" + ",".join(a) + "}" for a in alts)


def _shape(dec: Decision) -> str:
    """The shape text of a decision: each condition's alternatives, with its scope."""
    parts = []
    for code in dec.codes:
        alts, _rat, platforms, _const = FORMS[code]
        text = _spell(alts)
        scope = [*platforms, *FORM_LANGUAGES.get(code, ())]
        if scope:
            text += " on " + ",".join(scope)
        parts.append(text)
    return " ; ".join(parts)


def markdown_table() -> str:
    """Kind | taxonomy | verdict | decision | rationale | evidence."""
    from abicheck.model.change_catalog.assembled import REGISTRY
    from abicheck.model.change_catalog.observability import UNCLASSIFIED

    owner = taxonomy_owner()
    ops = operator_evidence()
    tiers = designed_tiers()
    dets = detector_modules()
    rows = [
        "| Kind | Taxonomy | Verdict | Decision | Rationale | Operators (min_evidence) "
        "| Designed tier | Detectors |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for kind, entry in REGISTRY.entries.items():
        dec = DECISIONS.get(kind)
        if dec is None:
            decision, rationale = "UNCLASSIFIED", ""
        elif entry.observable_at is UNCLASSIFIED:
            # Decided, but the module would pass the line cap: not applied.
            decision = f"held (cap): {_shape(dec)}"
            rationale = dec.note or FORMS[dec.codes[0]][1]
        else:
            decision = _shape(dec)
            rationale = dec.note or FORMS[dec.codes[0]][1]
        mins = Counter(m for _, m in ops.get(kind, []))
        op_txt = ", ".join(f"{m}:{n}" for m, n in sorted(mins.items())) if mins else "-"
        det = ", ".join(sorted(dets.get(kind, set()))[:2]) or "-"
        rows.append(
            f"| `{kind}` | {owner.get(kind, '?')} | {entry.default_verdict.name} "
            f"| {decision} | {rationale} | {op_txt} | {tiers.get(kind, '-')} | {det} |"
        )
    return "\n".join(rows) + "\n"


def _entry_spans(lines: list[str]) -> list[tuple[str, int, int]]:
    """``(kind, start, end)`` for each ``_E(`` entry; ``end`` is its ``),`` line."""
    spans = []
    i = 0
    while i < len(lines):
        if lines[i] == "    _E(\n":
            kind = re.match(r'\s*"([a-z0-9_]+)",', lines[i + 1]).group(1)  # type: ignore[union-attr]
            j = i + 1
            while lines[j] != "    ),\n":
                j += 1
            spans.append((kind, i, j))
            i = j
        i += 1
    return spans


def _constants_block(codes: set[str]) -> list[str]:
    """Module-level ``observable`` constants for the forms used in one file."""
    block = []
    for code in sorted(codes, key=list(FORMS).index):
        alts, rationale, platforms, const = FORMS[code]
        parts = [
            "("
            + ", ".join(f'"{s}"' for s in alt)
            + ("," if len(alt) == 1 else "")
            + ")"
            for alt in alts
        ]
        if rationale:
            parts.append(f'rationale="{rationale}"')
        if platforms:
            plat = ", ".join(f"Platform.{p.upper()}" for p in platforms)
            parts.append(
                f"condition=ObservabilityCondition(platform=frozenset({{{plat}}}))"
            )
        block.append(f"{const} = observable({', '.join(parts)})")
    return block


def _ruff_format(path: Path, text: str) -> str:
    """Format *text* as ``ruff format`` would, keyed on *path*'s name."""
    proc = subprocess.run(
        ["ruff", "format", "--stdin-filename", str(path), "-"],
        input=text,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout


FAMILIES = ("symbols", "types", "platform", "source")


def _next_sibling(family: str, taken: dict[str, int]) -> int:
    """Next free numeric suffix in *family* (``symbols_3`` after ``symbols_2``)."""
    nums = [
        int(m.group(1))
        for path in CATALOG.glob(f"{family}*.py")
        if (m := re.fullmatch(rf"{family}(?:_(\d+))?\.py", path.name)) and m.group(1)
    ]
    n = max(nums, default=0) + 1
    n = max(n, taken.get(family, 0) + 1)
    taken[family] = n
    return n


def _header_for_sibling(
    owner_lines: list[str], head: int, family: str, stem: str, number: int
) -> list[str]:
    """Docstring, __future__, registry import and the ``_B``/``_E``-style aliases."""
    reg = next(
        i for i, ln in enumerate(owner_lines) if ln.startswith("from .registry import")
    )
    if owner_lines[reg].rstrip().endswith("("):
        reg_end = next(i for i in range(reg, head) if owner_lines[i] == ")\n")
        reg_block = owner_lines[reg : reg_end + 1]
    else:
        reg_block = [owner_lines[reg]]
    aliases = [ln for ln in owner_lines[:head] if re.match(r"_[A-Z]+ = ", ln)]
    doc = (
        f'"""ADR-093 WS-1: entries of ``{stem}.py`` split out at the 800-line cap.\n\n'
        f"Whole entries moved unchanged from ``{stem}.py`` (each gains only its\n"
        f"``observable_at``). Registered in ``{family}.py``.\n"
        '"""\n\n'
    )
    license_block = [ln for ln in owner_lines[:head] if ln.startswith("#")]
    return [
        *license_block,
        doc,
        "from __future__ import annotations\n\n",
        *reg_block,
        "\n",
        *aliases,
        "\n",
    ]


def _register_in_family(
    family: str, sibling_stem: str, list_name: str, stem_lines: list[str] | None
) -> list[str] | None:
    """Add the sibling to the family's assembly. Returns the new family text, or None.

    ``stem_lines`` is the owner's own text when the owner *is* the family module
    (``source.py`` holds ``SOURCE_ENTRIES``); otherwise the family module is read.
    """
    if family == "source":
        lines = stem_lines
        assert lines is not None
        imp = max(
            i
            for i, ln in enumerate(lines)
            if re.match(r"from \.source_\d+ import ", ln)
        )
        lines.insert(imp + 1, f"from .{sibling_stem} import {list_name}\n")
        ext = max(
            i for i, ln in enumerate(lines) if ln.startswith("SOURCE_ENTRIES.extend(")
        )
        lines.insert(ext + 1, f"SOURCE_ENTRIES.extend({list_name})\n")
        return lines
    path = CATALOG / f"{family}.py"
    lines = path.read_text().splitlines(keepends=True)
    fam = family.upper()
    imp = max(
        i for i, ln in enumerate(lines) if re.match(rf"from \.{family}_\d+ import ", ln)
    )
    lines.insert(imp + 1, f"from .{sibling_stem} import {list_name}\n")
    head = next(
        i
        for i, ln in enumerate(lines)
        if ln.startswith(f"{fam}_ENTRIES: list[ChangeKindMeta] = ")
    )
    if lines[head].rstrip().endswith("("):
        tail = next(i for i in range(head, len(lines)) if lines[i].strip() == ")")
    else:
        tail = head
    names = sorted(
        {
            m.group(2)
            for ln in lines
            for m in [
                re.match(rf"from \.({family}_\d+) import ({fam}_ENTRIES_\d+)", ln)
            ]
            if m
        },
        key=lambda n: int(n.rsplit("_", 1)[1]),
    )
    # The sibling's import was inserted above, so it is already in ``names``.
    lines[head : tail + 1] = [
        f"{fam}_ENTRIES: list[ChangeKindMeta] = {' + '.join(names)}\n"
    ]
    return lines


def _split_owner(
    path: Path,
    lines: list[str],
    decs: list[Decision],
    spans: dict[str, tuple[int, int]],
    taken: dict[str, int],
    dry_run: bool,
) -> tuple[str, int]:
    """Move an overflowing owner's decided entries, unchanged, into a new sibling."""
    family = next(
        f for f in FAMILIES if path.stem == f or path.stem.startswith(f + "_")
    )
    number = _next_sibling(family, taken)
    sibling_stem = f"{family}_{number}"
    list_name = f"{family.upper()}_ENTRIES_{number}"
    head = next(
        i
        for i, ln in enumerate(lines)
        if re.match(r"[A-Z_0-9]+: list\[ChangeKindMeta\] = \[", ln)
    )
    body: list[str] = []
    for dec in sorted(decs, key=lambda d: spans[d.kind][0]):
        s, e = spans[dec.kind]
        block = lines[s : e + 1]
        block.insert(
            len(block) - 1,
            f"        observable_at=({', '.join(FORMS[c][3] for c in dec.codes)},),\n",
        )
        body += block
    codes = {c for d in decs for c in d.codes}
    used = {"observable"}
    if any(FORMS[c][2] for c in codes):
        used |= {"ObservabilityCondition", "Platform"}
    if any(FORM_LANGUAGES.get(c) for c in codes):
        used |= {"Language", "ObservabilityCondition"}
    header = _header_for_sibling(lines, head, family, path.stem, number)
    obs_line = f"from .observability import {', '.join(sorted(used))}\n"
    # The observability import belongs with the other imports, before the aliases.
    alias_at = next(i for i, ln in enumerate(header) if re.match(r"_[A-Z]+ = ", ln))
    header = [*header[:alias_at], obs_line, *header[alias_at:]]
    new_text = "".join(
        header
        + [ln + "\n" for ln in _constants_block(codes)]
        + ["\n", f"{list_name}: list[ChangeKindMeta] = [\n"]
        + body
        + ["]\n"]
    )
    new_text = _ruff_format(CATALOG / f"{sibling_stem}.py", new_text)
    if new_text.count("\n") > PRODUCTION_LINE_CAP:
        raise SystemExit(f"{sibling_stem}.py would itself exceed the cap")
    # The owner keeps everything except the moved entries.
    moved = {spans[d.kind] for d in decs}
    kept = [ln for i, ln in enumerate(lines) if not any(s <= i <= e for s, e in moved)]
    if path.stem == family:
        # source.py holds SOURCE_ENTRIES itself: register the sibling in the owner.
        owner_text = _ruff_format(
            path, "".join(_register_in_family(family, sibling_stem, list_name, kept))
        )
    else:
        owner_text = _ruff_format(path, "".join(kept))
        fam_text = _ruff_format(
            CATALOG / f"{family}.py",
            "".join(_register_in_family(family, sibling_stem, list_name, None)),
        )
        if not dry_run:
            (CATALOG / f"{family}.py").write_text(fam_text)
    if not dry_run:
        (CATALOG / f"{sibling_stem}.py").write_text(new_text)
        path.write_text(owner_text)
    return sibling_stem, len(decs)


def apply_decisions(dry_run: bool) -> dict[str, int]:
    """Apply the decisions. Overflowing owners are split into numbered siblings."""
    owner = taxonomy_owner()
    applied = [p.name for p in taxonomy_files() if "observable_at=(" in p.read_text()]
    if applied:
        raise SystemExit(
            "taxonomy already carries observable_at lines in "
            f"{', '.join(sorted(applied))}: restore abicheck/model/change_catalog/ "
            "from the base commit before applying again"
        )
    by_file: dict[str, list[Decision]] = defaultdict(list)
    for dec in DECISIONS.values():
        if dec.kind not in owner:
            raise SystemExit(f"{dec.kind}: not a ChangeKind taxonomy entry")
        by_file[owner[dec.kind]].append(dec)

    taken: dict[str, int] = {}
    counts: dict[str, int] = {}
    for name, decs in sorted(by_file.items()):
        path = CATALOG / name
        original = path.read_text().splitlines(keepends=True)
        spans = {k: (s, e) for k, s, e in _entry_spans(original)}
        # Try the in-place form first.
        lines = list(original)
        for dec in sorted(decs, key=lambda d: -spans[d.kind][1]):
            _, end = spans[dec.kind]
            lines.insert(
                end,
                f"        observable_at=({', '.join(FORMS[c][3] for c in dec.codes)},),\n",
            )
        head = next(
            i
            for i, ln in enumerate(lines)
            if re.match(r"[A-Z_0-9]+: list\[ChangeKindMeta\] = \[", ln)
        )
        lines[head:head] = [
            ln + "\n" for ln in _constants_block({c for d in decs for c in d.codes})
        ] + ["\n"]
        imp = next(
            i for i, ln in enumerate(lines) if ln.startswith("from .registry import")
        )
        used = {"observable"}
        if any(FORMS[c][2] for d in decs for c in d.codes):
            used |= {"ObservabilityCondition", "Platform"}
        lines.insert(imp, f"from .observability import {', '.join(sorted(used))}\n")
        text = _ruff_format(path, "".join(lines))
        if text.count("\n") <= PRODUCTION_LINE_CAP:
            counts[name] = len(decs)
            if not dry_run:
                path.write_text(text)
            continue
        sibling, n = _split_owner(path, original, decs, spans, taken, dry_run)
        counts[name] = n
        counts[f"  -> {sibling}.py"] = n
    return counts


def pr_table() -> str:
    """Applied kinds as PR-body rows, plus the undecided names (same columns as the PR)."""
    from abicheck.model.change_catalog.assembled import REGISTRY
    from abicheck.model.change_catalog.observability import UNCLASSIFIED

    owner = taxonomy_owner()
    ops = operator_evidence()
    tiers = designed_tiers()
    dets = detector_modules()
    rows = []
    for kind, dec in DECISIONS.items():
        if REGISTRY.entries[kind].observable_at is UNCLASSIFIED:
            continue
        rationale = dec.note or FORMS[dec.codes[0]][1] or "exported-symbol fact"
        op_names = ", ".join(sorted({n for n, _ in ops.get(kind, [])})[:2]) or "-"
        det = ", ".join(sorted(dets.get(kind, set()))[:1]) or "-"
        shape = _shape(dec)
        rows.append(
            f"| `{kind}` | {owner[kind]} | {shape} | {rationale} | {op_names} "
            f"| {tiers.get(kind, '-')} | {det} |"
        )
    undecided = sorted(
        k for k, e in REGISTRY.entries.items() if e.observable_at is UNCLASSIFIED
    )
    return (
        "\n".join(rows)
        + "\n\nUNDECIDED:"
        + ", ".join(f"`{k}`" for k in undecided)
        + "\n"
    )


def write_baseline() -> int:
    from abicheck.model.change_catalog.assembled import REGISTRY
    from abicheck.model.change_catalog.observability import UNCLASSIFIED

    unclassified = sorted(
        k for k, e in REGISTRY.entries.items() if e.observable_at is UNCLASSIFIED
    )
    data = json.loads(BASELINE.read_text())
    data["unclassified"] = unclassified
    BASELINE.write_text(json.dumps(data, indent=2) + "\n")
    return len(unclassified)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--table", action="store_true", help="print the evidence table")
    group.add_argument("--pr-table", action="store_true", help="print the PR-body rows")
    group.add_argument("--apply", action="store_true", help="write observable_at lines")
    group.add_argument("--baseline", action="store_true", help="rewrite the U baseline")
    group.add_argument("--dry-run", action="store_true", help="count, write nothing")
    args = parser.parse_args(argv)
    if args.table:
        sys.stdout.write(markdown_table())
    elif args.pr_table:
        sys.stdout.write(pr_table())
    elif args.apply or args.dry_run:
        counts = apply_decisions(dry_run=args.dry_run)
        for name, n in counts.items():
            print(f"{name}: {n} observable_at lines" if n >= 0 else f"{name}: blocked")
        blocked = {n: -c for n, c in counts.items() if c < 0}
        applied = sum(
            c for k, c in counts.items() if c > 0 and not k.startswith("  ->")
        )
        for name, n in blocked.items():
            print(f"{name}: {n} decisions NOT applied (file would exceed the cap)")
        print(f"applied: {applied} (run ruff format afterwards)")
    else:
        print(f"UNCLASSIFIED after baseline: {write_baseline()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
