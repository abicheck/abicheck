---
doc_type: explanation
audience:
  - library-maintainer
  - ci-owner
level: beginner
canonical_for:
  - break-symptoms
summarizes:
  - evidence-model
depends_on:
  - scripts/evidence_tiers.py
lifecycle: active
generated: false
---

# How a Break Shows Up

## A break is a symptom before it is a mechanism

The rest of this series is organised by *mechanism* — symbols, type layout,
C++ specifics, the linker — because that is how you fix a break. But nobody
meets a break as a mechanism. You meet it as something that *happened*: a
link failed, a program would not start, a customer's machine crashed, a
rebuild stopped compiling. This page starts from that side. Each symptom
names the mechanism family behind it and the *first kind of evidence* a
checker needs before it can see it at all — the evidence levels that
[Evidence & Detectability](evidence-and-detectability.md) defines and
[What Each Level Sees](what-each-level-sees.md) walks one by one.

## The eight symptoms

### 1. The link fails

You rebuild your program against the new library and the linker stops:

```text
ld: undefined reference to `foo_open'
```

A symbol the program needs is gone from the library's export table — the
most direct break there is ([Part 2](abi-series/02-symbol-contracts.md)).
The export table is in the binary itself, so the cheapest comparison, a
symbol-only diff of the two libraries
([Level 0](what-each-level-sees.md#level-0-the-shipped-binary-symbols-only)),
already shows it: [case01](../reference/examples/case01_symbol_removal.md).

### 2. The program will not start

Nothing was rebuilt; the library was upgraded in place, and the loader
refuses the program at startup:

```text
./app: symbol lookup error: ./app: undefined symbol: foo_open
./app: /lib/libfoo.so.1: version `FOO_1.2' not found (required by ./app)
```

The loader's own contract broke — a symbol or a *symbol version node*
disappeared ([Part 5](abi-series/05-linker-elf.md)). Both facts live in the
binary, so Level 0 sees them too:
[case65](../reference/examples/case65_symbol_version_removed.md).

### 3. It crashes, or quietly corrupts data, after an upgrade nobody rebuilt for

