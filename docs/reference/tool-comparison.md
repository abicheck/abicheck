# Benchmark & Tool Comparison

This document explains how each ABI checking tool works, what analysis method it uses,
benchmark results across real-world test cases, and why the numbers come out the way they do.

> **Note:** abicheck detects 269 change kinds (see [Change Kind Reference](change-kinds.md)).
> The current cross-tool benchmark covers a pinned 74-case subset of the
> `examples/` catalog (`case01`-`case73` + `case26b`); the full
> `examples/ground_truth.json` catalog now has 162 entries (153 compare-mode
> cases plus 9 single-build audit/cross-source cases), split as 157
> single-library cases plus 5 multi-library bundle cases. The subset is pinned
> so accuracy numbers stay reproducible across releases.
>
> **Which denominator is which.** Several case counts appear below because each
> lane measures a different slice: **162** = the whole catalog (157 single-library
> + 5 bundle); **134** = the catalog cases run through the default/debug *verdict*
> lane; **129** = the single-library cases that are *buildable* by the autodiscovery
> harness (excludes hand-built snapshot-only fixtures); **74** = the pinned
> cross-tool subset. They are subsets of the same catalog, not conflicting totals.

> **Why the tools disagree.** The accuracy gaps below are mostly an *evidence*
> story: each tool sees a different subset of the binary/debug/header inputs. For
> the conceptual model — which evidence detects which change class — see
> [Evidence & Detectability](../concepts/evidence-and-detectability.md).

---

## Current scan-quality snapshot

`Examples Validation` is the workflow for the runnable compare-mode catalog. It
validates the current abicheck scan quality separately from the pinned vendor
benchmark below: the catalog lanes answer "what does abicheck currently cover?",
while the pinned 74-case subset answers "how does abicheck compare to
ABICC/libabigail on a stable cross-tool corpus?"

| Scan | Scope | Execution | Result | Quality signal |
|------|:-----:|-----------|--------|----------------|
| Catalog metadata | 162 ground-truth entries | `examples/ground_truth.json` + `tests/test_evidence_tiers.py` | 153 compare / 9 audit-cross-source | Single source of truth for examples, verdicts, expected kinds, and minimum evidence |
| Build/autodiscovery | 161 integration items | `python -m pytest tests/test_example_autodiscovery.py -v --tb=short -m integration` in CI | gcc: 132 passed / 29 skipped; clang: 133 passed / 28 skipped | Green default single-library build lane; skipped items are covered by dedicated bundle/source/audit/BTF tests |
| Full example proof matrix | 162 catalog cases | `validation/scripts/collect_full_example_matrix.py` over CI artifacts + bundle/G20/L3-L5/BTF proofs | 159 COVERED / 3 UNRESOLVED | Full-catalog source of truth; a `SKIP` in one lane is accepted only when a dedicated lane proves the case |
| Default/debug verdicts | 162 catalog cases | `PYTHONPATH=. python tests/validate_examples.py --toolchain {gcc,clang} --json` in CI | gcc: 132 PASS / 4 XFAIL / 26 SKIP; clang: 133 PASS / 4 XFAIL / 25 SKIP | Single-library debug lane only; XFAIL is not green full-catalog coverage |
| Bundle release verdicts | 5 bundle cases | `PYTHONPATH=. python validation/scripts/run_bundle_examples.py --json` | 5 PASS | Runs the ADR-023 multi-library examples through `abicheck compare old/ new/` |
| Runtime smoke | 162 catalog cases | `PYTHONPATH=. python validation/scripts/run_example_runtime_smoke.py --json` | 73 DEMONSTRATED / 52 NO_RUNTIME_SIGNAL / 8 BASELINE_SIGNAL / 29 SKIP | Runtime harness has no BUILD_ERROR/BASELINE_ERROR bucket |
| Release headers | 162 catalog cases | `validate_examples.py --artifact-variant release-headers --json` in CI artifact | 132 PASS / 4 XFAIL / 26 SKIP | Reduced-evidence informational lane; false-positive guard passed |
| Stripped headers | 162 catalog cases | `validate_examples.py --artifact-variant stripped-headers --json` in CI artifact | 127 PASS / 3 FAIL / 6 XFAIL / 26 SKIP | Reduced-evidence informational lane; three expected signal-loss backlogs remain |
| Build/source smoke | 7 representative cases | `validate_examples.py case01 case04 case129 case130 case131 case132 case133 --artifact-variant build-source --json` in CI artifact | 7 PASS | Build/source evidence catches the build-flag mode cases in the smoke set |

