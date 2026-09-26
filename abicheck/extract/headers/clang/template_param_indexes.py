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

"""One raw clang AST's three template-parameter indexes, bundled and frozen.

A leaf beside ``templates.py`` rather than more lines inside it. That module
carries an ``architecture/debt.yaml`` ``no_growth`` baseline, and ``AGENTS.md``
is explicit that the way to respect one is to "move responsibility out to a
properly-owned module, never to trim the file to fit" -- so the responsibility
this adds (bundling the three indexes into one shareable, deeply read-only
value) gets its own owner. The dependency runs one way only, this module onto
``templates.py``; ``templates.py`` takes nothing back from here, spelling its
own widened parameter annotations as the bare ``Mapping``/``Sequence`` pair
instead of importing the aliases below.

The three builders themselves stay exactly where they are, and keep returning
the plain mutable dicts their direct callers and tests already expect (see
``dumper_clang_vtable``'s re-exports). Nothing here migrates that contract; it
adds a narrow immutable view for one new shared consumer.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from .templates import (
    _index_template_param_defaults,
    _index_template_param_kinds,
    _index_template_param_names,
    class_template_decls,
)

#: One position's worth of template-parameter metadata, as the three indexes
#: above spell it. Aliased so the widened consumer signatures below read as
#: one concept rather than three repetitions of a nested generic.
TemplateParamRow = Sequence[str | None]
TemplateParamIndex = Mapping[str, TemplateParamRow]


@dataclass(frozen=True)
class TemplateParamIndexes:
    """The three template-parameter indexes of ONE raw clang AST, together.

    Every one of :func:`_index_template_param_kinds`/:func:`_index_template_
    param_defaults`/:func:`_index_template_param_names` is a pure function of
    *root* alone -- it reads ``kind``/``name``/``inner``/``previousDecl`` and
    each ``ClassTemplateDecl``'s own parameter children, and nothing else. In
    particular none of them sees a binary's export table, the public-header
    selection, the target triple, or the C-vs-C++ language mode, so their
    results are identical for every member of a release fan-out that shares
    one header context, and for the neutral (export-evidence-free) parse of
    the same AST. That is what makes this bundle safe to build once per raw
    AST and hand to every parser over it (``dumper_clang._ClangAstParser``'s
    own ``_template_param_indexes_for``), instead of paying three whole-AST
    walks -- four, really: ``_index_template_param_defaults`` runs
    ``_index_template_param_names`` internally as well -- per constructed
    parser.

    Deliberately NOT the three ``dict[str, list[...]]`` objects the builders
    return. A value shared across parsers must not be mutable through any
    consumer's own supported interface, and a ``frozen=True`` dataclass alone
    would not give that: it freezes the three *attribute bindings* and leaves
    the dicts and their nested lists fully writable, so one member's parser
    could still corrupt what every other member reads. :func:`build_template_
    param_indexes` therefore rebuilds each index as a ``MappingProxyType``
    over ``str -> tuple[str | None, ...]``: the mapping rejects item
    assignment/deletion, each row is a tuple, and a row's own elements are
    ``str``/``None``, which are immutable outright. So the whole structure is
    read-only to its depth, not just at its surface -- and it costs one
    shallow rebuild per AST rather than a per-parser ``deepcopy``, which
    would put back exactly the per-parser work this exists to remove.

    The builders themselves keep returning plain mutable dicts, unchanged:
    they are a long-standing helper contract with direct callers and tests of
    their own (``dumper_clang_vtable`` re-exports all three), and this is a
    narrow immutable view added *for the new shared consumer*, not a
    migration of that contract.
    """

    kinds: TemplateParamIndex
    defaults: TemplateParamIndex
    names: TemplateParamIndex


def _freeze_template_param_index(
    index: dict[str, list[str | None]],
) -> TemplateParamIndex:
    """*index* as a read-only mapping of read-only rows (see above)."""

    return MappingProxyType({key: tuple(row) for key, row in index.items()})


def build_template_param_indexes(root: dict[str, Any]) -> TemplateParamIndexes:
    """Build all three template-parameter indexes of *root* in one call.

    A thin composition of the three existing builders, deliberately *not* a
    fused single registration pass: each one carries its own hard-won registration
    semantics (``_register_template_param_metadata``'s ``previousDecl``
    merge, and ``_index_template_param_defaults``'s dependent-default
    translation through the names index, which is why it consumes the names
    index rather than being folded into it), and merging the three walks is a
    separate optimization with separate risk. This one only changes *how
    often* they run, never what they answer.
    """

    # One whole-document traversal; each index replays its own registration
    # over the collected declarations (see `class_template_decls`).
    decls = class_template_decls(root)
    names = _index_template_param_names(root, decls)
    return TemplateParamIndexes(
        kinds=_freeze_template_param_index(_index_template_param_kinds(root, decls)),
        defaults=_freeze_template_param_index(
            _index_template_param_defaults(root, names, decls)
        ),
        names=_freeze_template_param_index(names),
    )
