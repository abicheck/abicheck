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

"""Which ``std::`` record a signature slot names, when its spelling hides it.

Split out of :mod:`abicheck.idioms` (which sits at its line budget) for the
STL-by-value anti-pattern. A header backend may spell a slot by the bare
alias it was written with: castxml renders a ``std::string`` parameter as
just ``string`` (a Typedef node's own name, unqualified). The slot's resolved
type identities (``Param.type_identities_fact`` /
``Function.return_type_identities_fact``, schema v54) still say which record
it names -- ``std::basic_string<char>``.
"""

from __future__ import annotations

from ..model.availability import FactStatus
from ..model.fact import Fact
from ..model.source_graph_query import is_stdlib_owned_name


def std_spelling(type_str: str, identities: Fact[tuple[str, ...]] | None) -> str:
    """*type_str*, or the slot's lone resolved ``std::`` identity when the
    spelling carries no ``std::`` of its own.

    Several identities, none, or an unrecorded fact leave the spelling as it
    is: the slot is then not provably one ``std::`` type.
    """
    if "std::" in type_str or identities is None:
        return type_str
    if identities.status != FactStatus.PRESENT or not identities.value:
        return type_str
    if len(identities.value) != 1:
        return type_str
    (ident,) = identities.value
    return ident if is_stdlib_owned_name(ident) else type_str
