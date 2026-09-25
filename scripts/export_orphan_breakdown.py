#!/usr/bin/env python3
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

"""Why a declaration joins no export, and an export no declaration.

Measurement tooling for Phase 4 of
``docs/contribute/plans/evidence-entity-model.md``. Reads one stored
snapshot, runs the I4 ``exports`` query (``compare.edge_query.
EdgeEvidence``) over every declaration and every observed export, and
assigns each orphan -- a subject the query does not answer ``present`` for
-- one cause, first match wins:

Declarations with no export:

1. ``unknown`` -- the query answers ``unknown`` (the export table was not
   read): nothing about the orphan can be concluded.
2. ``compiler-generated`` -- ``is_compiler_generated``, ``= delete`` or pure
   virtual: no out-of-line symbol exists by definition.
3. ``inline or header-only`` -- ``is_inline``.
4. ``template, not instantiated`` -- a template specialization or member of a
   class template (``<`` in the qualified name).
5. ``hidden visibility`` -- ``Visibility.HIDDEN`` or an ELF hidden/internal
   ``st_other``.
6. ``.symtab-only`` -- the linker name is in the binary's static ``.symtab``
   but not the dynamic table (``--lib`` needed).
7. ``versioned alias`` -- the table holds the name only with a version suffix.
8. ``internal namespace`` -- a ``detail``/``internal``/``impl`` scope segment.
9. ``structor variant`` -- a constructor/destructor whose recorded Itanium
   variant (``C1``/``D1``...) is absent while a sibling variant of the same
   structor is exported: a matching gap, not a missing export.
10. ``unexplained`` -- none of the above.

Exports with no declaration:

1. ``unknown`` -- the query answers ``unknown`` under the export's default
   scope (a toolchain export whose system headers the dump filtered out, or a
   snapshot without a header AST).
2. ``structor variant`` -- a ``C1``/``C2``/``D0``/``D1``/``D2`` variant of a
   structor some declaration names through another variant (before the
   artifact check, which also matches structors).
3. ``compiler-generated`` -- vtables/typeinfo/VTT/thunks/guard variables and
   other ABI artifacts (``model.cxx_artifact_symbols.
   is_cxx_class_artifact_symbol``, ``elf_symbol_filter.
   is_abi_relevant_elf_symbol``).
4. ``versioned alias`` -- exists only as a non-default version.
5. ``internal namespace`` -- as above, on the demangled name.
6. ``template instantiation`` -- a template instantiation the header AST
   records only as the template.
7. ``truly undeclared`` -- the query answers ``proven_absent``.

Usage::

    python scripts/export_orphan_breakdown.py SNAPSHOT.json [--lib libfoo.so]
        [--samples 8] [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from abicheck.compare.edge_query import EdgeEvidence  # noqa: E402
from abicheck.elf_symbol_filter import is_abi_relevant_elf_symbol  # noqa: E402
from abicheck.model import Visibility  # noqa: E402
from abicheck.model.cxx_artifact_symbols import (  # noqa: E402
    is_cxx_class_artifact_symbol,
)
from abicheck.model.edge_coverage import EdgeAnswer  # noqa: E402
from abicheck.model.graph_join import EDGE_KIND_EXPORTS  # noqa: E402
from abicheck.serialization import load_snapshot  # noqa: E402

_INTERNAL_SEGMENTS = frozenset({"internal", "detail", "details", "impl", "__detail"})
#: An Itanium structor: ``C1``..``C5``/``D0``..``D5`` just before the
#: parameter list (``E``-terminated nested name, then the ``C``/``D`` tag).
_STRUCTOR = re.compile(r"^(.*?)([CD])([0-5])(E.*|I.*)$")


def _demangle_all(names: list[str]) -> dict[str, str]:
    """One ``c++filt`` process for every name (one per name costs ~7 ms)."""
    tool = shutil.which("c++filt")
    if not tool or not names:
        return {n: n for n in names}
    out = subprocess.run(
        [tool], input="\n".join(names), capture_output=True, text=True, check=True
    ).stdout.splitlines()
    return dict(zip(names, out)) if len(out) == len(names) else {n: n for n in names}


def _symtab_names(lib: Path | None) -> set[str] | None:
    if lib is None:
        return None
    from elftools.elf.elffile import ELFFile

    with lib.open("rb") as fh:
        section = ELFFile(fh).get_section_by_name(".symtab")
        return {s.name for s in section.iter_symbols()} if section else set()


def _scope_segments(qualified: str) -> set[str]:
    return {seg.split("<", 1)[0].strip() for seg in qualified.split("::")}


def _structor_key(mangled: str) -> str | None:
    """*mangled* with its structor variant digit erased, or ``None``."""
    m = _STRUCTOR.match(mangled)
    return f"{m.group(1)}{m.group(2)}*{m.group(4)}" if m else None


def _declaration_cause(fn, answer, symtab, table, exported_structors) -> str:
    mangled = fn.mangled or fn.name
    elf_vis = getattr(fn.elf_visibility, "value", fn.elf_visibility)
    if answer is EdgeAnswer.UNKNOWN:
        return "unknown"
    if getattr(fn, "is_compiler_generated", None) or getattr(fn, "is_deleted", False):
        return "compiler-generated"
    if getattr(fn, "is_pure_virtual", False):
        return "compiler-generated"
    if getattr(fn, "is_inline", False):
        return "inline or header-only"
    if "<" in fn.name:
        return "template, not instantiated"
    if fn.visibility is Visibility.HIDDEN or str(elf_vis).lower() in (
        "hidden",
        "internal",
    ):
        return "hidden visibility"
    if symtab is not None and mangled in symtab:
        return ".symtab-only"
    if any(t.startswith(mangled + "@") for t in table):
        return "versioned alias"
    if _scope_segments(fn.name) & _INTERNAL_SEGMENTS:
        return "internal namespace"
    key = _structor_key(mangled)
    if key is not None and key in exported_structors:
        return "structor variant"
    return "unexplained"


def _export_cause(entry, answer, demangled, declared_structors) -> str:
    s = entry.spelling
    if answer is EdgeAnswer.UNKNOWN:
        return "unknown"
    key = _structor_key(s)
    if key is not None and key in declared_structors:
        return "structor variant"
    if is_cxx_class_artifact_symbol(s) or not is_abi_relevant_elf_symbol(s):
        return "compiler-generated"
    if not entry.default_version:
        return "versioned alias"
    if _scope_segments(demangled.split("(", 1)[0]) & _INTERNAL_SEGMENTS:
        return "internal namespace"
    if "<" in demangled.split("(", 1)[0]:
        return "template instantiation"
    return "truly undeclared"


def breakdown(snapshot: Path, lib: Path | None, samples: int) -> dict[str, object]:
    snap = load_snapshot(snapshot)
    t0 = time.perf_counter()
    evidence = EdgeEvidence(snap)
    join = evidence.exports
    t_join = time.perf_counter() - t0
    ids = join.identities
    symtab = _symtab_names(lib)
    table = {e.spelling for e in join.entries.values()}
    exported_structors = {k for s in table if (k := _structor_key(s))}
    declared_structors = {
        k
        for f in (*snap.functions, *snap.variables)
        if (k := _structor_key(f.mangled or ""))
    }

    t1 = time.perf_counter()
    decl_causes: Counter[str] = Counter()
    decl_samples: dict[str, list[str]] = {}
    seen: set[str] = set()
    for fn, ident in (
        *zip(snap.functions, ids.functions),
        *zip(snap.variables, ids.variables),
    ):
        node = ident.node_id
        if node in seen:
            continue
        seen.add(node)
        answer = evidence.query(EDGE_KIND_EXPORTS, node).answer
        if answer is EdgeAnswer.PRESENT:
            continue
        cause = _declaration_cause(fn, answer, symtab, table, exported_structors)
        decl_causes[cause] += 1
        decl_samples.setdefault(cause, []).append(f"{fn.name} [{fn.mangled}]")

    orphans = [
        (node, entry)
        for node, entry in join.entries.items()
        if evidence.query(EDGE_KIND_EXPORTS, node).answer is not EdgeAnswer.PRESENT
    ]
    demangled = _demangle_all([e.spelling for _, e in orphans])
    export_causes: Counter[str] = Counter()
    export_samples: dict[str, list[str]] = {}
    for node, entry in orphans:
        answer = evidence.query(EDGE_KIND_EXPORTS, node).answer
        name = demangled.get(entry.spelling, entry.spelling)
        cause = _export_cause(entry, answer, name, declared_structors)
        export_causes[cause] += 1
        export_samples.setdefault(cause, []).append(f"{name} [{entry.spelling}]")
    t_query = time.perf_counter() - t1

    return {
        "snapshot": str(snapshot),
        "declarations": len(seen),
        "exports": len(join.entries),
        "declaration_orphans": dict(decl_causes.most_common()),
        "export_orphans": dict(export_causes.most_common()),
        "samples": {
            "declarations": {k: v[:samples] for k, v in sorted(decl_samples.items())},
            "exports": {k: v[:samples] for k, v in sorted(export_samples.items())},
        },
        "seconds": {"join": round(t_join, 3), "queries": round(t_query, 3)},
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    ap.add_argument("snapshot", type=Path)
    ap.add_argument("--lib", type=Path, help="the binary, for the .symtab check")
    ap.add_argument("--samples", type=int, default=8)
    ap.add_argument("--json", type=Path, help="write the full result here")
    args = ap.parse_args(argv)
    result = breakdown(args.snapshot, args.lib, args.samples)
    if args.json:
        args.json.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    for key in ("declaration_orphans", "export_orphans"):
        print(f"{key}: {sum(result[key].values())}")  # type: ignore[union-attr]
        for cause, n in result[key].items():  # type: ignore[union-attr]
            print(f"  {cause:32} {n:>7}")
    print(f"seconds: {result['seconds']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
