# Part 8 — Detecting Breaks: Evidence, Tools, and Why One Method Is Never Enough

> **Series navigation:** [0. Product Contract](00-product-contract.md) ·
> [1. Foundations](01-foundations.md) ·
> [2. Symbol Contracts](02-symbol-contracts.md) ·
> [3. Type Layout](03-type-layout.md) ·
> [4. C++ ABI](04-cpp-abi.md) ·
> [5. Linker & ELF](05-linker-elf.md) ·
> [6. Transitive Breaks](06-transitive-breaks.md) ·
> [7. Designing for Stability](07-designing-for-stability.md) ·
> **8. Detecting Breaks**

Parts 0–7 explained the *mechanisms*: what the compiler bakes into a binary, and
which changes corrupt that contract. This part turns the telescope around and asks
the engineering question: **how do you actually catch each of those breaks before
you ship?**

Three things matter, and this page covers all three:

1. **The general approaches** to ABI/API tracking — and the failure mode each one
   has when used alone.
2. **What evidence each break family requires** — matching every family from the
   [break-families table](../abi-api-handling.md#break-families-at-a-glance) to
   the minimum input that makes it visible, with the example cases that prove it.
3. **Why classic single-method checkers (libabigail's `abidiff`, ABICC) are not
   sufficient** — and, just as honestly, where *any* static tool stops, including
   abicheck.

> **Tool-track companion pages:** this page teaches the concepts; the precise
> per-source capability matrix lives in
> [Evidence & Detectability](../evidence-and-detectability.md), measured accuracy
> numbers in [Tool Comparison & Benchmarks](../../reference/tool-comparison.md),
> and the boundary of static checking in [Limitations](../limitations.md).

---

## 1. The general approaches to ABI/API tracking

Every team tracks compatibility somehow, even if only by hope. The approaches
below are ordered roughly by how much they *observe*; each catches something the
previous ones cannot, and each has a blind spot that motivates the next.

| # | Approach | What it observes | Catches | Blind spot |
|---|----------|------------------|---------|------------|
| 1 | **Process discipline** — SemVer policy, review checklists, "don't touch public headers" rules | Human judgement | Anything a reviewer happens to notice | Everything a reviewer doesn't notice — layout shifts from an "internal" change, transitive leaks, toolchain flips. Unverifiable by construction. |
| 2 | **Runtime swap testing** — build an app against v1, run it against v2 | One consumer's actual usage | Real crashes in the paths the app exercises | Surface the test app doesn't call (usually most of it); silent corruption that doesn't crash; needs a representative app per consumer. |
| 3 | **Symbol-table diffing** — `nm`/`readelf` diff, or any tool run on stripped binaries (**L0**) | Exported symbol names, versions, SONAME | Removed/renamed symbols, C++ mangled-signature changes, linker metadata drift | Everything that doesn't change a symbol name: struct layout, enum values, vtable order, C parameter types. |
| 4 | **Debug-info diffing** — DWARF/PDB-based tools (**L1**) | Type layout as compiled: sizes, offsets, enum values, vtables | The whole layout family from [Part 3](03-type-layout.md) and most of [Part 4](04-cpp-abi.md) | Requires `-g` artifacts (release builds are usually stripped); largely blind to *source-level* API facts — access control, default arguments, `explicit`, hidden friends — which DWARF doesn't record or tools don't model. |
| 5 | **Header/AST diffing** — compiling public headers and comparing the AST (**L2**) | The declared source contract | Source-only API breaks, plus *scoping*: knowing which types are actually public | Blind to binary truth: what was *actually* exported and with which SONAME/versions, and what flags the shipped binary was really built with. |
| 6 | **Build- and source-aware overlay** (**L3/L4**) | Compile flags, default-argument *values*, inline/template bodies, uninstantiated templates | Facts that never reach any shipped artifact — the [source-only tail](../limitations.md#source-only-changes-invisible-to-binaryobject-analysis) | Highest setup cost; meaningless without the artifact layers underneath it to anchor the shipped-ABI verdict. |

The pattern: **each approach is a projection of the library onto one kind of
evidence.** None of the projections is the library. A checker is only complete to
the extent that it overlays several projections and lets the strongest evidence
win — which is exactly the [five-layer evidence model](../evidence-and-detectability.md#0-the-five-sources-of-information)
abicheck implements, and why runtime testing (approach 2) still belongs in your
release pipeline *next to* static checking: it is the only approach that observes
behaviour.

---

## 2. What it takes to find each break family

The table below extends the
[break-families table](../abi-api-handling.md#break-families-at-a-glance) with the
detection dimension: the **minimum evidence** that makes the family visible
(`L0` binary · `L1` +debug info · `L2` +headers · `L3` +build data · `L4`
+sources), and whether a symbol-level or debug-info-level checker can see it at
all. Per-case minimums are machine-readable in
[`examples/ground_truth.json`](https://github.com/napetrov/abicheck/blob/main/examples/ground_truth.json)
(`min_evidence` field) and measured in
[Benchmarking by evidence tier](../../reference/tool-comparison.md#benchmarking-by-evidence-tier).

| Break family | Min evidence | Symbol-only (L0) sees it? | DWARF tools (L1) see it? | Why — and representative cases |
|---|:---:|:---:|:---:|---|
| Symbol/function/variable removal | **L0** | ✅ | ✅ | The symbol vanishes from `.dynsym` — every tool's home turf ([case01](../../examples/case01_symbol_removal.md), [case12](../../examples/case12_function_removed.md)) |
| C++ signature/qualifier changes | **L1** | ⚠️ partial | ✅ | Itanium mangling encodes parameters, `const`, `static` — so even a stripped binary shows *a symbol vanished and a new one appeared*. But classifying it as a qualifier change on the *same* method (rather than an unrelated removal + addition) takes debug info or headers ([case21](../../examples/case21_method_became_static.md), [case22](../../examples/case22_method_const_changed.md) are measured at L1) |
| **C** signature changes | **L1/L2** | ❌ | ✅ | C symbols are just the function name — `foo(int)` → `foo(long)` keeps the identical symbol. Needs DWARF or headers ([case02](../../examples/case02_param_type_change.md), [case10](../../examples/case10_return_type.md)) |
| Struct/class layout, packing, alignment | **L1/L2** | ❌ | ✅ | No symbol changes when a field moves; layout lives in debug info and headers ([case07](../../examples/case07_struct_layout.md), [case40](../../examples/case40_field_layout.md), [case56](../../examples/case56_struct_packing_changed.md)) |
| Enum value reassignment | **L1/L2** | ❌ | ✅ | Constants are compiled into *callers*; the library's symbols are untouched ([case08](../../examples/case08_enum_value_change.md), [case20](../../examples/case20_enum_member_value_changed.md)) |
| Vtable reordering | **L1/L2** | ❌ | ✅ | Every symbol still exists — only the *slot indexes* moved ([case09](../../examples/case09_cpp_vtable.md)) |
| Source-only API breaks: access narrowed, `explicit` added, default argument removed, hidden friends | **L2** | ❌ | mostly ❌ | DWARF doesn't reliably model these; they live in the declared AST ([case34](../../examples/case34_access_level.md), [case106](../../examples/case106_ctor_became_explicit.md), [case123](../../examples/case123_default_argument_removed.md), [case96](../../examples/case96_hidden_friend_removed.md)) |
| ELF/linker metadata: SONAME, visibility, symbol versions, RPATH | **L0** | ✅ | ✅ | Binary-only facts — which means *header-only* checkers (ABICC's XML mode) are the blind ones here ([case05](../../examples/case05_soname.md), [case65](../../examples/case65_symbol_version_removed.md)) |
| Toolchain/build-flag drift: `-std` floor, ABI version, flag changes | **L1/L3** | ❌ | partly | Compilers record their flags in `DW_AT_producer`, so a `-g` build exposes some drift; the rest needs the compile DB ([case103](../../examples/case103_toolchain_flag_drift.md)). The libstdc++ dual-ABI flip is the notable exception: it *renames mangled symbols* (`std::__cxx11::`), so even a stripped binary betrays it at L0 ([case104](../../examples/case104_glibcxx_dual_abi_flip.md)) |
| Header **`const`/`constexpr`** constant values | **L2** | ❌ | ❌ | The value lives in the declared AST, not the binary — header comparison sees it ([case124](../../examples/case124_header_constant_value_changed.md)). Plain `#define` macros are *not* part of the AST — see the next row |
| Plain `#define` macro values, inline/template **bodies**, uninstantiated templates | **L4** | ❌ | ❌ | These never reach the shipped binary or the header AST — only source/preprocessor evidence sees them. [case122](../../examples/case122_template_signature_uninstantiated.md) is deliberately a *no-change* case: it marks the boundary of what even source analysis can prove about templates that were never instantiated |
| Multi-library release skew (bundle SONAME/dependency drift) | release model | ❌ | ❌ | Not a property of any *single* binary diff — needs a bundle-level comparison ([multi-binary guide](../../user-guide/multi-binary.md), bundle cases 84/90–93 in `examples/`) |
| Internal-only changes (**should be NO_CHANGE**) | **L2** | FP ⚠️ | FP ⚠️ | The inverse problem: without header scoping, tools *flag* private `detail::` churn as breaking. Evidence here removes false positives ([case118](../../examples/case118_internal_struct_field_added_scoped.md)–[120](../../examples/case120_internal_struct_reordered_scoped.md)) |

Two lessons hide in this table:

- **Evidence runs in both directions.** More input doesn't just find more breaks —
  it *dismisses* false alarms. Header scoping is what lets a checker say
  "that struct changed, but it was never part of the public surface."
- **The staircase is real and measurable.** Over the example catalog, a stripped
  binary alone reaches the correct verdict for about a third of cases; adding
  debug info takes it to ~81%; headers to ~99%; build/source data closes the rest
  (current numbers in the
  [evidence-tier table](../../reference/tool-comparison.md#which-source-discovers-what)).

---

## 3. Why an abidiff- or ABICC-class checker is not sufficient

This is a structural argument, not tool-bashing — both tools are good at what
their evidence lets them see (details and per-case results in the
[Tool Comparison](../../reference/tool-comparison.md)):

1. **Each is capped at one rung of the staircase.**
   `abidiff` is DWARF-first (L0+L1): hand it the stripped release binary you
   actually ship and it degrades toward symbol-only; the source-only API family —
   access changes, default arguments, `explicit`, `noexcept` semantics — stays
   invisible even with debug info, because a header directory acts as a symbol
   *filter* there, not a full AST. ABICC leans the other way: its header/XML
   workflow sees the declared contract but not the binary truth (exports,
   SONAME, symbol versions), and its `abi-dumper` workflow inherits the DWARF
   ceiling. Neither overlays *all* the layers, so each one misses families the
   other catches — and both miss the L3/L4 tail (flag drift, inline bodies,
   uninstantiated templates).

2. **No public-surface scoping.** Without resolving what is *public*, every
   internal `detail::` struct edit shows up as a break. In practice that
   noise — not missed breaks — is what makes teams turn checkers off. The
   scoped-internal cases ([118](../../examples/case118_internal_struct_field_added_scoped.md)–[120](../../examples/case120_internal_struct_reordered_scoped.md))
   exist precisely to test that a checker can stay *silent* correctly.

3. **A binary verdict is not a release decision.** "Compatible / incompatible"
   collapses distinctions that [Part 0](00-product-contract.md) showed are
   policy-relevant: a source-level `API_BREAK` ships fine for prebuilt binaries
   but breaks rebuilders; a `COMPATIBLE_WITH_RISK` `noexcept` change is fine
   unless a consumer relied on it. The 5-tier verdict and policy profiles exist
   because real release gates need that resolution — as do bundle-level
   comparison, application-scoped checks, and suppression workflows.

**And where everything stops:** no static tool — abicheck included — can prove
*behaviour*. A function that keeps its signature and layout but starts returning
different values is invisible to every approach in §1 except runtime testing.
The honest boundary is documented in
[Limitations](../limitations.md) and
[What ABI tools cannot prove](../evidence-and-detectability.md#5-what-abi-tools-cannot-prove);
treat static ABI checking as the part of release safety you can automate
*exhaustively*, not as all of it.

---

## 4. Using the encyclopedia as a detection atlas

Every capability claim in this series is backed by a runnable fixture, and the
mapping is maintained mechanically — CI checks that every `ChangeKind` is
produced by a detector, documented, and (for the catalog) carries a verified
verdict and minimum evidence tier:

- **Capability → meaning:** the [Change Kind Reference](../../reference/change-kinds.md)
  lists every detectable change kind with its classification.
- **Capability → proof:** each [example page](../../examples/index.md) names the
  change kinds it triggers, its verdict, and includes a *Real Failure Demo*; the
  expected results live in `ground_truth.json`, which the benchmark gates on.
- **Capability → required input:** the `min_evidence` field per case, aggregated
  in the [evidence-tier benchmark](../../reference/tool-comparison.md#benchmarking-by-evidence-tier),
  tells you exactly which input you must provide before that break becomes
  visible — which is the practical answer to "what do I need to feed the
  checker in *my* CI?"

---

## Where to go next

- Back to the [series hub](../abi-api-handling.md) for the other parts.
- [Evidence & Detectability](../evidence-and-detectability.md) — the full
  per-source capability matrix this page summarizes.
- [Choose Your Workflow](../../user-guide/choose-your-workflow.md) — turn the
  evidence you *have* into the right command for your CI.