Current unresolved full-example cases: `case97_api_depends_on_consumer_env`,
`case105_concept_tightening`, and
`case111_enumerable_thread_specific_lambda_ambiguity`. These remain blockers for
claiming full-catalog expected-behavior coverage.

Current stripped-header signal-loss cases: `case103_toolchain_flag_drift`,
`case117_no_unique_address`, and `case129_struct_return_convention`.

The full release/stripped/build-source mode matrix is intentionally not a
blocking CI gate. It remains a manual extended-scan path because it is much
heavier than the default/debug full-catalog gate.

---

## How each tool analyses ABI

### abicheck (compare mode)

```
.so (v1) ──► ELF reader: exported symbols, SONAME, visibility
             castxml (Clang AST): types, methods, vtable, noexcept
             DWARF reader: size cross-check
          ──► snapshot (JSON)
                              ├──► checker engine ──► verdict
.so (v2) ──► (same) ──► snapshot (JSON) ┘
```

**Analysis basis:** ELF symbol table + Clang AST via castxml + DWARF.
**Header requirement:** Yes — headers are passed to castxml for full type analysis.
**Compiler requirement:** None — castxml runs separately as a standalone tool.

This gives abicheck three independent data sources per symbol: ELF (what is exported),
AST (what the C++ type contract says), and DWARF (actual compiled layout for cross-check).

---

### abicheck (compat mode)

Same analysis engine as `compare`, but accepts **ABICC-format XML descriptors**
instead of snapshots:

```xml
<descriptor>
  <version>1.0</version>
  <headers>/path/to/include/foo.h</headers>
  <libs>/path/to/libfoo.so</libs>
</descriptor>
```

Used as a drop-in for ABICC-based CI pipelines (`abicheck compat check -lib foo -old v1.xml -new v2.xml`).

**Why compat scores lower than compare mode:**
`compat` follows ABICC's verdict vocabulary: COMPATIBLE, BREAKING, NO_CHANGE.
It cannot represent the full `compare` verdict vocabulary cleanly in ABICC-style
pipelines, especially source-level-only breaks that are binary-safe (for example
an enum/member rename or reduced access level in a class method).
This is intentional and documented in `examples/ground_truth.json` as `expected_compat`.

**When to use `compat`:** When you have an existing ABICC XML pipeline and want to
migrate to abicheck without rewriting scripts.
**When to use `compare`:** For all new integrations — full verdict set including `API_BREAK`.

---

### abicheck (strict mode)

`compat` with `-s` / `--strict` flag. Promotes `COMPATIBLE` → `BREAKING` and
`API_BREAK` → `BREAKING`.

Two sub-modes via `--strict-mode`:
- `full` (default with `-s`): `COMPATIBLE` + `API_BREAK` → `BREAKING` (matches ABICC `-strict`)
- `api`: only `API_BREAK` → `BREAKING`, additive `COMPATIBLE` changes stay `COMPATIBLE`

**Why strict scores lower than compat mode:**
Several catalog cases are legitimately `COMPATIBLE` or `API_BREAK`. `--strict-mode full`
promotes these to `BREAKING` intentionally, just like ABICC `-strict`. These are correct
tool outputs for the strict policy, but score as misses against the ground truth.

**Why strict still has a full denominator:**
`abicheck strict` runs on all 74 cases in the benchmark subset. ABICC and abidiff runs can time out or error on
specific cases, so their scored denominators are lower in the benchmark matrix.

**When to use strict:** CI gates where any COMPATIBLE addition (e.g. new symbol) should
fail the build. Use `--strict-mode api` to avoid false positives on purely additive changes.

---

### abidiff (ELF mode, no headers)

```
.so (v1) ──► abidw ──► ABI XML ──┐
                                  ├──► abidiff ──► report
.so (v2) ──► abidw ──► ABI XML ──┘
```

**Analysis basis:** DWARF (primary), CTF/BTF fallback; pure ELF symbol table if no debug info present.
**Header requirement:** None (in ELF mode).
**Compiler requirement:** None.

