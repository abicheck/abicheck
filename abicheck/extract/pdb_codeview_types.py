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

"""Parsed CodeView type records: the values ``pdb_parser.TypeDatabase`` builds.

Plain data, no parsing: kept apart from the parser so the record shapes
have one home that the PDB readers and their consumers share.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CvStruct:
    """Parsed LF_STRUCTURE / LF_CLASS / LF_UNION."""

    type_index: int
    name: str
    field_list_ti: int
    byte_size: int
    is_forward_ref: bool
    is_packed: bool
    is_union: bool
    count: int  # number of members


@dataclass
class CvEnum:
    """Parsed LF_ENUM."""

    type_index: int
    name: str
    field_list_ti: int
    underlying_type_ti: int
    is_forward_ref: bool
    count: int


@dataclass
class CvMember:
    """Parsed LF_MEMBER (non-static data member)."""

    name: str
    type_ti: int
    offset: int
    access: int  # CV_fldattr_t access bits


@dataclass
class CvEnumerator:
    """Parsed LF_ENUMERATE."""

    name: str
    value: int


@dataclass
class CvOneMethod:
    """Parsed LF_ONEMETHOD (non-overloaded member function).

    Carries the method's function-type index so the calling convention of the
    referenced LF_MFUNCTION/LF_PROCEDURE can be resolved by name — the piece
    the PDB path needs to feed ``AdvancedDwarfMetadata.calling_conventions``
    (a PDB has no DWARF-style per-symbol linkage, but methods are named right
    in their class's fieldlist).
    """

    name: str
    type_ti: int


@dataclass
class CvProcedure:
    """Parsed LF_PROCEDURE."""

    type_index: int
    return_type_ti: int
    calling_convention: int
    param_count: int
    arglist_ti: int


@dataclass
class CvMemberFunction:
    """Parsed LF_MFUNCTION."""

    type_index: int
    return_type_ti: int
    class_type_ti: int
    this_type_ti: int
    calling_convention: int
    param_count: int
    arglist_ti: int
    this_adjust: int


@dataclass
class CvPointer:
    """Parsed LF_POINTER."""

    type_index: int
    referent_ti: int
    attrs: int
    byte_size: int


@dataclass
class CvArray:
    """Parsed LF_ARRAY."""

    type_index: int
    element_type_ti: int
    index_type_ti: int
    byte_size: int
    name: str


@dataclass
class CvModifier:
    """Parsed LF_MODIFIER."""

    type_index: int
    modified_ti: int
    is_const: bool
    is_volatile: bool
    is_unaligned: bool


@dataclass
class CvBitfield:
    """Parsed LF_BITFIELD."""

    type_index: int
    underlying_ti: int
    length: int  # bit width
    position: int  # bit position
