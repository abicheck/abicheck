# Glossary

Quick definitions for the recurring terms in the
[ABI/API Handling series](../abi-api-handling.md). Each entry links to where the
concept is developed in full.

| Term | Meaning |
|------|---------|
| **ABI** (Application Binary Interface) | The binary-level contract between compiled modules: symbol names, struct layout, calling convention, vtable layout. Changing it can break *already-compiled* callers. See [Part 1](01-foundations.md). |
| **API** (Application Programming Interface) | The source-level contract: declarations, signatures, types as written in headers. Changing it can break *recompilation* even when the ABI is intact. See [Part 6 §Source-only API breaks](06-transitive-breaks.md#source-only-api-breaks-binary-identical). |
| **ABI epoch** | A generation of a library's binary contract. The **SONAME major** number *is* the ABI epoch; bumping it lets an incompatible new library coexist with the old one. See [Part 5](05-linker-elf.md). |
| **ABI tag** (`[[gnu::abi_tag]]`) | A token mangled into a C++ symbol name to distinguish otherwise-identical signatures across ABI variants. Adding/removing one renames the symbol. See [case113](../../examples/case113_abi_tag_changed.md). |
| **castxml** | The tool abicheck uses to parse C/C++ **headers** into an AST, recovering source-level facts (access, `noexcept`, default arguments, constants) that binaries don't carry. The `header_aware` tier. |
| **Calling convention** | The rules for passing arguments and return values (registers vs stack, who cleans up). Flipping trivially-copyable → non-trivial silently changes it. See [Part 4](04-cpp-abi.md). |
| **Copy relocation** | An ELF mechanism where an executable gets its *own* copy of a library global; growing that global's type then mismatches the copy. See [Part 5](05-linker-elf.md). |
| **Demangling** | Translating a mangled C++ symbol (`_ZN3foo3barEi`) back to a readable signature (`foo::bar(int)`). |
| **DWARF** | The debug-info format (Linux/macOS, from `-g`) that records struct layout, field offsets, enum values, and calling convention — the ground-truth *emitted* ABI. The `dwarf_aware` tier. |
| **Dual ABI** (`_GLIBCXX_USE_CXX11_ABI`) | libstdc++ ships two parallel `std::string`/`std::list` ABIs behind the `__cxx11` inline namespace; the macro selects which, re-mangling affected symbols. See [case104](../../examples/case104_glibcxx_dual_abi_flip.md). |
| **IFUNC** (indirect function) | A symbol resolved *once at load time* by a resolver that picks an implementation (e.g. by CPU features). See [case29](../../examples/case29_ifunc_transition.md). |
| **Install name** | A Mach-O dylib's self-recorded identity (`LC_ID_DYLIB`), baked into clients at link time; the macOS analog of SONAME. `@rpath`/`@loader_path` make it relocatable. See [Part 5](05-linker-elf.md#pecoff-and-mach-o-parallels). |
| **Interposition** | Replacing a library's symbol at load time with another definition (e.g. `LD_PRELOAD`); default visibility makes a symbol interposable. See [Part 5](05-linker-elf.md). |
| **Mangling** | The encoding of a C++ name + qualifiers + parameter types into a unique linker symbol. Any qualifier change re-mangles, vanishing the old symbol. See [Part 4](04-cpp-abi.md). |
| **ODR** (One Definition Rule) | C++'s requirement that every entity have exactly one definition across a program; inline/template body divergence between versions is an ODR-class hazard. See [Part 4](04-cpp-abi.md). |
| **Opaque handle** | A type consumers only ever see as a pointer (`FILE*`, `sqlite3*`); the strongest layout firewall. See [Part 7](07-designing-for-stability.md). |
| **Ordinal** | A Windows PE export's integer index. A caller bound by ordinal ignores the name, so reordering exports — not renaming — is what breaks it. See [Part 5](05-linker-elf.md#pecoff-and-mach-o-parallels). |
| **PDB** | Windows program database — the debug-info format (`/Zi`) abicheck reads for PE layout and calling-convention checks. The Windows `dwarf_aware` equivalent. |
| **Pimpl** ("pointer to implementation") | A C++ idiom that hides all data members behind one pointer to a `.cpp`-defined `Impl`, freezing `sizeof` and offsets. See [Part 7](07-designing-for-stability.md). |
| **PLT / GOT** | The Procedure Linkage Table / Global Offset Table — ELF's lazy-binding indirection for cross-library calls and data. See [Part 1](01-foundations.md). |
| **RPATH / RUNPATH** | Library search paths baked into a binary; absolute ones are non-portable and a security hazard. See [Part 5](05-linker-elf.md). |
| **SONAME** | The identity an ELF library advertises (`libfoo.so.1`); the loader matches clients to it. Its major number is the ABI epoch. See [Part 5](05-linker-elf.md). |
| **Symbol version** (version node) | A GNU ELF label (`GLIBC_2.17`) attached to a symbol so multiple ABIs of the same name coexist. Removing a node breaks clients bound to it. See [case65](../../examples/case65_symbol_version_removed.md). |
| **Thunk** | A small generated stub that adjusts `this` (or a return value) before forwarding — used for multiple inheritance and covariant returns in vtables. See [Part 4](04-cpp-abi.md). |
| **TLS model** | How thread-local storage is accessed (`global-dynamic`, `initial-exec`, …); a variable reached via `dlopen` needs `global-dynamic`. See [case67](../../examples/case67_tls_var_size_changed.md). |
| **Trivially copyable** | A type the compiler may move with `memcpy` and pass in registers; adding a user destructor/copy ctor flips this, changing the calling convention. See [case69](../../examples/case69_trivial_to_nontrivial.md). |
| **Two-level namespace** | The Mach-O scheme where each import records *which* library a symbol came from, so identical names from different libraries are distinct. See [Part 5](05-linker-elf.md#pecoff-and-mach-o-parallels). |
| **Universal (fat) binary** | A Mach-O file bundling multiple CPU-architecture slices; compare the matching slice. See [Part 5](05-linker-elf.md#pecoff-and-mach-o-parallels). |
| **vptr / vtable** | Each polymorphic object holds a hidden **vptr** to its class's **vtable** — a fixed-order array of function pointers. Callers hard-code slot *indices*, so reordering virtuals corrupts dispatch. See [Part 4](04-cpp-abi.md). |
| **Weak import** | A Mach-O import allowed to resolve to null (instead of failing) when missing — lets a client built against a newer dylib still run on an older one. See [Part 5](05-linker-elf.md#pecoff-and-mach-o-parallels). |

---

*Back to the [series overview](../abi-api-handling.md) · the
[ABI Cheat Sheet](../abi-cheat-sheet.md) · the
[Examples Encyclopedia](../../examples/index.md).*
