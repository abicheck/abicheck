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

"""Header-text scan for C++20 concepts and requires-clauses.

Neither header backend models a concept definition or the constraint a
``requires`` clause places on a template, so a tightened concept compares
``NO_CHANGE`` from headers. This scan answers the narrower question both
backends can be checked against: does a public header spell a ``concept``
definition or a ``requires`` clause/expression at all? When one does, the
snapshot records that the constraints were not observed, and the assurance
rollup reads ``partial`` instead of ``complete``.

Like :mod:`.template_default_scan`, it is a text heuristic over the headers
the parse was given, with comments and string literals stripped first. A
macro that expands to ``concept``/``requires`` is not seen, and a header
included indirectly is not read; both make the scan say ``False`` (no gap
reported), never a clean pass. A ``#define`` line is skipped, so a macro
*named* or *defined with* the keyword does not count on its own.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from .template_default_scan import strip_comments_and_strings

__all__ = ["concept_header_scan", "header_declares_concept_or_requires"]

#: ``template <...> concept Name =`` -- a concept definition. A bare
#: ``concept`` identifier (a pre-C++20 variable or parameter) does not match.
_CONCEPT_DEFINITION = re.compile(r"\bconcept\s+[A-Za-z_]\w*\s*=")
#: ``requires`` followed by what can start a clause or expression: ``(``,
#: ``{``, a name, ``!`` or ``::``. A lone ``requires`` used as an identifier
#: (``int requires;``) is followed by ``;``, ``,``, ``)`` or ``=``.
_REQUIRES = re.compile(r"\brequires\s*(?:[({!]|::|[A-Za-z_])")
_DIRECTIVE = re.compile(r"^[ \t]*#(?:[^\n]*\\\n)*[^\n]*", re.MULTILINE)


def header_declares_concept_or_requires(text: str) -> bool:
    """Whether the header *text* defines a concept or spells a ``requires``
    clause or expression outside comments, string literals and preprocessor
    directives."""
    code = _DIRECTIVE.sub(" ", strip_comments_and_strings(text))
    return bool(_CONCEPT_DEFINITION.search(code) or _REQUIRES.search(code))


def concept_header_scan(paths: Iterable[str | Path]) -> bool:
    """Whether any header in *paths* defines a concept or spells a
    ``requires`` clause. Unreadable files are skipped, as in
    :func:`.template_default_scan.template_default_header_scan`."""
    for path in paths:
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if header_declares_concept_or_requires(text):
            return True
    return False
