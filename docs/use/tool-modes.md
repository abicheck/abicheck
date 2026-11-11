---
doc_type: reference
audience:
  - library-maintainer
level: intermediate
summarizes:
  - evidence-model
lifecycle: active
generated: false
---

# ABI Tool Modes Reference

This document explains the analysis modes relevant to `abicheck`: **abicheck's
own native mode** (what you get by default — which itself adapts to the
[evidence sources](../learn/evidence-and-detectability.md#0-the-five-sources-of-information)
you give it) plus the three external reference modes (`abidiff` and the two
ABICC usages) that abicheck is benchmarked against and can emulate. The
external-tool names match ABICC's official documentation.

> **Looking for what each *data source* gives you** (just the binary, debug
> symbols, headers, build data, sources) rather than what each *tool* does? That
> is the [five-source / L0–L4 model](../learn/evidence-and-detectability.md#0-the-five-sources-of-information);
> the [next section](#abicheck-native-modes-by-evidence-source-l0l4) maps it onto
> the abicheck commands you actually run.

---

## Mode Overview

| Mode | What it is | Compiler needed? | Debug info needed? | Headers needed? |
|------|------------|:----------------:|:------------------:|:---------------:|
| **abicheck (native, default)** | abicheck's own pipeline, which adapts to the evidence you give it: binary metadata (L0) + DWARF/BTF/CTF (ELF) or PDB (PE) layout (L1) + header AST via castxml/clang (L2), optionally + build context (L3) / source replay (L4) — see [modes by source](#abicheck-native-modes-by-evidence-source-l0l4) | ⚠️ for headers only (castxml/clang; GCC/Clang/MSVC) | optional (improves accuracy) | recommended (falls back to L1 debug info if present, else L0 binary-metadata analysis) |
| abidiff + headers | `abidiff` (libabigail) | ❌ | optional (improves accuracy) | ✅ always |
| ABICC+headers (ABICC Usage #2) | Original / headers mode | ✅ **GCC only** | ❌ | ✅ |
| ABICC+dump (ABICC Usage #1) | abi-dumper / binary mode | ❌ | ✅ (`-g -Og`) | ❌ (optional) |

> The default mode is **abicheck native** — you do not need `abidiff` or
> `abi-compliance-checker` installed. The external modes are documented here
> because abicheck reports parity against them ([Tool Comparison](../reference/tool-comparison.md)).

---

## abicheck (native layered analysis)

### Overview

By default `abicheck` does **not** shell out to any external ABI tool. It runs
its own independent analysis layers — binary metadata (L0), debug-info layout
(L1), and header AST (L2), optionally enriched with build (L3) and source (L4)
evidence — and reconciles them into a single verdict. This is the mode behind
`abicheck compare`, `abicheck dump`, and the [GitHub Action](github-action.md).
See [modes by evidence source](#abicheck-native-modes-by-evidence-source-l0l4)
for what each one provides.

### How it works

```mermaid
flowchart LR
    BIN["Binary metadata<br/>ELF / PE / Mach-O<br/>(symbols, SONAME, versions)"]
    AST["Header AST<br/>castxml → Clang<br/>(signatures, layout, vtables)"]
    DWARF["Debug-info cross-check<br/>DWARF / PDB<br/>(sizes, offsets, calling conv.)"]
    V["Reconciled verdict"]
    BIN --> V
    AST --> V
    DWARF --> V
```

See [Architecture](../learn/architecture.md) for the full per-layer breakdown.

### Requirements

| Requirement | Mandatory? | Notes |
|-------------|-----------|-------|
| Two inputs (`.so`/`.dll`/`.dylib`, JSON snapshot, package, or directory) | ✅ | Core input; mix freely |
| `castxml` **or** `clang` + a compiler | ⚠️ for header AST | Needed when you pass `-H`/`--header`. `compile.frontend: auto` resolves to CastXML or the `ABICHECK_AST_FRONTEND` pin. Select Clang with `--set compile.frontend=clang`; opt-in fallback is `compile.ast_frontend_fallback` / `ABICHECK_ALLOW_AST_FALLBACK=1`. Clang records declarations and can enrich host record layouts through its built-in compiler pass or the optional companion; DWARF adds evidence. `hybrid` requires both tools and is never auto-selected. |
| Debug info (`-g`) | ❌ optional | DWARF/PDB enrich layout, calling-convention, and packing checks |
| Headers | strongly recommended | Without them abicheck uses DWARF/BTF/CTF (ELF) or PDB (PE) if present, falling back further to L0 binary-metadata analysis when neither headers nor debug info are present — exported symbols plus platform-specific facts (SONAME/dependencies/rpaths on ELF; machine type, imports, delay-load, hardening flags, version/OS floor on PE; install name, dependencies, rpaths, and other load-command facts on Mach-O), never a bare symbol list on any platform (ELF prints an explicit warning either way; PE/Mach-O don't yet); type/signature breaks may be missed |

### What it catches

abicheck is a superset of the external modes for most categories — see the
[quick-reference table](#tool-comparison-quick-reference) below and the
[428-kind Change Kind Reference](../reference/change-kinds.md). Below
highlights what a single external tool misses:

- ✅ `noexcept`, `const`/`static` qualifier, and access-level changes (header AST)
- ✅ Calling-convention, `#pragma pack`, and alignment drift (DWARF/PDB)
- ✅ ELF/PE/Mach-O symbol-table changes (visibility, binding, versioning)
- ✅ Trivially-copyable → non-trivial calling-convention flips (DWARF traits)
- ✅ Cross-platform: ELF, PE/COFF, and Mach-O from one tool

### Usage

```bash
# Header-aware (recommended; needs castxml)
abicheck compare libv1.so libv2.so --header old=v1.h --header new=v2.h

# Binary-only fallback (no castxml required)
abicheck compare libv1.so libv2.so
```

Full flag reference: [CLI Usage](cli-usage.md).

---

## abicheck native modes by evidence source (L0–L4)

The "native mode" above is not one fixed mode — it **adapts to the evidence you
give it**: each additional source (binary → debug info → headers → build data →
sources) switches on more detectors, and going down the ladder only ever
*adds* — never hides an artifact-proven break. Run `abicheck dump <lib>
--dry-run` to see exactly which `Lx` layers a binary affords.

> **Quick decision.** Stripped production binary → **L0**. Debug build, no
> headers handy → **L1**. Have the public headers → **L2** (do this whenever you
> can). Reproducible build tree → add **L3**. Need macro/default-arg/inline
> guarantees → **L4**.

For the full per-layer table (what you provide, what it catches) and the
authority rule (only L0–L2 can declare `BREAKING`), see [Evidence &
Detectability](../learn/evidence-and-detectability.md); for the practical
`--depth` flag mapping, see [Evidence Depth](evidence-depth.md); for a
worked walkthrough of what each layer actually sees on one example, see
[What Each Level Sees](../learn/what-each-level-sees.md).

---

## Decision Flowchart

This flowchart applies when you replicate the analysis with the **external
reference tools**. With abicheck native, you simply pass headers (and `-g`
binaries when available) and abicheck picks the strongest layers automatically.

```mermaid
flowchart TD
    P["Project policy:<br/>public headers mandatory"]
    H{"Headers available?"}
    FAIL["Fail fast — fetch the<br/>devel/include package first"]
    G{"Was the .so compiled<br/>with -g (debug symbols)?"}
    PROD["Production / stripped .so<br/><br/>abidiff+headers + ABICC+headers (Usage #2)<br/>Any break from either → ABI-breaking"]
    DBG["CI / staging debug build<br/><br/>abidiff+headers + ABICC+dump (Usage #1)<br/>Most accurate: DWARF ground truth<br/>(no compiler needed)"]

    P --> H
    H -- no --> FAIL
    H -- yes --> G
    G -- no --> PROD
    G -- yes --> DBG
```

> **Production default:** abidiff+headers + ABICC+headers (ABICC Usage #2).
> Production `.so` files have no debug info → Usage #1 unavailable.

---

## abidiff + headers (libabigail)

### Overview

`abidiff` from **libabigail** compares two `.so` files using their ELF symbol tables
and optionally DWARF debug sections. **We always pass headers** via `--headers-dir`
to improve type resolution.

### How it works

```
libv1.so ──► abidw --headers-dir include/ ──► v1.xml ──┐
                                                         ├──► abidiff ──► report
libv2.so ──► abidw --headers-dir include/ ──► v2.xml ──┘
```

### Requirements

| Requirement | Mandatory? | Notes |
|-------------|-----------|-------|
| Two `.so` files | ✅ | Core input |
| Headers (`--headers-dir`) | ✅ our policy | Greatly improves type resolution |
| DWARF debug info (`-g`) | ❌ optional | Provides additional type layout info |
| Compiler | ❌ | Not needed |

### What it catches

- ✅ Symbol removal/addition
- ✅ Type layout changes (struct/class field changes) — with DWARF or headers
- ✅ vtable changes — with DWARF
- ✅ Return type, parameter type changes — with DWARF/headers
- ✅ Enum value changes
- ✅ ELF-only symbol changes (visibility, binding)

### What it misses

- ❌ `noexcept` specifier (not in DWARF or ELF)
- ❌ `inline` → non-inline ODR changes (inline functions absent from `.so`)
- ❌ C++ `[[nodiscard]]`, `[[deprecated]]`, `explicit` attribute changes
- ❌ Template instantiation details without DWARF
- ❌ Dependency ABI leaks (transitive header type changes) without DWARF

### Usage

```bash
sudo apt-get install abigail-tools

abidw --headers-dir include/ --out-file v1.xml libv1.so
abidw --headers-dir include/ --out-file v2.xml libv2.so
abidiff v1.xml v2.xml
```

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | No ABI change |
| 4 | ABI change (type/layout diff or compatible addition) |
| 12 | Breaking change (symbol removed) |

---

## ABICC+headers (ABICC Usage #2 — Original / Headers Mode)

> This is what `abi-compliance-checker` calls **USAGE #2 (ORIGINAL)** in its docs.

### Overview

ABICC receives an XML descriptor pointing to the `.so` and headers directory. It uses
**GCC** to compile the headers, extract the full C++ AST, compute type layouts, and
build an ABI dump. Then it compares two such dumps.

### How it works

```
OLD.xml (headers + .so) ──► abi-compliance-checker ──► ABI-old.dump ──┐
                                 (compiles via GCC)                     ├──► report
NEW.xml (headers + .so) ──► abi-compliance-checker ──► ABI-new.dump ──┘
```

### Requirements

| Requirement | Mandatory? | Notes |
|-------------|-----------|-------|
| Two `.so` files | ✅ | |
| Headers | ✅ | The main input for ABI description |
| `abi-compliance-checker` | ✅ | |
| **GCC** | ✅ **GCC only** | ABICC calls GCC internally to compile headers. Proprietary compilers (`icpx`/`icc`) and Clang are **not supported**. |
| DWARF debug info | ❌ | Not needed — headers provide type information |

> **Note:** GCC must be installed even if the library itself is built
> with a different compiler (e.g. `icpx`). ABICC only uses GCC to parse headers, not to compile the library.

### What it catches (beyond abidiff)

- ✅ Everything abidiff catches (with headers as source)
- ✅ `noexcept` specifier changes
- ✅ `inline` → non-inline ODR (symbol absent from v1 .so, appears in v2)
- ✅ C++ attribute changes (`[[nodiscard]]`, `explicit`, etc.)
- ✅ Template instantiation ABI via AST
- ✅ Dependency ABI leaks (if transitive headers are included)
- ✅ Works on stripped production `.so` (no `-g` needed)

### What it misses

- ❌ ELF-only symbol visibility changes (no symbol table analysis)
- ❌ Anonymous struct/union not expressible in headers
- ❌ Types resolved differently by `#ifdef`/macro guards at compile time (header AST may differ from actual compiled result)

### Usage

```bash
sudo apt-get install abi-compliance-checker gcc

# Create OLD.xml descriptor:
cat > OLD.xml << EOF
<version>1.0</version>
<headers>/path/to/v1/include/</headers>
<libs>/path/to/libfoo_v1.so</libs>
EOF

# Create NEW.xml similarly, then compare:
abi-compliance-checker -lib libfoo -old OLD.xml -new NEW.xml
```

---

## ABICC+dump (ABICC Usage #1 — abi-dumper / Binary Mode)

> This is what `abi-compliance-checker` calls **USAGE #1 (WITH ABI DUMPER)** in its docs.

### Overview

`abi-dumper` reads DWARF debug sections directly from the `.so` binary and produces
a `.dump` file. **No compiler is involved at any step.** ABICC then compares two dumps.

### How it works

```
libv1.so (with -g) ──► abi-dumper ──► ABI-1.dump ──┐
                                                     ├──► abi-compliance-checker ──► report
libv2.so (with -g) ──► abi-dumper ──► ABI-2.dump ──┘
```

### Requirements

| Requirement | Mandatory? | Notes |
|-------------|-----------|-------|
| Two `.so` compiled with `-g -Og` | ✅ | DWARF is the input — no debug info = no dump |
| `abi-dumper` | ✅ | `sudo apt-get install abi-dumper` |
| `abi-compliance-checker` | ✅ | |
| `universal-ctags` | ✅ | Required by abi-dumper |
| `vtable-dumper` | ✅ | For C++ vtable extraction |
| Compiler | ❌ **not needed** | abi-dumper reads binary DWARF, no compilation |
| Headers | ❌ optional | `abi-dumper -public-headers include/` filters to public API |

### What it catches (beyond Usage #2)

- ✅ Anonymous struct/union layouts (in DWARF but not expressible in headers)
- ✅ Types resolved by compiler flags/macros (DWARF = actual compiled result)
- ✅ Complex typedef chains to actual underlying types
- ✅ Bit-field layouts at bit-level precision
- ✅ `#pragma pack` effects
- ✅ Types from `.cpp` implementation files leaked into ABI

### What it misses

- ❌ Inline-only (header-only) API — never compiled into `.so`, no DWARF
- ❌ `noexcept` specifier (not stored in DWARF)
- ❌ ELF-only symbol visibility changes
- ❌ Requires `-g` build — not available for production stripped binaries

### Limitations

1. **Requires debug builds** — production `.so` files are stripped. This mode
   requires CI/staging debug builds.
2. **No compiler required, but debug info required** — the `.so` must be compiled
   with `-g -Og`. The compiler used (GCC, icpx, clang) does not matter for this mode —
   abi-dumper reads DWARF directly.

### Usage

```bash
sudo apt-get install abi-dumper abi-compliance-checker universal-ctags vtable-dumper

# Build with debug info (any compiler: gcc, icpx, clang)
g++ -shared -fPIC -g -Og -o libfoo_v1.so src_v1.cpp

abi-dumper libfoo_v1.so -o ABI-1.dump -lver 1.0 -public-headers include/
abi-dumper libfoo_v2.so -o ABI-2.dump -lver 2.0 -public-headers include/
abi-compliance-checker -lib libfoo -old ABI-1.dump -new ABI-2.dump
```

---

## Reference parity combination

abicheck's **native** mode (above) is the production default and needs no
external tools. When you instead want to replicate the analysis with the
external reference tools — for parity benchmarking or in an ABICC-only
environment — the recommended combination is **abidiff+headers + ABICC+headers
(ABICC Usage #2)**, taking the worst-of verdict:

```mermaid
flowchart LR
    A["abidiff + headers"] --> R1["ELF-level report"]
    B["ABICC + headers (Usage #2)<br/>GCC compiles headers"] --> R2["AST-level report"]
    R1 --> V["Combined verdict<br/>(worst-of: any break = breaking)"]
    R2 --> V
```

**Why not Usage #1 by default:**
- Production `.so` files are stripped (no `-g`) — abi-dumper cannot read DWARF
- Usage #2 works on production binaries and catches the C++ semantic cases (noexcept,
  templates, ODR) that abidiff misses
- Usage #1 is available as an optional mode when CI/staging provides debug builds

**Why two tools combined:**
- abidiff catches ELF-only symbol changes that ABICC+headers (ABICC Usage #2) misses
- ABICC+headers (ABICC Usage #2) catches noexcept/template/ODR that abidiff misses
- Together they cover the full ABI contract

---

## Tool Comparison Quick Reference

| ABI break type | abicheck (native) | abidiff+headers | ABICC+headers (ABICC Usage #2) | ABICC+dump (ABICC Usage #1) |
|---|:---:|:---:|:---:|:---:|
| Symbol removed | ✅ | ✅ | ✅ | ✅ |
| Symbol added | ✅ | ✅ | ✅ | ✅ |
| Param type change | ✅ | ✅ | ✅ | ✅ |
| Struct layout change | ✅ | ⚠️ DWARF | ✅ | ✅ |
| vtable change | ✅ | ⚠️ DWARF | ✅ | ✅ |
| `noexcept` removed | ✅ | ❌ | ✅ | ❌ |
| `inline` → non-inline | ✅ | ❌ | ✅ | ❌ |
| Template ABI | ✅ | ⚠️ DWARF | ✅ | ✅ |
| Dependency leak | ⚠️ DWARF | ⚠️ DWARF | ✅ | ✅ |
| Anonymous types | ⚠️ DWARF | ❌ | ❌ | ✅ |
| Macro-resolved types | ⚠️ DWARF | ❌ | ❌ | ✅ |
| ELF/PE/Mach-O visibility | ✅ | ✅ | ❌ | ❌ |
| Calling-convention drift | ✅ | ❌ | ❌ | ⚠️ DWARF |
| Cross-platform (PE, Mach-O) | ✅ | ❌ | ❌ | ❌ |
| Needs compiler | ⚠️ headers only | ❌ | ✅ GCC | ❌ |
| Needs debug build | ❌ | ❌ | ❌ | ✅ |


## Selecting a header frontend for a compiler context

The compiler that built the library, the frontend reading its headers, and the
compiler building a consumer are separate inputs. Selecting a header backend
never rebuilds the library and does not certify every possible consumer.
`auto` remains CastXML unless the environment pins a frontend. Explicit
`compile.frontend` / `--set compile.frontend=...` choices take precedence over that pin.

| Build / consumer context | Header frontend | Compiler selection and limits |
|---|---|---|
| GCC / G++ | CastXML is the conservative default | Pass the matching compiler and public-header flags. CastXML uses its embedded Clang while emulating the selected compiler's predefined macros; it is not GCC's own AST and cannot guarantee every GCC extension. |
| Clang / Apple Clang | Explicit `clang` | Pass the intended `clang` / `clang++` executable, including a versioned path. An installed newer Clang does not reproduce an older compiler's predefined feature macros automatically. |
| Intel oneAPI SYCL | Explicit `clang`, with `icpx` / `dpcpp` | Preserve the SYCL flags and requested host/device context. System Clang is not a substitute for DPC++. CastXML/hybrid cannot represent a requested device context. |
| Several supported consumer toolchains | A separate configured scan per context | Keep the target, language standard, standard library, defines and ABI options for each context. Do not treat one successful scan as a consumer matrix. |

For a direct-Clang parse, `compile.compiler: g++` does **not** select GCC to execute the
AST pass: that pass resolves a Clang driver. Choose CastXML when GCC macro
emulation is required. Conversely, select the actual Clang-family executable
for Clang or SYCL rather than relying on whichever driver happens to be on PATH.
The dry-run receipt reports the resolved frontend and missing tools.

Keep old/new inputs in the same intended comparison context: for example,
`_GLIBCXX_USE_CXX11_ABI`, `-fshort-enums`, packing, target architecture, sysroot,
`-std`, and libstdc++ versus libc++ can affect the contract. Compiler-version
changes and library-version changes should be separate matrix axes. The stored
acquisition context is evidence of what was scanned, not a proof that a different
consumer compiler will accept the same declarations or use the same ABI.

Direct Clang has documented extraction gaps in [known gaps](../contribute/known-gaps.md).
In particular, a successful parse is not evidence of semantic parity with
CastXML. The unconditional default change was deferred after the complete CI
matrix exposed missing type identities, enum representations and template
coverage differences. Select Clang explicitly for a validated context; changing
the default requires closing those gaps across the supported workflows.

Compiler references: [CastXML compiler emulation](https://github.com/CastXML/CastXML/blob/master/doc/manual/castxml.1.rst),
[Clang compatibility](https://clang.llvm.org/docs/UsersManual.html), and
[oneAPI DPC++ compilation modes](https://intel.github.io/llvm/UsersManual.html).

Extractor generation 52 corrects ordinary Clang enum representations and adds
function-slot type identities. Re-dump older Clang baselines when using those
facts: a stored `int` does not reveal whether the original enum was packed,
and missing identities cannot be recovered safely from a bare type name.

For a producer/consumer split, use the project's `profiles[].compile` and
`profiles[].consumer_compile` overlays with real toolchain bindings. The
check-project/check-target workflow dumps the unchanged candidate binary under
the consumer header context; baseline publication resolves the same consumer
overlay, so old and new use the same context. This does not compile a client
program or prove every client's runtime behavior. See the
[multi-platform profile workflow](../integration/scenarios/multi-platform.md).
