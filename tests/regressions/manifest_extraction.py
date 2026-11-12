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
            "A castxml vector node, and a complex (float _Complex) or bit-precise "
            "integer (_BitInt(N)) node castxml cannot name, resolves to the spelling "
            "clang gives for the same declaration, or stays unresolved; it is never "
            "a shared opaque spelling (such as the literal 'Unimplemented') that "
            "hides a same-size element change through a header comparison."
        ),
        fixed_by=(1791,),
        seed_tests=(
            "tests/test_castxml_vector_integration.py",
            "tests/test_castxml_complex_spelling.py",
            "tests/test_graph_reconcile_fundamental_cli.py",
        ),
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
            "tests/test_function_type_calling_convention.py",
            "tests/test_dwarf_subroutine_calling_convention.py",
            "tests/test_type_spelling_differential_matrix.py",
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
        id="extraction.dwarf_array_bound_erased",
        invariant=(
            "A DWARF array type spells each dimension's bound, outermost "
            "first, as C writes it (char[8], int[2][3]); only an unbounded "
            "dimension spells []. A bound change is a member type change even "
            "when the record's size does not move, and a pre-generation-45 "
            "DWARF baseline's erased T[] against a fresh T[N] reads as "
            "extractor drift, never a break."
        ),
        fixed_by=(1958, 1962),
        seed_tests=(
            "tests/test_dwarf_array_extents.py",
            "tests/test_dwarf_erased_extent_generation_drift.py",
            "tests/test_type_spelling_differential_matrix.py",
        ),
        public_surfaces=("cli",),  # test_dwarf_array_extents runs `abicheck dump`
        axes={
            "compiler": ("gcc", "clang"),
            "packing": ("natural", "packed"),
            "array_shape": (
                "one_dim",
                "multi_dim",
                "array_of_struct",
                "flexible",
                "zero_length",
            ),
            "edit": (
                "shrink_in_padding",
                "grow",
                "reshape",
                "inner_bound",
                "unchanged",
            ),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "castxml spells a multi-dimensional array with its bounds "
                    "reversed (int[3][2] for int[2][3])."
                ),
                reference="docs/contribute/known-gaps.md",
            ),
            KnownGap(
                description=(
                    "No seed test drives a DWARF array-bound change through "
                    "`abicheck.service` (python-api); the CLI is reached only "
                    "by `abicheck dump`, and the comparison by "
                    "`checker.compare()`."
                ),
                reference="docs/contribute/plans/bug-class-regression-testing.md#phase-5",
            ),
        ),
    ),
    BugClass(
        id="extraction.dwarf_out_of_line_definition_dropped",
        invariant=(
            "A DWARF-only snapshot records a function defined outside its "
            "class or namespace -- a definition DIE that names its declaration "
            "only through DW_AT_specification (and DW_AT_abstract_origin for a "
            "concrete constructor instance) -- with its declaration's name, "
            "scope, virtuality, access and the cv/ref qualifiers of its this "
            "parameter, so a method edit reads as the kind the header lane "
            "reports (func_cv_changed, func_static_changed, "
            "func_virtual_became_pure), not as an ELF removal plus addition."
        ),
        fixed_by=(1958, 1962),
        seed_tests=(
            "tests/test_dwarf_member_functions.py",
            "tests/test_dwarf_semantic_ir.py",
            "tests/test_depth_projection_matches_shallow_dump.py",
        ),
        public_surfaces=(),  # the seed tests call dump/compare directly
        axes={
            "compiler": ("gcc", "clang"),
            "edit": (
                "const_drop",
                "const_add",
                "volatile_drop",
                "static_to_member",
                "return_retype",
                "became_pure",
                "unchanged",
            ),
            "scope": ("class_member", "namespace_function", "ctor_concrete_instance"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "GCC writes DW_VIRTUALITY_virtual for a pure virtual, so a "
                    "GCC-built DWARF snapshot cannot report func_virtual_became_pure."
                ),
                reference="docs/contribute/known-gaps.md",
            ),
            KnownGap(
                description=(
                    "No seed test reaches a public surface (CLI or "
                    "`abicheck.service`): the member-function shapes are "
                    "dumped and compared through `dump()`/`checker.compare()`."
                ),
                reference="docs/contribute/plans/bug-class-regression-testing.md#phase-5",
            ),
        ),
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
            "changed), and so are the removal and addition of the one function by "
            "that name whose written signature was rewritten in the same header "
            "with the same return type, as the binary-only rule pairs them. A "
            "retype to a record the old release never declared (case189), an "
            "overloaded name, or a mangling change the header evidence does not "
            "explain stays a removal and an addition. "
            "A fundamental type (float _Complex, __int128, a vector or SIMD type, "
            "or a pointer, cv or array of one) is never paired as a rename or "
            "move: a builtin change is a true removal and addition, not 'the same "
            "declaration, renamed'."
        ),
        fixed_by=(1779, 1812, 1958, 1965),
        seed_tests=(
            "tests/test_header_parameter_replacement_pairing.py",
            "tests/test_header_param_change_pairing_integration.py",
            "tests/test_closure_param_churn_pipeline.py",
            "tests/test_family_f3_identity.py",
            "tests/test_graph_reconcile_fundamental.py",
            "tests/test_graph_reconcile_fundamental_cli.py",
        ),
    ),
    BugClass(
        id="classification.removed_virtual_slot_read_as_plain_removal",
        invariant=(
            "A virtual method removed together with its vtable slot -- an entry "
            "of every old vtable that held it, gone from each of those records' "
            "shorter new vtable, with no new declaration or export left -- "
            "reports func_virtual_removed beside the function's removal, on the "
            "header and DWARF lanes alike; removing an override (the slot "
            "stays) or devirtualizing a method that stays exported does not."
        ),
        fixed_by=(1958, 1965),
        seed_tests=("tests/test_virtual_slot_removal.py",),
        public_surfaces=("cli", "python-api"),
        axes={
            "lane": ("dwarf", "castxml", "clang"),
            "shape": (
                "last_virtual_removed",
                "middle_virtual_removed",
                "override_removed",
                "devirtualized",
                "inherited_by_derived",
            ),
        },
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
            "gave always wins. Likewise a Windows image's C++ ABI: the clang "
            "frontend parses for the ABI the exports are mangled for (MinGW "
            "Itanium vs MSVC), not clang's own Windows default, unless the "
            "caller named a target. Of the producer's -m options, multilib "
            "options (-m16/-m32/-m64/-mx32) and byte-order options "
            "(-mlittle-endian, -mbig-endian, -mel, -meb, -mlittle, -mbig) never "
            "reach the frontend, and -mabi= is dropped only for an AArch64 "
            "image (by ELF e_machine), whose GCC spelling castxml's clang "
            "refuses; every other -m option, -mabi= elsewhere included, is "
            "forwarded unless the caller set it. Instead of forwarding byte "
            "order, a parse whose frontend byte order differs from the image's "
            "EI_DATA is refused with a named reason unless the caller aimed it "
            "at a target (cross prefix/path, -target, or a byte-order flag)."
        ),
        # #1842: a Windows-default clang parsed a MinGW DLL for MSVC; every
        # declaration mangled '?...' matched no export.
        fixed_by=(1778, 1842),
        seed_tests=(
            "tests/test_binary_dialect.py",
            "tests/test_binary_dialect_target_abi.py",
            "tests/test_binary_dialect_integration.py",
            "tests/test_clang_binary_abi_target.py",
        ),
        public_surfaces=("cli",),
        axes={
            "dialect": ("c++17", "c++20", "c++23"),
            "windows_cxx_abi": ("itanium", "msvc", "c-only", "mixed"),
            "target_arch": ("x86_64", "aarch64", "riscv64", "ppc64", "mips64el"),
            "byte_order": ("matching", "mismatched", "mismatched-aimed"),
        },
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
                    "A PE export carries no linkage flag, so its binding stays "
                    "unknown: `binding: weak` never matches a PE removal, by "
                    "design (fail closed), and `binding: global` does not "
                    "either. A PE COMDAT-level weakness is not visible."
                ),
                reference="abicheck/workflows/dump/dependency_internals.py",
            ),
        ),
    ),
    BugClass(
        id="extraction.backend_fact_family_parity",
        invariant=(
            "A header backend that cannot read a fact family the other backend "
            "records gets it from its own toolchain instead of leaving the field "
            "empty, and both backends store each layout fact in the model's unit: "
            "the clang backend sizes records from clang's own "
            "-fdump-record-layouts when DWARF has only a declaration stub -- C "
            "records, empty records, empty bases and the members of an unnamed "
            "member aggregate included, a virtual base never in base_offsets -- "
            "records an explicitly instantiated class template as its own X<Args> "
            "record, records a field declared through an alias template by its "
            "target, and a leak path written as a partial scope (detail::Impl) "
            "reaches the changed record through C++ name lookup, never a spelling "
            "match; castxml stores base offsets in bits, as DWARF and clang do. "
            "Every size and offset either backend reports equals sizeof/offsetof "
            "of a compiled program, and for every library-owned record of every "
            "catalog case the two backends agree, except for a declared, tracked "
            "naming limitation."
        ),
        fixed_by=(1918,),
        seed_tests=(
            "tests/test_clang_record_layout_dump.py",
            "tests/test_family_f8_target_parity.py",
            "tests/test_family_f8_catalog_backend_parity.py",
            # clang records a static data member's access as castxml does.
            "tests/test_clang_variable_access_parity.py",
            # the native gcc backend records constraints as conjunctive sets.
            "tests/test_native_gcc_concept_constraints.py",
            # an overload set read from real symbols (ctor C1/C2) is one declaration each.
            "tests/test_overload_ambiguity_symbol_variants.py",
            # castxml reads noexcept and C++20 constraints from the clang AST
            # of the same header; without clang they read not observed.
            "tests/test_noexcept_header_backends.py",
            "tests/test_castxml_clang_semantics_join.py",
        ),
        public_surfaces=("cli", "python-api"),
        axes={
            "frontend": ("castxml", "clang"),
            "language": ("c", "c++"),
            "record_shape": (
                "plain",
                "polymorphic",
                "derived",
                "explicit_instantiation",
                "bitfield",
                "empty",
                "empty_base",
                "virtual_base",
                "anonymous_member",
                "c_typedef_struct",
            ),
            "leak_path": ("inheritance", "by_value_field", "vtable"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "A record sizeof cannot name (private nested, unnamed) still "
                    "depends on DWARF."
                ),
                reference="docs/contribute/known-gaps.md",
            ),
            KnownGap(
                description=(
                    "castxml elides defaulted template arguments and inline "
                    "namespaces from record names; the clang backend records only "
                    "explicitly instantiated specializations (BACKEND_EXCEPTIONS "
                    "in the catalog harness)."
                ),
                reference="docs/contribute/known-gaps.md",
            ),
        ),
    ),
    BugClass(
        id="extraction.clang_ast_shape_version_drift",
        invariant=(
            "The clang header backend yields the same surface from every clang "
            "JSON AST shape it can receive: a link a newer clang spells "
            "differently (clang 22's isTagOwned tag type for clang 21's "
            "ElaboratedType.ownedTagDecl) is read in both spellings, never as "
            "absent, and the opt-in dependency pruner never elides a C++ "
            "method-shaped node, including one nested inside a prunable "
            "function or variable. A base specifier resolves to its record "
            "whether or not clang wrote a fully qualified desugaredQualType "
            "(clang 22 writes a relative base only as written), and a base "
            "that does not resolve makes the vtable PARTIAL, never a "
            "confirmed list. Oracle: an AST rewritten into the other "
            "release's shape (from LLVM's JSONNodeDumper), the "
            "method-shaped node count with pruning off, and an independent "
            "enumeration of C++ name lookup over a namespace tree."
        ),
        fixed_by=(1921,),
        seed_tests=(
            "tests/test_clang_ast_shape_version_drift.py",
            "tests/test_clang_relative_base_vtable.py",
            "tests/test_clang_vtable_base_migration.py",
            # F8 harness cell: castxml and the clang on PATH name the same
            # typedef'd anonymous tags from one C header.
            "tests/test_family_f8_target_parity.py",
            # The live half: the clang-latest lane runs these against the
            # newest released LLVM next to the distro clang.
            "tests/test_clang_header_backend_integration.py",
        ),
        axes={
            "ast_shape": ("clang<=21", "clang>=22"),
            "tag": ("record", "enum"),
            "pruned_holder": ("FunctionDecl", "VarDecl"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "Only shapes a pinned clang release has shown are covered; a "
                    "future release's change is caught by the clang-latest CI "
                    "lane once its pin is bumped, not before."
                ),
                reference=".github/workflows/clang-latest.yml",
            ),
        ),
    ),
    BugClass(
        id="extraction.header_surface_discarded_on_export_mismatch",
        invariant=(
            "A PE or Mach-O dump with headers keeps every header declaration "
            "and type the headers parsed, whether or not any declaration's "
            "linker spelling meets an export; and a header declaration binds "
            "to exactly the export it names, whatever mangling scheme each "
            "side spells it in (Itanium, MSVC, x86 C decoration), and to no "
            "other. The same sources and headers built for ELF, PE and "
            "Mach-O therefore yield the same verdict and findings."
        ),
        fixed_by=(1967,),
        seed_tests=(
            "tests/test_header_export_binding.py",
            "tests/test_pe_macho_header_binding_integration.py",
        ),
        public_surfaces=("cli",),
        axes={
            "frontend": ("castxml", "clang"),
            "format": ("elf", "pe", "macho"),
            "mangling": ("itanium->msvc", "msvc->itanium", "c->x86-decorated"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "PE overloads whose parameters are not builtin types, and "
                    "MSVC operators/structors/template instantiations, stay "
                    "unbound and are compared from the export table only."
                ),
                reference="docs/contribute/known-gaps/pe-overloads-with-class-typed-parameters-stay-unbound.md",
            ),
        ),
    ),
    BugClass(
        id="extraction.dwarf_lane_drops_a_recorded_fact",
        invariant=(
            "A DWARF-only snapshot records every fact the binary and its debug "
            "info carry that the other lanes record: an ABI-relevant export no "
            "DWARF declaration names still gets an export-table-only record (a "
            ".symver implementation, an IFUNC), a variadic subprogram's "
            "DW_TAG_unspecified_parameters sets is_variadic, and a plain-char "
            "signedness flag in DW_AT_producer is read; and an export-table-only "
            "record paired with a described declaration compares no signature."
        ),
        # Found by the generated edit corpus (#1958) while expanding its operators.
        fixed_by=(1958, 1980),
        seed_tests=("tests/test_dwarf_lane_extractor_parity.py",),
        axes={
            "producer": ("gcc", "clang"),
            "fact": (
                "export without a same-named DIE",
                "variadic arity",
                "char signedness",
            ),
            "direction": ("added", "removed"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "clang does not record command-line switches in "
                    "DW_AT_producer by default, so a clang-built char-signedness "
                    "flip is read only through the plain-char base type's "
                    "DW_ATE encoding, which is not extracted yet."
                ),
                reference="docs/contribute/known-gaps.md",
            ),
        ),
    ),
    BugClass(
        id="extraction.variable_alignment_misread_as_declared",
        invariant=(
            "A variable's alignment is recorded as declared (Fact PRESENT) only "
            "where the evidence states it, and a value that only bounds it is "
            "recorded PARTIAL and never vetoes an address reduction: castxml, "
            "which writes no align on a Variable or Typedef, records the "
            "type's natural alignment as a lower bound wherever an alignment "
            "specifier can apply; a DWARF 5 variable without DW_AT_alignment "
            "records its type's default (DWARF states it), as a lower bound "
            "on a GCC declaration/definition split; DWARF 4 and records stay "
            "unknown. A declared alignment change is then reported at every "
            "level whose evidence states declarations, and an unchanged one "
            "never is."
        ),
        # Found by the generated edit corpus (#1958): a dropped aligned(64)
        # read NO_CHANGE under castxml headers, a raised one under DWARF.
        fixed_by=(1958,),
        seed_tests=(
            "tests/test_variable_declared_alignment.py",
            "tests/test_variable_declared_alignment_integration.py",
            "tests/test_address_alignment_bound.py",
            "tests/test_rebuild_toolchain_drift_integration.py",
        ),
        axes={
            "compiler": ("gcc", "clang"),
            "evidence": ("binary", "dwarf", "headers"),
            "frontend": ("castxml", "clang"),
            "declared_alignment": ("none", "16", "64"),
            "dwarf_version": ("4", "5"),
            "die_form": ("standalone", "completes a declaration", "declaration"),
        },
        known_gaps=(
            KnownGap(
                description=(
                    "Under castxml headers a specifier is read only as a lower "
                    "bound: a dropped one is reported but demoted with the "
                    "unknown-requirement rule, and a raised one reads "
                    "NO_CHANGE."
                ),
                reference="docs/contribute/known-gaps.md",
            ),
            KnownGap(
                description=(
                    "GCC writes a specifier spelled only on a definition that "
                    "completes a declaration on neither DIE, so DWARF records "
                    "the default as a lower bound and a reduction is demoted."
                ),
                reference="docs/contribute/known-gaps.md",
            ),
        ),
    ),
    BugClass(
        id="extraction.tool_probe_answer_depends_on_host_load",
        invariant=(
            "An external-tool call whose answer is recorded (a compiler's "
            "target, platform, family, version or default standard, its include "
            "search path, a castxml version, a demangled name, a git commit) "
            "gives up only on a hung tool: every such call is bounded by the one "
            "shared hang guard (deadline.PROBE_HANG_TIMEOUT_S) or the active "
            "--budget, so a working tool that answers slowly on a loaded host "
            "records the same fact it records on an idle one."
        ),
        fixed_by=(1991,),
        seed_tests=("tests/test_tool_probe_hang_guard.py",),
        public_surfaces=(),
        axes={
            "probe": (
                "clang_target",
                "dumpmachine",
                "target_platform",
                "compiler_family",
                "compiler_version",
                "language_standard",
            ),
            "tool_latency": ("within_old_bound", "past_old_bound"),
            "call_shape": (
                "timeout_kwarg",
                "deadline_scope",
                "local_bound",
                "module_constant",
                "process_wait",
            ),
        },
    ),
)
