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

"""Whether a public declaration is a template specialization, and therefore
owes no dynamic export.

Its own module rather than a function inside
:mod:`abicheck.buildsource.cross_source_checks`: that file sits on an
``architecture/debt.yaml`` ``no_growth`` baseline, and ``AGENTS.md``'s rule for
such a file is to move responsibility out to an owned module rather than grow
it. The question here -- "is this entity a template specialization" -- is a
self-contained one about a declaration's linkage, answered from the Itanium
mangling with the display spelling only as a fallback, and both export-
obligation checks (functions and variables) ask it identically.
"""

from __future__ import annotations

from ..model.mangled_name import itanium_name_carries_template_arguments

__all__ = ["names_a_template_specialization"]


def names_a_template_specialization(name: str, mangled: str) -> bool:
    """Whether the declaration spelled *name*/*mangled* is a template
    specialization, and therefore owes no dynamic export.

    Two independent sources, mangling first:

    * ``model.mangled_name.itanium_name_carries_template_arguments`` reads the
      ``I…E`` template-argument production out of the Itanium mangling
      structurally. This is the semantic signal; the mangling is the ABI's own
      statement about what the entity is.
    * ``_looks_templated`` over the display name stays as the fallback for
      what the mangling cannot answer -- a non-Itanium (MSVC) spelling, or a
      production the structural parser does not model -- and for nothing else.

    Why the order matters, and why the display check could not stay first: a
    header backend can report a function template specialization under the
    bare display name ``is_specified`` with the real mangling
    ``_Z12is_specifiedI12OptionalBoolEbT_``. ``_looks_templated`` looks for
    angle brackets, finds none, and the declaration acquired an unconditional
    export obligation -- ``public_not_exported`` at ``Confidence.HIGH``,
    claiming an undefined-symbol error for a consumer that in fact compiles,
    links and runs with no library present at all, because the header carries
    the definition and the consumer emits its own vague-linkage copy.

    Note what this deliberately does *not* do. It does not suppress templates
    as a class from every check, and it does not touch the separate question
    of whether an old binary actually exported a symbol that the new one no
    longer does -- that is an observed export loss, evidence of a different
    kind, and it stays reported (see `compare/undeclared_exports.py`). A
    declaration-only non-inline specialization that really does require a
    library-provided definition is unchanged from before this function
    existed: the display-name check already excluded every such spelling, so
    this can only ever widen the exclusion to manglings whose display
    spelling hid the same fact, never narrow a check that was running.
    """
    from_mangling = itanium_name_carries_template_arguments(mangled)
    if from_mangling is not None:
        return from_mangling
    return _looks_templated(name)


def _looks_templated(name: str) -> bool:
    """Whether *name* is a template instantiation spelling (``Foo<int>``), not an operator.

    A bare ``<`` is not enough: ``operator<``, ``operator<<``, and ``operator<=>``
    legitimately contain one but are ordinary (non-template) functions with a real
    exported symbol, so testing ``"<" in name`` would wrongly skip a genuinely
    missing exported operator (Codex review). A template's ``<`` opens an argument
    list immediately after the template name, so the token right before the first
    ``<`` is the template's name — never ``operator``.
    """
    idx = name.find("<")
    if idx == -1:
        return False
    return not name[:idx].rstrip().endswith("operator")
