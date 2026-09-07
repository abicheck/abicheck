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

"""Regression coverage for ``dumper_hybrid._macho_normalize_mangled``'s
idempotence on an already-normalized Itanium mangled name.

Split out of ``tests/test_dumper_hybrid.py`` (that file is already at its
recorded ``architecture/debt.yaml`` no-growth baseline) rather than growing
it further -- see this repo's root ``AGENTS.md`` "Files that are large —
edit carefully": move responsibility to a properly-owned module instead of
trimming to fit.

Root cause: ``extract.headers.clang.functions.parse_function_element``/
``dumper_clang.parse_variables`` now strip Darwin's leading-underscore
linker decoration at parse time (needed so a *bare* ``--ast-frontend
clang`` dump, with no castxml side to reconcile against, gets a correctly
undecorated ``Function.mangled``/``Variable.mangled`` too -- previously
only the castxml+clang hybrid-merge path below normalized it). That means
a clang-side ``Function``/``Variable`` reaching ``merge_snapshots`` is now
ordinarily **already** in castxml's pure ``"_Z..."`` form, not the
Mach-O-decorated ``"__Z..."`` shape ``_macho_normalize_mangled`` was
originally written to expect -- so its old *unconditional* single-
underscore strip would corrupt an already-pure name into ``"Z..."``,
leaving it forever unmatched against castxml's own identical spelling.
"""

from __future__ import annotations

from abicheck.dumper_hybrid import merge_snapshots
from abicheck.model import AbiSnapshot, AccessLevel, Function


def _snap(functions=None, **kwargs) -> AbiSnapshot:
    return AbiSnapshot(
        library="libtest.so.1",
        version="1.0",
        functions=functions or [],
        from_headers=True,
        **kwargs,
    )


class TestMachoMangledNormalizationIdempotence:
    def test_already_normalized_itanium_mangled_name_is_not_double_stripped(self):
        f = Function(
            name="foo",
            mangled="_ZN2ns3fooEv",
            return_type="void",
            access=AccessLevel.PUBLIC,
        )
        castxml = _snap(functions=[f], ast_producer="castxml", platform="macho")
        clang = _snap(functions=[f], ast_producer="clang", platform="macho")
        merged = merge_snapshots(castxml, clang)

        assert len(merged.functions) == 1
        assert merged.func_by_mangled("_ZN2ns3fooEv") is not None
        assert merged.func_by_mangled("ZN2ns3fooEv") is None
