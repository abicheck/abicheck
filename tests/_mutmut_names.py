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

"""Map mutmut's rewritten definition names back to the source names.

The mutation lane runs the suite against a copy of ``abicheck/`` in which
mutmut 3 rewrites every mutated function ``f`` into a trampoline named ``f``,
the original body as ``x_f__mutmut_orig`` (methods: ``xǁClassǁf__mutmut_orig``),
one copy per mutant as ``x_f__mutmut_<n>``, and a module-level
``x_f__mutmut_mutants`` table. A whole-tree AST inventory (the defect-family
harnesses' site scans) must read that tree as the source it came from, or
every mutant copy looks like a new, unregistered site and the lane aborts
before measuring anything.
"""

from __future__ import annotations

import re

_MUTMUT_NAME_RE = re.compile(r"^x(?:ǁ[^ǁ]+ǁ|_)(?P<name>.+)__mutmut_(?P<tag>\w+)$")


def canonical_def_name(name: str) -> str | None:
    """Source name for a definition, or ``None`` for a mutmut-only artifact.

    ``x_f__mutmut_orig`` -> ``f``; ``x_f__mutmut_3`` / ``x_f__mutmut_mutants``
    -> ``None``; any other name is returned unchanged.
    """
    m = _MUTMUT_NAME_RE.match(name)
    if m is None:
        return name
    return m.group("name") if m.group("tag") == "orig" else None


def is_mutmut_artifact(name: str) -> bool:
    """True for a name that exists only in mutmut's rewritten copy."""
    return canonical_def_name(name) is None