abidiff reads type information from DWARF sections of the `.so` when available. If DWARF
is absent it falls back to CTF (Oracle/Solaris-style binaries) or BTF (Linux kernel/eBPF
modules), and finally to ELF symbol names only when no debug info is present.

For our benchmark, all `.so` files are built with `-g` so DWARF is used throughout.

**Current benchmark result:** see the 74-case benchmark-subset matrix below.
abidiff misses anything that is not directly a symbol removal or a change that DWARF
fully describes. Specifically:
- Struct layout, vtable, return type changes → DWARF often marks as COMPATIBLE because
  it cannot determine binary impact without header type context
- Enum value semantics, typedef chains → COMPATIBLE
- noexcept, static qualifier, const qualifier, access level → not in DWARF at all

> **Stripped binaries (no debug info):** abidiff degrades to ELF-only (symbol names).
> abicheck continues to work via castxml — header-based type analysis does not need
> debug symbols. This makes abicheck significantly more useful for production binaries.

---

### abidw + headers → abidiff

```
.so (v1) ──► abidw --headers-dir /path/to/headers/ ──► ABI XML ──┐
                                                                   ├──► abidiff ──► report
.so (v2) ──► abidw --headers-dir /path/to/headers/ ──► ABI XML ──┘
```

> Note: `--headers-dir` is a flag for **`abidw`** (the dumper), not `abidiff` itself.
> The filtering happens at dump time; `abidiff` only compares the resulting XML.

**`--headers-dir` role:** Filters which symbols are considered public API.
It does **not** provide additional type information — `abidw` still reads types from DWARF.

**Why abidiff+headers tracks abidiff in our suite:**
Our benchmark examples are compiled with `-fvisibility=default`, meaning all symbols
are exported by default. None of the headers use `__attribute__((visibility("hidden")))`.
So the header filter changes nothing — all symbols are already public in both modes.
The fundamental limitation is that abidiff relies on DWARF for types, not AST.
Even with perfect headers, it cannot see noexcept, static-qualifier changes, or
source-level-only changes that have no ELF/DWARF representation.

**When would `--headers-dir` help?** If the library uses `visibility("hidden")` for internal
symbols in the headers, `--headers-dir` would filter them out and reduce false positives.
It does not improve detection of semantic changes.

---

### ABICC (abi-dumper workflow)

```
.so (v1, compiled with -g) ──► abi-dumper ──► v1.abi ──┐
                                                         ├──► abi-compliance-checker ──► report
.so (v2, compiled with -g) ──► abi-dumper ──► v2.abi ──┘
```

**Analysis basis:** DWARF — same as abidiff, but through Perl-based abi-dumper.
**Header requirement:** Optional (pass `-public-headers` to filter to public API).
**Compiler requirement:** None. Debug build (`-g`) required.

**Current benchmark result:** see the 74-case benchmark-subset matrix below. The abi-dumper workflow
still times out or errors on specific C++ cases and can leave runaway
`abi-compliance-checker` child processes if the outer wrapper is interrupted.

---

### ABICC (XML / legacy mode)

```
v1.xml (headers dir + .so path) ──► abi-compliance-checker (invokes GCC internally) ──► report
v2.xml (headers dir + .so path) ──┘
```

**Analysis basis:** GCC-compiled AST from headers.
**Header requirement:** Yes — must point to headers directory.
**Compiler requirement:** Yes — **GCC only**. Clang and icpx are not supported.

**Why ABICC(xml) is slow and unreliable:**
1. **GCC invocation per case** — even for 5-line headers, GCC startup costs dominate
2. **Directory input causes redefinition errors** — if the descriptor's `<headers>` tag
   points to a directory, `abi-compliance-checker` includes ALL `.h` files found there,
   including duplicates from build subdirs → redefinition errors → wrong verdicts
3. **GCC compatibility** — `abi-compliance-checker` uses `gcc -fdump-lang-class` internally,
   whose output format changed between GCC major versions. ABICC 2.3 prints a compatibility
   warning on every run when used with GCC 11+. Results may differ across GCC versions.
4. **`case16_inline_to_non_inline`**: reliably hits 120s timeout

**Current mitigation:** Pass a specific header file path instead of a directory
in `<headers>`. This drops runtime from 120s → ~1s and fixes wrong verdicts.

**Current benchmark result:** see the 74-case benchmark-subset matrix below.

