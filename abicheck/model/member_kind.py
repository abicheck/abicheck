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

"""Whether a function is a class member, and which kind
(``Function.member_kind_fact``).

No older recorded fact says this. ``is_static`` means a static member on a
member and internal linkage on a free function; the stored name is the leaf;
the symbol of an uninstantiated template method is its bare name; and the
``entity_id`` scope of a mangled function is empty. A header backend reads it
from the declaration's own AST kind instead: clang's ``CXXMethodDecl`` /
``CXXConstructorDecl`` / ``CXXDestructorDecl`` / ``CXXConversionDecl`` versus
``FunctionDecl``, castxml's ``Method`` / ``OperatorMethod`` / ``Converter`` /
``Constructor`` / ``Destructor`` versus ``Function`` / ``OperatorFunction``,
and the GCC plugin's method list versus its function list. A friend function
defined in a class is a ``FunctionDecl``: free, with no ``this``.

The call axis (ADR-094 D8, ``compare/call_interface.py``) reads it to know
whether the call carries an implicit ``this`` argument.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = ["MemberKind"]


class MemberKind(StrEnum):
    """What kind of function a declaration is, as its AST node says."""

    #: A namespace-scope function, or a friend: no implicit ``this``.
    FREE = "free"
    #: A non-static member function (including an operator or a conversion
    #: function): receives ``this`` as its first argument.
    INSTANCE = "instance"
    #: A static member function: no implicit ``this``.
    STATIC = "static"
    #: A constructor: ``this``, plus ABI variants (C1/C2, a VTT argument on
    #: the base-object constructor of a class with virtual bases).
    CONSTRUCTOR = "constructor"
    #: A destructor: ``this``, plus ABI variants (D0/D1/D2, VTT).
    DESTRUCTOR = "destructor"
