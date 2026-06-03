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

"""ELF ABI-relevance filtering shared by symbol and DWARF paths."""
from __future__ import annotations

# Prefixes that identify GCC/compiler-internal symbols which may leak into
# .dynsym through statically-linked runtime (e.g. libgcc_s, SVML).
_GCC_INTERNAL_PREFIXES = (
    "ix86_",
    "x86_64_",
    "__cpu_model",
    "__cpu_features",
    "_ZGV",          # GCC SIMD vector variants (e.g. _ZGVbN2v_sin)
    "__svml_",       # Intel Short Vector Math Library
    "__libm_sse2_",
    "__libm_avx_",
)

# Prefixes that identify transitive C++ standard-library symbols which may
# appear in .dynsym via weak linkage (libstdc++ / libc++).
_STDLIB_PREFIXES = (
    "_ZNSt",              # std:: namespace members (libstdc++)
    "_ZNKSt",             # const std:: methods
    "_ZNVSt",             # volatile std:: methods
    "_ZNRSt",             # ref-qualified std:: methods
    "_ZNKRSt",            # const/ref-qualified std:: methods
    "_ZNVRSt",            # volatile/ref-qualified std:: methods
    "_ZNSt3__1",          # libc++ inline-namespace __1
    "_ZdlPv",             # operator delete(void*)
    "_ZnwSt",             # operator new(std::size_t)
    "_ZnaSt",             # operator new[](std::size_t)
    "_ZdaPv",             # operator delete[](void*)
    "_ZTVN10__cxxabiv",   # vtables for RTTI (typeinfo infrastructure)
    "_ZTI",               # typeinfo objects
    "_ZTS",               # typeinfo strings
    "_ZSt",               # std:: global symbols (e.g. _ZSt4cout)
)


def is_abi_relevant_elf_symbol(name: str) -> bool:
    """Return False for ELF symbols that are not the library's own ABI.

    This filter must be shared by both the ELF-only and DWARF-backed snapshot
    paths. Otherwise a weak transitive libstdc++ export can be excluded from
    symbols-only reports yet re-enter as a ``PUBLIC`` DWARF function, producing
    false ``FUNC_REMOVED`` and type-reachability findings.
    """
    if not name:
        return False

    for prefix in _GCC_INTERNAL_PREFIXES:
        if name.startswith(prefix):
            return False

    for prefix in _STDLIB_PREFIXES:
        if name.startswith(prefix):
            return False

    # Private C symbols with __ as a namespace separator
    # (e.g. H5C__flush_marked_entries, MPI__send). C++ mangled names start
    # with _Z and are handled separately above.
    if not name.startswith("_Z") and "__" in name[2:]:
        return False

    return True
