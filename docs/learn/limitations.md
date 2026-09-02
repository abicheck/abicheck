---
doc_type: explanation
audience:
  - library-maintainer
level: intermediate
canonical_for:
  - platform-support-matrix
summarizes:
  - static-and-header-only
lifecycle: active
generated: false
---

# Limitations & Known Boundaries

`abicheck` is designed to catch real ABI and API breaks with high accuracy, but has specific
limitations you should understand before relying on it in production.

> **Conceptual companion.** This page is the *practical* boundary list. For the
> *why* — which evidence (symbols, debug info, headers, source, runtime, bundle)
> lets any tool see a given change at all, and what no artifact comparison can
> prove — see [Evidence & Detectability](evidence-and-detectability.md).

---

Setup failures — a frontend that will not parse, a rejected toolchain, a
missing dependency — are not limitations of the model and live in
[Troubleshooting](../use/troubleshooting.md), which also carries the
diagnostic decision tree for unexpected verdicts.

## Platform support matrix

| Platform | Binary format | Binary metadata | Header AST (castxml) | Debug info cross-check |
|----------|--------------|:---------------:|:--------------------:|:----------------------:|
| Linux | ELF (`.so`) | Yes (pyelftools) | Yes (GCC, Clang) | Yes (DWARF) |
| Windows | PE/COFF (`.dll`) | Yes (pefile) | Yes (MSVC, MinGW) | Yes (PDB) |
| macOS | Mach-O (`.dylib`) | Yes (macholib) | Yes (Clang, GCC) | **No** |

**Header AST analysis** (via castxml) is available on all platforms. castxml is
maintained by Kitware and available via conda-forge, Homebrew, apt, or direct download.

