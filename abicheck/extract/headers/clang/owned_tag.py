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

"""The anonymous tag a typedef's type node *owns*, in either clang JSON shape.

``typedef struct {…} Foo;`` / ``typedef enum {…} Foo;`` give the unnamed
``RecordDecl``/``EnumDecl`` its only public name. clang's JSON AST links the
typedef's type to that declaration in one of two version-dependent shapes:

- clang <= 21: an ``ElaboratedType`` node carrying
  ``"ownedTagDecl": {"id", "kind", "name"}``;
- clang >= 22 (``ElaboratedType`` was removed from the AST): the
  ``RecordType``/``EnumType`` node itself carries ``"isTagOwned": true`` next
  to its ordinary ``"decl": {"id", "kind", "name"}`` (``JSONNodeDumper::
  VisitTagType``).

Reading only the first shape made the clang header backend drop every
typedef'd anonymous struct/enum on clang 22+ (an empty surface, not an error),
which the clang-latest CI lane found. Every reader of this link goes through
:func:`owned_tag_decl` so the two shapes cannot drift apart again.
"""

from __future__ import annotations

from typing import Any


def owned_tag_decl(node: Any) -> dict[str, Any] | None:
    """The bare decl ref (``id``/``kind``/``name``) of the tag *node* owns,
    or ``None`` when *node* owns no tag declaration.

    A plain ``decl`` on a tag type that does not own its declaration (``Foo``
    in ``typedef struct Foo Bar;``) is deliberately not an owned tag.
    """
    if not isinstance(node, dict):
        return None
    owned = node.get("ownedTagDecl")
    if isinstance(owned, dict):
        return owned
    if node.get("isTagOwned") is True:
        decl = node.get("decl")
        if isinstance(decl, dict):
            return decl
    return None
