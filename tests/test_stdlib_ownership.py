# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
""" "Is this a standard-library entity" is decided by ownership, not mention.

Bug class: a substring test standing in for an ownership test.
``looks_like_system_name`` asked whether ``std::`` appeared *anywhere* in a
spelling, so the project's own ``mylib::Wrapper<std::string>`` or
``void mylib::f(std::string)`` read as standard library, while
``source_link``'s stdlib-export check used a prefix rule -- the two disagreed
on the same name. The oracle is the generator: an entity built in a
stdlib scope is stdlib-owned however it is decorated, and an entity built in
a project scope is not, whatever stdlib types it mentions.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.buildsource.source_link import _is_stdlib_export
from abicheck.model.source_graph_query import (
    is_stdlib_owned_name,
    looks_like_system_name,
)

_STD_SCOPES = ("std", "std::__1", "__gnu_cxx", "__cxxabiv1")
_PROJECT_SCOPES = ("mylib", "mylib::detail", "a::b")
_MENTIONS = (
    "",
    "std::string",
    "std::vector<int>",
    "__gnu_cxx::__normal_iterator<int*>",
)


def _decorations(entity: str, mention: str):
    yield entity
    yield f"::{entity}"
    if mention:
        yield f"{entity}<{mention}>"
        yield f"void {entity}({mention})"
        yield f"{mention} {entity}()"
        yield f"{entity}<{mention}>::method({mention} const&)"


@pytest.mark.parametrize(
    ("scope", "mention"), list(itertools.product(_STD_SCOPES, _MENTIONS))
)
def test_a_stdlib_entity_is_stdlib_however_decorated(scope, mention) -> None:
    for spelling in _decorations(f"{scope}::thing", mention):
        assert is_stdlib_owned_name(spelling), spelling
        assert looks_like_system_name(spelling), spelling
        assert _is_stdlib_export("_Zfake", spelling), spelling


@pytest.mark.parametrize(
    ("scope", "mention"), list(itertools.product(_PROJECT_SCOPES, _MENTIONS))
)
def test_a_project_entity_is_never_stdlib_whatever_it_mentions(scope, mention) -> None:
    for spelling in _decorations(f"{scope}::thing", mention):
        assert not is_stdlib_owned_name(spelling), spelling
        assert not looks_like_system_name(spelling), spelling
        assert not _is_stdlib_export("_Zfake", spelling), spelling
