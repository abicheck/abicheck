#!/usr/bin/env python3
"""Measure how the Phase 4 clang projection differs from the current L4 emitter.

Plan ``l4-l2-extraction-convergence`` Phase 4 would project L2's
``dumper_clang._ClangAstParser`` output through ``source_extractors/base.py``'s
``entity_from_*`` instead of L4's own clang emitter. ADR-063's amendment "L4
clang payload comparability" (A3/A5) admits a ``(kind, field)`` pair into
``REVISION_COMPARABLE_FIELDS`` only when the two payloads agree on every catalog
TU. This script produces that evidence: it dumps each catalog TU once, builds
both entity sets from the same AST, and compares them per ``(kind, field)``.

Nothing here changes L4 output. The production emitter is only *read*; the
projection is built in this script, not in ``abicheck/``.

Pure comparison logic (``EntityView``, ``pair_entities``, ``compare_views``,
``aggregate``) takes plain views and needs no clang, so it is unit-tested
without the tool. ``measure_corpus`` is the only part that runs clang.

Usage::

    python scripts/measure_l4_clang_projection.py --json OUT.json
    python scripts/measure_l4_clang_projection.py --limit 5 --json /tmp/x.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent

#: Candidate ``(kind, field)`` pairs, ADR-063 amendment A3's candidate set plus
#: the value and hash fields the task asks for. ``mangled_name`` is listed for
#: every kind the amendment names; records, enums and typedefs leave it empty on
#: both sides, so those rows compare empty strings and say so.
CANDIDATE_FIELDS: dict[str, tuple[str, ...]] = {
    "function": (
        "qualified_name",
        "mangled_name",
        "visibility",
        "api_relevant",
        "value",
        "signature_hash",
    ),
    "variable": (
        "qualified_name",
        "mangled_name",
        "visibility",
        "api_relevant",
        "value",
        "type_hash",
    ),
    "record": (
        "qualified_name",
        "mangled_name",
        "visibility",
        "api_relevant",
        "type_hash",
    ),
    "enum": (
        "qualified_name",
        "mangled_name",
        "visibility",
        "api_relevant",
        "type_hash",
    ),
    "typedef": (
        "qualified_name",
        "mangled_name",
        "visibility",
        "api_relevant",
        "value",
        "type_hash",
    ),
}

#: Counters that are not single fields: the explicit mangled-name rows.
#: Pairing-free counters for function ``mangled_name``. Pair-level comparison is
#: unreliable for overloads (a k-th-with-k-th pairing can cross two overloads of
#: one name), so these compare each bare name's multiset of mangled names and
#: count blanked names directly.
MANGLED_COUNTERS: tuple[str, ...] = (
    "group_multiset_equal",
    "group_multiset_differ",
    "current_function_entities",
    "current_blank_mangled",
    "projected_in_scope_function_entities",
    "projected_blank_mangled",
)


def bare_name(qualified: str) -> str:
    """The last ``::`` component of *qualified*, ignoring ``::`` inside ``<>``/``()``.

    Used only to pair entities across the two sides; it is not a comparison
    field. Operator spellings containing ``<`` or ``(`` are not split correctly,
    so a member named ``operator<`` pairs by its full spelling. That limit is
    recorded in ``known-gaps``.
    """
    depth = 0
    last = 0
    i = 0
    while i < len(qualified):
        ch = qualified[i]
        if ch in "<(":
            depth += 1
        elif ch in ">)":
            depth = max(depth - 1, 0)
        elif ch == ":" and depth == 0 and qualified[i : i + 2] == "::":
            last = i + 2
            i += 2
            continue
        i += 1
    return qualified[last:]


@dataclass(frozen=True)
class EntityView:
    """One entity reduced to the fields this measurement compares."""

    kind: str
    qualified_name: str
    fields: dict[str, str]

    @property
    def bare(self) -> str:
        return bare_name(self.qualified_name)


@dataclass
class FieldStat:
    compared: int = 0
    #: Pairs where both sides are the empty string: equal, but carrying no
    #: information about the producer (e.g. ``mangled_name`` on a record).
    both_empty: int = 0
    differing: list[dict[str, str]] = field(default_factory=list)


@dataclass
class KindPresence:
    left: int = 0
    right: int = 0
    #: Projected entities whose ``(kind, bare name)`` the current side lacks.
    #: The projection walks every declaration in the AST, system headers
    #: included; these are excluded from pairing and counted here, not hidden.
    right_out_of_scope: int = 0
    paired: int = 0
    left_only: list[str] = field(default_factory=list)
    right_only: list[str] = field(default_factory=list)


@dataclass
class TuComparison:
    tu: str
    presence: dict[str, KindPresence] = field(default_factory=dict)
    fields: dict[tuple[str, str], FieldStat] = field(default_factory=dict)
    mangled: dict[str, int] = field(default_factory=dict)


def pair_entities(
    left: Sequence[EntityView], right: Sequence[EntityView]
) -> tuple[list[tuple[EntityView, EntityView]], list[EntityView], list[EntityView]]:
    """Pair entities of one kind by ``(kind, bare name)``, in producer order.

    Same-named entities (overloads) pair k-th with k-th in the order each side
    produced them. Entities left over on one side are returned unpaired.
    """
    lgroups: dict[tuple[str, str], list[EntityView]] = defaultdict(list)
    rgroups: dict[tuple[str, str], list[EntityView]] = defaultdict(list)
    for e in left:
        lgroups[(e.kind, e.bare)].append(e)
    for e in right:
        rgroups[(e.kind, e.bare)].append(e)
    pairs: list[tuple[EntityView, EntityView]] = []
    left_only: list[EntityView] = []
    right_only: list[EntityView] = []
    for key in sorted(set(lgroups) | set(rgroups)):
        ls = lgroups.get(key, [])
        rs = rgroups.get(key, [])
        n = min(len(ls), len(rs))
        pairs.extend(zip(ls[:n], rs[:n]))
        left_only.extend(ls[n:])
        right_only.extend(rs[n:])
    return pairs, left_only, right_only


def compare_views(
    tu: str, left: Sequence[EntityView], right: Sequence[EntityView]
) -> TuComparison:
    """Compare one TU's current (*left*) and projected (*right*) entity views."""
    out = TuComparison(tu=tu)
    # Scope the projected side to the (kind, bare name) keys the current side
    # declares. Without this, a same-named std/system overload pairs k-th-with-k-th
    # against a surface declaration and reads as a mangled-name difference.
    current_keys = {(e.kind, e.bare) for e in left}
    in_scope = [e for e in right if (e.kind, e.bare) in current_keys]
    pairs, left_only, right_only = pair_entities(left, in_scope)
    for kind in CANDIDATE_FIELDS:
        out.presence[kind] = KindPresence(
            left=sum(1 for e in left if e.kind == kind),
            right=sum(1 for e in right if e.kind == kind),
            right_out_of_scope=sum(
                1
                for e in right
                if e.kind == kind and (e.kind, e.bare) not in current_keys
            ),
            paired=sum(1 for lv, _ in pairs if lv.kind == kind),
            left_only=[e.qualified_name for e in left_only if e.kind == kind],
            right_only=[e.qualified_name for e in right_only if e.kind == kind],
        )
    for name in MANGLED_COUNTERS:
        out.mangled[name] = 0
    for lv, rv in pairs:
        for fname in CANDIDATE_FIELDS.get(lv.kind, ()):
            stat = out.fields.setdefault((lv.kind, fname), FieldStat())
            stat.compared += 1
            lval, rval = lv.fields.get(fname, ""), rv.fields.get(fname, "")
            if lval == "" and rval == "":
                stat.both_empty += 1
            if lval != rval:
                stat.differing.append(
                    {"name": lv.qualified_name, "current": lval, "projected": rval}
                )
    _count_mangled_groups(out.mangled, left, in_scope)
    return out


def _count_mangled_groups(
    counters: dict[str, int], left: Sequence[EntityView], right: Sequence[EntityView]
) -> None:
    """Pairing-free function-name counters over the in-scope entities."""
    lfuncs = [e for e in left if e.kind == "function"]
    rfuncs = [e for e in right if e.kind == "function"]
    counters["current_function_entities"] = len(lfuncs)
    counters["current_blank_mangled"] = sum(
        1 for e in lfuncs if not e.fields["mangled_name"]
    )
    counters["projected_in_scope_function_entities"] = len(rfuncs)
    counters["projected_blank_mangled"] = sum(
        1 for e in rfuncs if not e.fields["mangled_name"]
    )
    lgroups: dict[str, list[str]] = defaultdict(list)
    rgroups: dict[str, list[str]] = defaultdict(list)
    for e in lfuncs:
        lgroups[e.bare].append(e.fields["mangled_name"])
    for e in rfuncs:
        rgroups[e.bare].append(e.fields["mangled_name"])
    for key in set(lgroups) | set(rgroups):
        if sorted(lgroups.get(key, [])) == sorted(rgroups.get(key, [])):
            counters["group_multiset_equal"] += 1
        else:
            counters["group_multiset_differ"] += 1


@dataclass
class FieldRow:
    kind: str
    field: str
    compared: int
    differing: int
    both_empty: int
    tus_differing: list[str]
    status: str


def aggregate(comparisons: Sequence[TuComparison]) -> list[FieldRow]:
    """Corpus-level status per ``(kind, field)``.

    ``equal`` means compared on at least one pair, never different, and not
    empty on every pair. ``equal_empty`` means never different only because
    both sides are the empty string on every pair: no evidence about the
    producer. A field compared on no pair in the whole corpus is ``vacuous``,
    never equal. Any difference on any TU is ``differs``. Per A3 a ``differs``
    field is dropped for the whole revision pair.
    """
    rows: list[FieldRow] = []
    for kind, fields in CANDIDATE_FIELDS.items():
        for fname in fields:
            compared = 0
            differing = 0
            both_empty = 0
            tus: list[str] = []
            for comp in comparisons:
                stat = comp.fields.get((kind, fname))
                if stat is None:
                    continue
                compared += stat.compared
                both_empty += stat.both_empty
                if stat.differing:
                    differing += len(stat.differing)
                    tus.append(comp.tu)
            if compared == 0:
                status = "vacuous"
            elif differing:
                status = "differs"
            elif both_empty == compared:
                status = "equal_empty"
            else:
                status = "equal"
            rows.append(
                FieldRow(kind, fname, compared, differing, both_empty, tus, status)
            )
    return rows


def aggregate_presence(
    comparisons: Sequence[TuComparison],
) -> dict[str, dict[str, Any]]:
    """Corpus totals per kind for existence (not a field): counts and TUs that disagree."""
    totals: dict[str, dict[str, Any]] = {}
    for kind in CANDIDATE_FIELDS:
        left = right = out_of_scope = paired = 0
        tus: list[str] = []
        for comp in comparisons:
            p = comp.presence[kind]
            left += p.left
            right += p.right
            out_of_scope += p.right_out_of_scope
            paired += p.paired
            if p.left_only or p.right_only:
                tus.append(comp.tu)
        totals[kind] = {
            "current": left,
            "projected": right,
            "projected_out_of_scope": out_of_scope,
            "paired": paired,
            "tus_with_unpaired": tus,
        }
    return totals


def aggregate_mangled(comparisons: Sequence[TuComparison]) -> dict[str, int]:
    """Corpus totals for the explicit mangled-name counters (functions only)."""
    totals = {name: 0 for name in MANGLED_COUNTERS}
    for comp in comparisons:
        for name, n in comp.mangled.items():
            totals[name] += n
    return totals


# ---------------------------------------------------------------------------
# Runner: the only part that needs clang and abicheck's extractor.
# ---------------------------------------------------------------------------


def view_of(entity: Any) -> EntityView:
    """Reduce a ``SourceEntity`` to the candidate fields for its kind."""
    fields = {
        "qualified_name": entity.qualified_name,
        "mangled_name": entity.mangled_name,
        "visibility": entity.visibility,
        "api_relevant": "True" if entity.api_relevant else "False",
        "value": entity.value,
        "signature_hash": entity.signature_hash,
        "type_hash": entity.type_hash,
    }
    return EntityView(
        kind=entity.kind,
        qualified_name=entity.qualified_name,
        fields={k: fields[k] for k in CANDIDATE_FIELDS[entity.kind]},
    )


def corpus_cases(cases_root: Path) -> list[Path]:
    """The catalog's translation units: ``catalog/cases/*/*.cpp``, sorted."""
    return sorted(cases_root.glob("*/*.cpp"))


def measure_tu(cpp: Path, extractor: Any) -> TuComparison:
    """Dump *cpp* once and compare the current and projected entity sets.

    Imports abicheck lazily so the pure half above stays importable on its own.
    """
    from abicheck.buildsource.build_evidence import CompileUnit
    from abicheck.buildsource.source_extractors import base
    from abicheck.dumper_clang import _ClangAstParser

    case_dir = cpp.parent
    roots = [str(case_dir)]
    cu = CompileUnit(
        id=f"cu://{case_dir.name}/{cpp.name}",
        source=str(cpp),
        directory=str(case_dir),
        language="CXX",
        standard="c++17",
        argv=["c++", "-std=c++17", "-fPIC", "-fvisibility=hidden", "-c", str(cpp)],
    )
    dump = extractor.dump_for(cu)
    try:
        if dump.ast is None:
            raise RuntimeError(
                f"clang AST dump failed: stage={dump.stage} {dump.error[:200]}"
            )
        current = extractor.source_abi_from_dump(
            cu, dump, public_header_roots=roots, target_id="target://spike"
        )
        parser = _ClangAstParser(
            dump.ast,
            set(),
            set(),
            public_header_paths=[],
            public_dir_paths=roots,
            target_triple=None,
            no_binary_evidence=True,
            is_cxx=True,
        )
        projected: list[Any] = [
            base.entity_from_function(f) for f in parser.parse_functions()
        ]
        projected += [base.entity_from_variable(v) for v in parser.parse_variables()]
        projected += [base.entity_from_record(r) for r in parser.parse_types()]
        projected += [base.entity_from_enum(e) for e in parser.parse_enums()]
        projected += [
            base.entity_from_typedef(name, target)
            for name, target in parser.parse_typedefs_qualified().items()
        ]
    finally:
        dump.release()
    current_entities = list(current.all_entities())
    left = [view_of(e) for e in current_entities if e.kind in CANDIDATE_FIELDS]
    right = [view_of(e) for e in projected if e.kind in CANDIDATE_FIELDS]
    return compare_views(f"{case_dir.name}/{cpp.name}", left, right)


def measure_corpus(
    cases: Iterable[Path], *, progress: bool
) -> tuple[list[TuComparison], list[dict[str, str]]]:
    from abicheck.buildsource.source_extractors.clang import ClangSourceExtractor

    extractor = ClangSourceExtractor()
    if not extractor.available():
        raise SystemExit("clang is not on PATH; the measurement needs clang")
    comparisons: list[TuComparison] = []
    failures: list[dict[str, str]] = []
    for i, cpp in enumerate(cases, 1):
        try:
            comparisons.append(measure_tu(cpp, extractor))
        except Exception as exc:  # recorded, never hidden: a failed TU is reported
            failures.append(
                {
                    "tu": f"{cpp.parent.name}/{cpp.name}",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        if progress and i % 25 == 0:
            print(f"  {i} TUs measured", file=sys.stderr, flush=True)
    return comparisons, failures


def _report(
    rows: list[FieldRow],
    presence: dict[str, dict[str, Any]],
    mangled: dict[str, int],
    n_tus: int,
    failures: list[dict[str, str]],
) -> str:
    lines = [f"TUs measured: {n_tus}  failed: {len(failures)}", ""]
    lines.append(
        "| kind | field | compared | both empty | differing pairs | status | TUs differing |"
    )
    lines.append("|---|---|---:|---:|---:|---|---:|")
    for r in rows:
        lines.append(
            f"| {r.kind} | {r.field} | {r.compared} | {r.both_empty} | {r.differing} | {r.status} | {len(r.tus_differing)} |"
        )
    lines += [
        "",
        "| kind | current | projected | projected out of scope | paired | TUs with unpaired |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for kind, p in presence.items():
        lines.append(
            f"| {kind} | {p['current']} | {p['projected']} | {p['projected_out_of_scope']} | {p['paired']} | {len(p['tus_with_unpaired'])} |"
        )
    lines += ["", "Mangled-name counters (function entities, pairing-free):"]
    lines += [f"  {k}: {v}" for k, v in mangled.items()]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--cases", type=Path, default=REPO / "catalog" / "cases")
    parser.add_argument(
        "--limit", type=int, default=0, help="measure only the first N TUs (0 = all)"
    )
    parser.add_argument(
        "--json", type=Path, help="write the full per-TU and aggregate result here"
    )
    args = parser.parse_args(argv)

    cases = corpus_cases(args.cases)
    if args.limit:
        cases = cases[: args.limit]
    started = time.monotonic()
    comparisons, failures = measure_corpus(cases, progress=True)
    rows = aggregate(comparisons)
    presence = aggregate_presence(comparisons)
    mangled = aggregate_mangled(comparisons)
    print(_report(rows, presence, mangled, len(comparisons), failures))
    print(f"elapsed {time.monotonic() - started:.1f}s")
    if args.json:
        payload = {
            "corpus_tu_count": len(cases),
            "measured": len(comparisons),
            "failures": failures,
            "fields": [r.__dict__ for r in rows],
            "presence": presence,
            "mangled": mangled,
            "per_tu": [
                {
                    "tu": c.tu,
                    "presence": {k: v.__dict__ for k, v in c.presence.items()},
                    "mangled": c.mangled,
                    "differing": {
                        f"{kind}.{fname}": stat.differing
                        for (kind, fname), stat in c.fields.items()
                        if stat.differing
                    },
                }
                for c in comparisons
            ],
        }
        args.json.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
