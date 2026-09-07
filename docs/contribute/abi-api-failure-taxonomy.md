---
doc_type: contributor
audience:
  - maintainer
level: advanced
lifecycle: active
generated: false
---

# ABI/API failure taxonomy

This is Phase 1 of
[ABI/API knowledge and corpus](plans/abi-api-knowledge-and-corpus.md): a
top-down enumeration of the *domain* of known ways a compiled or
source-level compatibility contract can break, organized the way the field
itself is organized — not the way `catalog/` happens to be organized, and
not the way abicheck's own `ChangeKind` registry (`abicheck/model/
change_catalog/`) names things.

**This taxonomy is deliberately independent of abicheck.** It names
mechanisms a reader with no abicheck installed would recognize from reading
the Itanium C++ ABI, the System V / Windows x64 / AArch64 calling-convention
specifications, `ld.so(8)`, CPython's stable-ABI documentation, or the
Linux kernel's module-versioning scheme — not abicheck's detectors. That
independence is the point: mapping each leaf mechanism to which
`docs/learn/` page explains it, which `catalog/` case demonstrates it, and
which `ChangeKind`/detector claims to observe it is the next plan phase
(Phase 2 — "map existing knowledge and corpus onto the taxonomy"), not this
one. A leaf mechanism existing here is not a claim that abicheck detects it,
documents it, or has a corpus case for it; several are known to be
undetectable by any static tool (see Phase 3's `KNOWN_UNDETECTABLE` status
in the plan). This document changes no code path, detector, or default.

## Scope and stability of ids

Every leaf mechanism carries a stable `id` of the form
`<branch-slug>.<leaf-slug>`, a one- or two-sentence description, and the
platforms and/or languages it applies to. An id, once published, is never
renumbered or reused for a different mechanism — later phases (and any
future revision of this document) may add a leaf, split one leaf into two
when a real distinction is found, or mark one `superseded-by: <new-id>`, but
never silently repoint an existing id at different content. This mirrors
the stability contract `AGENTS.md` already states for `ChangeKind` values
(additive, never renumbered) applied to a taxonomy that has no equivalent
mechanical enforcement (no `changekind-partition`-style gate exists for this
document — see the plan's own "Risk" section on why this taxonomy cannot be
checked against an external authority the way the `ChangeKind` registry
can).

"Platforms" below uses the same vocabulary as `docs/reference/platforms.md`
(ELF, PE/COFF, Mach-O); "Languages" names the source language(s) the
mechanism is intrinsic to, not merely "any language abicheck happens to
support."

## 1. Symbol identity (`symbol-identity`)

How a compiled artifact names an exported entity, and the ways that naming
can shift without necessarily changing what the entity *does*.

| id | Mechanism | Description | Platforms / languages |
|---|---|---|---|
| `symbol-identity.removal` | Symbol removal | An exported symbol present in the old artifact is absent from the new one. | ELF, PE/COFF, Mach-O · C, C++ |
| `symbol-identity.rename` | Symbol rename | The source-level name of an exported entity changes, changing the exported symbol name with it. | ELF, PE/COFF, Mach-O · C, C++ |
| `symbol-identity.mangling-scheme-change` | Mangling-scheme change | The compiler's C++ name-mangling scheme itself changes (a mangling-ABI revision), changing every affected exported symbol's spelling without any source edit. | ELF (Itanium), PE/COFF (MSVC) · C++ |
| `symbol-identity.mangling-signature-change` | Mangling-signature change | A detail folded into the mangled name (parameter type, template argument, cv-qualifier, ref-qualifier) changes, producing a different exported symbol even though the declaration reads as "the same function" at a glance. | ELF, PE/COFF, Mach-O · C++ |
| `symbol-identity.linkage-change` | Linkage change | A symbol's linkage changes between external and internal (`static`, an anonymous namespace, adding/removing `extern "C"`), changing whether — and how — it is exported. | ELF, PE/COFF, Mach-O · C, C++ |
| `symbol-identity.visibility-change` | Visibility attribute change | An explicit visibility annotation changes (`__attribute__((visibility(...)))`, `-fvisibility`, `__declspec(dllexport/dllimport)`), changing whether a symbol is exported at all. | ELF, PE/COFF, Mach-O · C, C++ |
| `symbol-identity.version-node-change` | Symbol-version-node change | A symbol moves to a different version node in a linker version script, or a version node is renamed or removed. | ELF | C, C++ |
| `symbol-identity.weak-strong-binding-change` | Weak/strong binding change | A symbol's binding changes between weak and strong (`STB_WEAK`/`STB_GLOBAL`), altering override and interposition behavior at load time. | ELF, Mach-O · C, C++ |
| `symbol-identity.alias-change` | Symbol alias change | A symbol alias (`__attribute__((alias(...)))`, a `.symver` directive) is added, removed, or repointed at a different definition. | ELF | C, C++ |
| `symbol-identity.ifunc-resolver-change` | Indirect-function resolver change | A GNU indirect function (`ifunc`) resolver's selection logic changes, altering which concrete implementation binds to an unchanged exported name at load time. | ELF (glibc) · C, C++ |

## 2. Function calling contract (`calling-contract`)

The machine-level agreement between caller and callee for one function:
what arguments look like, where they go, and how control and exceptions
flow across the call.

| id | Mechanism | Description | Platforms / languages |
|---|---|---|---|
| `calling-contract.parameter-type-change` | Parameter type change | A parameter's type changes in a way that changes its representation, size, or classification. | ELF, PE/COFF, Mach-O · C, C++ |
| `calling-contract.parameter-count-change` | Parameter count (arity) change | The number of parameters changes. | ELF, PE/COFF, Mach-O · C, C++ |
| `calling-contract.parameter-order-change` | Parameter order change | Parameters are reordered without changing the set of types, shifting which argument each register/stack slot carries. | ELF, PE/COFF, Mach-O · C, C++ |
| `calling-contract.return-type-change` | Return type change | The return type changes, including a change between register-returned and memory-returned (via hidden pointer) classification. | ELF, PE/COFF, Mach-O · C, C++ |
| `calling-contract.calling-convention-change` | Calling-convention change | The calling convention changes (`cdecl`/`stdcall`/`fastcall`/`thiscall`/`vectorcall`, or a register-passing ABI variant), changing how arguments and the return value move between caller and callee. | PE/COFF (MSVC conventions), ELF (SysV/ARM register-passing variants) · C, C++ |
| `calling-contract.exception-specification-abi-change` | Exception-specification ABI change | An exception specification changes in a way that affects generated unwind tables or call-site codegen — `noexcept` addition/removal, or (pre-C++17) a dynamic `throw()` specification's removal. | ELF, PE/COFF, Mach-O · C++ |
| `calling-contract.variadic-change` | Variadic/fixed-arity change | A function changes between fixed arity and variadic (`...`), changing argument-passing convention for any additional arguments. | ELF, PE/COFF, Mach-O · C, C++ |
| `calling-contract.implicit-this-change` | Implicit `this` change | A member function becomes `static` or vice versa, adding or removing the hidden receiver parameter. | ELF, PE/COFF, Mach-O · C++ |
| `calling-contract.aggregate-classification-change` | Aggregate-classification change | A struct/class passed or returned by value changes which of its fields determine its ABI classification (e.g. Itanium/SysV register-class assignment), changing how it is passed even though its declared signature type is unchanged. | ELF (SysV/AAPCS classification), PE/COFF · C, C++ |
| `calling-contract.pass-by-value-threshold-change` | Pass-by-value size-threshold crossing | A by-value aggregate parameter or return type grows or shrinks across the platform's register-vs-memory (invisible-reference) threshold. | ELF, PE/COFF, Mach-O · C, C++ |

## 3. Data layout (`data-layout`)

How the bytes of a type are arranged in memory — the layer every other
object-model and calling-convention rule ultimately rests on.

| id | Mechanism | Description | Platforms / languages |
|---|---|---|---|
| `data-layout.size-change` | Type size change | A type's overall size changes. | ELF, PE/COFF, Mach-O · C, C++ |
| `data-layout.alignment-change` | Alignment change | A type's required alignment changes (`alignas`, `#pragma pack`, or a field-type change that alters natural alignment). | ELF, PE/COFF, Mach-O · C, C++ |
| `data-layout.field-offset-change` | Field offset change | A field's byte offset within its containing type changes. | ELF, PE/COFF, Mach-O · C, C++ |
| `data-layout.field-reorder` | Field reorder | Fields are reordered without changing the set of field types, shifting offsets. | ELF, PE/COFF, Mach-O · C, C++ |
| `data-layout.padding-change` | Packing/padding change | Compiler packing directives change (`#pragma pack`, `__attribute__((packed))`), changing inter-field padding without a field-list change. | ELF, PE/COFF, Mach-O · C, C++ |
| `data-layout.bitfield-allocation-change` | Bitfield allocation change | A bitfield's declared width, allocation order, or storage-unit boundary changes, shifting which bits carry which field. | ELF, PE/COFF, Mach-O · C, C++ |
| `data-layout.enum-underlying-type-change` | Enum underlying-type change | An enum's underlying integer type changes, changing its size and/or signedness. | ELF, PE/COFF, Mach-O · C, C++ |
| `data-layout.union-active-member-change` | Union member change | A union's member set changes (add/remove/reorder/retype), changing the union's size and/or alignment. | ELF, PE/COFF, Mach-O · C, C++ |
| `data-layout.embedded-array-dimension-change` | Embedded fixed-array dimension change | A fixed-size array field's declared dimension changes, changing the size of every type that embeds it by value. | ELF, PE/COFF, Mach-O · C, C++ |
| `data-layout.flexible-array-member-change` | Flexible array member change | A C99 flexible array member is added, removed, or retyped, changing the struct's own fixed size and its trailing-allocation contract. | ELF, PE/COFF, Mach-O · C |
| `data-layout.tail-padding-reuse-change` | Tail-padding reuse change | Whether a type's trailing padding may be reused by a derived class or subsequent member changes — C++ empty-base optimization or `[[no_unique_address]]` adoption/removal. | ELF, PE/COFF, Mach-O · C++ |