**Debug info cross-check** uses DWARF (Linux only) and PDB (Windows). PDB
support extracts struct/class/union layouts, enum types, calling conventions, and
toolchain info from PDB files produced by MSVC (`/Zi` flag). Use `--pdb-path` to
specify the PDB file location if automatic discovery fails. **Mach-O has no
debug-info cross-check**: `abicheck` has no Mach-O debug-map/DWARF reader
today, so a headerless macOS `.dylib`'s own binary/debug-info evidence is
always L0 (exports + load-command metadata) only, even when the binary
carries debug info — this is about the L0/L1 binary-evidence layers
specifically, not the whole scan. `-H` (via either header-AST frontend,
castxml or `--ast-frontend clang`) is the way to get past exports-only
binary evidence on this platform; `--sources`/`--build-info` can still
attach L3–L5 build/source evidence independently of the platform. See
[Architecture](architecture.md) and the
[platform evidence table](../reference/platforms.md#what-no-headers-actually-means)
for the full picture.

**"Yes" above means implemented capability, not per-toolchain CI-proven
maturity.** Windows in particular has more than one toolchain path with very
different validation status — see the next section before relying on a
specific Windows toolchain in production.

---


### Windows toolchain distinction

Windows support depends on the compiler/toolchain used for headers and binary production:

Two distinct paths have different maturity — don't read "MSVC" as a single status:

| Toolchain / path | Status | Notes |
|----------|--------|-------|
| MinGW (GCC) | **Experimental** | Covered by current CI smoke/integration jobs. |
| MSVC PE/COFF + PDB — *binary & verdicts* | **Parsers unit-tested; MSVC e2e non-blocking** | The PE/PDB parsers have (blocking) unit tests. The `windows-msvc` end-to-end lane asserts MSVC+PDB verdicts (PDB layout depth best-effort) but runs `continue-on-error` (informational, does **not** block CI) until proven stable — treat MSVC verdicts as experimental. |
| MSVC `castxml` + `cl.exe` — *native header/type analysis* | **Untested in CI** | Expected to work in many cases, but this native header path is not yet validated end-to-end. |

Tracked ABICC compatibility issues for this area: **#9, #50, #56, #121**.
For detailed matrix + per-issue notes, see [Platform Support](../reference/platforms.md#windows-toolchain-support-matrix).

## Header / Binary Mismatch Risk

**The most important limitation.** `abicheck` uses `castxml` to parse headers and
compares the result against the compiled `.so`. If the headers passed to analysis
don't exactly match what was compiled, results will be unreliable.

**This happens when:**
- You pass generic system headers but the library was compiled with custom `#define` flags
- Preprocessor macros change the public API surface (`#ifdef FEATURE_X`)
- Third-party dependency headers differ between versions
- Platform-specific code paths (`#ifdef __linux__`) differ between compile and analysis environments

**Mitigation:**
- Always use the exact same headers that were used to build the `.so`
- Pass the build's include roots, dialect, and defines to the header frontend:
  `abicheck dump libfoo.so -H foo.h -I include/ --compiler-option -std=c++20 --compiler-option -DFEATURE_X`
  (the same flags work on `abicheck scan`; persist them in a `.abicheck.yml`
  `compile:` block so every run is reproducible — see
  [Compile context for header parsing](../use/scan-levels.md#compile-context-for-header-parsing-l2))
- For `abicheck compat`, use `-s` (strict mode) to promote `COMPATIBLE`/`API_BREAK` to BREAKING:
  `abicheck compat check -lib foo -old OLD.xml -new NEW.xml -s`
  (use `--strict-mode api` to promote only `API_BREAK`; `-s` is not available on `abicheck compare`)
- Cross-check with `abicheck compat check` (ABICC mode) for independent validation

### System-include auto-detection (and what it does *not* fix)

The `clang` frontend now auto-detects the host C++ standard library the way
`castxml` always has (it probes `g++ -E -v` and injects the system include dirs),
so a bare `scan -H include/` finds `<cstddef>` without extra flags. This is
**system headers only** — it cannot guess your project's own `-I` roots, `-D`
feature macros, or the exact `-std`. Disable it with `--nostdinc`, an explicit
`--sysroot`, or `ABICHECK_AUTO_SYSTEM_INCLUDES=0`.

!!! danger "Scope divergence: missing L2 context can turn internal cleanups into false BREAKING"
    The header AST (L2) is what tells `abicheck` which symbols are **public**. If
    the headers cannot be parsed — because the compile context above is missing —
    the scan has only the binary, so it treats the export table as the surface and
    flags the removal of *internal* symbols (e.g. macro-guarded `detail::`/`impl::`
    helpers, or statically-bundled third-party routines) as BREAKING. These are
    **missing-context artifacts, not real breaks**: supply the include roots /
    dialect / defines (so L2 parses) and they demote to COMPATIBLE. Always read
    the scan's coverage block — if L2 is `not_collected`, treat any BREAKING on an
    `impl`/`detail`/`internal` symbol with suspicion and fix the header context
    first.

---

## Stripped Production Binaries

The layout-level checks (`L1`) require debug symbols (`-g`). Production `.so`
files are typically stripped, which removes the `L1` evidence source — in this
case:

- Struct field offset changes may be missed (`L1` layout unavailable)
- Calling convention drift, struct packing changes not detected (`L1` unavailable)
- Symbol-only checks (`L0`) and, if you pass headers, the header AST (`L2`) still
  run — most critical breaks are still caught

**Mitigation:** Use `--debug-root` to point abicheck at separate debug files
(distro debuginfo packages, build-id trees, or dSYM bundles). abicheck
automatically searches for debug artifacts via a resolver chain. For
Fedora/RHEL, use `--debuginfod` to fetch debug info by build-id from
debuginfod servers. See the [CLI usage guide](../use/cli-usage.md) for
details. For production binaries without debug info, `L0`+`L2` analysis covers
the majority of real-world ABI breaks. See
[Evidence & Detectability](evidence-and-detectability.md) for the full evidence
model.

---

## Template Instantiation

C++ template instantiations with complex type parameters can produce unexpected results:
- Explicit instantiations in `.so` are analyzed; implicit instantiations in headers are not
- Template specializations may not all be captured
- `case17_template_abi` in the examples demonstrates a detectable case

**Mitigation:** Use explicit template instantiation (`template class Foo<int>;`) for
ABI-sensitive types you want to guarantee are tracked.

---

## `COMPATIBLE` Does Not Mean "Invisible"

`COMPATIBLE` changes are detected and reported — they are not silent. Examples:
- Adding a new export symbol is `COMPATIBLE` but grows the library's API surface
  (relevant for semver policy: additive changes may still require a minor version bump)
- Enum member addition is `COMPATIBLE` but can affect exhaustive `switch` statements

For `abicheck compat` pipelines, use `-s` to treat `COMPATIBLE` as blocking.
For `abicheck compare` pipelines, a bare `compare` never fails on
`COMPATIBLE`/addition findings at all (legacy exit scheme: `0` compatible,
`2` source break, `4` ABI break — additions don't raise either). To block
on them, set any severity value — `--severity-preset`, or a per-category key
in `.abicheck.yml`'s `severity:` block (e.g. `addition: error`) — which
switches `compare` to the severity-aware exit scheme and makes an
error-level addition finding exit `1` — **but only when nothing already
pins the scheme explicitly.** If a project's `.abicheck.yml` sets
`exit_code_scheme: legacy` (auto-discovered or via `--config`), that
explicit project-config value outranks the implicit severity-flag
inference and the legacy scheme stays in effect; a `--severity-*` flag
alone won't switch it. In that case also pass `--exit-code-scheme severity`
explicitly (the CLI flag outranks project config), or remove the config's
`legacy` pin. See [Exit Codes](../reference/exit-codes.md) for the full
contract — exit `2` under the severity-aware scheme means an error-level
`potential_breaking` finding, not a `COMPATIBLE` addition.

---

## `compat` Mode Verdict Limitations

`abicheck compat` *does* emit exit code `2` for `API_BREAK` conditions, but the
report text uses ABICC-style phrasing rather than a bare `API_BREAK` verdict string.
Source-level-only breaks (e.g. `case31_enum_rename`, `case34_access_level`) will
appear as warnings in the compat HTML/text report.

Use `abicheck compare --format json` for precise machine-readable `API_BREAK` verdicts.

---

## Inline / Header-Only Code

Functions defined entirely in headers (inline, `constexpr`, template) may not appear
in the `.so` symbol table. By **default** (binary + headers only, no `--sources`),
abicheck analyzes the public exported ABI — header-only changes that don't affect
exported symbols will not be detected. **L4 source ABI replay** (`--sources`,
ADR-030) substantially closes this gap; see [Source & Build
Data](build-source-data.md#source-abi-replay-findings-l4) for the full list of
L4-only change kinds, and the next section for the residual that even L4 cannot see.

For the full compatibility model of a header-only (or static) library — what
still needs checking once there's no separate compiled artifact, and why
source-level evidence is the only way to check it at all — see [Static &
Header-Only Contracts](static-and-header-only.md).

---

## Source-only changes invisible to binary/object analysis

Some C++ changes are real source/API breaks that leave **no trace in the
compiled object** — the two `.so` files are ABI-identical. Comparing only
binaries (or stripped / DWARF-only builds) reports `NO_CHANGE` for them. This is
intrinsic to comparing *built artifacts*, not a bug.

abicheck addresses this with its layered model (see
[Architecture](architecture.md)). Each layer recovers signals the layers below
cannot see:

The internal label names below map onto the `L0`–`L2` evidence codes used
everywhere else in the docs (see
[Evidence & Detectability](evidence-and-detectability.md)):

| Evidence code | Internal label | Data source | Recovers |
|:-------------:|----------------|-------------|----------|
| `L0` | `elf_only` | symbol table only | symbol add/remove, versioning |
| `L1` | `dwarf_aware` | DWARF/PDB (needs `-g` / `/Zi`) | struct layout, field offsets, enum values, calling convention, struct packing |
| `L2` | `header_aware` | public headers via castxml | source-level qualifiers — `final`, access, ref-qualifiers, `inline`, `noexcept`, `explicit`, **default-argument values**, **`const`/`constexpr` constant values** |

So whether a change is detectable depends on the evidence you give abicheck. The
first three columns are the **artifact tiers** (L0–L2, no source parsing); the
fourth is abicheck's own **L4 source ABI replay** (`--sources`,
ADR-030) — not a separate external tool:

| Change | object/DWARF | header (castxml) | abicheck L4 (`--sources`) |
|--------|:---:|:---:|:---:|
| Class gains `final` ([`case125`](../reference/examples/case125_class_became_final.md)) | ❌ invisible | ✅ `type_became_final` | ✅ |
| Method access narrowed ([`case34`](../reference/examples/case34_access_level.md)) | ❌ invisible | ✅ `method_access_changed` | ✅ |
| Ref-qualifier change (`& → &&`) | ❌ (DWARF has no ref-qual) | ✅ `func_ref_qual_changed` | ✅ |
| Default argument removed/changed ([`case123`](../reference/examples/case123_default_argument_removed.md), [`case32`](../reference/examples/case32_param_defaults.md)) | ❌ invisible | ✅ `param_default_value_removed` / `_changed` | ✅ `default_argument_changed` |
| `const`/`constexpr` constant value changed ([`case124`](../reference/examples/case124_header_constant_value_changed.md)) | ❌ invisible (internal linkage, no symbol) | ✅ `constant_changed` | ✅ `constexpr_value_changed` |
| `#define` macro constant changed ([`case156`](../reference/examples/case156_public_macro_removed.md)) | ❌ invisible | ❌ (castxml emits no macros) | ✅ `public_macro_value_changed`/`_removed` |
| Inline/`constexpr`/template function *body* change (signature unchanged) | ❌ invisible | ❌ (declaration only; body not modelled) | ✅ `inline_body_changed`/`template_body_changed` |
| Public header-only inline function *removed* entirely ([`case157`](../reference/examples/case157_inline_function_removed.md)) | ❌ invisible | ❌ (no exported symbol to compare) | ✅ `inline_function_removed` |
| Uninstantiated template signature/body changed ([`case122`](../reference/examples/case122_template_signature_uninstantiated.md)) | ❌ invisible | ❌ (castxml omits uninstantiated templates) | ✅ `template_body_changed` (a template that disappears entirely is `uninstantiated_template_removed`) |

The upper rows are recovered by **supplying public headers** (L2/`header_aware`)
— note that several (default-argument values, `const`/`constexpr` constant
values) leave *no symbol at all* in the binary, so only header analysis can reach
them. The lower three rows are code that never becomes a symbol *and* is not
modelled by castxml (`#define` macros, inline/template **bodies**, uninstantiated
templates); these require the **L4 source ABI replay** layer (needs clang, or
castxml for the declaration subset) — see [Source ABI replay findings
(L4)](build-source-data.md#source-abi-replay-findings-l4) for the full change-kind
list and its evidence-tier caveats (L4 findings are `API_BREAK`/risk, never
`breaking`, per the authority rule). Without `--sources`, these rows are genuinely
invisible to abicheck; with it, they are not — binary, header, and source-replay
analysis are complementary layers of the same tool, not a tool-vs.-tool boundary.

> Constant extraction is deliberately scoped to the **user-provided public
> headers** — `const`/`constexpr` values pulled in transitively from system or
> private headers are *not* reported, so the finding stays a real public-API
> contract change rather than third-party noise.

### Recommendation: feed abicheck `.so` + debug info + headers for the best result

The three tiers are additive, and the **maximum-coverage configuration is a
single comparison of debug-enabled libraries with their public headers supplied**:

```bash
# Build (or obtain) BOTH versions with -g, then compare WITH headers:
abicheck compare libfoo_v1.so libfoo_v2.so \
    --header old=include/v1/foo.h --header new=include/v2/foo.h
```

This combination gives you all three tiers at once:

- **`.so` + DWARF (`-g` / `/Zi`)** → ground-truth *emitted* ABI: struct layout,
  field offsets, alignment/packing, enum values, calling convention — exactly as
  the compiler produced them.
- **public headers (castxml)** → source-level API surface the binary cannot carry:
  `final`, access, ref-qualifiers, `noexcept`/`explicit`, **default-argument
  values**, and **`const`/`constexpr` constant values** (which have no symbol).

These three artifact tiers are layers **L0–L2** of the [five-source evidence
model](evidence-and-detectability.md). Two further layers refine the result
without ever overriding an artifact-proven break: **L3** build context
(`-p build/`, the exact ABI-affecting flags) and **L4** source/build/source packs
(`--build-info`, recovering macro/`constexpr` and
uninstantiated-template facts). They are optional but raise confidence and
localize findings — see [Source & Build Data](build-source-data.md).

Comparing a **stripped release binary with no headers** gives only `elf_only`
coverage (symbol add/remove) and will silently miss every layout and
source-level break above. If you ship stripped, build a **debug copy purely as an
analysis input** and compare that with headers — even though the shipped artifact
stays stripped. (See [Stripped Production Binaries](#stripped-production-binaries)
if you can only obtain debug info as separate files.)

---

## Static / import library archives (`.a`, `.lib`)

`abicheck` analyses **single linkable images** — shared libraries (`.so`,
`.dll`, `.dylib`) and individual object files — not static/import library
archives (`.a` on Unix, `.lib` on Windows, both `ar`-format member
containers). This is a deliberate non-goal: a static archive has no runtime
ABI surface for abicheck's verdict semantics to be built on. See [Project
Goals → Non-goals](../contribute/goals.md#non-goals) for the full reasoning.

Handing a `.a`/`.lib` to `dump` or `compare` produces a **clear, actionable
error** rather than a misleading "unknown format" message or a traceback:

```text
'libfoo.a' is a static/import library archive (.a/.lib), which abicheck does
not analyse — it compares single linkable images (shared libraries and
objects). Extract the members (e.g. `ar x lib.a`) and compare the resulting
object files or the shared library built from them instead.
```

For the full compatibility model of a static (or header-only) library —
what's still worth checking without a dynamic ABI boundary, and the
recommended practical workaround (building a purpose-built shared object
from the same sources so abicheck has a real artifact to compare) — see
[Static & Header-Only Contracts](static-and-header-only.md).

---

## ELF-Only Mode and Symbol Filtering

Run without header files — i.e. directly against `.so` binaries — abicheck
infers the public ABI surface from exported ELF symbols (`.dynsym`), falling
back to a strictly symbols-only view only when the binary also carries no
usable DWARF. Shared libraries often export symbols that aren't part of their
intended public ABI (statically-linked compiler runtime internals, transitive
C++ stdlib symbols, private-namespace C separators), so abicheck applies a
heuristic ABI-relevance filter to `.dynsym` — without it, comparing two builds
that differ in compiler/stdlib provenance can trigger hundreds of spurious
BREAKING findings. That filter runs **whether or not headers are supplied**:
`-H` improves type evidence and surface scoping, but does not bypass it.

For the exact filtered prefixes, the filter's known limitations, and how
header scoping works on PE/Mach-O, see [ELF-Only Mode and Symbol
Filtering](elf-symbol-filtering.md).

---

**Ladder:** ← [ELF-Only Mode and Symbol Filtering](elf-symbol-filtering.md) · Concepts c2 · The evidence model · [Architecture](architecture.md) →
