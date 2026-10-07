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

"""The ``exported_not_public`` finding and its per-reason wording.

Split out of :mod:`abicheck.buildsource.cross_source_checks` (which carries a
``no_growth`` baseline) when the wording gained two reasons of its own: an
external template instantiated over the library's own types, and an export
whose declaration the header *text* shows in a region the run did not analyse
(:class:`~abicheck.buildsource.export_declaration_evidence.DeclarationHint`),
where the finding must not claim the declaration is absent.
"""

from __future__ import annotations

from ..model import Function, Variable
from ..model.change import Change
from ..model.change_catalog.kinds import ChangeKind
from ..model.evidence_status import Confidence
from .cross_source_checks_base import _change
from .export_accounting import (
    ACCOUNT_EXTERNAL_DEP,
    ACCOUNT_INTERNAL_NS,
    ACCOUNT_OWN_TYPE_INSTANTIATION,
    ACCOUNT_TEMPLATE_INST,
)
from .export_declaration_evidence import DeclarationHint


#: Per-category message templates for an undocumented export. Each states the
#: precise reason and the fix, so a maintainer can triage a leaked dependency
#: symbol differently from an internal-namespace escape (ADR-035 D4 accounting).
def exported_not_public_finding(
    sym: str,
    category: str,
    origin_lib: str | None,
    decl: Function | Variable | None,
    hint: DeclarationHint | None = None,
) -> Change:
    """Build the ``exported_not_public`` finding for one undocumented export.

    The message is category-specific — an external-dependency leak names the
    originating library and points at the linkage fix, an internal-namespace or
    template escape points at the visibility fix — so the *precise reason* rides
    on the finding, not just the aggregate count.
    """
    where = ""
    if decl is not None and category != ACCOUNT_EXTERNAL_DEP:
        kind = "function" if isinstance(decl, Function) else "variable"
        where = f" (declared as {kind} {decl.name!r} in a non-public header)"
    if category == ACCOUNT_EXTERNAL_DEP:
        message = (
            f"Symbol {sym!r} is exported by the binary but originates from an "
            f"external dependency ({origin_lib}) statically linked and re-exported "
            "— not part of this library's API. Hide it (visibility/version script) "
            "or link the dependency dynamically; a differing dependency version on "
            "another host makes the leaked symbol an ODR/compatibility hazard."
        )
    elif category == ACCOUNT_INTERNAL_NS:
        message = (
            f"Symbol {sym!r} is exported by the binary but declared in no public "
            f"header{where}; it belongs to an internal namespace "
            "(impl/internal/detail/anonymous). It is accidental ABI surface — hide "
            "it with -fvisibility=hidden or a version script."
        )
    elif category == ACCOUNT_OWN_TYPE_INSTANTIATION:
        message = (
            f"Symbol {sym!r} is this library's own instantiation of an external "
            "(standard/third-party) template over the library's own types -- a "
            "vague-linkage copy, not a statically linked dependency. No public "
            "header needs it exported; hide it unless consumers rely on it."
        )
    elif category == ACCOUNT_TEMPLATE_INST:
        message = (
            f"Symbol {sym!r} is an exported C++ template instantiation with no "
            f"matching public declaration{where} (the public headers declare the "
            "template, the binary carries this instantiation). Confirm it is "
            "intended surface, or hide it."
        )
    else:  # ACCOUNT_UNDECLARED
        message = (
            f"Symbol {sym!r} is exported by the binary but declared in no public "
            f"header{where}. It is accidental ABI surface — hide it "
            "(visibility/version script) or document it."
        )
    if hint is not None:  # the header text speaks for it: never claim absence
        where_seen = (
            "an excluded header (--exclude-header)"
            if hint.reason == "excluded_header"
            else f"a conditionally compiled region ({hint.guard})"
        )
        message = (
            f"Symbol {sym!r} is exported by the binary and no *parsed* public "
            f"declaration maps to it, but {hint.header} declares it in {where_seen}, "
            "which this run did not analyse. Its public status is unverified: "
            "re-run with that header/configuration before hiding it."
        )
    return _change(
        ChangeKind.EXPORTED_NOT_PUBLIC,
        sym,
        message,
        new_value=sym,
        old_value=origin_lib,
        confidence=Confidence.HIGH if hint is None else Confidence.LOW,
    )