## 4. C++ object model (`cpp-object-model`)

Mechanisms specific to C++ classes with virtual dispatch and inheritance —
layered on top of `data-layout`, but with their own failure modes.

| id | Mechanism | Description | Platforms / languages |
|---|---|---|---|
| `cpp-object-model.base-class-addition-removal` | Base class addition/removal | A base class is added or removed, changing the derived class's layout and/or subobject set. | ELF, PE/COFF, Mach-O · C++ |
| `cpp-object-model.virtual-function-insertion-reorder` | Virtual function insertion/reorder | A new virtual function is inserted before an existing one, or existing virtual functions are reordered, shifting every subsequent function's vtable slot index. | ELF, PE/COFF, Mach-O · C++ |
| `cpp-object-model.virtual-function-removal` | Virtual function removal | A virtual function is removed from the class, removing (and potentially collapsing) its vtable slot. | ELF, PE/COFF, Mach-O · C++ |
| `cpp-object-model.pure-virtual-transition` | Pure/non-pure virtual transition | A virtual function changes between pure (`= 0`) and providing a definition, or vice versa. | ELF, PE/COFF, Mach-O · C++ |
| `cpp-object-model.multiple-inheritance-thunk-change` | Multiple-inheritance thunk/vtable change | Restructuring multiple or virtual inheritance changes the thunk layout or the number/order of vtables a class carries. | ELF, PE/COFF, Mach-O · C++ |
| `cpp-object-model.rtti-representation-change` | RTTI representation change | The `type_info` layout or its mangled type-name changes, affecting `dynamic_cast`/`typeid` behavior across compiled units. | ELF (Itanium RTTI), PE/COFF (MSVC RTTI) · C++ |
| `cpp-object-model.virtual-base-layout-change` | Virtual base layout change | A virtual base's offset or the virtual-base table (vbtable/vbase table) layout changes. | ELF, PE/COFF, Mach-O · C++ |
| `cpp-object-model.covariant-return-type-change` | Covariant return-type change | An overriding virtual function's covariant return type changes in a way that affects the thunk needed to adjust the returned pointer. | ELF, PE/COFF, Mach-O · C++ |
| `cpp-object-model.vptr-presence-change` | Vptr presence change | A class gains or loses its first virtual function, adding or removing the implicit vtable pointer and shifting every subsequent member's offset. | ELF, PE/COFF, Mach-O · C++ |

