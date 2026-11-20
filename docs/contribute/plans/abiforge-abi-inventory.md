# ABI coverage inventory: profile P0, draft 1

This is a specification-derived research backlog, not a claim that 40 rows exhaust
C++ ABI changes, nor that one row is one test. The unit is an obligation family;
subrules and admissible combinations must be expanded and independently reviewed.

## Contract and profile

Direction: a consumer compiled and linked against v1 must remain binary-compatible
when v2 replaces the DSO without rebuilding the consumer. Scope includes documented
object representation, calling, symbol binding and C++ runtime protocols. It excludes
source-only API changes and arbitrary functional/performance equivalence.

Proposed P0: Linux x86-64 LP64, ELF shared libraries, SysV AMD64 plus the platform's
Itanium C++ conventions, GCC 14.2.0, C++20, -march=x86-64 -mtune=generic -fPIC,
-fabi-version=19 -fabi-compat-version=13, RTTI/exceptions enabled, no LTO,
_GLIBCXX_USE_CXX11_ABI=1. Pin linker, glibc, libstdc++ and libgcc binary hashes in
execution receipts before declaring this a fully frozen reproducible profile.
The installed compiler was confirmed as 14.2.0 / x86_64-linux-gnu. Previous C11
layout runs are evidence for their stated fragment, not full P0 certification.
Baseline configuration is held fixed. Packing/visibility and other per-declaration
features are edits inside explicitly tagged cases; flag changes become separately
named profile transitions. No blanket CPU/backend configuration multiplier.

## Inventory

Every row requires predicates, admissibility constraints, an independent oracle,
positive and negative controls, a generator mapping and evidence of checker behavior.
Only L2/A1–A4 have partial research evidence so far; no row is certified complete.

| ID | Family | Required obligation | Source anchor |
|---|---|---|---|
| L1 | Object layout | scalar/enum representation | psABI data representation; Itanium 2.1–2.4 |
| L2 | Object layout | field insertion/removal/reordering | psABI data representation; Itanium 2.1–2.4 |
| L3 | Object layout | packing/bitfield boundaries | psABI data representation; Itanium 2.1–2.4 |
| L4 | Object layout | union/overaligned/overlapping members | psABI data representation; Itanium 2.1–2.4 |
| A1 | Aggregate composition | nested size/alignment propagation | psABI aggregates; Itanium 2.2–2.4 |
| A2 | Aggregate composition | array element stride | psABI aggregates; Itanium 2.2–2.4 |
| A3 | Aggregate composition | indexed field offsets | psABI aggregates; Itanium 2.2–2.4 |
| A4 | Aggregate composition | enclosing tail/size rounding | psABI aggregates; Itanium 2.2–2.4 |
| C1 | Calls | argument register/memory classification | psABI parameter passing; Itanium 3.1 |
| C2 | Calls | return registers/hidden result pointer | psABI parameter passing; Itanium 3.1 |
| C3 | Calls | register exhaustion and stack alignment | psABI parameter passing; Itanium 3.1 |
| C4 | Calls | variadic argument classification | psABI parameter passing; Itanium 3.1 |
| H1 | Inheritance | primary/non-primary base placement | Itanium 2.4 |
| H2 | Inheritance | empty base and tail-padding reuse | Itanium 2.4 |
| H3 | Inheritance | virtual base offsets and diamonds | Itanium 2.4 |
| H4 | Inheritance | base conversion adjustment | Itanium 2.4 |
| V1 | Virtual dispatch | virtual slot order and signatures | Itanium 2.5–2.6, 3.2 |
| V2 | Virtual dispatch | secondary vtables and this thunks | Itanium 2.5–2.6, 3.2 |
| V3 | Virtual dispatch | covariant-return adjustment | Itanium 2.5–2.6, 3.2 |
| V4 | Virtual dispatch | construction vtables/VTT | Itanium 2.5–2.6, 3.2 |
| M1 | Names and member pointers | external mangled names and ABI tags | Itanium 2.3, 5.1 |
| M2 | Names and member pointers | template/inline-namespace external names | Itanium 2.3, 5.1 |
| M3 | Names and member pointers | data member pointer representation | Itanium 2.3, 5.1 |
| M4 | Names and member pointers | member function pointer representation | Itanium 2.3, 5.1 |
| R1 | Lifetime and runtime | constructor/destructor entry variants | Itanium 2.7–2.8, 3.3 |
| R2 | Lifetime and runtime | nontrivial-for-calls transitions | Itanium 2.7–2.8, 3.3 |
| R3 | Lifetime and runtime | array allocation cookies/deallocation | Itanium 2.7–2.8, 3.3 |
| R4 | Lifetime and runtime | initialization guards and destruction registration | Itanium 2.7–2.8, 3.3 |
| E1 | RTTI and exceptions | typeinfo identity across DSOs | Itanium 2.9; exception ABI |
| E2 | RTTI and exceptions | dynamic_cast hierarchy navigation | Itanium 2.9; exception ABI |
| E3 | RTTI and exceptions | throw/catch type matching | Itanium 2.9; exception ABI |
| E4 | RTTI and exceptions | unwind/cleanup/personality contract | Itanium 2.9; exception ABI |
| S1 | ELF binding | export removal/type/binding/visibility | psABI ELF; GNU ld VERSION |
| S2 | ELF binding | symbol version requirement/provider | psABI ELF; GNU ld VERSION |
| S3 | ELF binding | SONAME/DT_NEEDED resolution | psABI ELF; GNU ld VERSION |
| S4 | ELF binding | weak/COMDAT/unique/preemption identity | psABI ELF; GNU ld VERSION |
| D1 | Data and runtime dependencies | exported data size/alignment | psABI TLS; GCC ABI/runtime docs |
| D2 | Data and runtime dependencies | TLS model/symbol contract | psABI TLS; GCC ABI/runtime docs |
| D3 | Data and runtime dependencies | required runtime symbol-version floor | psABI TLS; GCC ABI/runtime docs |
| D4 | Data and runtime dependencies | standard-library representation at boundary | psABI TLS; GCC ABI/runtime docs |