The program starts, every symbol resolves, and it fails later — a segfault,
or worse, wrong numbers with no error at all. A struct grew, a field moved,
a vtable slot was renumbered; the old caller keeps using the old offsets
([Part 3](abi-series/03-type-layout.md), [Part 4](abi-series/04-cpp-abi.md)).
The export table is identical, so Level 0 calls the release clean. Layout
is only in the debug information
([Level 1](what-each-level-sees.md#level-1-debug-info-dwarfpdb-layout-ground-truth)):
[case07](../reference/examples/case07_struct_layout.md).

### 4. Rebuilding against the new headers fails

Prebuilt programs keep working, but a consumer who *recompiles* gets:

```text
error: too few arguments to function 'int foo_open(const char*, int)'
```

A default argument was removed, a member went private, a constructor
became `explicit` — a source-only break
([Part 6 § Source-only API breaks](abi-series/06-transitive-breaks.md#source-only-api-breaks-binary-identical)).
No binary changed, so only a comparison that parses the public headers
([Level 2](what-each-level-sees.md#level-2-public-headers-the-source-level-api))
can see it: [case123](../reference/examples/case123_default_argument_removed.md).

### 5. A call silently binds to a different value

The program compiles and runs, and behaves differently after a rebuild,
because a constant it compiled in — an enum member's value, a header
`constexpr` — changed underneath it
([Part 6](abi-series/06-transitive-breaks.md)). The value lives in the
declared header, not in any symbol, so this is Level 2 as well:
[case124](../reference/examples/case124_header_constant_value_changed.md).

### 6. The source you compile against changed, but no binary did

A public `#define` disappeared, an inline function was removed, an
uninstantiated template's signature moved. No shipped artifact carries any
of these — not the export table, not the debug info, not even the header
AST for the template case — so only replaying the sources themselves
([Level 4](what-each-level-sees.md#level-4-sources-the-facts-that-never-reach-the-binary))
finds them: [case156](../reference/examples/case156_public_macro_removed.md),
[case157](../reference/examples/case157_inline_function_removed.md),
[case122](../reference/examples/case122_template_signature_uninstantiated.md).
A *silent* behavioural change inside an inline body has no catalog
fixture today; that gap is real, and the series says so rather than
pointing at a case that shows something else.

### 7. It works on the build machine and fails on the customer's distro

The library loads fine where it was built and refuses to load on an older
OS release:

```text
/lib/libc.so.6: version `GLIBC_2.34' not found (required by libfoo.so.1)
```

Nothing in the library's own contract changed; its *floor* did — a rebuild
on a newer toolchain now requires a newer runtime
([Dependency & Runtime Floors](dependency-floors.md)). The requirement is
recorded in the binary, so Level 0 reads it; deciding whether it is a break
needs a declared supported-OS matrix (`--env-matrix`):
[case170](../reference/examples/case170_env_runtime_floor_raised.md).

### 8. It works for the application and breaks the plugin, or a sibling library

Every consumer you tested is fine, and a plugin — or another library in the
same release — fails, because it depended on something the "main" consumer
never touched. Which consumer shape you promised compatibility to is its
own question ([Consumer Models](consumer-models.md)), and a release of
several libraries is one contract, not several
([What Each Level Sees § two orthogonal sources](what-each-level-sees.md#two-orthogonal-sources-app-swap-and-bundle-scan)):
case90, described with its three sibling bundle cases in
[Multi-Binary Releases](../use/multi-binary.md#references) (the bundle fixtures
have no generated case pages).

## The table

| Symptom | Mechanism family | First evidence that shows it |
|---|---|---|
| Link error | [Part 2](abi-series/02-symbol-contracts.md) | [L0](what-each-level-sees.md#level-0-the-shipped-binary-symbols-only) |
| Load error, version not found | [Part 5](abi-series/05-linker-elf.md) | [L0](what-each-level-sees.md#level-0-the-shipped-binary-symbols-only) |
| Crash or silent corruption, no rebuild | [Part 3](abi-series/03-type-layout.md), [Part 4](abi-series/04-cpp-abi.md) | [L1](what-each-level-sees.md#level-1-debug-info-dwarfpdb-layout-ground-truth) |
| Compile error after an upgrade | [Part 6](abi-series/06-transitive-breaks.md#source-only-api-breaks-binary-identical) | [L2](what-each-level-sees.md#level-2-public-headers-the-source-level-api) |
| A call binds to a different value | [Part 6](abi-series/06-transitive-breaks.md) | [L2](what-each-level-sees.md#level-2-public-headers-the-source-level-api) |
| The source changed, no binary did | [Part 6](abi-series/06-transitive-breaks.md#source-only-api-breaks-binary-identical) | [L4](what-each-level-sees.md#level-4-sources-the-facts-that-never-reach-the-binary) |
| Fails on the customer's distro | [Dependency floors](dependency-floors.md) | [L0](what-each-level-sees.md#level-0-the-shipped-binary-symbols-only) + a declared matrix |
| Breaks the plugin or a sibling library | [Consumer models](consumer-models.md) | [bundle scan](what-each-level-sees.md#two-orthogonal-sources-app-swap-and-bundle-scan) |

## What this means for you

- You cannot see the third row without debug information: a stripped
  binary with no headers makes a genuinely breaking release look clean
  ([Level 1](what-each-level-sees.md#level-1-debug-info-dwarfpdb-layout-ground-truth)).
- You cannot see the fourth or fifth row without the public headers; the
  break is in what consumers compile against, not in what you ship
  ([Level 2](what-each-level-sees.md#level-2-public-headers-the-source-level-api)).
- You cannot see the sixth row from any binary at all; only the sources
  carry it ([Level 4](what-each-level-sees.md#level-4-sources-the-facts-that-never-reach-the-binary)).

---

**Ladder:** ← [ABI in Five Minutes](abi-series/abi-in-5-minutes.md) · Tier 0 · Orientation · [ABI Cheat Sheet](abi-cheat-sheet.md) →