## 5. Inline/template/source ABI (`source-abi`)

Mechanisms where the "ABI" is determined by what got compiled into each
consuming translation unit, not by one fixed compiled artifact.

| id | Mechanism | Description | Platforms / languages |
|---|---|---|---|
| `source-abi.inline-function-body-change` | Inline function body change | An inline (or header-defined) function's body differs across translation units that each compile their own copy, an ODR-relevant divergence rather than a single symbol change. | ELF, PE/COFF, Mach-O · C, C++ |
| `source-abi.template-instantiation-set-change` | Template instantiation-set change | The set of template specializations actually emitted (implicitly or via explicit instantiation) changes, changing which mangled symbols exist to satisfy a consumer's use. | ELF, PE/COFF, Mach-O · C++ |
| `source-abi.odr-violation` | One Definition Rule violation | The same entity is defined differently in different translation units or shared objects linked into one program, an undefined-behavior condition rather than a single localized change. | ELF, PE/COFF, Mach-O · C, C++ |
| `source-abi.constexpr-evaluation-change` | `constexpr` evaluation change | A `constexpr` value baked into consumers at their own compile time changes, so consumers compiled against the old value silently disagree with a new binary — without themselves being recompiled. | C++ |
| `source-abi.macro-driven-layout-change` | Macro-driven layout/signature change | A feature-flag macro alters a struct's layout or a function's signature depending on the including translation unit's own macro state at compile time. | C, C++ |
| `source-abi.default-template-argument-change` | Default template-argument change | A template's default argument changes, changing which specialization (and mangled name) an unqualified use resolves to. | C++ |