## Initial interaction edges (not a complete interaction graph)

| From | To | Why it needs a composed witness |
|---|---|---|
| L2 | C1 | layout changes call classification |
| L3 | C1 | unaligned fields change call classification |
| C1 | C3 | classification consumes finite register banks |
| R2 | C1 | nontrivial objects use different call lowering |
| H1 | V2 | base placement changes this adjustment |
| H3 | V4 | virtual bases affect construction dispatch |
| H3 | E2 | hierarchy changes cast navigation |
| V3 | C2 | covariant returns require result adjustment |
| M1 | S1 | name is a loader lookup key |
| E1 | S4 | binding affects runtime type identity |
| R3 | A2 | cookie and element size affect array destruction |
| D4 | M1 | library representation and ABI tags interact |

## A count we can budget, and counts we cannot yet claim

Draft inventory: 40 local obligation families + 12 explicit interaction obligations
= 52 work items. These are author-selected starting rows, not a measured denominator
of all possible ABI failures. Every row must be split when independent rules appear.

A first audit budget assigns four control roles to each item: identity, a witnessed
breaking edit, a compatibility-preserving edit, and a condition-changing/boundary
case. This yields 208 planned fixture-role slots, NOT 208 sufficient tests. Some
slots are inapplicable, others require many examples; record reasons and expand.
Across binary-only, binary+DWARF, and stripped-binary+headers acquisition this is
624 planned checker comparisons before eligibility filtering. Build/probe artifacts
can be reused. Missing evidence must produce unknown, not a fabricated pass.

No honest total for representatives or elapsed time exists yet: each row lacks its
own finite abstraction or declared bound, and higher-order interactions remain open.
The prior 544 transitions and array bound of 64 belong to different local models;
do not add them to 208 or use them as percentages of this inventory. One batched
512-to-35 experiment cannot calibrate compiler/runtime integration costs globally.

Estimate runtime from measured phases per eligible cell:
build + independent oracle + dump old/new + compare, with explicit reuse/cache policy.
For costs of selector construction, compiler extraction and comparisons report separate
curves. Graph reachability reductions require a conservative dependency graph and a
proof that omitted interactions cannot influence the chosen observation.

## Existing repository catalog is a different denominator

At b0b8793989c150a5b05f48e4e999a7df4219365a, abiforge/coverage_ledger.json records
296 declared covered ChangeKinds, 9 declared uncovered, 1 derived-by-construction,
and 128 out-of-scope. These sum to 434 checker vocabulary entries. This mixes source,
binary, platform and other findings; declaration is not verified detection and is
not language completeness. Do not reinterpret 296/434 as C++ ABI coverage.

Map rows to operators by reviewed semantics, not by keyword/name matching. Record
operator ID, exact applicability, independent oracle, observed findings, missing facts
and guarantee level. The mapping itself remains TODO; existing functionality must
not be called absent merely because this research has not audited it.

## Completion criterion and next execution slice

A row is closed only relative to a stated fragment: all applicable specification
rules traced, valid edits covered or excluded explicitly, reduction proof checked,
compiler/model correspondence tested, checker extraction/reporting audited, and
composition assumptions established. Arbitrary implementation bugs still require
conformance assumptions or proof of implementation. Pairwise interaction tests alone
cannot establish absence of higher-order failures.

Next: L2 x C1 x C3 and R2 x C1. Build old consumers once and replace only providers;
exercise ordinary aggregates around calling-class boundaries, register exhaustion,
and trivial/nontrivial-for-calls changes. Include same-symbol/same-size counterexamples
and controls. Score independent caller/callee transfer witnesses separately from
checker verdicts. This expands beyond the layout fragment into a major unexamined
ABI mechanism while retaining explicit boundaries.

## Primary references

- [Itanium C++ ABI](https://itanium-cxx-abi.github.io/cxx-abi/abi.html): chapters 2–5. Generic ABI; platform supplements remain authoritative.
- [AMD64 psABI source](https://gitlab.com/x86-psABIs/x86-64-ABI/-/raw/master/x86-64-ABI/low-level-sys-info.tex): data representation and parameter passing. Pin a source revision before formalizing rules.
- [Exception ABI](https://itanium-cxx-abi.github.io/cxx-abi/abi-eh.html): unwind and C++ exception runtime protocols.
- [GCC 14.2 ABI options](https://gcc.gnu.org/onlinedocs/gcc-14.2.0/gcc/C_002b_002b-Dialect-Options.html): ABI version and compatibility aliases.
- [GNU ld symbol versions](https://sourceware.org/binutils/docs/ld/VERSION.html): provider/consumer version binding.
- [libstdc++ dual ABI](https://gcc.gnu.org/onlinedocs/libstdc++/manual/using_dual_abi.html): ABI macro is independent of the language-standard switch.
