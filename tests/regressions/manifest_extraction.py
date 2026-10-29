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
        id="extraction.castxml_vector_element_not_modelled",
        invariant=(
            "A castxml vector node resolves to the spelling clang gives for the same "
            "declaration, or stays unresolved; it is never a shared opaque spelling "
            "that hides a same-size element change through a header comparison."
        ),
        fixed_by=(1791,),
        seed_tests=("tests/test_castxml_vector_integration.py",),
        axes={
            "backend": ("castxml", "clang"),
            "vector_shape": ("typedef", "inline_field", "inline_param", "return"),
        },
    ),
    BugClass(
        id="extraction.castxml_function_type_collapsed_to_opaque_tag",
        invariant=(
            "A castxml function type spells its full declarator (return type, "
            "parameter types, variadic marker, calling-convention attributes, "
            "and the pointer/reference/cv/array declarator around it), so two "
            "callbacks compare equal exactly when their signatures are equal, "
            "wherever the callback appears: inline parameter, pointer to it, "
            "typedef, struct field or return."
        ),
        fixed_by=(1796,),
        seed_tests=(
            "tests/test_castxml_function_pointer_spelling.py",
            "tests/test_castxml_function_pointer_integration.py",
        ),
        axes={
            "signature_part": ("arity", "param_type", "return", "variadic", "cc"),
            "site": ("inline_param", "pointer_to_fp", "typedef", "field"),
        },
    ),
    BugClass(
        id="extraction.vector_type_collapsed_to_plain_array",
        invariant=(
            "A type whose identity is a DWARF attribute, not its shape, keeps "
            "that attribute in the recorded spelling: a DW_AT_GNU_vector array "
            "spells with its element and total byte size, so a same-size "
            "element, lane-count, or vector-versus-array change is a different "
            "type, never the same plain T[] spelling."
        ),
        fixed_by=(1785,),
        seed_tests=("tests/test_dwarf_vector_types.py",),
        axes={
            "backend": ("dwarf",),
            "vector_shape": ("element", "lane_count", "array_vs_vector"),
        },
    ),
    BugClass(
        id="extraction.compile_entry_collapsed_to_first_source",
        invariant=(
            "A compile-database entry yields one compile unit per source "
            "operand it names, each with the language the driver applies at "
            "that operand's own -x position; a link with no source operand "
            "yields no compile unit and one link unit; a single-source entry "
            "yields exactly the unit it always yielded."
        ),
        fixed_by=(1755,),
        seed_tests=("tests/test_compile_db_operand_routing.py",),
        axes={"sources": ("single", "multi", "link_only")},
    ),
    BugClass(
        id="extraction.target_flag_dropped_from_header_parse",
        invariant=(
            "Every target-machine -m flag a translation unit is compiled with "
            "(ISA, CPU, FPU, float ABI) reaches the header AST parse of that "
            "unit, under both castxml and clang, so the predefined feature "
            "macros a header selects its types by match the build that "
            "produced the binary. A unit with no -m flag gains none."
        ),
        fixed_by=(1769,),
        seed_tests=(
            "tests/test_header_target_isa_flags.py",
            "tests/test_family_f4_heuristics.py",
        ),
        axes={
            "frontend": ("castxml", "clang"),
            "isa": ("sse4.2", "avx", "avx2", "avx512f", "none"),
        },
    ),
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
            "tests/test_typedef_retention_instantiation_independent.py",
            "tests/test_field_shapes.py",
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
        id="extraction.msvc_include_operand_spelling_not_probed",
        invariant=(
            "Every spelling a CL-mode driver accepts for an include-search "
            "operand -- `/I` and `-I`, separate or joined, and `/external:I` "
            "separate or joined -- is recorded as a typed include-search "
            "operand, so the public-root probe reads the same directories the "
            "compile searches; a GNU driver's `-I` stays structured and is not "
            "re-probed. The `/I`-only scanner left `-I` and `/external:I` out, "
            "so a public header root reached only through them was missed."
        ),
        fixed_by=(1742,),
        seed_tests=("tests/test_compile_invocation_differential.py",),
        public_surfaces=(),
        axes={
            "driver": ("cl", "clang-cl", "g++"),
            "spelling": (
                "/I",
                "/Idir",
                "-I",
                "-Idir",
                "/external:I",
                "/external:Idir",
            ),
        },
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
        id="scoping.dependency_internals_in_contract",
        invariant=(
            "A dependency entity -- a record, a typedef, a function (hidden "
            "friends included) or a variable declared by a dependency's "
            "header, or a weak template instance in a dependency-only "
            "namespace -- that no public declaration of the library names and "
            "the library's binary does not define (a GLOBAL ELF export, a "
            "Mach-O export that is not a weak definition, any PE export) is "
            "recorded but out of contract (dependency_internal_unreachable), "
            "on a DWARF-only and on a header-derived snapshot alike; one the "
            "library names or defines stays in contract. A header-only SIMD "
            "dependency (intel/xvec) rebuilt -mavx2 against -mavx512f gated "
            "a header compare on ~90 of its own instances while the library's "
            "own break was 6 findings. A finding raised on a record follows the "
            "record's presence: judged on the side that declares it when only "
            "one does, internal on both when both do, so a record a release "
            "starts (or stops) naming publicly keeps its leak in contract."
        ),
        fixed_by=(1775, 1813, 1819),
        seed_tests=(
            "tests/test_header_dependency_internals.py",
            "tests/test_family_f1_evidence_ablation.py",
            "tests/test_contract_dependency_origin.py",
            "tests/test_header_dependency_internals_integration.py",
            "tests/test_dwarf_dependency_scope_integration.py",
            "tests/test_dependency_record_leak_integration.py",
        ),
        public_surfaces=("cli",),
        axes={
            "evidence": ("dwarf_only", "headers_stored", "headers_live"),
            "record_state_per_side": ("absent", "reached", "internal", "unread"),
            "entity": (
                "record",
                "typedef",
                "hidden_friend",
                "variable",
                "weak_instance",
            ),
            "layout": (
                "sibling_include_root",
                "nested_include_root",
                "no_recorded_include_root",
            ),
            "export_table": ("elf", "macho", "pe"),
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
    BugClass(
        id="extraction.recorded_scope_matches_parse_skip",
        invariant=(
            "A dump's recorded `dependency_scope` agrees with what its parse "
            "was allowed to drop, however dumps nest: a full-surface request "
            "(`include_dependencies=True`) parses with no streaming prune and "
            "no parse-time dependency skip even inside an enclosing scoped "
            "dump, and a scoped request may skip only what scoping with its "
            "own header roots drops afterwards. Both hybrid entry points "
            "(`service.run_dump` and `dumper.dump`) therefore parse both legs "
            "unscoped and scope the merged result once."
        ),
        fixed_by=(1258, 1505),
        seed_tests=(
            "tests/test_dump_extraction_scope.py",
            "tests/test_dependency_exclusion_scope.py",
            "tests/test_family_f2_route_parity.py",
        ),
        public_surfaces=("python-api",),
        axes={
            "outer_scope": ("none", "scoped", "full"),
            "inner_scope": ("scoped", "full"),
            "header_backend": ("hybrid",),
        },
    ),
    BugClass(
        id="extraction.default_extractor_lacks_the_promised_fact_family",
        invariant=(
            "A depth that promises a fact family runs, by default, an "
            "extractor that records it, and runs it in the mode its input's "
            "language needs: L4 source replay under the default frontend "
            "runs clang (macros, inline/template/constexpr bodies), not "
            "L2's castxml, which records none of them; a stated frontend "
            "still selects the extractor, resolved as L2 resolves it. "
            "castxml replays a C unit in C emulation (`--castxml-cc-gnu-c`), "
            "never C++ emulation, which fails it outright. A capability "
            "that holds only because a later, hidden re-run with another "
            "extractor overwrote a failed first one is not a capability."
        ),
        fixed_by=(1569,),
        seed_tests=(
            "tests/test_source_replay_extractor.py",
            "tests/test_castxml_source_emulation_mode.py",
            "tests/test_l4_default_extractor_end_to_end.py",
            "tests/test_family_f2_route_parity.py",
        ),
        public_surfaces=("cli", "python-api"),
        axes={
            "language": ("C", "C++"),
            "frontend": ("default", "castxml", "clang", "env"),
            "compiler_mode": ("gnu", "gnu-c", "msvc"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "Without clang on PATH, a default-frontend L4 replay "
                    "degrades to partial coverage rather than falling back "
                    "to castxml's declaration-only facts."
                ),
                reference="docs/learn/build-source-data.md",
            ),
        ),
    ),
    BugClass(
        id="package.ar_magic_is_not_deb",
        invariant=(
            "A file is a Debian package iff its first ar member is "
            "`debian-binary`; a plain ar archive (static .a / Rust .rlib) "
            "is never routed to the Debian extractor, whatever its member "
            "names or file suffix, in either the Python detector or the "
            "Action's shell operand predicate."
        ),
        # Filled with the PR that introduced this entry (see the PR body).
        fixed_by=(1623,),
        seed_tests=(
            "tests/test_deb_extractor_detection.py",
            "tests/test_cli_compare_bundle_facts_rejections_more.py",
            "tests/test_action_run_sh_helpers.py",
        ),
        axes={"sniffer": ("python-detector", "action-shell-predicate")},
    ),
    BugClass(
        id="templates.fn_type_arguments_counted_as_template_arguments",
        invariant=(
            "Template arity counts only top-level template arguments: a "
            "function type's parameter list (commas inside parentheses or "
            "brackets) and the `>` of a `->` arrow never change how many "
            "arguments a template takes, so a callback's signature change "
            "is not reported as a template gaining a mandatory parameter."
        ),
        fixed_by=(1623,),
        seed_tests=("tests/test_diff_templates_fn_type_arity.py",),
    ),
    BugClass(
        id="templates.parameter_template_args_read_as_family_membership",
        invariant=(
            "A declaration joins a template-instantiation family only when its "
            "own declared name carries template arguments: a template argument "
            "list inside a parameter or return type never makes a non-template "
            "function an instantiation, so a parameter type's mangling change is "
            "not reported as a missing instantiation of that function."
        ),
        fixed_by=(239,),
        seed_tests=(
            "tests/test_instantiation_family_name_scope.py",
            "tests/test_parameter_replacement_pairing.py",
            "tests/test_family_f3_identity.py",
        ),
    ),
    BugClass(
        id="identity.header_declaration_retype_read_as_removal",
        invariant=(
            "With headers on both sides, a removed and an added symbol that are "
            "the same header declaration -- declared name, written parameter and "
            "return spellings, declaring header -- are one parameter change when "
            "a parameter written the same resolves to a different type (its "
            "resolved record/enum, that record's layout, or a typedef's target "
            "changed). A changed written signature, or a mangling change the "
            "header evidence does not explain, stays a removal and an addition."
        ),
        fixed_by=(1779, 1812),
        seed_tests=(
            "tests/test_header_parameter_replacement_pairing.py",
            "tests/test_header_param_change_pairing_integration.py",
            "tests/test_closure_param_churn_pipeline.py",
            "tests/test_family_f3_identity.py",
        ),
    ),
    BugClass(
        id="templates.explicit_instance_removal_read_as_visibility_change",
        invariant=(
            "An instance the old header spelled explicitly (extern template, "
            "template instantiation, explicit specialization) is removed when the "
            "new header stops declaring it: its absence is a removed declaration, "
            "never an export change, whatever the primary template still declares. "
            "A producer that cannot read the declaration kind leaves the implicit "
            "path in force."
        ),
        fixed_by=(1807,),
        seed_tests=("tests/test_explicit_instantiation_removal.py",),
    ),
    BugClass(
        id="evidence.malformed_input_yields_value",
        invariant=(
            "A malformed input that has no exact interpretation -- a "
            "stack-consuming op on an insufficient stack, an opcode the "
            "evaluator does not implement, a missing or truncated operand -- "
            "yields an explicit unknown, never a plausible value. A guessed "
            "offset becomes a layout fact that can manufacture a field-offset "
            "break or hide a real one. The defect was an evaluator with no "
            "failure channel: an underflowing DW_OP_plus fell through to a "
            "constant (0x22 pushed as 34), a non-opcode item or an unknown "
            "tuple opcode was skipped (0), and a truncated LEB128 operand "
            "was read as its partial value."
        ),
        fixed_by=(1649,),
        seed_tests=(
            "tests/test_dwarf_location_expr.py",
            "tests/test_dwarf_snapshot.py",
            "tests/test_phase3_dwarf_helpers.py",
            # F1 harness cell: a malformed member location reads as unknown.
            "tests/test_family_f1_evidence_ablation.py",
            # A header that is not text is refused; a header that parses to zero
            # declarations is named per side, never a silent empty surface.
            "tests/test_cli_header_input_text_and_empty_surface.py",
        ),
        known_gaps=(
            KnownGap(
                description=(
                    "The evaluator implements only DW_OP_plus, "
                    "DW_OP_plus_uconst, DW_OP_constu, DW_OP_consts and "
                    "DW_OP_lit0..31. A member location using any other op "
                    "(minus, mul, deref, shl, ...) now reads as unknown instead "
                    "of an exact offset. Mainstream compilers do not emit these "
                    "in data_member_location, but layouts that depend on them "
                    "lose their field-offset evidence until the ops are "
                    "implemented."
                ),
                reference="docs/contribute/known-gaps.md (unfiled; no issue number yet)",
            ),
        ),
    ),
    BugClass(
        id="extraction.header_dialect_not_recovered_from_binary",
        invariant=(
            "A header parse of an ELF image with no build data uses the C++ "
            "standard and target ISA flags the image's own DWARF producer "
            "records, whenever the caller gave no explicit standard: a C++20 "
            "or C++23 image parses at gnu++20, and its recorded -m flags reach "
            "the frontend. The image's dialect refines a parse that is already "
            "C++ and never turns a C parse into C++, and a standard the caller "
            "gave always wins."
        ),
        fixed_by=(1778,),
        seed_tests=("tests/test_binary_dialect_integration.py",),
        public_surfaces=("cli",),
        axes={"dialect": ("c++17", "c++20", "c++23")},
    ),
    BugClass(
        id="symbol_rule.single_object_format",
        invariant=(
            "A symbol-level rule -- what an object exports (a hidden/private "
            "symbol is not an export), a symbol's linkage (a removed weak "
            "export carries it, so `binding: weak` applies), or whether an "
            "unread side's dependency types were classified -- must reach the "
            "same finding-class outcome on every object format for its twin "
            "input. A fact a format cannot supply is reported unknown (None, "
            "unread), never silently set to the value another format's "
            "default happens to be. Written for ELF (STV_HIDDEN, STB_WEAK, "
            ".dynsym) and implemented for ELF alone, the Mach-O side took "
            "N_PEXT exports as public, stamped no linkage (so `binding: weak` "
            "never matched a Mach-O removal), and read an unread side as an "
            "empty classification."
        ),
        fixed_by=(1629, 1630, 1819),
        seed_tests=(
            "tests/test_family_f8_symbol_rule_parity.py",
            "tests/test_macho_private_extern_export.py",
            "tests/test_macho_export_linkage_binding.py",
            "tests/test_dwarf_dependency_types.py",
        ),
        public_surfaces=("cli",),
        axes={"object_format": ("elf", "macho", "pe")},
        known_gaps=(
            KnownGap(
                description=(
                    "Mach-O exports are the union of the nlist and the dyld "
                    "export trie; the trie is not treated as the sole authority, "
                    "so a defined external the nlist marks N_EXT (without "
                    "N_PEXT) but the trie omits is still reported as an export."
                ),
                reference="abicheck/macho_metadata.py",
            ),
            KnownGap(
                description=(
                    "A PE export carries no linkage flag, so its binding stays "
                    "unknown: `binding: weak` never matches a PE removal, by "
                    "design (fail closed), and `binding: global` does not "
                    "either. A PE COMDAT-level weakness is not visible."
                ),
                reference="abicheck/workflows/dump/dependency_internals.py",
            ),
        ),
    ),
)