---

## Verdict vocabulary comparison

| Verdict | abicheck compare | abicheck compat | abidiff | ABICC |
|---------|:---:|:---:|:---:|:---:|
| `NO_CHANGE` | ✅ | ✅ | ✅ (exit 0) | ⚠️ reports 100% compat |
| `COMPATIBLE` | ✅ | ✅ | ✅ (exit 4) | ⚠️ reports 100% compat |
| `API_BREAK` | ✅ | ❌ not supported | ❌ | ❌ |
| `BREAKING` | ✅ | ✅ | ✅ (exit 8+) | ✅ |

`API_BREAK` = source-level break, binary-compatible. Example: parameter renamed,
access level changed, pure API contract violation with no ABI binary change.
Only `abicheck compare` can emit this verdict.

---

## Why abicheck leads the matrix

abicheck uses three independent analysis passes per comparison:

1. **ELF pass** — symbol table diff: detects visibility changes, SONAME, symbol binding,
   symbol version policy, added/removed/renamed exported symbols
2. **castxml pass** — Clang AST diff: detects noexcept, static qualifier, const qualifier,
   method-became-static, pure virtual additions, access level, parameter/return type changes
   that are invisible in ELF/DWARF
3. **DWARF cross-check** — validates actual compiled type sizes, struct/class member offsets,
   vtable slot offsets, base class offsets, and `#pragma pack` / `-march`-sensitive alignment
   that header analysis alone may compute incorrectly

Neither abidiff nor ABICC runs all three passes. abidiff has no AST (misses noexcept, static,
const). ABICC has no ELF pass (misses SONAME, visibility). ABICC(dump) has no AST
(same gaps as abidiff plus instability on complex C++).

---

## Benchmarking by evidence tier

