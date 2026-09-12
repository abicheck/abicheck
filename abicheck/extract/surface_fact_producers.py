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

"""Each extractor's answer to the three surface facts — and only to the
ones it actually observed.

``model/surface_facts.py`` owns what the three facts *mean*; this module
owns what each producer is entitled to assert about them. The rule every
helper here follows is the one the old :class:`Visibility` enum could not:
**a producer states a fact only for the evidence it holds, and leaves the
rest unknown**. A header-AST backend saw headers, so it may answer (a); it
saw an export table only if the dump had a binary, so (c) is unknown for a
header-only dump. A DWARF/export-table extractor never looked at a header
at all, so (a) stays unknown there — never ``False``, which would turn
"we have no header evidence" into "this entity is not declared".

Public-contract membership (b) is mostly *not* answered here: it depends
on the run's scope selection, which these extractors do not see. The one
positive assertion a header backend may make without a declared public
set is "the binary exports it", and ``provenance.tag_provenance`` later
adds the stronger, scope-aware ``PUBLIC_HEADER`` evidence when a public
header set was actually supplied.
"""

from __future__ import annotations

from typing import Any

from ..model import Fact

__all__ = [
    "debug_info_surface_facts",
    "export_table_surface_facts",
    "header_ast_surface_facts",
]


def header_ast_surface_facts(
    *,
    exported: bool | None,
    judged_public: bool = False,
    producer: str | None = None,
) -> dict[str, Any]:
    """Facts for a declaration a header-AST backend parsed out of a header.

    *exported*: ``True``/``False`` when a real export table was consulted,
    ``None`` for a header-only dump with no binary at all — in which case
    (c) is unknown rather than ``False``, since there is no artifact that
    could have exported anything.

    *judged_public*: the backend resolved this declaration's visibility to
    ``PUBLIC`` on evidence **other than** the export lookup. Two real
    fallbacks do that, and both are contract judgements rather than
    observations of the binary: a customisation point object that was never
    ODR-used has no emitted symbol at all
    (``dumper_castxml._variable_visibility``), and a constructor/destructor
    with no contrary attribute is "declared public without contrary
    evidence" (``_ctor_or_dtor_visibility``). Recording only the export
    lookup for those left (b) unknown *and* (c) confirmed false, which made
    ``in_public_surface`` answer ``False`` and silently dropped the CPO from
    ``detect_cpo_kind_changed`` (CodeRabbit review). A pre-v46 snapshot did
    not have that problem, because the bridge reads (b) straight off the
    ``PUBLIC`` the fallback stored — so this is what keeps a fresh dump
    agreeing with a stored one.

    Deliberately ``PARTIAL``: it is a judgement the producer derived from
    its own attribute evidence, not a public-header set it was handed.
    """
    return {
        "declared_in_headers_fact": Fact.present(True, producer=producer),
        # A confirmed export is positive evidence of contract membership
        # even with no public-header set declared. Its absence is *not*
        # the negative: an unexported inline function declared in a public
        # header is an ordinary member of the promised surface, so this
        # stays unknown until `tag_provenance` can answer from a real
        # scope selection.
        "in_public_contract_fact": (
            Fact.present(True, producer=producer)
            if exported
            else Fact.partial(
                True,
                "backend resolved visibility to public without export evidence",
                producer=producer,
            )
            if judged_public
            else Fact.not_collected(
                "no public-header set declared and no export evidence",
                producer=producer,
            )
        ),
        "binary_exported_fact": (
            Fact.not_collected("no binary in this dump", producer=producer)
            if exported is None
            else Fact.present(exported, producer=producer)
        ),
    }


def debug_info_surface_facts(
    *, exported: bool, producer: str | None = "dwarf"
) -> dict[str, Any]:
    """Facts for a declaration recovered from debug info (DWARF/BTF/CTF).

    Debug info records where a definition was compiled, which is not
    evidence about the *available headers* a consumer compiles against —
    so (a) stays unknown. (c) is real: the extractor checked the dynamic
    export set to admit the entity in the first place.

    *exported* must be that **real lookup's** answer, not a re-derivation
    from the record's legacy ``visibility``. They are not the same for one
    case that matters: a ``DW_AT_deleted`` subprogram deliberately bypasses
    the admission check and keeps a synthetic ``Visibility.PUBLIC`` so the
    deleted declaration stays available for cross-reference — while having,
    by construction, no symbol in the binary at all. Reading the enum there
    would record an export that does not exist, and every consumer of this
    fact (``binary_exported``, bundle signature evidence, the report's
    ``surface_facts``) would trust it (Codex review, P2).
    """
    return {
        "declared_in_headers_fact": Fact.not_collected(
            "debug info carries no public-header evidence", producer=producer
        ),
        "in_public_contract_fact": (
            Fact.present(True, producer=producer)
            if exported
            else Fact.not_collected(
                "no public-header set declared and no export evidence",
                producer=producer,
            )
        ),
        "binary_exported_fact": Fact.present(exported, producer=producer),
    }


def export_table_surface_facts(
    *, headers_parsed: bool = False, producer: str | None = None
) -> dict[str, Any]:
    """Facts for an entry synthesized from an export table alone.

    *headers_parsed* says whether this run parsed headers at all. Only
    then is "absent from the headers" an observation; without it, (a) is
    unknown — the whole point of the split, since a headerless dump must
    not start claiming every symbol is undeclared.
    """
    return {
        "declared_in_headers_fact": (
            Fact.present(False, producer=producer)
            if headers_parsed
            else Fact.not_collected("no headers parsed in this dump", producer=producer)
        ),
        "in_public_contract_fact": Fact.not_collected(
            "export-table entry with no contract evidence", producer=producer
        ),
        "binary_exported_fact": Fact.present(True, producer=producer),
    }
