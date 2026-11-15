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

"""The linker spellings that can witness an empty tag type's rename.

Split out of :mod:`abicheck.diff_cpp_patterns` (``detect_tag_type_renamed``).
The re-mangled symbols are explicit instantiations over the tag
(``kfam<ktag_a>()`` -> ``_Z4kfamI6ktag_aEiv``). Only a header backend that
declares the instantiation puts it in the function model; a template is
usually declared generically, so the instantiation is an export with no
declaration -- in the binary's export table, absent from ``functions`` (the
gcc-plugin and clang header backends model no such entry at all).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from abicheck.model.export_index import snapshot_export_names
from abicheck.model.synthetic_key import is_synthetic_ctor_key, is_synthetic_dtor_key

if TYPE_CHECKING:
    from abicheck.model.declarations import Function
    from abicheck.model.snapshot import AbiSnapshot


def tag_rename_symbol_evidence(
    snap: AbiSnapshot, functions: Iterable[Function]
) -> set[str]:
    """*functions*' linker spellings plus every export *snap*'s tables carry.

    The export table makes the evidence independent of which header backend
    made the snapshot. A synthetic special-member key (castxml's
    ``__abicheck_ctor__ktag_a()`` / ``~ktag_a`` placeholder for an implicit
    member) is no linker symbol and witnesses nothing: it would "re-mangle"
    for any two empty tags swapped in one namespace.
    """
    names = {
        f.mangled
        for f in functions
        if not is_synthetic_ctor_key(f.mangled) and not is_synthetic_dtor_key(f.mangled)
    }
    return names | set(snapshot_export_names(snap))
