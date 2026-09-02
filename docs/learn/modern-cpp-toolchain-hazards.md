---
doc_type: explanation
audience:
  - library-maintainer
level: advanced
canonical_for:
  - modern-cpp-toolchain-hazards
depends_on:
  - abicheck/diff_symbols.py
  - scripts/evidence_tiers.py
  - abicheck/build_context.py
  - abicheck/dwarf_advanced.py
lifecycle: active
generated: false
---

# Modern C/C++ and Toolchain ABI Hazards

The break families in [Part 4 — C++ ABI Specifics](abi-series/04-cpp-abi.md)
predate C++11. Newer language features and toolchain *flags* introduce a
second class of hazard: the **declaration looks unchanged in the header,
but the bytes the compiler emits move** because a type's size, mangling, or
passing rule shifted under it. These are the cases reviewers miss most
often, because nothing in the diff "looks like" an ABI change.

| Hazard | What silently changes | abicheck case |
|--------|----------------------|---------------|
| **`_GLIBCXX_USE_CXX11_ABI` flip** | libstdc++ ships *two* `std::string`/`std::list` ABIs in parallel behind the `__cxx11` inline namespace; flipping the macro re-mangles every symbol that touches those types. | [case104](../reference/examples/case104_glibcxx_dual_abi_flip.md) |
| **ABI tags (`[[gnu::abi_tag]]`)** | A tag is mangled into the symbol name; adding/removing one renames the symbol with no source-visible signature change. | [case113](../reference/examples/case113_abi_tag_changed.md) |
| **`char8_t` (C++20)** | `const char*` → `const char8_t*` is a *distinct type*: different mangling, and a new overload-resolution result. | [case114](../reference/examples/case114_char8t_migration.md) |
| **`_BitInt(N)` width** | Changing `N` changes size/alignment and the register/stack class the value is passed in. | [case115](../reference/examples/case115_bit_int_width_changed.md) |
| **`_Atomic` qualifier** | The representation of an `_Atomic`-qualified type is implementation-defined: adding/removing the qualifier can change size and alignment, and with them how the object is classified for passing/returning. | [case116](../reference/examples/case116_atomic_qualifier_changed.md) |
| **`[[no_unique_address]]`** | *Permits* an empty member to overlap the next field — so adding it can shrink the struct and shift following offsets, though existing alignment padding can absorb the overlap and leave both size and offsets unchanged. | [case117](../reference/examples/case117_no_unique_address.md) |
| **Concept tightening (C++20)** | Narrowing a constraint removes instantiations the consumer relied on — a *source* break with no symbol-table change for already-emitted instantiations. | [case105](../reference/examples/case105_concept_tightening.md) |
| **LP64 → ILP64 / data-model drift** | The library's public integer typedef widens (case112: `MKL_INT` `int`→`long`, 32→64-bit), so every dimension, stride, and count in the API changes width at once while the `extern "C"` names stay identical — the consumer links fine and silently passes integers of the wrong width. | [case112](../reference/examples/case112_lp64_ilp64.md) |

Several more live only in the **build flags**, not the source, and abicheck
surfaces them as toolchain/deployment risk when build context is captured:
`-fno-exceptions` / `-fno-rtti` (drop EH/RTTI machinery callers may rely
on — see [Exception Unwinding](exception-unwinding-abi.md) for what
`-fno-rtti` actually strips; [case131](../reference/examples/case131_rtti_mode_flip.md)
is the RTTI-mode flip), `-fshort-enums` (changes enum underlying size
— see [Part 3](abi-series/03-type-layout.md)), packing/alignment flags,
vector-ABI flags, and CPU-dispatch/IFUNC selection
([case83](../reference/examples/case83_cpu_dispatch_isa_dropped.md),
[case29](../reference/examples/case29_ifunc_transition.md)).

!!! warning "Which of these need debug info or headers"
    Most hazards above are recoverable only when DWARF/PDB *or* headers are
    supplied — they change layout, size, or a passing rule that doesn't show
    up in the export table alone. Two are the exception: the `_GLIBCXX_USE_CXX11_ABI`
    flip and an ABI-tag change are both mangled straight into the exported
    symbol name (`glibcxx_dual_abi_flip_detected`/`abi_tag_changed`, both
    minimum evidence `L0`), so they're visible from the
    *mangled* export table alone — no DWARF/PDB/headers needed. A stripped
    binary that has been additionally **demangled** (its export names
    rewritten to human-readable form) can still hide them, since the
    mangled encoding is what carries the signal; an ordinary stripped
    binary's raw mangled export table does not.

See also: [Part 4 — C++ ABI Specifics](abi-series/04-cpp-abi.md) for the
core, pre-C++11 mechanisms this page's hazards sit alongside, and
[Exception Unwinding](exception-unwinding-abi.md) for the
`-fno-exceptions`/`-fno-rtti` machinery in full.

---

**Ladder:** ← [Part 4 — C++ ABI Specifics](abi-series/04-cpp-abi.md) · Tier 2 · Mechanics · [Part 5 — ELF & Linker-Level Concerns](abi-series/05-linker-elf.md) →