The cross-tool matrix above answers *"how does abicheck compare to other tools
when each is given its best input?"* A second, orthogonal benchmark answers
*"how much of the catalog can be discovered from each **source of information**?"*
— i.e. how detection grows as you feed abicheck more of the
[five sources](../concepts/evidence-and-detectability.md#0-the-five-sources-of-information).

This is tracked in two layers: `examples/ground_truth.json` records the minimum
evidence layer for each case, while a dedicated benchmark mode empirically scans
the runnable cases at progressively richer artifact layers:

```bash
python3 scripts/benchmark_comparison.py --evidence-tiers
# restrict to specific cases/suite as usual:
python3 scripts/benchmark_comparison.py --evidence-tiers --cases case01 case07 case34
```

> This is the **slow path**: it builds each case once and then runs the full
> `dump`+`compare` pipeline up to four times per case (L0-L3), so scope it with
> `--cases`/`--suite` for quick iteration.

For each case it builds the libraries once, then runs the full `dump`+`compare`
pipeline four times:

| Tier | abicheck input | `--show-data-sources` mode | Active detectors |
|:----:|----------------|----------------------------|:----------------:|
| **L0** binary only | stripped `.so`, no `-H` | Symbols-only | ≈ 6 / 30 |
| **L1** + debug info | `-g` `.so`, no `-H` | DWARF-only | ≈ 24 / 30 |
| **L2** + public headers | `-g` `.so`, `-H include/` | Full (AST + DWARF) | 30 / 30 |
| **L3** + build context | L2 plus `-p build/` (when a compile DB exists) | Full + build evidence | 30 / 30 + L3 |

> **L4 (source ABI replay)** uses the build/source pack produced by `collect`.
> The tiered benchmark runner does not exercise that mode yet, so the empirical
> L0-L3 run still reports L4-only cases as not reached until source-pack support
> is added. The table below includes the L4 minimum from `ground_truth.json`.

### Which source discovers what

Each case in [`examples/ground_truth.json`](../../examples/ground_truth.json)
carries a `min_evidence` field — the weakest source at which abicheck reaches the
correct verdict — derived by
[`scripts/evidence_tiers.py`](../../scripts/evidence_tiers.py)
and validated by `tests/test_evidence_tiers.py`. Aggregated over the 153-case
compare-mode catalog, that yields the cumulative minimum-evidence coverage:

| Source provided | Layer | Cases first detectable here | Cumulative | Representative cases |
|-----------------|:-----:|:---------------------------:|:----------:|----------------------|
| Just the binary | L0 | 50 | **50 / 153 (33%)** | symbol removal ([01](../examples/case01_symbol_removal.md)), SONAME ([05](../examples/case05_soname.md)), visibility ([06](../examples/case06_visibility.md)), symbol-version removed ([65](../examples/case65_symbol_version_removed.md)), all 5 bundle cases |
| + Debug symbols | L1 | 65 | **115 / 153 (75%)** | struct layout ([07](../examples/case07_struct_layout.md)), enum value ([08](../examples/case08_enum_value_change.md)), vtable ([09](../examples/case09_cpp_vtable.md)), calling convention ([64](../examples/case64_calling_convention_changed.md)), bitfield ([63](../examples/case63_bitfield_changed.md)), toolchain flag drift ([103](../examples/case103_toolchain_flag_drift.md)) |
| + Public headers | L2 | 22 | **137 / 153 (90%)** | access level ([34](../examples/case34_access_level.md)), default arg removed ([123](../examples/case123_default_argument_removed.md)), class `final` ([125](../examples/case125_class_became_final.md)), `detail::` leaks ([74](../examples/case74_detail_base_class_changed.md)–[77](../examples/case77_detail_templated_base_changed.md)), scoped-internal *no-change* ([118](../examples/case118_internal_struct_field_added_scoped.md)–[120](../examples/case120_internal_struct_reordered_scoped.md)) |
| + Build data | L3 | 8 | **145 / 153 (95%)** | build-mode flips: exceptions ([130](../examples/case130_exceptions_mode_flip.md)), RTTI ([131](../examples/case131_rtti_mode_flip.md)), thread-safe statics ([132](../examples/case132_threadsafe_statics_flip.md)), TLS model ([133](../examples/case133_tls_model_flip.md)), enum size ([152](../examples/case152_enum_size_flag_flip.md)), struct packing ([153](../examples/case153_struct_packing_flip.md)), LTO ([154](../examples/case154_lto_mode_flip.md)), char signedness ([155](../examples/case155_char_signedness_flip.md)) |
| + Sources | L4 | 5 | **150 / 153 (98%)** | uninstantiated template ([122](../examples/case122_template_signature_uninstantiated.md)), public macro removed ([156](../examples/case156_public_macro_removed.md)), inline function removed ([157](../examples/case157_inline_function_removed.md)), concept tightening ([105](../examples/case105_concept_tightening.md)), public typedef removed ([158](../examples/case158_public_typedef_removed.md)) |
| + Source graph | L5 | 3 | **153 / 153 (100%)** | public API internal dependency ([160](../examples/case160_public_api_internal_dep_added.md)), target dependency added ([161](../examples/case161_target_dependency_added.md)), exported symbol source owner changed ([162](../examples/case162_symbol_source_owner_changed.md)) |

> **Why L3 now matters.** Earlier snapshots had no standalone L3-only catalog
> cases. The current compare-mode catalog includes build-mode flips whose
> relevant facts come from build context when artifact metadata is insufficient:
> exceptions, RTTI, thread-safe statics, TLS model, enum size, struct packing,
> LTO, and char signedness policy.
>
> **Why L5 is listed.** L5 is a derived source graph, not a sixth input. It is
> included here because `ground_truth.json` uses it as the minimum evidence for
> source-to-symbol reachability cases.
>
> **Crediting rule.** A tier only counts as *discovering* a case when it emits
> the cataloged change **kind** with the right verdict, not merely a matching
> verdict — otherwise a weak tier that returns a bare `COMPATIBLE`/`NO_CHANGE`
> (the "found nothing" defaults) would be miscredited. Active `BREAKING`/`API_BREAK`
> verdicts are genuine findings, so a verdict match suffices there (and avoids
> penalising tier-appropriate variant kinds such as L0's `func_removed_elf_only`).

Two directions matter, not just one:

- **Discovery.** Most layout and source-only breaks are simply *invisible*
  without the right source — a struct-field insertion is `NO_CHANGE` at L0 and
  `BREAKING` only once L1 debug info is present.
- **False-positive suppression.** More evidence also *removes* spurious breaks:
  the scoped-internal cases ([118](../examples/case118_internal_struct_field_added_scoped.md)–[120](../examples/case120_internal_struct_reordered_scoped.md))
  change an internal struct that looks like a layout break at L1, and only L2
  header scoping lets abicheck correctly return `NO_CHANGE`.

> **Caveat.** The L2/L3 columns require `castxml` (and, for L3, a
> `compile_commands.json`) to be present in the benchmark environment; where a
> source is unavailable the runner records the tier as `n/a`/`ERROR` rather than
> a miss, so read the tiered numbers together with the
> [evidence-coverage](../concepts/build-source-data.md#evidence-coverage) report for
> the run.

---

## Pinned vendor benchmark summary (2026-05-19, 74-case subset)

Release-pinned scan status from `python3 scripts/benchmark_comparison.py --suite pinned74` on the original
74-case benchmark subset. ABICC runs used `--abicc-timeout 20` to keep known hangs bounded.

| Tool | Cases attempted | Scored | Correct | Accuracy | Not scored / notes |
|------|:---------------:|:------:|:-------:|:--------:|--------------------|
| abicheck compare | 74 | 74 | 74 | **100%** | Full exact match after forcing Clang for `case64` |
| abicheck compat | 74 | 74 | 71 | 95% | ABICC-style compatibility mode |
| abicheck strict | 74 | 74 | 62 | 83% | Intentional strict promotion of compatible/API breaks |
| abidiff | 74 | 73 | 22 | 30% of scored | `case16_inline_to_non_inline` hangs/timeouts |
| abidiff+headers | 74 | 73 | 22 | 30% of scored | `case16_inline_to_non_inline` hangs/timeouts |
| ABICC(dump) | 74 | 71 | 51 | 71% of scored | `case09`, `case59` timeout; `case16` error |
| ABICC(xml) | 74 | 72 | 50 | 69% of scored | `case16`, `case60` timeout |

### Scan-status matrix

| Check configuration | 74-case benchmark subset | Status |
|---------------------|:----------------:|--------|
| `abicheck` | ✅ 74/74 completed | 74/74 exact |
| `abicheck_compat` | ✅ 74/74 completed | 71/74 exact |
| `abicheck_strict` | ✅ 74/74 completed | 62/74 exact |
| `abidiff` | ⚠️ 73/74 completed | `case16_inline_to_non_inline` hangs |
| `abidiff_headers` | ⚠️ 73/74 completed | `case16_inline_to_non_inline` hangs |
| `abicc_dumper` | ⚠️ 71/74 scored | `case09`, `case59` timeout; `case16` error |
| `abicc_xml` | ⚠️ 72/74 scored | `case16`, `case60` timeout |

### Commands used

```bash
python3 scripts/benchmark_comparison.py \
  --suite pinned74 \
  --tools abicheck abicheck_compat abicheck_strict \
  --skip-abicc

# abidiff and abidiff+headers were run on all cases except case16,
# which hangs in both modes in this environment.
python3 scripts/benchmark_comparison.py \
  --suite pinned74 \
  --tools abidiff abidiff_headers \
  --skip-abicc \
  --cases case01_symbol_removal ... case73_typedef_underlying_changed

timeout 600 python3 scripts/benchmark_comparison.py \
  --suite pinned74 \
  --tools abicc_xml \
  --abicc-mode xml \
  --abicc-timeout 20

timeout 600 python3 scripts/benchmark_comparison.py \
  --suite pinned74 \
  --tools abicc_dumper \
  --abicc-mode dumper \
  --abicc-timeout 20
```

---

## Run the benchmark yourself

```bash
# Fresh benchmark for the current checkout
python3 scripts/benchmark_comparison.py --abicc-mode both
```

```bash
# Skip ABICC (CI-friendly, ~15s total)
python3 scripts/benchmark_comparison.py --skip-abicc
```

```bash
# Select specific cases or tools
python3 scripts/benchmark_comparison.py --cases case01 case09 case21
python3 scripts/benchmark_comparison.py --tools abicheck abidiff
```

---

## Choosing the right tool

| Scenario | Recommended |
|----------|-------------|
| New CI pipeline, full accuracy | `abicheck compare` |
| Migrating from ABICC XML pipeline | `abicheck compat check` |
| Strict gate (any addition = fail) | `abicheck compat check -s` |
| Debug build available, DWARF check | `abicheck compare` (castxml already better) |
| Quick ELF-only sanity check | `abidiff` (fast, 30% (22/73) but catches symbol removals) |
