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

"""Closed vocabularies every ABI entity is described with.

Visibility, access level, parameter passing kind, and the ADR-024 Origin
axis. Leaf module: no first-party imports, so every other model module and
every consumer package can depend on it without ordering concerns.
"""

from __future__ import annotations

from enum import Enum


class Visibility(str, Enum):
    PUBLIC = "public"  # default visibility / exported
    HIDDEN = "hidden"  # __attribute__((visibility("hidden")))
    ELF_ONLY = "elf_only"  # present in ELF symbol table, not in headers


class ElfVisibility(str, Enum):
    """ELF st_other visibility from .dynsym — separate from API-level Visibility."""

    DEFAULT = "default"  # STV_DEFAULT
    PROTECTED = "protected"  # STV_PROTECTED
    HIDDEN = "hidden"  # STV_HIDDEN
    INTERNAL = "internal"  # STV_INTERNAL


class AccessLevel(str, Enum):
    PUBLIC = "public"
    PROTECTED = "protected"
    PRIVATE = "private"


class ParamKind(str, Enum):
    VALUE = "value"
    POINTER = "pointer"
    REFERENCE = "reference"
    RVALUE_REF = "rvalue_ref"


class ScopeOrigin(str, Enum):
    """Where a declaration's defining header sits relative to the
    user-provided public-header set — the *Origin* axis of the two-axis
    Linkage × Origin surface model (ADR-024 D1, ADR-015 schema v6).

    Classification is opt-in: it is only meaningful when the caller
    supplies a public-header set (``-H``/``--header``; ``scan`` also takes
    ``--public-header-dir``).
    Without one, every declaration is ``UNKNOWN`` and downstream behaviour
    is unchanged.
    """

    PUBLIC_HEADER = "public_header"  # defined in a provided public header
    PRIVATE_HEADER = "private_header"  # project header outside the public set
    SYSTEM_HEADER = "system_header"  # toolchain/system header (/usr/include, ...)
    GENERATED = "generated"  # machine-generated header (moc_*, *.pb.h, generated/ ...)
    EXPORT_ONLY = "export_only"  # exported by the binary but absent from any header
    UNKNOWN = "unknown"  # no public set, or no source location
