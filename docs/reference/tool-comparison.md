# Benchmark & Tool Comparison

This document explains how each ABI checking tool works, what analysis method it uses,
benchmark results across real-world test cases, and why the numbers come out the way they do.

> **Note:** abicheck detects 384 change kinds (see [Change Kind Reference](change-kinds.md)).
> The current cross-tool benchmark covers a pinned 74-case subset of the
> `examples/` catalog (`case01`-`case73` + `case26b`); the full
> `examples/ground_truth.json` catalog now has 191 entries. Tool-to-tool
> competitor scans use the 134 binary shared-library `.so` lanes; fixture/source
> L2/L5/source cases (`case152`-`case158`, `case160`-`case164`, `case187`-`case191`)
> and the other audit, cross-source, bundle, BTF, and snapshot cases are tracked
> in dedicated non-`.so` lanes. The subset is pinned so accuracy numbers stay
> reproducible across releases.
>
> **Which denominator is which.** **191** is the whole catalog. The binary
> competitor lane is **134** shared-library pairs. The scan-depth matrix is
> compare-style and intentionally uses only comparable v1/v2 shared-library
> targets: **141/141** of that scope are scanned at every depth. FP/FN math now
> uses all **141** comparable targets: `NO_CHANGE` sentinel cases are checked as
> compatible/no-change outcomes, and bundle cases are scored against their single canonical case verdict;
> per-library `library_assertions` are structural diagnostics only. Dedicated lanes cover fixture/source-only L2/L5
> cases, audit, cross-source, bundle, BTF, and snapshot cases.

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
| Catalog metadata | 191 ground-truth entries | `examples/ground_truth.json` + `tests/test_evidence_tiers.py` | 134 binary competitor `.so` lanes + 52 dedicated non-`.so` lanes | Single source of truth for examples, verdicts, expected kinds, and minimum evidence; fixture/source-only L2/L5/source cases are not counted as binary competitor pairs |
| Build/autodiscovery | 161 integration items | `python -m pytest tests/test_example_autodiscovery.py -v --tb=short -m integration` in CI | gcc: 132 passed / 29 skipped; clang: 133 passed / 28 skipped | Green default single-library build lane; skipped items are covered by dedicated bundle/source/audit/BTF tests |
| Full example proof matrix | 191 catalog cases | `validation/scripts/collect_full_example_matrix.py` over CI artifacts + bundle/G20/L3-L5/BTF proofs | Dedicated full-catalog proof lane | Full-catalog source of truth; a `SKIP` in one lane is accepted only when a dedicated lane proves the case |
| Default/debug verdicts | 191 catalog cases | `PYTHONPATH=. python tests/validate_examples.py --toolchain {gcc,clang} --json` in CI | Single-library debug lane; dedicated non-`.so` cases skip here by design | Single-library debug lane only; XFAIL is not green full-matrix scope |
| Bundle release verdicts | 5 bundle cases | `PYTHONPATH=. python validation/scripts/run_bundle_examples.py --json` | 5 PASS | Runs the ADR-023 multi-library examples through `abicheck compare old/ new/` |
| Runtime smoke | 191 catalog cases | `PYTHONPATH=. python validation/scripts/run_example_runtime_smoke.py --json` | Runtime-only proof lane | Runtime harness has no BUILD_ERROR/BASELINE_ERROR bucket |
| Release headers | 191 catalog cases | `validate_examples.py --artifact-variant release-headers --json` in CI artifact | Reduced-evidence informational lane | False-positive guard passed |
| Stripped headers | 191 catalog cases | `validate_examples.py --artifact-variant stripped-headers --json` in CI artifact | Reduced-evidence informational lane | Expected signal-loss backlogs remain |
| Build/source smoke | 10 representative cases | `validate_examples.py case01 case04 case98 case105 case122 case129 case130 case131 case132 case133 --artifact-variant build-source --json` in CI artifact | 10 PASS | Build/source evidence catches the build-flag mode cases in the smoke set |
| Binary competitor scan | 134 shared-library pairs × 2 external tools | abicc/ABI Compliance Checker and libabigail `abidiff` over built `.so` pairs | 268 tool results: abicc 134, abidiff 134 | Competitor `.so` lane only; fixture/source-only L2/L5/source cases are represented in dedicated lanes, not as missing `.so` results |
| Scan-depth matrix | 141 comparable targets × 5 depths | `abicheck scan --depth {binary,headers,build,source,full}` | 141/141 scans completed at each depth. Correct/FP/FN on all 141 comparable targets: binary 79 / 1 / 61; headers 115 / 0 / 26; build 115 / 0 / 26; source 141 / 0 / 0; full 141 / 0 / 0 | Compare-style status by depth; full-catalog audit/cross-source/bundle/BTF/snapshot cases are covered by dedicated lanes |
`case97_api_depends_on_consumer_env` and `case105_concept_tightening` are
resolved: the former is proven by its own source_smoke oracle at the default
compiler lanes, the latter by the build/source (L4) lane. The one case not
proven by a direct detector/CLI match is
`case111_enumerable_thread_specific_lambda_ambiguity`: every evidence tier
(L0-L5) currently reaches `COMPATIBLE`, a real tracked detector gap (see its
README), so it is credited in the full example matrix via known-gap-oracle
provenance — its own `source_smoke` proves the canonical `API_BREAK` — rather
than direct coverage. See
[the validation runbook](../development/examples-validation-runbook.md) for
the direct-vs-known-gap-oracle accounting (180 direct + 1 known-gap-oracle
= 181 `COVERED`).

