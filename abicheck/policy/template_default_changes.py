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

"""ADR-085 pipeline entry: the exported-instantiation and header-level default
template-argument detectors, run as one step.

Lives in ``policy/`` because it records a covered relation in the disposition
ledger, which ``compare/`` may not import (ADR-061 dependency direction).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..compare.template_param_defaults import (
    _pattern_records,
    detect_default_template_arg_changed,
    detect_function_template_param_default_changed,
    detect_template_param_default_changed,
)
from ..compare.template_surface import (
    qualified_name_lookup,
    strip_template_args as _callable_stem,
)
from ..model.change_catalog.kinds import ChangeKind
from .disposition_ledger import Disposition

if TYPE_CHECKING:
    from ..model.change import Change
    from ..model.snapshot import AbiSnapshot
    from .finding_disposition.run_state import PipelineContext


_FUNCTION_CHANGE_KINDS = frozenset({ChangeKind.FUNC_REMOVED, ChangeKind.FUNC_ADDED})


#: A template-id spelling: an optional leading ``::`` (global scope), a possibly
#: qualified name, and the ``<`` that opens its arguments. The lookbehind keeps
#: a match from starting in the middle of a longer qualified name.
_SPELLED_TEMPLATE_ID = re.compile(r"(?<![\w:])(::)?((?:\w+::)*\w+)\s*<")


def _templates_named_by_reported_functions(
    old: AbiSnapshot, new: AbiSnapshot, changes: list[Change]
) -> set[str]:
    """Qualified class-template keys spelled in the signature of a reported
    function.

    A removed or added function whose parameter or return type names
    ``Box<...>`` already reports the break (shape C), so the header-level
    finding for ``Box`` would be a second count of it. The spelling is resolved
    the way C++ resolves it, from the function's own enclosing scope outward,
    so ``b::take(Box<char>)`` names ``b::Box`` and not ``a::Box``.
    """
    reported = {c.symbol for c in changes if c.kind in _FUNCTION_CHANGE_KINDS}
    if not reported:
        return set()
    keys = set(_pattern_records(old)) | set(_pattern_records(new))
    named: set[str] = set()
    for snap in (old, new):
        name_of = qualified_name_lookup(snap)
        for fn in snap.declarations.functions:
            if fn.mangled not in reported:
                continue
            # The qualified name carries the parameter list; the scope is the
            # path before it (``b::take(b::Box<char> const&)`` -> ``b``).
            scope = _callable_stem(name_of(fn)).split("(", 1)[0].split("::")[:-1]
            spellings = [fn.return_type, *(param.type for param in fn.params)]
            for text in spellings:
                for match in _SPELLED_TEMPLATE_ID.finditer(text):
                    resolved = _resolve_spelled_name(match, scope, keys)
                    if resolved is not None:
                        named.add(resolved)
    return named


def _resolve_spelled_name(
    match: re.Match[str], scope: list[str], keys: set[str]
) -> str | None:
    """The template key a spelled template-id denotes, or ``None``.

    A global ``::Box`` names exactly ``Box``. An unqualified or partly
    qualified ``Box`` / ``Outer::Box`` is looked up the way C++ does: each
    enclosing scope of the function, innermost first, down to the global
    namespace, and the first scope that holds a known template wins. So inside
    ``a::Outer`` a spelled ``Box`` that has an ``a::Outer::Box`` names that one,
    not ``a::Box`` as well.
    """
    global_scope, spelled = match.group(1), match.group(2)
    if global_scope:
        return spelled if spelled in keys else None
    for depth in range(len(scope), -1, -1):
        candidate = "::".join([*scope[:depth], spelled])
        if candidate in keys:
            return candidate
    return None


def detect_template_default_changes(
    old: AbiSnapshot, ctx: PipelineContext, changes: list[Change]
) -> list[Change]:
    """The pipeline entry for ADR-085: the exported-instantiation detector
    (``DEFAULT_TEMPLATE_ARG_CHANGED``, shape A) plus the header-level ones
    (``TEMPLATE_PARAM_DEFAULT_CHANGED``, shape B) for class and function
    templates alike.

    A header-level relation that shape A (an exported instantiation) or shape
    C (a reported function signature) already covers is recorded in the
    disposition ledger as ``DEDUPLICATED`` rather than dropped, so the raw total
    still counts it (ADR-067 "record before disposing").
    """
    new = ctx.new
    ledger = ctx.disposition_ledger
    default_template_findings = detect_default_template_arg_changed(old, new)
    covered_symbols = {c.symbol for c in default_template_findings}
    old_name_of = qualified_name_lookup(old)
    instantiation_stems = {
        _callable_stem(old_name_of(fn))
        for fn in old.declarations.functions
        if fn.mangled in covered_symbols
    }
    header_findings, suppressed = detect_template_param_default_changed(
        old,
        new,
        instantiation_stems=instantiation_stems,
        reported_templates=_templates_named_by_reported_functions(old, new, changes),
    )
    function_findings, function_suppressed = (
        detect_function_template_param_default_changed(
            old, new, instantiation_stems=instantiation_stems
        )
    )
    if ledger is not None:
        for change in [*suppressed, *function_suppressed]:
            ledger.record(
                change,
                Disposition.DEDUPLICATED,
                application_point="detect_template_default_changes",
            )
    return [*default_template_findings, *header_findings, *function_findings]
