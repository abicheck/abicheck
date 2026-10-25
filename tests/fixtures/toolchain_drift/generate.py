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

"""Regenerate the reduced SVS toolchain-drift snapshot pair used by
``tests/test_toolchain_drift_stated_contract.py``.

The SVS field report (PR 387): a plain rebuild of ``libsvs_c_api.so`` with a
newer toolchain exited 1 under a stated ``scope: public: true`` contract on
22 unsuppressible ``finding_unresolved`` records. Its library did not change;
only what the toolchain put around it did. This pair keeps that shape and
nothing else, written by hand (no SVS binaries):

* the C API (``svs_index_build``/``svs_format``) is identical on both sides
  and declared in a public header;
* 10 ``imported_symbol_added`` + 2 ``imported_symbol_removed`` -- the
  inlining decisions moved which libstdc++/libc entry points are imported;
* 8 ``symbol_leaked_from_dependency_changed`` -- libstdc++ template
  instantiations the old build re-exported and the new one does not (and
  vice versa), each attributed to ``libstdc++.so.6``;
* 2 ``symbol_binding_strengthened`` -- ``std::`` exports that went
  ``WEAK`` -> ``GLOBAL``.

Run ``python tests/fixtures/toolchain_drift/generate.py`` from the repository
root; it writes ``old.json`` and ``new.json`` next to itself.
"""

from __future__ import annotations

from pathlib import Path

from abicheck.elf_metadata import ElfImport, ElfMetadata, ElfSymbol, SymbolBinding
from abicheck.model import AbiSnapshot, Function, Param, ScopeOrigin, Visibility
from abicheck.serialization import snapshot_to_json

HERE = Path(__file__).parent
PUB = {"visibility": Visibility.PUBLIC, "origin": ScopeOrigin.PUBLIC_HEADER}
LIBSTDCXX = "libstdc++.so.6"

API = (
    Function(
        name="svs_index_build",
        mangled="svs_index_build",
        return_type="int",
        params=[Param(name="data", type="const float *"), Param(name="n", type="int")],
        **PUB,  # type: ignore[arg-type]
    ),
    Function(
        name="svs_format",
        mangled="svs_format",
        return_type="int",
        params=[
            Param(name="buf", type="char *"),
            Param(name="cap", type="int"),
            Param(name="value", type="unsigned int"),
        ],
        **PUB,  # type: ignore[arg-type]
    ),
)

#: Imports both builds keep.
COMMON_IMPORTS = ("memcpy", "strlen", "__cxa_finalize")
#: Only the old build imports these (2 -> imported_symbol_removed).
OLD_IMPORTS = (
    "_ZNSt7__cxx1112basic_stringIcSt11char_traitsIcESaIcEE9_M_createERmm",
    "_ZSt20__throw_length_errorPKc",
)
#: Only the new build imports these (10 -> imported_symbol_added).
NEW_IMPORTS = (
    "_ZNSo9_M_insertImEERSoT_",
    "_ZNSt8ios_base4InitC1Ev",
    "_ZNSt8ios_base4InitD1Ev",
    "_ZSt4cerr",
    "_ZNKSt7__cxx1119basic_ostringstreamIcSt11char_traitsIcESaIcEE3strEv",
    "_ZNSt7__cxx1119basic_ostringstreamIcSt11char_traitsIcESaIcEEC1Ev",
    "_ZNSt7__cxx1119basic_ostringstreamIcSt11char_traitsIcESaIcEED1Ev",
    "_ZSt16__ostream_insertIcSt11char_traitsIcEERSt13basic_ostreamIT_T0_ES6_PKS3_l",
    "_ZSt24__throw_invalid_argumentPKc",
    "__cxa_atexit",
)

#: Template instantiations each build re-exported, attributed to libstdc++
#: (4 removed + 4 added -> 8 symbol_leaked_from_dependency_changed).
OLD_LEAKS = (
    "_ZNSt6vectorIfSaIfEED2Ev",
    "_ZNSt6vectorIfSaIfEEC2IPKfvEET_S5_RKS0_",
    "_ZNSt12_Vector_baseIfSaIfEED2Ev",
    "_ZNSt7__cxx119to_stringEj",
)
NEW_LEAKS = (
    "_ZNSt8__detail18__to_chars_10_implIjEEvPcjT_",
    "_ZNSt8__detail15__to_chars_lenIjEEjT_i",
    "_ZSt8to_charsPcS_j",
    "_ZSt13__to_chars_iIjESt15to_chars_resultPcS1_T_i",
)

#: ``std::`` exports both builds carry, WEAK before and GLOBAL after
#: (2 -> symbol_binding_strengthened).
STRENGTHENED = (
    "_ZNSt12_Vector_baseIfSaIfEE11_M_allocateEm",
    "_ZNSt16allocator_traitsISaIfEE8allocateERS0_m",
)


def _snapshot(
    version: str, imports: tuple[str, ...], leaks: tuple[str, ...]
) -> AbiSnapshot:
    binding = SymbolBinding.WEAK if version == "0.5.0" else SymbolBinding.GLOBAL
    symbols = [ElfSymbol(name=f.mangled) for f in API]
    symbols += [
        ElfSymbol(name=s, binding=SymbolBinding.WEAK, origin_lib=LIBSTDCXX)
        for s in leaks
    ]
    symbols += [
        ElfSymbol(name=s, binding=binding, origin_lib=LIBSTDCXX) for s in STRENGTHENED
    ]
    return AbiSnapshot(
        library="libsvs_c_api.so",
        version=version,
        functions=list(API),
        elf=ElfMetadata(
            soname="libsvs_c_api.so",
            machine="EM_X86_64",
            hash_styles=frozenset({"gnu"}),
            needed=[LIBSTDCXX, "libm.so.6", "libgcc_s.so.1", "libc.so.6"],
            symbols=symbols,
            imports=[ElfImport(name=n) for n in (*COMMON_IMPORTS, *imports)],
        ),
    )


def build_pair() -> tuple[AbiSnapshot, AbiSnapshot]:
    """The v0.5.0 build and the toolchain-drifted rebuild of the same source."""
    return (
        _snapshot("0.5.0", OLD_IMPORTS, OLD_LEAKS),
        _snapshot("0.5.0-rebuild", NEW_IMPORTS, NEW_LEAKS),
    )


def main() -> int:
    old, new = build_pair()
    (HERE / "old.json").write_text(snapshot_to_json(old))
    (HERE / "new.json").write_text(snapshot_to_json(new))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
