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
and their demangled spellings, count them and attribute the ones whose
signature names a recognizable conditionally-compiled API to a cause. It
creates no finding and decides nothing -- the caller renders the result as
a coverage line (``confidence.undeclared_export_warnings``).
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

#: Substring every census coverage line carries, so a consumer can filter for it.
UNDECLARED_EXPORT_WARNING_MARKER = (
    "exported symbols not declared under the parsed header configuration"
)


@dataclass(frozen=True)
class UndeclaredExportCensus:
    """How many exports had no header declaration, and why, for one side."""

    total: int = 0
    by_cause: Mapping[str, int] = field(default_factory=dict)

    @property
    def attributed(self) -> int:
        return sum(self.by_cause.values())


def census_undeclared_exports(
    undeclared: Iterable[str], demangled: Mapping[str, str]
) -> UndeclaredExportCensus:
    """Count *undeclared* export names and attribute each to at most one cause.

    *demangled* maps a mangled name to its demangled spelling; a name absent
    from it (a C symbol) is matched on its raw spelling.
    """
    names = set(undeclared)
    by_cause: dict[str, int] = {}
    for name in names:
        text = demangled.get(name, name)
        for cause, needle, _macro in CONDITIONAL_DECLARATION_CAUSES:
            # A whole namespace component: ``mysycl::`` is not ``sycl::``.
            if re.search(rf"(?<![\w]){re.escape(needle)}", text):
                by_cause[cause] = by_cause.get(cause, 0) + 1
                break
    return UndeclaredExportCensus(total=len(names), by_cause=by_cause)


def census_warning(side: str, census: UndeclaredExportCensus) -> str | None:
    """The coverage line for one side, or ``None`` when no cause was recognized.

    Only an *attributed* census is worth a line: every C++ library carries
    some undeclared exports (internal helpers, instantiations), and those are
    already accounted for by ``exported_not_public`` / ``*_elf_only``. A
    recognized conditional cause is the blind spot the header parse itself
    introduced.
    """
    if not census.attributed:
        return None
    parts = []
    for cause, needle, macro in CONDITIONAL_DECLARATION_CAUSES:
        count = census.by_cause.get(cause, 0)
        if count:
            parts.append(
                f"{count} reference {needle} (likely declared under "
                f"#ifdef {macro}, undefined in this parse; pass -D {macro}=... "
                f"to include them)"
            )
    return (
        f"{side} side headers: {census.total} {UNDECLARED_EXPORT_WARNING_MARKER} "
        f"({'; '.join(parts)}); their API changes are not header-checked"
    )