## 6. Export/public-surface contract (`export-surface`)

Whether what a header declares, what a binary actually exports, and what
consumers actually use agree with each other.

| id | Mechanism | Description | Platforms / languages |
|---|---|---|---|
| `export-surface.header-export-divergence` | Header/export divergence | A symbol is declared in a public header but not actually present in the binary's export table, or vice versa (exported but undeclared). | ELF, PE/COFF, Mach-O · C, C++ |
| `export-surface.visibility-demotion-without-header-change` | Visibility demotion without header change | A symbol's exported status changes purely through build flags or a version-script edit, with no corresponding header change to signal it. | ELF, PE/COFF, Mach-O · C, C++ |
| `export-surface.accidental-export` | Accidental export | An internal/private symbol is unintentionally exported (e.g. missing `static`, an omitted version-script entry) and becomes a de facto contract once consumers start using it. | ELF, PE/COFF, Mach-O · C, C++ |
| `export-surface.version-script-map-change` | Version-script/export-map change | A linker version script or module-definition (`.def`) file changes which symbols are exported, independent of the source code. | ELF (version scripts), PE/COFF (`.def` files) · C, C++ |
| `export-surface.documented-contract-drift` | Documented-contract drift | The project's documented/declared public contract and its actual observable surface diverge over time, e.g. through accretion of unreviewed exports. | ELF, PE/COFF, Mach-O · C, C++ |

## 7. Dynamic linker contract (`dynamic-linker`)

