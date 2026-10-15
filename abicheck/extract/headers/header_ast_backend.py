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

"""The header-AST backend protocol (lane B, stage B2).

A :class:`HeaderAstBackend` runs one AST frontend (castxml or clang) over a
:class:`HeaderParseRequest` -- the compile context of one header parse --
and returns the parser that exposes the shared ``parse_*`` format-builder
interface (:class:`~abicheck.extract.header_ast_fields._HeaderAstParser`).
That parser is the IR-fragment producer:
:func:`~abicheck.extract.header_ast_fields.parse_header_ast_fields` turns it
into a :class:`~abicheck.extract.header_ast_fields.HeaderAstFields`.

Backends take their process runner as a constructor argument, so a test
supplies a fake runner (or a fake backend) instead of patching a module
name. ``dumper.HEADER_AST_BACKENDS`` is the registry the dump dispatches
through.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ..header_ast_fields import _HeaderAstParser


@dataclass(frozen=True)
class HeaderParseRequest:
    """The compile context of one header parse.

    Field names match ``dumper._header_ast_parser``'s keyword arguments:
    the headers and include roots, the compiler/toolchain selection
    (``compiler``, ``gcc_path``, ``gcc_prefix``, ``gcc_options``,
    ``gcc_option_tokens``, ``sysroot``, ``nostdinc``), the language
    (``lang``; ``None`` lets the backend auto-detect), the exported symbol
    sets and public-header roots the parser classifies declarations
    against, and the cache/pruning inputs.
    """

    headers: list[Path]
    extra_includes: list[Path]
    compiler: str = "c++"
    gcc_path: str | None = None
    gcc_prefix: str | None = None
    gcc_options: str | None = None
    gcc_option_tokens: tuple[str, ...] = ()
    sysroot: Path | None = None
    nostdinc: bool = False
    lang: str | None = None
    exported_dynamic: set[str] = field(default_factory=set)
    exported_static: set[str] = field(default_factory=set)
    public_header_paths: list[str] = field(default_factory=list)
    public_dir_paths: list[str] = field(default_factory=list)
    extra_hash_dirs: tuple[Path, ...] = ()
    frontend_context: str = "host"
    pruning_header_roots: tuple[str, ...] | None = None
    no_binary_evidence: bool = False


@runtime_checkable
class HeaderAstBackend(Protocol):
    """One header-AST frontend (``castxml`` or ``clang``)."""

    name: str

    def parse(
        self, request: HeaderParseRequest, *, fallback_reason: str | None = None
    ) -> _HeaderAstParser:
        """Run the frontend for *request* and return its stamped parser.

        *fallback_reason*, when given, records why this backend runs in
        place of another (the castxml-to-clang auto fallback).
        """
        ...
