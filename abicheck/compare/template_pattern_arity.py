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

"""Mandatory template arity of a class-template *pattern* record.

A backend that models a class template as its pattern (the clang and
gcc-plugin header backends: ``KMb`` with ``is_template_pattern``) records no
``KMb<int>`` instantiation record whose spelled arity could be counted, so
the recorded parameter defaults (``RecordType.template_param_defaults_fact``,
ADR-085 D1) are the only witness of how many arguments a use must spell.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..model.availability import FactStatus

if TYPE_CHECKING:
    from ..model import RecordType


def pattern_mandatory_arity(rec: RecordType) -> int | None:
    """The number of *rec*'s template parameters with no default, or ``None``
    when *rec* is no pattern or its defaults were not recorded (castxml).

    Defaults are trailing, so that count is exactly the arguments every use
    must spell. Known limit: the fact does not mark a parameter pack, which
    reads as a parameter with no default, so appending ``class... Ts`` counts
    as one more mandatory argument.
    """
    if not rec.is_template_pattern:
        return None
    fact = rec.template_param_defaults_fact
    if fact is None or fact.status != FactStatus.PRESENT or fact.value is None:
        return None
    return sum(1 for d in fact.value if d is None)