Mechanisms specific to how a dynamic loader resolves and binds a shared
library at load or call time, independent of any one symbol's own contract.

| id | Mechanism | Description | Platforms / languages |
|---|---|---|---|
| `dynamic-linker.soname-change` | SONAME change | A shared library's `SONAME` (or Mach-O install name) changes, changing the dependency string consumers must resolve against. | ELF (`DT_SONAME`), Mach-O (install name) |
| `dynamic-linker.symbol-versioning-scheme-change` | Symbol-versioning scheme change | The overall structure of version nodes across a release changes (nodes added, removed, or restructured), rather than one symbol moving between existing nodes. | ELF |
| `dynamic-linker.dt-needed-change` | `DT_NEEDED`/dependency-list change | A dependency is added, removed, or reordered in the binary's needed-library list. | ELF (`DT_NEEDED`), PE/COFF (import table), Mach-O (load commands) |
| `dynamic-linker.rpath-runpath-change` | `RPATH`/`RUNPATH` change | The library search path embedded in the binary changes, changing which on-disk library actually satisfies a dependency at load time. | ELF |
| `dynamic-linker.binding-mode-change` | Binding-mode change | The binding mode changes between lazy (PLT-resolved on first call) and eager (`BIND_NOW`/full RELRO), changing when a missing symbol is discovered. | ELF |
| `dynamic-linker.plt-got-representation-change` | PLT/GOT representation change | The PLT/GOT structure or relocation kind changes (e.g. `IRELATIVE` relocations introduced by `ifunc` adoption), changing load-time behavior without changing any symbol's name. | ELF |

## 8. Dependency ABI (`dependency-abi`)

How a break in a library the artifact under test links against propagates
to that artifact's own consumers.

| id | Mechanism | Description | Platforms / languages |
|---|---|---|---|
| `dependency-abi.transitive-break` | Transitive dependency break | A linked dependency itself breaks ABI, propagating the incompatibility to every consumer of the artifact under test without that artifact's own source changing at all. | ELF, PE/COFF, Mach-O · C, C++ |
| `dependency-abi.version-range-widening` | Dependency version-range widening | A dependency version constraint is loosened enough to admit a version that is not, in fact, ABI-compatible with the versions previously admitted. | ELF, PE/COFF, Mach-O · C, C++ |
| `dependency-abi.incompatible-substitution` | ABI-incompatible dependency substitution | A build substitutes a different, ABI-incompatible provider of nominally the same dependency (e.g. a different C library, allocator, or vendored fork). | ELF, PE/COFF, Mach-O · C, C++ |
| `dependency-abi.linking-mode-change` | Dependency linking-mode change | A dependency switches between static and dynamic linking, changing symbol visibility, duplication, or ODR exposure for that dependency's own symbols. | ELF, PE/COFF, Mach-O · C, C++ |

## 9. Toolchain/platform ABI (`toolchain-platform`)

Mechanisms where the same source code produces a different ABI purely
because of the compiler, target, or platform runtime it is built against.

