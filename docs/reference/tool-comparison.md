# Benchmark & Tool Comparison

This document explains how each ABI checking tool works, what analysis method it uses,
benchmark results across real-world test cases, and why the numbers come out the way they do.

> **Note:** abicheck detects 388 change kinds (see [Change Kind Reference](change-kinds.md)).
> The `examples/` catalog currently has **193 cases** (`examples/ground_truth.json`
> is the source of truth — see `examples/README.md`). Two benchmarks run against it:
>
> - A **pinned 74-case cross-tool subset** (`case01`-`case73` + `case26b`),
>   frozen so accuracy numbers stay reproducible release to release. See
>   [Pinned vendor benchmark summary](#pinned-vendor-benchmark-summary-2026-07-18-74-case-subset)
>   (marked historical, superseded by the full-catalog benchmark below).
> - A **full-catalog sweep** scoring every case, with SKIP/ERROR/TIMEOUT counted
>   as misses. See [Full-catalog benchmark](#full-catalog-benchmark-2026-07-18-all-193-cases).
>
> **Which denominator is which.** Of the 193 catalog cases, **159** are
> compilable `v1`/`v2` shared-library (`.so`) pairs that abidiff/ABICC can also
> run against — abicheck's own competitor benchmark builds and scores these
> through the normal build → dump → compare pipeline. The remaining **34**
> don't fit that shape (10 single-artifact audit/cross-source checks, 11
> build-source-pack (L3-L5) replays, 6 committed snapshot-pair fixtures, 5
> multi-library bundle directories, 1 kernel-BTF blob, 1 Python stub-pair) and
> have no abidiff/ABICC equivalent, so they're scored by abicheck alone through
> [dedicated test lanes](#current-scan-quality-snapshot) instead of the
> tool-vs-tool tables. This split is derived directly from each case's `mode`/
> `bundle`/`fixtures`/`skip` fields in `ground_truth.json` (the same fields
> `scripts/benchmark_comparison.py`'s `_try_special_case()` routes on), so it
> stays accurate as the catalog grows — recompute it with:
> ```bash
> python3 -c "
> import json
> v = json.load(open('examples/ground_truth.json'))['verdicts']
> special = sum(1 for e in v.values() if e.get('mode') == 'audit' or e.get('skip')
>               or e.get('bundle') is True or e.get('category') == 'bundle'
>               or e.get('mode') in ('snapshot-pair', 'reconcile')
>               or e.get('fixtures') == ['old.json', 'new.json'] or e.get('stub_pair'))
> print(f'{len(v)} total, {len(v) - special} .so-pair, {special} dedicated-lane')"
> ```

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
| Catalog metadata | 193 ground-truth entries | `examples/ground_truth.json` + `tests/test_evidence_tiers.py` | 159 binary competitor `.so` lanes + 34 dedicated non-`.so` lanes | Single source of truth for examples, verdicts, expected kinds, and minimum evidence; split recomputed directly from `ground_truth.json`'s `mode`/`bundle`/`fixtures`/`skip` fields (see the "Which denominator is which" note above) |
| Build/autodiscovery | 211 integration items | `python -m pytest tests/test_example_autodiscovery.py -v --tb=short -m integration` | gcc: 146 passed / 60 skipped / 5 xfailed; clang: 146 passed / 59 skipped / 6 xfailed | Green default single-library build lane; skipped items are covered by dedicated bundle/source/audit/BTF tests |
| Full example proof matrix | 193 catalog cases | `validation/scripts/collect_full_example_matrix.py` over CI artifacts + bundle/G20/L3-L5/BTF proofs | Dedicated full-catalog proof lane | Full-catalog source of truth; a `SKIP` in one lane is accepted only when a dedicated lane proves the case |
| Default/debug verdicts | 193 catalog cases | `PYTHONPATH=. python tests/validate_examples.py --toolchain {gcc,clang} --json` | gcc: 146 PASS / 42 SKIP / 5 XFAIL; clang: 146 PASS / 41 SKIP / 6 XFAIL. Both lanes carry the same 1-2 undocumented `expected_kinds` mismatches (verdict correct, kind set incomplete): gcc on `case116_atomic_qualifier_changed`; clang on that plus `case115_bit_int_width_changed` | Single-library debug lane; dedicated non-`.so` cases skip here by design; XFAIL is not green full-matrix scope |
| Bundle release verdicts | 5 bundle cases | `PYTHONPATH=. python validation/scripts/run_bundle_examples.py --json` | 5 PASS | Runs the ADR-023 multi-library examples through `abicheck compare old/ new/` |
| Runtime smoke | 193 catalog cases | `PYTHONPATH=. python validation/scripts/run_example_runtime_smoke.py --json` | Runtime-only proof lane | Runtime harness has no BUILD_ERROR/BASELINE_ERROR bucket |
| Release headers | 193 catalog cases | `validate_examples.py --artifact-variant release-headers --json` in CI artifact | Reduced-evidence informational lane | False-positive guard passed |
| Stripped headers | 193 catalog cases | `validate_examples.py --artifact-variant stripped-headers --json` in CI artifact | Reduced-evidence informational lane | Expected signal-loss backlogs remain |
| Build/source smoke | 10 representative cases | `validate_examples.py case01 case04 case98 case105 case122 case129 case130 case131 case132 case133 --artifact-variant build-source --json` in CI artifact | 10 PASS | Build/source evidence catches the build-flag mode cases in the smoke set |
| Binary competitor scan | 159 shared-library pairs × 2 external tools (4 tool/mode combinations) | abicc (dumper + xml) and libabigail `abidiff` (+headers) over built `.so` pairs | 636 tool invocations attempted; per-tool correct/accuracy in the [full-catalog benchmark](#full-catalog-benchmark-2026-07-18-all-193-cases) below | Competitor `.so` lane only; the 34 dedicated non-`.so` cases are represented in their own lanes, not as missing `.so` results |
| Scan-depth matrix | not independently re-run this pass | `abicheck scan --depth {binary,headers,build,source,full}` | see prior methodology note below | Compare-style status by depth; full-catalog audit/cross-source/bundle/BTF/snapshot cases are covered by dedicated lanes |

Rows sourced from CI-artifact-generating scripts that this pass did not
independently re-execute (full example proof matrix, runtime smoke, release/
stripped headers, build/source smoke, scan-depth matrix) keep their
case-count denominator updated to the current 193-case catalog but carry
forward their last-known CI result; re-run them via the commands above for a
byte-for-byte refresh. The scan-depth matrix specifically needs a fresh run
of `abicheck scan --depth` across the current comparable-target set (it was
previously pinned to 141 targets against an older, smaller catalog) — that
regeneration is a tracked follow-up, not fabricated here.

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
the direct-vs-known-gap-oracle accounting.

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
carries a `min_evidence` field — the weakest source at which abicheck reaches
*every one of the case's cataloged `expected_kinds`*, not just its verdict —
derived by
`scripts/evidence_tiers.py`
(`compute_min_evidence()` takes the strongest tier across all `expected_kinds`,
by design: "the whole break is only fully visible once every contributing
kind is") and validated by `tests/test_evidence_tiers.py`. Aggregating
`min_evidence` over the catalog's **182 compare-style cases** (everything
except the 11 single-artifact audit/cross-source/BTF checks, which have no
old-vs-new concept to place on an evidence staircase) yields the cumulative
minimum-evidence coverage below. One of those 182,
[case111](../examples/case111_enumerable_thread_specific_lambda_ambiguity.md),
has no `min_evidence` at all — it is the one documented detector gap where no
tier currently reaches the canonical verdict — so it's excluded from the
181-case denominator rather than miscounted against a tier. Recompute this
table directly from `ground_truth.json` any time with:

```bash
python3 -c "
import json
from collections import Counter
v = json.load(open('examples/ground_truth.json'))['verdicts']
cs = {k: e for k, e in v.items() if not (e.get('mode') == 'audit' or e.get('skip'))}
counts = Counter(e.get('min_evidence') for e in cs.values() if e.get('min_evidence') not in (None, 'none'))
total = sum(counts.values())
cum = 0
for tier in ['L0', 'L1', 'L2', 'L3', 'L4', 'L5']:
    cum += counts[tier]
    print(f'{tier}: +{counts[tier]:<3} cumulative {cum}/{total} ({cum/total:.0%})')
"
```

| Source provided | Layer | Cases first detectable here | Cumulative | Representative cases |
|-----------------|:-----:|:---------------------------:|:----------:|----------------------|
| Just the binary | L0 | 64 | **64 / 181 (35%)** | symbol removal ([01](../examples/case01_symbol_removal.md)), SONAME ([05](../examples/case05_soname.md)), visibility ([06](../examples/case06_visibility.md)), symbol-version removed ([65](../examples/case65_symbol_version_removed.md)), all 5 bundle cases |
| + Debug symbols | L1 | 68 | **132 / 181 (73%)** | struct layout ([07](../examples/case07_struct_layout.md)), enum value ([08](../examples/case08_enum_value_change.md)), vtable ([09](../examples/case09_cpp_vtable.md)), calling convention ([64](../examples/case64_calling_convention_changed.md)), bitfield ([63](../examples/case63_bitfield_changed.md)), toolchain flag drift ([103](../examples/case103_toolchain_flag_drift.md)) |
| + Public headers | L2 | 25 | **157 / 181 (87%)** | access level ([34](../examples/case34_access_level.md)), default arg removed ([123](../examples/case123_default_argument_removed.md)), class `final` ([125](../examples/case125_class_became_final.md)), `detail::` leaks ([74](../examples/case74_detail_base_class_changed.md)–[77](../examples/case77_detail_templated_base_changed.md)), scoped-internal *no-change* ([118](../examples/case118_internal_struct_field_added_scoped.md)–[120](../examples/case120_internal_struct_reordered_scoped.md)) |
| + Build data | L3 | 10 | **167 / 181 (92%)** | build-mode flips: exceptions ([130](../examples/case130_exceptions_mode_flip.md)), RTTI ([131](../examples/case131_rtti_mode_flip.md)), thread-safe statics ([132](../examples/case132_threadsafe_statics_flip.md)), TLS model ([133](../examples/case133_tls_model_flip.md)), enum size ([152](../examples/case152_enum_size_flag_flip.md)), struct packing ([153](../examples/case153_struct_packing_flip.md)), LTO ([154](../examples/case154_lto_mode_flip.md)), char signedness ([155](../examples/case155_char_signedness_flip.md)), C++ standard floor ([98](../examples/case98_cxx_standard_floor_raised.md)) |
| + Sources | L4 | 5 | **172 / 181 (95%)** | uninstantiated template ([122](../examples/case122_template_signature_uninstantiated.md)), public macro removed ([156](../examples/case156_public_macro_removed.md)), inline function removed ([157](../examples/case157_inline_function_removed.md)), concept tightening ([105](../examples/case105_concept_tightening.md)), public typedef removed ([158](../examples/case158_public_typedef_removed.md)) |
| + Source graph | L5 | 9 | **181 / 181 (100%)** | public API internal dependency ([160](../examples/case160_public_api_internal_dep_added.md)), target dependency added ([161](../examples/case161_target_dependency_added.md)), exported symbol source owner changed ([162](../examples/case162_symbol_source_owner_changed.md)), private-field/base/parameter-type leaks ([187](../examples/case187_public_struct_private_field_type.md)–[189](../examples/case189_public_function_private_parameter_type.md), [191](../examples/case191_header_only_graph_field_type.md)), call-graph reachability through suppression ([192](../examples/case192_call_graph_break_survives_suppression.md)) |

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
> verdict per case, credited from `ground_truth.json` labels); it does not
> penalize a tier for *over-calling* elsewhere in the catalog. The
> [full-catalog benchmark](#full-catalog-benchmark-2026-07-18-all-193-cases) below is the stricter,
> empirically-measured number — it scores all 193 cases including false
> positives, which is why `L3-L5` reads 99.5% there rather than the 100%
> this table's `L5` row shows (the full-catalog run also treats `SKIP` on
> the 34 dedicated-lane cases as no-signal until their own dedicated lane
> proves them, whereas this staircase credits them by their cataloged
> `min_evidence` label directly).

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

## Full-catalog benchmark (2026-07-18, all 193 cases)

Every catalog case scored, with **SKIP/ERROR/TIMEOUT/incapacity all counted as
misses** — a tool that hung, crashed, or simply has no mode for a case shape
scores exactly like a wrong verdict. This is a stricter (and more honest)
denominator than "accuracy over cases the tool managed to complete," so read
it as the answer to *"if I pointed this tool at the whole catalog blind, how
often would it tell me the truth?"*

> **Reproducibility envelope.** abicheck `0.5.0`, code commit `ffa860c`
> (this benchmark run's own commits were docs/data-only — `git diff ffa860c
> HEAD -- abicheck/` is empty — so the code under test is `ffa860c`
> regardless of which docs commit is checked out; that SHA is on `main` and
> stable across a squash-merge, unlike a branch-local docs commit),
> `ground_truth.json` sha256 `7836d8b79f96`. All six lanes below (`abicheck`,
> `abicheck_full`, `abidiff`, `abidiff_headers`, `abicc_dumper`, `abicc_xml`)
> were regenerated live against the current **193-case** catalog on
> **2026-07-18** — no frozen/carried-over data (ABICC's two modes are each
> frozen right after their own live run since they can't run concurrently
> with themselves, then merged into the same live pass that runs the other
> four tools; see commands below). Tool versions: castxml `0.6.3`, libabigail
> `abidiff` `2.4.0`, `abi-compliance-checker` `2.3`. Wall time 1396s (~23
> min) for the live abicheck/abidiff pass; peak RSS 708.5 MiB.

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

| Tool | Correct / 193 | Accuracy | False positives | False negatives | Total time |
|------|:---:|:---:|:---:|:---:|:---:|
| **abicheck (L2, headers)** | 185 | **95.9%** | **0** | 8 | 199s (~3 min) |
| **abicheck (L3-L5, +sources)** | 192 | **99.5%** | **0** | 1 | 916s (~15 min) |
| libabigail (`abidiff`) | 55 | 28.5% | 5 | 133 | 1.2s |
| libabigail + headers | 55 | 28.5% | 5 | 133 | 5.5s |
| ABICC (abi-dumper) | 86 | 44.6% | 8 | 99 | 872s (~15 min) |
| ABICC (xml/legacy) | 78 | 40.4% | 7 | 108 | 1871s (~31 min) |

**ABICC is roughly 150-1560× slower than libabigail** for the identical
193-case catalog (872-1871s vs ~1-6s) while scoring *lower* on accuracy than
abicheck's L2 lane. This is why ABICC/libabigail results are frozen
(`--freeze`) into `scripts/frozen_competitor_results.json` — a committed
reference file merged into every subsequent run automatically — rather than
re-run on every abicheck iteration; nothing in a competitor's own verdict
changes when abicheck itself is patched.

**Reading the false-positive/false-negative split:** a false positive is a
tool *over-calling* severity (reporting a worse verdict than the true one —
crying wolf); a false negative is *under-calling* it (silence on a real
break, including every SKIP/ERROR/TIMEOUT, since a tool that cannot tell you
about a break failed to warn just as surely as one that said COMPATIBLE).

- **libabigail's misses are overwhelmingly false negatives** (133/193, DWARF
  has no view into noexcept/static/const/layout-invisible changes) — it
  rarely cries wolf (FP=5), it mostly stays silent. 34 of those misses are a
  flat `SKIP` on the dedicated-lane cases (audit/cross-source, bundle,
  BTF, snapshot-pair, build-source-pack, stub-pair — see the "Which
  denominator is which" note up top) that have no ELF pair for
  `abidw`/`abidiff` to read at all.
- **ABICC's misses skew false-negative too** (99-108/193) for the same
  reason plus its own timeout/error behavior: the same 34 non-`.so` cases
  `SKIP` outright, and a further 5 (abi-dumper, plus 1 `ERROR` on
  `case16_inline_to_non_inline`) / 16 (xml) hit the 90s per-case timeout in
  this environment — `case09`, `case81`, `case104`, `case105`, `case109`,
  `case114`, `case129`-`case133` are among the routine offenders on the xml
  mode here.
- **abicheck's false positives are 0 on both lanes.** The L3-L5 lane's raw
  string mismatches include 6 cases (`case16`, `case47`, `case54`, `case62`,
  `case99`, `case185`) where the harness correctly credits a `COMPATIBLE` →
  `COMPATIBLE_WITH_RISK` promotion as *evidence enrichment* rather than a
  miss (ADR-028 D3: the source-replay lane sees a real risk signal — a
  reserved-field reuse, a stale-inlined-body risk, symbol-binding/ownership
  drift — that a binary/header-only lane structurally cannot see). The one
  genuine remaining miss on both lanes is
  `case111_enumerable_thread_specific_lambda_ambiguity` (`API_BREAK`
  expected, every evidence tier from L0 through L5 currently reaches
  `COMPATIBLE` — a documented detector gap, see its README, not a harness
  artifact).
- **abicheck L2's other 7 misses** (`case98`, `case105`, `case122`,
  `case130`-`case133`) are structurally below the L2 lane's evidence floor
  per `ground_truth.json`'s `min_evidence` — build-mode flips and
  concept/source-replay facts an L2 (headers, no `-p build/`) lane cannot
  see by design, not by gap. The L3-L5 lane resolves all seven.

> **Methodology history.** Earlier passes of this benchmark scored
> substantially lower for the L3-L5 lane (as low as 61%) due to a mix of
> benchmark-*harness* bugs — a forced `-include` crashing legal type
> redefinitions, a build-source-pack helper silently bypassed after a CLI
> refactor, inconsistent per-case source-file naming defeating rename
> detection — and a couple of real product fixes (a C-function
> inline-removal false positive, a `field_renamed` classification gap). Each
> was root-caused and fixed individually; see git/PR history around commit
> `1d2487c82ec5` for the full account rather than a narrated blow-by-blow
> here. The only genuine, currently unresolved gap across both abicheck
> lanes is `case111_enumerable_thread_specific_lambda_ambiguity`.

---

## Pinned vendor benchmark summary (2026-07-18, 74-case subset)

> **Historical.** Superseded by the [full-catalog benchmark](#full-catalog-benchmark-2026-07-18-all-193-cases)
> above, which covers all 193 cases with a stricter denominator (SKIP/ERROR/TIMEOUT
> count as misses) plus an FP/FN breakdown. Kept here because the original
> 74-case release-pinned methodology stays useful as a small, fast, stable
> corpus for spot-checking a tool change without paying the full-catalog
> runtime (this refresh: 86s for `abicheck`, vs 199s for the same lane over
> the full 193-case catalog above). The harness has since dropped the
> standalone `abicheck_compat`/`abicheck_strict` tool lanes — `--tools` only
> accepts `abicheck`, `abicheck_full`, `abidiff`, `abidiff_headers`,
> `abicc_dumper`, `abicc_xml` now, so the original 2026-05-19 run's compat
> (71/74, 95%) and strict (62/74, 83%) numbers can no longer be reproduced
> verbatim; `abicheck compat`/`compat check -s` remain real CLI modes,
> documented above under "How each tool analyses ABI", just no longer
> re-benchmarked as separate harness columns.

Release-pinned scan status from `python3 scripts/benchmark_comparison.py --suite pinned74 --abicc-mode both`
on the original 74-case benchmark subset (same code commit `ffa860c` and `ground_truth.json` as the
full-catalog run above — see that section's reproducibility envelope for why the code commit, not a
branch-local docs commit, is the stable reference).

| Tool | Correct / 74 | Accuracy | False positives | False negatives | Total time |
|------|:---:|:---:|:---:|:---:|:---:|
| **abicheck (L2, headers)** | 74 | **100%** | **0** | 0 | 86.0s |
| **abicheck (L3-L5, +sources)** | 74 | **100%** | **0** | 0 | 454.1s |
| libabigail (`abidiff`) | 21 | 28.4% | 2 | 51 | 0.5s |
| libabigail + headers | 21 | 28.4% | 2 | 51 | 3.1s |
| ABICC (abi-dumper) | 48 | 64.9% | 2 | 24 | 700s (~12 min) |
| ABICC (xml/legacy) | 47 | 63.5% | 1 | 26 | 240s (~4 min) |

### Scan-status matrix

| Check configuration | 74-case benchmark subset | Status |
|---------------------|:----------------:|--------|
| `abicheck` | ✅ 74/74 completed | 74/74 exact |
| `abicheck_full` | ✅ 74/74 completed | 74/74 exact |
| `abidiff` | ✅ 74/74 completed | 21/74 exact |
| `abidiff_headers` | ✅ 74/74 completed | 21/74 exact |
| `abicc_dumper` | ⚠️ 71/74 scored | `case09_cpp_vtable`, `case59_func_became_inline` timeout; `case16_inline_to_non_inline` error |
| `abicc_xml` | ⚠️ 72/74 scored | `case16_inline_to_non_inline`, `case60_base_class_position_changed` timeout |

### Commands used

```bash
python3 scripts/benchmark_comparison.py \
  --suite pinned74 \
  --abicc-mode both
```

A single run now covers all six tools; the previous multi-invocation
sequence (separate `--skip-abicc` and per-mode `--abicc-timeout 20` calls)
was a workaround for an older, flakier ABICC integration and is no longer
necessary — `--abicc-timeout` still exists if you need to bound a hang more
aggressively than the 90s default.

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
| Quick ELF-only sanity check | `abidiff` (fast, 28% (21/74) but catches symbol removals) |
