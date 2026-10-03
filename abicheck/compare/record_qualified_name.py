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

"""Recover a record's namespace-qualified name from the snapshots' own records.

A header-AST record carries its leaf in ``RecordType.name`` and its scope only
in ``RecordType.qualified_name``; a finding raised from it (``field_renamed``)
names the leaf. Detectors that judge *where* a record lives (an internal
``detail::``/``impl::`` namespace) then see no namespace at all -- unless DWARF
happened to contribute a second, qualified finding for the same record. That
made those detectors silently depend on optional debug info for a fact the
headers already state: case89's ``inline_body_references_renamed_member``
fired on Linux ``-g`` builds and on no other evidence profile (a macOS ``-g``
dylib keeps its DWARF in the object files; a release build has none).

:func:`qualify_record_name` answers from the records themselves, and only when
the answer is unambiguous: a leaf shared by records in two different scopes
stays unqualified, because choosing one would attribute the finding to a
record the evidence does not single out.
"""

from __future__ import annotations

from collections.abc import Iterable

__all__ = ["qualify_record_name"]


def qualify_record_name(name: str, records: Iterable[object]) -> str:
    """The unique qualified spelling of record *name*, else *name* unchanged.

    *name* already containing ``::`` is returned as is. Otherwise every record
    in *records* whose ``name`` is *name* contributes its ``qualified_name``
    (falling back to ``name``); exactly one distinct spelling is the answer.
    Zero or several leave *name* as given -- no evidence, or no unique answer.
    """
    if "::" in name:
        return name
    spellings = {
        getattr(rec, "qualified_name", None) or name
        for rec in records
        if getattr(rec, "name", None) == name
    }
    if len(spellings) == 1:
        return next(iter(spellings))
    return name