| id | Mechanism | Description | Platforms / languages |
|---|---|---|---|
| `toolchain-platform.compiler-abi-epoch-change` | Compiler ABI-epoch change | The compiler's own ABI for a language feature changes across a version boundary (e.g. the GCC 5 libstdc++ dual-ABI switch, an MSVC ABI-breaking release), affecting every binary built with the new compiler regardless of source changes. | ELF (libstdc++ dual ABI), PE/COFF (MSVC ABI versions) · C++ |
| `toolchain-platform.target-triple-change` | Target-triple change | The build target (architecture/vendor/OS/environment) changes, changing calling convention, type sizes, or the object format itself. | ELF, PE/COFF, Mach-O · C, C++ |
| `toolchain-platform.abi-relevant-flag-change` | ABI-relevant compiler-flag change | A compiler flag that affects generated ABI changes between builds (`-fshort-enums`, `-mabi=`, `-fpack-struct`, `/vd`, `-fno-exceptions`, etc.), independent of any source edit. | ELF, PE/COFF, Mach-O · C, C++ |
| `toolchain-platform.libc-runtime-abi-change` | libc/runtime ABI change | The C runtime's own ABI changes (glibc, musl, MSVCRT/UCRT version), affecting every binary linked against it. | ELF, PE/COFF · C, C++ |
| `toolchain-platform.endianness-change` | Endianness change | The target's byte order changes, changing the in-memory representation of every multi-byte value. | ELF, PE/COFF, Mach-O · C, C++ |
| `toolchain-platform.word-size-change` | Word-size (32-bit/64-bit) change | The target's pointer/word size changes, changing pointer size, `long` size on some platforms, and struct layout throughout. | ELF, PE/COFF, Mach-O · C, C++ |
| `toolchain-platform.hardening-flag-change` | Hardening/mitigation flag change | A security-hardening feature changes (stack protector variant, Control-Flow Integrity, ARM Pointer Authentication/BTI), which can alter calling-convention-adjacent codegen (e.g. extra hidden parameters, prologue/epilogue shape) even when it does not change the declared signature. | ELF, PE/COFF, Mach-O · C, C++ |

## 10. Multi-library/product ABI (`product-abi`)

Contracts that exist only *between* components of one product or release,
not within any single library's own compiled interface.

| id | Mechanism | Description | Platforms / languages |
|---|---|---|---|
| `product-abi.cross-component-contract-break` | Cross-component contract break | Two components shipped in one release disagree about a shared struct/interface version, each individually "unchanged" from its own last release. | ELF, PE/COFF, Mach-O · C, C++ |
| `product-abi.version-skew-within-release` | Version skew within a release | Components in one release carry mismatched, individually-plausible-looking versions that are jointly incompatible when combined. | ELF, PE/COFF, Mach-O · C, C++ |
| `product-abi.plugin-interface-break` | Plugin interface break | A host/plugin ABI boundary changes, breaking third-party plugins that are not part of the release being analyzed and cannot be recompiled by that release's own process. | ELF, PE/COFF, Mach-O · C, C++ |

## 11. Source-level API compatibility (`source-api`)

Mechanisms that break recompilation of consumer source code without
necessarily changing a prebuilt binary's runtime compatibility.

| id | Mechanism | Description | Platforms / languages |
|---|---|---|---|
| `source-api.signature-change-source-only` | Source-only signature change | A signature-level change breaks recompilation (e.g. adding an overload that changes overload resolution, a parameter-type change that is a source break but not a runtime one for existing compiled callers) without changing the already-compiled interface. | C, C++ |
| `source-api.header-macro-removal` | Public macro removal | A public macro consumer code relies on is removed or renamed, breaking compilation with no corresponding symbol-level change. | C, C++ |
| `source-api.deprecation-attribute-addition` | Deprecation-attribute addition | `[[deprecated]]`/`__attribute__((deprecated))` is added to a previously plain declaration, turning previously-silent consumer usage into a warning (and, under `-Werror`, an error) with no change to the compiled interface. | C, C++ |
| `source-api.default-argument-value-change` | Default-argument value change | A default argument's value changes, silently altering the behavior of call sites that omit it, without any compile error to signal the change. | C++ |
| `source-api.overload-resolution-change` | Overload-resolution change | Adding a new overload changes which existing function a pre-existing call site now binds to, without that call site's own text changing. | C++ |

## 12. Header-only compatibility (`header-only`)

Mechanisms specific to libraries that ship no compiled artifact at all —
where "the ABI" is whatever each consumer's own compiler produced.

