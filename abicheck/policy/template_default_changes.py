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

from typing import TYPE_CHECKING

from ..compare.template_param_defaults import (
    _pattern_records,
    detect_default_template_arg_changed,
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


def _templates_named_by_reported_functions(
    old: AbiSnapshot, new: AbiSnapshot, changes: list[Change]
) -> set[str]:
    """Class-template keys spelled in the signature of a reported function.

    A removed or added function whose parameter or return type names
    ``Box<...>`` already reports the break (shape C), so the header-level
    finding for ``Box`` would be a second count of it.
    """
    reported = {c.symbol for c in changes if c.kind in _FUNCTION_CHANGE_KINDS}
    if not reported:
        return set()
    keys = set(_pattern_records(old)) | set(_pattern_records(new))
    spellings: list[str] = []
    for snap in (old, new):
        for fn in snap.declarations.functions:
            if fn.mangled in reported:
                spellings.append(fn.return_type)
                spellings.extend(param.type for param in fn.params)
    return {key for key in keys if _spells_template(spellings, key)}


def _spells_template(texts: list[str], key: str) -> bool:
    """Whether a spelled type in *texts* names the template *key*.

    The leaf name must begin an identifier (no word character or ``:`` before
    it), and an opening ``<`` must follow it, after optional whitespace. So
    ``Box<char>`` matches and ``FlatBox<char>`` or ``ns2::Box<int>`` do not.
    """
    leaf = key.rsplit("::", 1)[-1]
    for text in texts:
        at = text.find(leaf)
        while at != -1:
            before = text[at - 1] if at else ""
            tail = text[at + len(leaf) :].lstrip()
            if (
                not (before and (before.isalnum() or before in "_:"))
                and tail[:1] == "<"
            ):
                return True
            at = text.find(leaf, at + 1)
    return False


def detect_template_default_changes(
    old: AbiSnapshot, ctx: PipelineContext, changes: list[Change]
) -> list[Change]:
    """The pipeline entry for ADR-085: the exported-instantiation detector
    (``DEFAULT_TEMPLATE_ARG_CHANGED``, shape A) plus the header-level one
    (``TEMPLATE_PARAM_DEFAULT_CHANGED``, shape B).

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
    if ledger is not None:
        for change in suppressed:
            ledger.record(
                change,
                Disposition.DEDUPLICATED,
                application_point="detect_template_default_changes",
            )
    return [*default_template_findings, *header_findings]
