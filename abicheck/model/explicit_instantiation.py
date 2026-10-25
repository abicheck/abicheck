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


"""How a template instance was declared in the header: the producer's answer
to "was this function or variable named by an explicit instantiation or an
explicit specialization" (``Function.explicit_instantiation_fact``,
``Variable.explicit_instantiation_fact``).

Only a clang header AST reads it (``extract/headers/clang/explicit_instantiation.py``);
castxml does not record the instantiation kind, so its side is ``NOT_CAPTURED``
and every rule that consumes the fact keeps its implicit-instance path.
"""

from __future__ import annotations

from enum import StrEnum


class ExplicitInstantiation(StrEnum):
    """The explicit form of one template instance, as the header spelled it."""

    #: ``extern template <decl>;`` -- an explicit instantiation declaration.
    DECLARATION = "declaration"
    #: ``template <decl>;`` -- an explicit instantiation definition.
    DEFINITION = "definition"
    #: ``template <> <decl>`` -- an explicit specialization.
    SPECIALIZATION = "specialization"
    #: Neither: an implicit instance, or a declaration the header never spelled.
    ABSENT = "absent"