| id | Mechanism | Description | Platforms / languages |
|---|---|---|---|
| `header-only.multiple-version-odr` | Multiple-version ODR violation | Two different versions of the same header-only library are included by different translation units linked into one binary, violating the One Definition Rule across the whole program. | C, C++ |
| `header-only.include-guard-macro-collision` | Include-guard/macro collision | A header-only library's include guard or configuration macro collides with another library's identically-named macro, silently suppressing one definition. | C, C++ |
| `header-only.inline-namespace-version-stamp-change` | Inline-namespace version-stamp change | An inline-namespace-based ABI version stamp changes as intended by the library author, but breaks any consumer code that named the versioned namespace explicitly rather than through the inline alias. | C++ |
| `header-only.template-heavy-recompilation-drift` | Per-TU recompilation drift | A header-only, template-heavy library's observed behavior differs per translation unit depending on which version of the header each TU happened to compile against, since there is no single compiled artifact whose ABI could be checked. | C++ |

## 13. Language/ecosystem-specific mechanisms (`ecosystem-specific`)

Mechanisms intrinsic to one language or runtime ecosystem that do not
generalize to the branches above.

| id | Mechanism | Description | Platforms / languages |
|---|---|---|---|
| `ecosystem-specific.c-restrict-qualifier-change` | `restrict` qualifier change | A pointer parameter gains or loses `restrict`, changing optimizer aliasing assumptions — an API contract change with no change to the function's signature or ABI. | C |
| `ecosystem-specific.cpp-stdlib-abi-variant` | C++ standard-library ABI variant mismatch | Consumers are built against a different C++ standard-library ABI implementation or configuration than the library (libstdc++ vs. libc++, or a dual-ABI mode mismatch within libstdc++ itself). | ELF, Mach-O · C++ |
| `ecosystem-specific.cpython-limited-api-tag-change` | CPython limited-API/stable-ABI tag change | A CPython extension module's `Py_LIMITED_API`/stable-ABI tag changes, or code built against the limited API accesses a struct field the stable ABI does not guarantee. | ELF, PE/COFF, Mach-O · C (CPython extension modules) |
| `ecosystem-specific.cpython-object-layout-change` | CPython object layout change | CPython's own `PyObject`/type-object layout changes across interpreter builds or versions (including a free-threaded/no-GIL build), affecting any extension that reads that layout directly. | ELF, PE/COFF, Mach-O · C (CPython extension modules) |
| `ecosystem-specific.sycl-kernel-abi-change` | SYCL kernel ABI change | A SYCL kernel's device-side ABI, or the host/device integration-header contract generated for it, changes. | ELF, PE/COFF · SYCL/C++ |
| `ecosystem-specific.sycl-device-binary-format-change` | SYCL device-binary format change | The packaged device-binary format or target list changes, breaking runtime device dispatch even though the host-visible symbol is unchanged. | ELF, PE/COFF · SYCL/C++ |
| `ecosystem-specific.kernel-symbol-versioning-crc-change` | Kernel `MODVERSIONS` CRC change | A Linux kernel symbol's `MODVERSIONS` CRC changes because an ABI-relevant struct or prototype reachable from that symbol changed, independent of the symbol's own name. | ELF (Linux kernel modules) · C |
| `ecosystem-specific.kernel-btf-type-id-change` | Kernel BTF type-ID/graph change | BTF type IDs or the type graph shift when kernel struct layouts change, affecting BPF CO-RE (Compile Once – Run Everywhere) relocations that reference those types. | ELF (Linux kernel, BTF) · C |

## Next steps

Phase 2 of the plan resolves, for every leaf above, which `docs/learn/`
page(s) explain it, which `catalog/` case(s) demonstrate it, and which
`ChangeKind`/detector (if any) claims to observe it. Phase 3 then classifies
each leaf's coverage status (`NOT_APPLICABLE`, `KNOWN_UNDETECTABLE`,
`NOT_IMPLEMENTED`, `MISSING_CASE`, `PARTIALLY_COVERED`, or `COVERED`), and
Phase 4 closes `MISSING_CASE` gaps with paired positive/negative control
cases. None of that mapping work is done by this document — see
[ABI/API knowledge and corpus](plans/abi-api-knowledge-and-corpus.md) for
the full plan.