Current stripped-header signal-loss cases: `case103_toolchain_flag_drift`,
`case117_no_unique_address`, `case129_struct_return_convention`,
`case60_base_class_position_changed`, and `case69_trivial_to_nontrivial`.

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
an enum/member rename or reduced access level in a class method). The examples
still keep one canonical ground-truth verdict. If `compat` cannot express or
detect it, that is a command/evidence limitation, not an alternate expectation.

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

| Tier | abicheck input | `--dry-run` mode | Active detectors |
|:----:|----------------|----------------------------|:----------------:|
| **L0** binary only | stripped `.so`, no `-H` | Symbols-only | ≈ 6 / 30 |
| **L1** + debug info | `-g` `.so`, no `-H` | DWARF-only | ≈ 24 / 30 |
| **L2** + public headers | `-g` `.so`, `-H include/` | Full (AST + DWARF) | 30 / 30 |
| **L3** + build context | L2 plus `-p build/` (when a compile DB exists) | Full + build evidence | 30 / 30 + L3 |

> The `/30` denominator above is a point-in-time snapshot from an earlier run
> and has not been refreshed since (the registered-detector count is now 56,
> per `detector_registry.registry` — see `abicheck/detector_registry.py`).
> `--dry-run` also no longer reports a detector-enabled fraction at
> all (it now lists which `Lx` layers are present, with basic per-layer
> stats). Re-run `python3 scripts/benchmark_comparison.py --evidence-tiers`
> (needs `castxml` + `gcc`/`g++`) for current per-tier numbers rather than
> trusting this table.

> **L4 (source ABI replay)** uses the build/source pack produced by `collect`.
> The tiered benchmark runner does not exercise that mode yet, so the empirical
> L0-L3 run still reports L4-only cases as not reached until source-pack support
> is added. The table below includes the L4 minimum from `ground_truth.json`.

### Which source discovers what

Each case in `examples/ground_truth.json`
carries a `min_evidence` field — the weakest source at which abicheck reaches the
correct verdict — derived by
`scripts/evidence_tiers.py`
and validated by `tests/test_evidence_tiers.py`. Aggregated over the 153 compare-style cases, that yields the cumulative minimum-evidence coverage. The binary competitor `.so` lane is narrower (134 built shared-library pairs); fixture/source-only L2/L5/source cases are listed here by evidence tier instead of being treated as missing competitor binaries:

