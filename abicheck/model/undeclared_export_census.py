# Copyright 2026 Nikolay Petrov
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

"""Census of exports the parsed headers did not declare (field report H5).

A header parse sees one preprocessor configuration. A declaration under
``#ifdef SYCL_LANGUAGE_VERSION`` (oneDAL's ``sycl::queue&`` overloads) is
removed by the preprocessor when castxml parses without that macro, and the
export it declares then has no header declaration at all. Nothing used to
count that: the only SYCL line in the coverage block was the *runtime* (PI
plugin) detector's, which is unrelated to header API coverage.

This module is the pure half: given the undeclared export names of one side
and their demangled spellings, count them and attribute each to

1. a **guard macro**, when this run's parse of the side scanned which
   public-header declarations its preprocessor configuration excluded
   (``AbiSnapshot.live_preprocessor_hidden``, decided by the compiler's own
   ``-E`` run in ``extract/headers/preprocessor_branches.py``, persisted since
   schema v67) and the export's function name is one of those declared under
   the macro; else
2. a **named cause** (:data:`CONDITIONAL_DECLARATION_CAUSES`), whose
   signature names a recognizable conditionally-compiled API. This is the
   fallback for a side with no scan: a pre-v67 stored snapshot, or a
   non-castxml parse.

It creates no finding and decides nothing -- the caller renders the result
as coverage lines (``confidence.undeclared_export_warnings``) and a
structured report block (:func:`census_to_dict`).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

#: A recognizable cause of an undeclared export: ``(cause id, demangled
#: substring, macro that usually guards the declaration)``. Matching is on the
#: demangled signature, so a parameter type is enough to attribute it.
CONDITIONAL_DECLARATION_CAUSES: tuple[tuple[str, str, str], ...] = (
    ("sycl", "sycl::", "SYCL_LANGUAGE_VERSION"),
)

#: Substring the named-cause census line carries, so a consumer can filter for it.
UNDECLARED_EXPORT_WARNING_MARKER = (
    "exported symbols not declared under the parsed header configuration"
)
#: Substring every per-guard-macro census line carries (followed by the macro).
GUARD_WARNING_MARKER = "exported symbols declared only under #ifdef"


@dataclass(frozen=True)
class UndeclaredExportCensus:
    """How many exports had no header declaration, and why, for one side."""

    total: int = 0
    by_cause: Mapping[str, int] = field(default_factory=dict)
    #: Exports per guard macro (an export under ``A && B`` counts for both).
    by_guard: Mapping[str, int] = field(default_factory=dict)
    #: Up to :data:`EXAMPLES_PER_GUARD` demangled examples per guard macro.
    guard_examples: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    #: Exports attributed to at least one guard macro.
    guard_attributed: int = 0

    @property
    def attributed(self) -> int:
        return self.guard_attributed + sum(self.by_cause.values())


#: How many example symbols the structured report keeps per guard macro.
EXAMPLES_PER_GUARD = 3


def function_base_name(text: str) -> str:
    """The unqualified function name of a demangled signature (or a C name).

    ``ns::foo<int>(sycl::queue&) const`` -> ``foo``. The parameter list is cut
    at the first ``(`` outside template arguments, template arguments are
    dropped, and the last ``::`` component is kept.
    """
    depth = 0
    head = text
    for i, ch in enumerate(text):
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth = max(0, depth - 1)
        elif ch == "(" and depth == 0:
            head = text[:i]
            break
    out: list[str] = []
    depth = 0
    for ch in head:
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth = max(0, depth - 1)
        elif depth == 0:
            out.append(ch)
    stripped = "".join(out).strip()
    return stripped.rsplit("::", 1)[-1].split()[-1] if stripped else ""


def census_undeclared_exports(
    undeclared: Iterable[str],
    demangled: Mapping[str, str],
    hidden: Mapping[str, Iterable[str]] | None = None,
) -> UndeclaredExportCensus:
    """Count *undeclared* export names and attribute each one.

    *demangled* maps a mangled name to its demangled spelling; a name absent
    from it (a C symbol) is matched on its raw spelling. *hidden* is the
    side's ``{guard macro: function names}`` scan; an export whose function
    name it lists is attributed to those guards and to no named cause.
    """
    names = set(undeclared)
    by_name: dict[str, list[str]] = {}
    for macro, declared in (hidden or {}).items():
        for fn in declared:
            by_name.setdefault(fn, []).append(macro)
    by_cause: dict[str, int] = {}
    by_guard: dict[str, int] = {}
    examples: dict[str, list[str]] = {}
    guard_attributed = 0
    for name in sorted(names):
        text = demangled.get(name, name)
        guards = by_name.get(function_base_name(text))
        if guards:
            guard_attributed += 1
            for macro in guards:
                by_guard[macro] = by_guard.get(macro, 0) + 1
                shown = examples.setdefault(macro, [])
                if len(shown) < EXAMPLES_PER_GUARD:
                    shown.append(text)
            continue
        for cause, needle, _macro in CONDITIONAL_DECLARATION_CAUSES:
            # A whole namespace component: ``mysycl::`` is not ``sycl::``.
            if re.search(rf"(?<![\w]){re.escape(needle)}", text):
                by_cause[cause] = by_cause.get(cause, 0) + 1
                break
    return UndeclaredExportCensus(
        total=len(names),
        by_cause=by_cause,
        by_guard=dict(sorted(by_guard.items())),
        guard_examples={m: tuple(e) for m, e in sorted(examples.items())},
        guard_attributed=guard_attributed,
    )


def census_to_dict(census: UndeclaredExportCensus) -> dict[str, object]:
    """The structured report form of one side's census."""
    return {
        "undeclared_total": census.total,
        "attributed": census.attributed,
        "by_guard": [
            {
                "macro": macro,
                "count": count,
                "examples": list(census.guard_examples.get(macro, ())),
            }
            for macro, count in census.by_guard.items()
        ],
        "by_cause": [
            {"cause": cause, "count": count}
            for cause, count in sorted(census.by_cause.items())
        ],
    }


def census_warnings(side: str, census: UndeclaredExportCensus) -> list[str]:
    """The coverage lines for one side: one per guard macro, then one for the
    named causes; empty when nothing was attributed.

    Only an *attributed* census is worth a line: every C++ library carries
    some undeclared exports (internal helpers, instantiations), and those are
    already accounted for by ``exported_not_public`` / ``*_elf_only``. A
    declaration the parse's own configuration excluded is the blind spot the
    header parse itself introduced.
    """
    lines = [
        f"{side} side headers: {count} {GUARD_WARNING_MARKER} {macro} "
        f"(not defined; pass -D {macro}); their API changes are not header-checked"
        for macro, count in census.by_guard.items()
    ]
    parts = []
    for cause, needle, macro in CONDITIONAL_DECLARATION_CAUSES:
        count = census.by_cause.get(cause, 0)
        if count:
            parts.append(
                f"{count} reference {needle} (likely declared under "
                f"#ifdef {macro}, undefined in this parse; pass -D {macro}=... "
                f"to include them)"
            )
    if parts:
        lines.append(
            f"{side} side headers: {census.total} {UNDECLARED_EXPORT_WARNING_MARKER} "
            f"({'; '.join(parts)}); their API changes are not header-checked"
        )
    return lines
