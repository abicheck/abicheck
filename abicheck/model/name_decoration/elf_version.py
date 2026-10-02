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

"""GNU ELF symbol-version suffix codec (``name@VER`` / ``name@@VER``).

pyelftools reports a ``.dynsym`` name without its version (the version
lives in ``.gnu.version``/``.gnu.version_d``), but runtime, linker and
``nm -D`` spellings append it: ``@@VER`` for the default version a new
link binds to, ``@VER`` for a non-default (hidden) one. An ELF symbol name
never contains ``@`` itself, so the first ``@`` starts the suffix.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["ElfVersioned", "decode", "encode", "unversioned_name"]


@dataclass(frozen=True)
class ElfVersioned:
    """``version`` is ``None`` for an unversioned spelling; ``is_default``
    is then ``False``."""

    name: str
    version: str | None = None
    is_default: bool = False


def encode(decoded: ElfVersioned) -> str:
    if decoded.version is None:
        return decoded.name
    return f"{decoded.name}{'@@' if decoded.is_default else '@'}{decoded.version}"


def decode(symbol: str) -> ElfVersioned | None:
    """Split *symbol* into name and version, or ``None`` when it is not a
    well-formed versioned/unversioned ELF spelling (empty name, empty
    version, or an ``@`` inside the version)."""
    name, sep, rest = symbol.partition("@")
    if not name:
        return None
    if not sep:
        return ElfVersioned(name)
    is_default = rest.startswith("@")
    version = rest[1:] if is_default else rest
    if not version or "@" in version:
        return None
    return ElfVersioned(name, version, is_default)


def unversioned_name(symbol: str) -> str:
    """*symbol*'s name without any version suffix (*symbol* unchanged when
    it does not decode)."""
    decoded = decode(symbol)
    return decoded.name if decoded is not None else symbol