> **Freshness note.** `examples/ground_truth.json` now has 191 total entries
> (verified via `len(json.load(open("examples/ground_truth.json"))["verdicts"])`),
> not the 153/134 cited above — this table's per-tier breakdown predates
> case growth since it was last regenerated and has not been re-derived from
> `scripts/evidence_tiers.py` against the current catalog. Treat the *shape*
> (evidence compounds, L0→L1 is the biggest single jump) as durable and the
> exact counts/percentages as stale; regenerating this table against the
> current catalog is a follow-up, not done as part of this pass.

| Source provided | Layer | Cases first detectable here | Cumulative | Representative cases |
|-----------------|:-----:|:---------------------------:|:----------:|----------------------|
| Just the binary | L0 | 50 | **50 / 153 (33%)** | symbol removal ([01](../examples/case01_symbol_removal.md)), SONAME ([05](../examples/case05_soname.md)), visibility ([06](../examples/case06_visibility.md)), symbol-version removed ([65](../examples/case65_symbol_version_removed.md)), all 5 bundle cases |
| + Debug symbols | L1 | 65 | **115 / 153 (75%)** | struct layout ([07](../examples/case07_struct_layout.md)), enum value ([08](../examples/case08_enum_value_change.md)), vtable ([09](../examples/case09_cpp_vtable.md)), calling convention ([64](../examples/case64_calling_convention_changed.md)), bitfield ([63](../examples/case63_bitfield_changed.md)), toolchain flag drift ([103](../examples/case103_toolchain_flag_drift.md)) |
| + Public headers | L2 | 22 + dedicated fixture/source cases | **137 / 153 (90%)** | access level ([34](../examples/case34_access_level.md)), default arg removed ([123](../examples/case123_default_argument_removed.md)), class `final` ([125](../examples/case125_class_became_final.md)), `detail::` leaks ([74](../examples/case74_detail_base_class_changed.md)–[77](../examples/case77_detail_templated_base_changed.md)), scoped-internal *no-change* ([118](../examples/case118_internal_struct_field_added_scoped.md)–[120](../examples/case120_internal_struct_reordered_scoped.md)) |
| + Build data | L3 | 8, including fixture/source-only cases | **145 / 153 (95%)** | build-mode flips: exceptions ([130](../examples/case130_exceptions_mode_flip.md)), RTTI ([131](../examples/case131_rtti_mode_flip.md)), thread-safe statics ([132](../examples/case132_threadsafe_statics_flip.md)), TLS model ([133](../examples/case133_tls_model_flip.md)), enum size ([152](../examples/case152_enum_size_flag_flip.md)), struct packing ([153](../examples/case153_struct_packing_flip.md)), LTO ([154](../examples/case154_lto_mode_flip.md)), char signedness ([155](../examples/case155_char_signedness_flip.md)) |
| + Sources | L4 | 5 | **150 / 153 (98%)** | uninstantiated template ([122](../examples/case122_template_signature_uninstantiated.md)), public macro removed ([156](../examples/case156_public_macro_removed.md)), inline function removed ([157](../examples/case157_inline_function_removed.md)), concept tightening ([105](../examples/case105_concept_tightening.md)), public typedef removed ([158](../examples/case158_public_typedef_removed.md)) |
| + Source graph | L5 | 3 fixture/source-only cases | **153 / 153 (100%)** | public API internal dependency ([160](../examples/case160_public_api_internal_dep_added.md)), target dependency added ([161](../examples/case161_target_dependency_added.md)), exported symbol source owner changed ([162](../examples/case162_symbol_source_owner_changed.md)); additional dedicated source fixture examples include Python keyword rename ([163](../examples/case163_python_kwarg_renamed.md)) and preprocessor-conditional field guard ([164](../examples/case164_preproc_conditional_field.md)) |

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
>
> **Not the same number as the full-catalog benchmark below.** This staircase
> is a discoverability *floor* (the weakest source that reaches the correct
> verdict per case, credited from `ground_truth.json` labels — L4/L5 rows are
> not yet re-run empirically, see the caveat above); it does not penalize a
> tier for *over-calling* elsewhere in the catalog. The
> [full-catalog benchmark](#full-catalog-benchmark-2026-07-17-all-191-cases)
> below is the stricter, empirically-measured number — it scores all 186 cases
> including false positives, which is why `L3-L5` reads 90.9% there rather
> than the 100% this table's `L5` row shows.

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

## Full-catalog benchmark (2026-07-17, all 191 cases)

Every catalog case scored, with **SKIP/ERROR/TIMEOUT/incapacity all counted as
misses** — a tool that hung, crashed, or simply has no mode for a case shape
scores exactly like a wrong verdict. This is a stricter (and more honest)
denominator than "accuracy over cases the tool managed to complete," so read
it as the answer to *"if I pointed this tool at the whole catalog blind, how
often would it tell me the truth?"*

> **Reproducibility envelope.** abicheck `0.5.0`, commit `1d2487c82ec5`,
> `ground_truth.json` sha256 `164a517d66f3…`. `abicheck`/`abicheck_full`/
> `abidiff`/`abidiff_headers` generated live via
> `scripts/generate_benchmark_report.py --check` (~36 min wall, peak RSS
> ~702 MiB); `abicc_dumper`/`abicc_xml` merged from
> `scripts/frozen_competitor_results.json`, refrozen from two standalone
> full-catalog runs (ABICC hangs on some cases, so each mode is run alone
> rather than interleaved — abi-dumper ~18 min, xml/legacy ~36 min). The
> catalog grew from 186 to 191 cases (`case187`-`case191`, upstream #584)
> after that ABICC data was frozen; the 5 new cases are L5-only
> build-source-pack fixtures with no compilable `v1`/`v2` `.so` pair, so
> they were confirmed structurally `SKIP` for ABICC/abidiff (no hang risk)
> and added to the frozen cache directly rather than re-running the full
> ABICC sweep for 5 already-known outcomes. `--check` verified this table
> matches a freshly-generated report byte-for-byte.

```bash
# ABICC lanes are frozen ahead of time (each mode run alone, ABICC hangs
# on some cases when run concurrently with itself):
python3 scripts/benchmark_comparison.py --tools abicc_dumper --freeze abicc_dumper
python3 scripts/benchmark_comparison.py --tools abicc_xml --freeze abicc_xml

# abicheck/abicheck_full/abidiff/abidiff_headers run live; the frozen
# abicc_dumper/abicc_xml columns above merge in automatically:
python3 scripts/generate_benchmark_report.py \
  --tools abicheck abicheck_full abidiff abidiff_headers --check
```

| Tool | Correct / 191 | Accuracy | False positives | False negatives | Total time |
|------|:---:|:---:|:---:|:---:|:---:|
| **abicheck (L2, headers)** | 183 | **95.8%** | **0** | 8 | 275s (~5 min) |
| **abicheck (L3-L5, +sources)** | 188 | **98.4%** | **0** | 3 | 1396s (~23 min) |
| libabigail (`abidiff`) | 54 | 28.3% | 5 | 132 | **~2s** |
| libabigail + headers | 54 | 28.3% | 5 | 132 | **~7s** |
| ABICC (abi-dumper) | 85 | 44.5% | 8 | 98 | 1104s (**~18 min**) |
| ABICC (xml/legacy) | 77 | 40.3% | 7 | 107 | 2175s (**~36 min**) |

**ABICC is roughly 160-1100× slower than libabigail** for the identical
191-case catalog (1104-2175s vs ~2-7s) while scoring *lower* on accuracy
than abicheck's L2 lane. This is why ABICC/libabigail results are frozen
(`--freeze`) into `scripts/frozen_competitor_results.json` — a committed
reference file merged into every subsequent run automatically — rather than
re-run on every abicheck iteration; nothing in a competitor's own verdict
changes when abicheck itself is patched.

**Reading the false-positive/false-negative split:** a false positive is a
tool *over-calling* severity (reporting a worse verdict than the true one —
crying wolf); a false negative is *under-calling* it (silence on a real
break, including every SKIP/ERROR/TIMEOUT, since a tool that cannot tell you
about a break failed to warn just as surely as one that said COMPATIBLE).

- **libabigail's misses are overwhelmingly false negatives** (132/191,
  DWARF has no view into noexcept/static/const/layout-invisible changes) —
  it rarely cries wolf (FP=5), it mostly stays silent. 36 of those misses are
  a flat `SKIP` on fixture/source-only, audit, cross-source, bundle, and BTF
  cases (including the 5 new `case187`-`case191`) that have no ELF pair for
  `abidw`/`abidiff` to read at all.
- **ABICC's misses skew false-negative too** (98-107/191) for the same two
  reasons: the same 36 non-`.so` cases `SKIP` outright, and a further 5
  (abi-dumper) / 14 (xml) hit the 90s per-case timeout in this environment —
  see the slowest-case tables the benchmark prints (`case85`, `case09`,
  `case105`, `case109`... routinely hit it on both ABICC modes here). A
  handful more (1 abi-dumper, 2 xml) `ERROR` outright.
- **abicheck L3-L5's false positives are now 0** (down from 7) — see the
  fifth-round note below. One of the 7 was a real product bug (`case186`);
  the other 6 were the source-replay lane correctly promoting a genuinely
  `COMPATIBLE` verdict to `COMPATIBLE_WITH_RISK` on evidence a binary/header
  lane structurally cannot see, which the harness now credits instead of
  penalizing (ADR-028 D3).

> **abicheck L3-L5's numbers above are post-fix (four rounds).** An earlier
> pass scored the L3-L5 lane at only 104/170 (61.2%, FP=17, FN=49, 3977s).
> Most of that gap was benchmark-harness bugs, not a product regression:
>
> 1. `_build_plugin_side` forced `-include <header>` into every
>    plugin-instrumented compile, which crashes any fixture whose `.c` file
>    independently redefines a type also declared in its header (a common,
>    legal pattern — case07, case08, case09, case14, case19, case21-23,
>    case25, case26, ...); the CMake macro already has a proper per-case
>    opt-in for this (`V{version}_FORCE_INCLUDE`), so the blanket duplicate
>    was redundant and actively harmful. Removed it.
> 2. The pack validator rejected `case04_no_change` as "wrong release
>    translation units" because its `CMakeLists.txt` deliberately points
>    both `V1_SOURCES`/`V2_SOURCES` at the same file to guarantee zero diff
>    — the naive `v1.c`/`v2.c` filename guess couldn't see that. Fixed via
>    a new `_cmake_declared_source()` helper that reads the real compiled
>    source from `CMakeLists.txt`.
>
> These two recovered 12 cases and cut total time ~5.7× (104/170, 61.2% →
> 116/170, 68.2%; 3977s → 694s).
>
> 3. The special-case dispatcher (audit/cross-source, bundles, BTF, L3-L5
>    fixture packs, snapshot-pairs, Python stubs — 28 cases with no
>    compilable v1/v2 source at all) only ever credited the `abicheck`
>    column, leaving `abicheck_full` at its `SKIP` default regardless of
>    which tools were active — these fixtures never go through a build lane,
>    so there is no L2-vs-full distinction to make. Now credits both.
>    `case16_inline_to_non_inline`'s `.cpp` is genuinely header-only (empty,
>    inline function lives entirely in the header) and needed
>    `V{version}_FORCE_INCLUDE` to produce any plugin facts at all — added
>    it, converting an `ERROR` into a real verdict.
>
> Recovered 28 more cases: 116/170 (68.2%) → 144/170 (84.7%).
>
> 4. `PUBLIC_REACHABILITY_CHANGED` was firing for declarations entering the
>    public-reachability closure even when they were *brand new* (didn't
>    exist in the old version at all) or fully removed — duplicating the
>    already-correct `var_added`/`func_added`/`var_removed`/`func_removed`
>    finding at an inflated severity. Narrowed to only fire for a
>    declaration present in *both* graphs (a real "persisting decl crosses
>    the public boundary" signal, not a same-turn addition/removal) —
>    a deliberate product-policy change, not a benchmark-harness fix, since
>    it replaces the behavior an existing test previously locked in.
> 5. 14 example cases (`case03`, `case05`, `case13`, `case16`, `case47`,
>    `case49`, `case52`, `case54`, `case61`, `case62`, `case99`, `case136`-
>    `138`) named their per-version source files inconsistently (`v1.c`/
>    `v2.c`, `bad.c`/`good.c` — a different basename per version) instead of
>    the `old/lib.<ext>`+`new/lib.<ext>` convention already used by
>    `case19` onward. For a case with only one declaring file per side,
>    `_common_prefix_len()` had no sibling file to structurally compare
>    against, so it fell back to comparing the full absolute path —
>    `old/lib.c` vs `new/lib.c` still differ there, so
>    `EXPORTED_SYMBOL_SOURCE_OWNER_CHANGED` false-fired on every one of
>    them. Renamed all 14 to the shared-basename convention and gave
>    `_common_prefix_len()` a single-declaring-file fallback (reserve just
>    the filename, matching the "unmoved" outcome multi-file sides already
>    reach structurally) so a lone declaring file can't be mistaken for a
>    real cross-version move.
>
> Recovered 9 more cases and cut the false-positive count more than half:
> 144/170 (84.7%, FP=17) → **153/170 (90.0%, FP=7)**.
>
> **A fourth round, discovered while regenerating this table for the 186-case
> catalog: the L3-L5 lane had silently gone back to 100% `ERROR`.** The
> ADR-043 CLI surface reset (PR #566) deleted the standalone `collect`/`merge`
> commands this lane's `run_abicheck_full()` shelled out to
> (`python -m abicheck merge ...`) and replaced them with inline
> `dump --sources/--build-info` embedding — but nothing updated the benchmark
> harness, so every case in the lane errored out from that point on with no
> test catching it (the harness's own smoke test mocked subprocess calls, so
> it didn't notice the real CLI had moved under it). Fixed by calling the
> surviving `embed_inputs_pack()` helper directly instead of the removed CLI
> command. A follow-up code review then caught that the naive replacement
> (`dump --sources <pack>`) skips the export-relink step
> (`_relink_combined_against_exports`) that `embed_inputs_pack()` performs —
> without it, a plugin-captured pack's `source_abi.roots["exported_symbols"]`
> stays empty (the plugin can't know the binary's final exports at compile
> time), silently under-exercising the L4/L5 checks. Calling
> `embed_inputs_pack()` directly avoids both problems. Recovered:
> **153/170 (90.0%, FP=7) → 169/186 (90.9%, FP=7, FN=10)** — consistent with
> the prior quality level, now scored against the grown catalog.

> **A fifth round, root-causing every remaining FP/FN from the fourth round
> instead of leaving them as an open tail:**
>
> 1. **`case186_c_api_pointee_const_abi_neutral`** was a real product bug,
>    not a harness issue: `_diff_inline_bodies()`'s inline-removal guard
>    checked `SourceEntity.mangled_name` to tell whether a removed inline
>    function was actually re-exported elsewhere — but plain C functions
>    never have a `mangled_name` (mangling is a C++-only concept), so the
>    guard was permanently blind for C code and a body-only change to a
>    stable-signature C function false-fired `INLINE_FUNCTION_REMOVED`.
>    Fixed in `abicheck/buildsource/source_diff.py` with an
>    `_export_identity()` helper that falls back to the entity's plain
>    qualified name (its real export symbol for un-mangled, non-`::`-scoped
>    C entities) when `mangled_name` is empty; a `::`-scoped C++ name
>    without a `mangled_name` still correctly reports removal (regression
>    tests in `tests/test_l3l4l5_new_kinds.py`).
> 2. **`case35_field_rename`**, newly missed at the 186-case catalog size,
>    was `abicheck/surface.py` classifying `field_renamed` as a symbol-level
>    finding — castxml's unmangled constructor-name collision let
>    public-surface scoping silently drop it. Added `"field_renamed"` to
>    `_TYPE_LEVEL_KIND_NAMES`.
> 3. **6 of the 7 false positives were not wrong**: `case16`, `case47`,
>    `case54`, `case62`, `case99`, and `case185` are the L3-L5 lane correctly
>    promoting a genuinely `COMPATIBLE` L2 verdict to `COMPATIBLE_WITH_RISK`
>    on source-graph evidence a binary/header-only lane structurally cannot
>    see (a declaration crossing the public export boundary, reserved-field
>    reuse, a stale-inlined-body risk, symbol-binding/ownership drift). Per
>    ADR-028 D3 this is evidence *enrichment*, not an error, so the harness
>    now credits this specific transition (`abicheck_full`, `COMPATIBLE` →
>    `COMPATIBLE_WITH_RISK`) as correct via `_is_source_enrichment_match()`
>    instead of penalizing it — lower-evidence tools are unaffected, since
>    the match is scoped to `abicheck_full` only.
> 4. **`case180_symbol_binding_lost_unique`** was structurally invisible in
>    the L3-L5 lane specifically: it dumped its own Clang-plugin-built `.so`
>    for L0-L2 evidence, but Clang never emits `STB_GNU_UNIQUE` (a GCC-only
>    ELF extension the case's signal depends on) — the lane was forced onto
>    the wrong compiler for its own binary evidence, independent of the
>    source-evidence question. Fixed by dumping the same real, already-built
>    binary the L2 lane sees instead (the plugin's source-only pack still
>    applies via `embed_inputs_pack()`'s name-based relink, which is
>    compiler-independent) — a general fix for Clang-vs-GCC codegen
>    discrepancies between the two lanes, not a case180-only patch.
> 5. **`case115_bit_int_width_changed`** `ERROR`ed because castxml's bundled
>    Clang 13 frontend cannot parse C23 `_BitInt(N)`, independent of which
>    GCC built the fixture. Passing `--ast-frontend clang` (shelling out to a
>    system Clang instead of castxml's bundled one) for this one case in
>    both lanes' dump calls resolves it.
> 6. **`case165_polymorphic_nonvirtual_dtor`** returned `COMPATIBLE` instead
>    of `COMPATIBLE_WITH_RISK` because its detector
>    (`POLYMORPHIC_TYPE_NON_VIRTUAL_DTOR`) is opt-in behind
>    `--pattern-verdicts` (ADR-027) and the harness never passed it. Added
>    the flag to both lanes' `compare` calls.
> 7. **`case175`/`case176`** (kABI CRC / symbol-namespace) `SKIP`ped because
>    they ship a committed `v1.symvers`/`v2.symvers` snapshot pair with no
>    compilable `v1`/`v2` source for the plugin-build lane to build at all —
>    there is nothing to compile. Routed through the existing
>    `mode: snapshot-pair` dispatch (already used by `case170`) instead,
>    which runs `abicheck compare` directly against the committed fixtures.
> 8. **`case111_enumerable_thread_specific_lambda_ambiguity`** and
>    **`case98_cxx_standard_floor_raised`** remain genuine, documented
>    detector gaps (see their READMEs): every evidence tier from L0 through
>    L5 currently reaches the same under-called verdict, so more evidence
>    does not recover them — these are not harness bugs.
>
> Recovered: **169/186 (90.9%, FP=7, FN=10) → 183/186 (98.4%, FP=0, FN=3)**
> for the L3-L5 lane; items 2 and 5-7 above apply to the L2 lane too, taking
> it from **173/186 (93.0%, FN=13) → 178/186 (95.7%, FN=8)**.

> **A sixth, unrelated change: the catalog grew from 186 to 191 mid-review.**
> While this benchmark was being finalized, upstream added 5 new cases
> (`case187`-`case191`, ADR-041 P0 roadmap) exercising L5 source-graph
> reachability through a private field type, a private base class, a private
> parameter type, an inline function referencing an internal constant, and
> the header-only graph variant — all `COMPATIBLE_WITH_RISK` at `min_evidence:
> L5`. None are compilable `v1`/`v2` `.so` pairs (build-source-pack fixtures
> only), so ABICC/abidiff structurally `SKIP` them, confirmed via a scoped
> run before folding them into the frozen cache. Both abicheck lanes reach
> the correct verdict on all 5 (the special-case dispatcher credits both
> columns identically for fixture-only cases, same as the other non-`.so`
> lanes), so this growth changes every tool's denominator from 186 to 191
> without changing any lane's false-positive/false-negative count.

**What's structurally left for the L3-L5 lane** (3 remaining misses, all
shared with the L2 lane and all genuine detector gaps rather than
evidence-floor or harness gaps — L4 source ABI replay is available in this
lane and still doesn't recover them):

- `case105_concept_tightening` (`API_BREAK` expected, both lanes return
  `NO_CHANGE`).
- `case111_enumerable_thread_specific_lambda_ambiguity` (`API_BREAK`
  expected, both lanes return `COMPATIBLE`) — see its README.
- `case98_cxx_standard_floor_raised` (`COMPATIBLE_WITH_RISK` expected, both
  lanes return `NO_CHANGE`).

Both lanes now correctly resolve gaps this table previously tracked as
misses: the L2 lane's old `case20`, `case78`, `case97` (both lanes share the
same underlying detectors) and the L3-L5 lane's old `case83` one-off and its
`case118`-`120` no-`CMakeLists.txt` structural gap — real product/harness
progress since the 170-case run, not an artifact of this regeneration.

abicheck L2's 8 misses (191 − 183): `case105`, `case111`, and `case98` are
the three genuine detector gaps above (shared with the L3-L5 lane); the
other five are structurally below the L2 lane's evidence floor per
`ground_truth.json`'s `min_evidence` — `case122` needs L4 (source ABI
replay) and `case130`-`case133` need L3 (build context) — so an L2-only
(headers, no `-p build/`) lane cannot see them by design, not by gap.

---

## Pinned vendor benchmark summary (2026-05-19, 74-case subset)

> **Historical.** Superseded by the [full-catalog benchmark](#full-catalog-benchmark-2026-07-17-all-191-cases)
> above, which covers all 186 cases with a stricter denominator (SKIP/ERROR/TIMEOUT
> count as misses) plus an FP/FN breakdown. Kept here for the original 74-case
> release-pinned methodology and historical numbers. The harness has since
> dropped the standalone `abicheck_compat`/`abicheck_strict` tool lanes (the
> cross-tool comparison now benchmarks only the two evidence depths that
> matter for tool-vs-tool comparison — `abicheck` at L2 and `abicheck_full`
> at L3-L5); `abicheck compat`/`compat check -s` remain real CLI modes,
> documented above under "How each tool analyses ABI", just no longer
> re-benchmarked as separate harness columns. The `--tools`/`--skip-compat`
> flags below reflect the harness as it existed at the time and are not
> reproducible verbatim on the current script.

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
