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

"""Bug classes about *what a header-AST backend lets into the snapshot*.

A themed sibling of `manifest.py` (see there for what this registry is and
is not), split out because `manifest.py` sits at its `architecture/debt.yaml`
`no_growth` baseline. The classes here are about a backend's declaration
lists admitting something that is not part of the parsed surface -- a wrong
answer that shows up as a fabricated identity or a refused dump, not as a
wrong computation over the right declarations.
"""

from __future__ import annotations

from .bug_class_schema import BugClass, KnownGap

__all__ = ["EXTRACTION_BUG_CLASSES"]


EXTRACTION_BUG_CLASSES: tuple[BugClass, ...] = (
    BugClass(
        id="extraction.function_local_declaration_leaks_into_surface",
        invariant=(
            "A declaration local to a function body (a local typedef, "
            "alias, enum, class, or any member of a local class, at any "
            "nesting depth) never enters a header-AST snapshot's "
            "declaration lists, whichever backend parsed it: it has no "
            "linkage and no namespace-scope identity to give it. Both "
            "backends therefore agree on the declaration set for the same "
            "header."
        ),
        fixed_by=(1350,),
        seed_tests=("tests/test_castxml_function_local_decls.py",),
        axes={"frontend": ("castxml", "clang")},
        known_gaps=(
            KnownGap(
                description=(
                    "A local class returned by value from an inline "
                    "function (a 'Voldemort type') has an ABI-relevant "
                    "layout, but neither backend records it: a layout "
                    "change there is visible only as the function's "
                    "return-type spelling, not as a type change."
                ),
                reference="abicheck/extract/headers/castxml/context.py",
            ),
        ),
    ),
    BugClass(
        id="scoping.declaration_kind_forwarded_unfiltered",
        invariant=(
            "Dump-time dependency scoping (and --exclude-header) applies one "
            "header-origin rule to every declaration kind a snapshot "
            "carries: an entry whose declaring header is a dependency is "
            "either dropped or retained by a stated reference rule, never "
            "carried through verbatim because the scoping pass's closing "
            "dataclasses.replace did not name its field. Unknown origin "
            "means kept, never dropped."
        ),
        fixed_by=(1001,),
        seed_tests=(
            "tests/test_flat_map_dependency_scope.py",
            "tests/test_exclude_header_flat_maps_integration.py",
        ),
        public_surfaces=("cli",),
        axes={
            "frontend": ("castxml", "clang"),
            "kind": ("constant", "typedef", "typedef_qualified", "semantic_ir"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "Attribution is runtime-only, so scoping a *loaded* "
                    "snapshot (whose maps decode as plain dicts) still "
                    "leaves its constants/typedefs untouched; nothing "
                    "enumerates AbiSnapshot fields to prove no further "
                    "kind is forwarded unfiltered."
                ),
                reference="abicheck/model/declaration_headers.py",
            ),
        ),
    ),
    BugClass(
        id="extraction.emulated_compiler_builtin_absent_from_frontend",
        invariant=(
            "A header-AST frontend that *emulates* another compiler makes the "
            "system headers take that compiler's branches, so every type those "
            "branches name as a builtin must exist in the frontend on every "
            "target it parses for -- or be supplied, inert wherever the frontend "
            "already provides it, and never attributed to the library's own "
            "surface. castxml emulating g++ >= 13 left glibc's `_Float128` "
            "(C++) and AArch64 `__Float32x4_t`/`__Float64x2_t` undefined on "
            "AArch64, so every C++ header reaching `<cwchar>` and every header "
            "reaching `<math.h>` failed to parse there."
        ),
        fixed_by=(1470,),
        seed_tests=(
            "tests/test_castxml_header_compat.py",
            "tests/test_family_f8_target_parity.py",
        ),
        public_surfaces=("cli",),
        axes={
            "frontend": ("castxml",),
            "target": ("x86_64", "aarch64"),
            "language": ("c", "c++"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "Only the builtins glibc's GCC branches name on the "
                    "targets this repo can reproduce (x86-64, AArch64 via a "
                    "cross toolchain) are covered; s390x, RISC-V and "
                    "LoongArch take the same `_Float128` block by the same "
                    "guard but are not exercised by a real castxml run."
                ),
                reference="abicheck/extract/castxml_header_compat.py",
            ),
            KnownGap(
                description=(
                    "castxml's `mangled` attribute for a function taking "
                    "`_Float128` is not the compiler's Itanium name on any "
                    "target, so such a function reads as not exported."
                ),
                reference="docs/contribute/known-gaps.md",
            ),
        ),
    ),
    BugClass(
        id="extraction.linker_summary_flag_read_as_the_fact",
        invariant=(
            "An artifact fact the binary records structurally (a relocation, a "
            "section, a segment) is read from that structure, not only from a "
            "summary flag some linker writes about it: a fact read from the "
            "flag alone is false wherever that linker omits the flag. GNU ld "
            "writes `DF_STATIC_TLS` for initial-exec TLS on x86-64 but not on "
            "AArch64, so `static_tls_introduced` never fired there."
        ),
        fixed_by=(1470,),
        seed_tests=(
            "tests/test_elf_static_tls.py",
            "tests/test_family_f8_target_parity.py",
        ),
        axes={
            "machine": ("x86_64", "aarch64"),
            "tls_model": (
                "global-dynamic",
                "local-dynamic",
                "initial-exec",
                "local-exec",
            ),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "Local-exec TLS linked into an AArch64 shared object "
                    "leaves neither a relocation nor the flag, so nothing in "
                    "the binary records it."
                ),
                reference="docs/contribute/known-gaps.md",
            ),
        ),
    ),
    BugClass(
        id="scoping.system_header_layout_unrecognized",
        invariant=(
            "Every layout a toolchain uses for the target's own system headers "
            "is recognized as a system root -- at any depth below it and "
            "through the `..` spelling the compiler reports -- while a project "
            "directory in the same position never is, so a dump keeps the "
            "same declarations whichever toolchain layout produced them. A "
            "Debian/Ubuntu cross toolchain's `/usr/<triple>/include` was not "
            "recognized, so a cross-target dump kept every libc/libstdc++ "
            "declaration (7,707 functions instead of 7)."
        ),
        fixed_by=(1470,),
        seed_tests=(
            "tests/test_cross_sysroot_system_headers.py",
            "tests/test_family_f8_target_parity.py",
        ),
        public_surfaces=("cli",),
        axes={
            "frontend": ("castxml",),
            "layout": (
                "usr/include",
                "usr/<triple>/include",
                "lib/gcc-cross/<triple>/<ver>/../../../../<triple>/include",
            ),
        },
    ),
    BugClass(
        id="extraction.aggregate_layout_inverted_by_line",
        invariant=(
            "A diagnostic raised while parsing the multi-header aggregate "
            "translation unit is attributed to the input whose include chain "
            "produced it by the file the aggregate frame includes, never by "
            "inverting the aggregate's line layout: the aggregate's writer "
            "owns that layout, and when castxml's gained a preamble include "
            "on its first line, the fallback's `line N -> header N-1` rule "
            "excluded the healthy neighbour of the failing header and the "
            "directory dump still failed. An error inside the preamble "
            "itself attributes to no header."
        ),
        fixed_by=(1470,),
        seed_tests=(
            "tests/test_unparseable_header_fallback.py",
            "tests/test_family_f8_target_parity.py",
        ),
        public_surfaces=("cli",),
        axes={
            "frontend": ("castxml",),
            "diagnostic_style": ("clang", "gcc"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "The clang backend's direct-inclusion-guard retry "
                    "(extract/headers/clang/error_header_retry.py) still maps "
                    "an aggregate line N to header N-1. It agrees with its two "
                    "writers today (dumper.py, clang_layout_tool.py: one "
                    "include per line, no preamble); a preamble there would "
                    "repeat this defect."
                ),
                reference="docs/contribute/known-gaps.md",
            ),
        ),
    ),
)
