# Multi-binary (bundle) ABI analysis

Most ABI tools answer one question: *"did this `.so` file's ABI change?"*
Real-world releases — oneDAL, libtorch, Intel MKL, the bundled CUDA
runtime — ship **several `.so` files that depend on each other**.
Per-library compare misses entire classes of breakage that live in the
relationships between siblings. The **bundle layer** (ADR-023) fixes
that.

This page covers:

- What "bundle analysis" actually checks
- The bundle-analysis flags on `compare` (directory/package inputs) and what they do
- The manifest file format
- How to read the JSON / markdown output
- When you'd want to turn it off

## What the bundle layer catches

| Scenario | Per-library compare says | Bundle layer says |
|---|---|---|
| `libcore.so` removes `core_mul`; `libalgo.so` still imports it | libcore: BREAKING; libalgo: NO_CHANGE | + `bundle_intra_dep_removed` on libalgo |
| `libcore.so` changes `core_add(int,int)` → `core_add(long,long)` (extern C, same mangled name); libalgo is byte-identical | libcore: BREAKING; libalgo: NO_CHANGE | + `bundle_intra_dep_signature_changed` on libalgo |
| Type `detail::Context` defined in libcore changes layout; libalgo's exported symbols embed it as a template parameter | libcore: BREAKING; libalgo: NO_CHANGE | + `bundle_intra_type_changed` on libalgo |
| `shared_util` moves from libcore to libutil; bundle still exports it once | libcore: BREAKING (`func_removed`); libutil: COMPATIBLE (`func_added`) | + `bundle_provider_changed` (COMPATIBLE_WITH_RISK) |
| Removed library was depended on by a surviving sibling | libcore removed (worst-of) | + `bundle_library_removed` with consumer attribution |
| Symbol's `gnu.version_d` tag drifts (`GLIBCXX_3.4.20` → `GLIBCXX_3.4.30`) | unchanged | + `bundle_intra_dep_resolved_to_different_version` |
| Manifest promises `train_double_sparse`; new bundle doesn't export it | per-library `func_removed` (can't tell promised from incidental) | + `bundle_manifest_instantiation_removed` |

Per-library findings are unchanged — the bundle layer only **adds**
cross-library findings; it never hides them. The aggregate `verdict`
becomes the worst of `bundle_verdict` and the per-library worst.

## Bundle findings answer a different question than public-surface findings

A `bundle_*` kind answers *"does the shipped bundle still work end-to-end"*
— not *"did the public API change"*. `bundle_intra_dep_removed` and its
siblings are classified as `BREAKING`/`COMPATIBLE_WITH_RISK`/etc. through the
same registry/verdict machinery every other `ChangeKind` uses — they aren't a
separate category — but the **scoping** layer that sits in front of that
classification treats some of them differently, and *which* bundle detector
you're looking at determines whether that's true.

First, a terminology note this page relies on throughout: **scoping** and
**policy** are two separate mechanisms, not two names for the same thing.
`--scope-public-headers` (on by default) and an explicit surface allowlist
control whether a `Change` is *removed from* `DiffResult.changes` at all —
unlike a `--policy` document's `overrides:` block, which never removes a
`Change` from `changes` and only reclassifies which `Verdict` a given
`ChangeKind` maps to. So "a policy profile scoped to the public surface"
isn't a real, separate filtering mechanism — a private `func_removed` still
shows up in the report under any `--policy` profile; only
`--scope-public-headers` (or suppression, a third, separate mechanism —
see below) decides whether it's there at all.

**`compare_bundle()`/`analyze_bundle()` honor a custom `PolicyFile` for
bundle findings on every entry point, including the CLI's directory/
package release fan-out (`compare-release`) — G38 Phase 16 closed the one
remaining gap.** `compare_bundle()` has a second, optional `policy_file:
PolicyFile | None` parameter alongside its original bare `policy: str`
name — when supplied, `BundleDiffResult.bundle_verdict` scores `bundle_*`
findings through `policy_file.compute_verdict(changes)` directly (the same
call the per-library path already makes), instead of always falling back
to the coarse three-way `policy_kind_sets()` switch
(`strict_abi`/`sdk_vendor`/`plugin_abi`) `compute_verdict()`'s own
docstring describes. `bundle_analysis.analyze_bundle()` (the shared
orchestrator both a live comparison and a stored-facts comparison route
through) accepts and forwards the same parameter. The **stored
`BundleFacts` Python-API driver** — `bundle_facts.compare_bundle_from_facts()`,
`bundle_side_input.compare_bundle_sides()`/
`compare_release_against_bundle_facts()` — resolves and threads a real
`policy_file` through to it, and so does the CLI's directory/package
`compare-release` fan-out: `pack_application.resolve_bundle_policy_file()`
resolves it (called from `cli_compare_release.py`), and
`cli_compare_release_helpers._collect_bundle_result()` sets it on the
`BundleDiffResult` it already got back from `_run_bundle_analysis()`
before reading `bundle_verdict` — `policy_file` is a plain mutable field
and `bundle_verdict` a lazily-computed property, so this reaches the
identical outcome without changing `_run_bundle_analysis()`'s own
signature — so a `--policy custom.yaml` document's `overrides:` entry for e.g.
`bundle_intra_dep_removed` now reaches the release fan-out's aggregate
bundle verdict on every entry point. See
[G38's plan, Phase 16](../contribute/plans/g38-bundle-facts-model-and-multibuild-comparability.md)
for the fix's history. Direct, per-finding suppression of a `bundle_*`
kind is still not supported on any entry point — see the suppression
section below, unchanged by this.

**Graph-native detectors ignore public-surface scoping entirely.**
`bundle_intra_dep_removed`, `bundle_library_removed`/`bundle_library_added`,
`bundle_intra_dep_resolved_to_different_version`, `bundle_soname_skew`, and
manifest enforcement (`bundle_manifest_instantiation_*`) work directly from
the bundle's own ELF resolution graph and declared contracts (manifest,
SONAME cohorts) — never from a per-library `DiffResult`'s already-scoped
`changes` list. `core_mul` in the table above never needs to be part of
`libcore.so`'s *public* API for `bundle_intra_dep_removed` to fire: `libalgo.so`
still imports it via DT_NEEDED, so removing it breaks `libalgo.so`'s runtime
load regardless of whether any external consumer ever called `core_mul`
directly, and `--scope-public-headers` never touches this detector's input at
all.

**Diff-derived detectors no longer inherit `--scope-public-headers` through
starvation — G38 Phase 14 closed that gap.** `bundle_intra_dep_signature_
changed`, `bundle_intra_type_changed`, and `bundle_provider_changed` are
computed by scanning each library's own per-library `DiffResult` for the
specific kinds they promote (`func_params_changed`/`func_return_changed`/
`var_type_changed` for the signature-change detector, `type_size_changed`/
`type_field_removed`/etc. for the type-change detector, `func_removed`+
`func_added` pairs for the provider-migration detector) — but the source
they scan is `diff.changes` **plus** `diff.out_of_surface_changes`, not
`diff.changes` alone. `--scope-public-headers` (on by default) never
*drops* a non-public-surface finding — `post_processing.
FilterNonPublicSurface` moves it to `out_of_surface_changes` instead (ADR-024
§D4/D5's "recorded, never silently dropped" ledger) — so an internal,
headerless C export with no public header naming either side still reaches
these three detectors, exactly as it should: the standalone per-library
report correctly excludes it from that library's own *public* API, but the
*bundle's* internal linkage contract between two siblings is a different
question, and these three detectors exist specifically to answer it.
Each detector keeps its own, already-shipped reachability rule unchanged —
this phase did not unify them to one gate: the signature-change detector
still requires a real resolved import edge
(`resolution.consumers_of`/`_consumer_resolves_via_provider`), the
type-change detector still requires only a name-embedding symbol-table
match (never an import edge — see that detector's own docstring for why an
import-edge requirement would be a strictly narrower, wrong gate here), and
the provider-migration detector still requires no reachability at all (a
provider move breaks an external consumer of the old DSO exactly as much as
a bundle sibling).

Contrast either case with an *ordinary* per-library finding that never gets
promoted to a bundle finding at all (`func_removed` on something no sibling
imports): that one **is** filtered from the per-library report by
`--scope-public-headers`, same as any other per-library finding — but is
unaffected by which `--policy` profile is selected, since policy never
removes a finding, only reclassifies its verdict.

**No `bundle_*` kind can be suppressed *directly* — and, unlike scoping,
suppression still reaches a diff-derived finding indirectly through
starvation, on the CLI fan-out specifically.** `compare_bundle()` is never
given a suppression ruleset itself, so no [suppression](suppressions.md)
rule can target a `bundle_*` kind by name — that part holds for every
`bundle_*` kind, with no exception, on every entry point. On the
directory/package `compare` fan-out, `--suppress` is applied to each
library's `DiffResult.changes` *before* it reaches `compare_bundle()` (the
same per-library compare pipeline that applies `--scope-public-headers`) —
and `post_processing.py`'s own step ordering runs `FilterNonPublicSurface`
*before* `ApplySuppression`, so a change already demoted to
`out_of_surface_changes` never reaches `ApplySuppression` at all and can
never be suppressed, while a change that stayed in-surface can still be
suppressed out of `diff.changes` there. So, for the three **diff-derived**
detectors, a suppression rule targeting the underlying per-library kind
(`func_params_changed`, `type_size_changed`, `func_removed`/`func_added`)
still starves the bundle detector for an *in-surface* finding exactly like
before — this is a side effect of suppressing the per-library finding, not
a way to suppress the bundle finding on its own terms — but has **no**
effect on an out-of-surface finding these detectors now also see, since
that finding was never checked against `--suppress` in the first place.
Closing that narrower residual (re-running suppression against the
out-of-surface ledger specifically) is tracked as a known gap, not
attempted as part of this fix — see the G38 plan doc's own Phase 14 entry.

**The whole-product baseline compare (`abicheck/product_baseline.py`'s
`compare_product_directories`) has no suppression mechanism at all, for
either kind of finding.** Its function signature carries no `suppress`
parameter, and it calls the per-library `run_compare()` unconditionally
unsuppressed — so the starvation effect described above is specific to the
directory/package CLI fan-out (and any caller manually constructing already-
suppressed `DiffResult`s for `compare_bundle()`/`compare_bundle_from_facts()`
itself); a `compare_product_directories()` caller has no suppression lever
of any kind, upstream or direct.

For the remaining, **graph-native** kinds
(`bundle_intra_dep_removed`, `bundle_library_removed`/`_added`, version
drift, SONAME skew, manifest enforcement), there is no per-library `Change`
to suppress upstream of them at all, so the only levers are
`--no-bundle-analysis` (turns off bundle analysis for the whole run — see
below) and, for a symbol that genuinely comes from outside the release,
`--bundle-system-providers` (see below).

**The sibling-consumption gate covers most, but not all, kinds — and even
those are gated only inside `compare_bundle()` itself.** Within
`compare_bundle()`, five kinds require a sibling to actually consume the
affected symbol/library before firing: `bundle_intra_dep_removed` (an import
with no provider at all), `bundle_library_removed` (a removed library, gated
on whether a surviving sibling actually imported one of its exports — a
standalone removal with no internal consumer there is by design left to the
directory/package CLI's separate `--fail-on-removed-library` flow),
`bundle_intra_dep_signature_changed` and `bundle_intra_type_changed` (each
gated on `new.resolution.consumers_of(...)`/a sibling's own symbols, as
described above), and `bundle_intra_dep_resolved_to_different_version`
(gated on `new.resolution.consumers_of(symbol)` returning at least one other
library — an exported version bump nobody in the bundle actually imports
produces no finding). Two kinds have no such gate even inside
`compare_bundle()`: `bundle_library_added` fires for any new library
unconditionally, and `bundle_provider_changed` fires whenever a symbol
migrates from one sibling to another, whether or not any third sibling
consumes it. Manifest/SONAME-cohort findings
(`bundle_manifest_instantiation_removed`, `bundle_soname_skew`) are a third
category entirely — driven by their own declared contract, not by internal
consumption at all — a manifest promising a since-removed symbol produces
`bundle_manifest_instantiation_removed` even if no sibling in the bundle ever
imported it.

The whole-product baseline compare (`abicheck/product_baseline.py`'s
`compare_product_directories`) goes further still: it calls `compare_bundle()`
for its intra-bundle analysis, but then unconditionally reports **every**
library present in the old product and absent from the new one as
`bundle_library_removed` (and the symmetric case as `bundle_library_added`),
with no sibling-consumption check at all — by design, since a whole-product
compatibility gate must not silently return `NO_CHANGE` for a release that
dropped its only public library and had no internal consumer to notice. So
the consumption gate on `bundle_library_removed` described above applies to
`compare_bundle()`/the directory-package `compare` CLI flow specifically, not
to the whole-product baseline API.

An *unconsumed*, *unmanifested* internal export removal (within a single
`compare_bundle()` call, outside the whole-product baseline's own
unconditional library-level fallback) is the
one case that falls through to the ordinary per-library `func_removed`,
governed by the usual public-surface/suppression rules, unaffected by the
bundle layer.

## Running it

The bundle layer is **enabled by default**:

```bash
abicheck compare release-1.0/ release-2.0/ -H include/
```

If the bundle is broken, you'll see a new section in the markdown
summary and new top-level keys in the JSON output:

```text
| **Verdict** | ❌ `BREAKING` |
| **Bundle**  | ❌ `BREAKING` (2 cross-library findings) |

## 🔗 Bundle (Cross-Library) Findings

- **bundle_intra_dep_removed** — `core_mul` (consumer: `libalgo.so`)
  - libalgo.so imports core_mul, but no library in the new bundle exports it.
    Runtime load of libalgo.so will fail with undefined symbol.
- **bundle_intra_dep_signature_changed** — `core_add` (consumer: `libalgo.so`) (provider: `libcore.so`)
  - libalgo.so calls core_add (mangled name unchanged) but libcore.so
    altered its DWARF signature. Calling convention is now mismatched.
```

## The bundle-analysis flags

### `--manifest PATH` *(Experimental)*

> **You probably don't need this flag.** For 95% of releases the
> headers passed to `-H include/` already define the public ABI
> contract, and the bundle layer derives the rest from ELF resolution.
> `--manifest` covers a narrow set of cases where the contract lives
> *outside* the headers. The manifest schema is still being shaped —
> expect changes between minor versions.

**What headers + bundle resolution already give you (no manifest needed):**

- Every public function, type, class declared in headers, with full
  signature / layout diff.
- Cross-DSO symbol resolution — sibling drops a symbol another sibling
  still imports, `extern "C"` signature drift, provider migration.
- Type drift propagated through template-instantiated symbols.

**When `--manifest` actually adds something:**

- **Template instantiation lists.** `extern template foo<int>;` in a
  header is just a declaration; the contract is *which specific
  instantiations get emitted as symbols in the .so*. That list lives
  in build files / `*_ops.cpp` files, not in headers.
- **dlopen/dlsym plugin contracts.** Symbols loaded at runtime by name
  with no header declaration.
- **Internal-but-stable APIs.** Symbols intentionally exported for
  trusted consumers (e.g. test harnesses, sibling tooling) but kept
  out of the public headers.
- **Symbol-version promises.** Specific `foo@GLIBCXX_3.4.30`
  guarantees that headers can't express.

You do not need to hand-list every symbol. Listing tens of thousands
of mangled names is impractical, fragile (mangling shifts with compiler
ABI / inline-namespace bumps), and unmaintainable. The manifest schema
provides three entry shapes for this reason:

#### Entry shape 1 — `pattern:` (most useful)

Glob (`fnmatch`) matched against the **demangled** form of every
exported symbol. The entry passes iff at least one symbol in the new
bundle matches the glob.

```yaml
version: 1
provides:
  - pattern: "oneapi::dal::train_ops<*>*"   # any instantiation of train_ops
    library: libonedal_core.so.1
    optional_provider: false
  - pattern: "oneapi::dal::detail::*"        # internal helpers — optional
    library: libonedal_core.so.1
    optional_provider: true
  - pattern: "onedal_ext_*"                  # extern-C plugin entry points
    library: libonedal_core.so.1
    optional_provider: false
```

Patterns work for both C++ (matched against the demangled form) and
`extern "C"` symbols (matched against the literal name, since they
don't demangle).

#### Entry shape 2 — `template:` + `instantiations:` (the right shape for template libs)

The contract for template-heavy libraries (oneDAL, libtorch, MKL) is
the **explicit instantiation matrix** the build system enumerates. The
manifest expresses that directly:

```yaml
version: 1
provides:
  - template: oneapi::dal::train_ops
    instantiations:
      - {Float: float,  Method: "method::dense",  Task: "task::train"}
      - {Float: float,  Method: "method::sparse", Task: "task::train"}
      - {Float: double, Method: "method::dense",  Task: "task::train"}
      - {Float: double, Method: "method::sparse", Task: "task::train"}
    library: libonedal_core.so.1
    optional_provider: false
```

abicheck expands each instantiation into the demangled form
`Template<v1, v2, ...>` and checks that some exported symbol's
demangled name contains it as a substring. Parameter values appear in
the angle-bracket list in the order the manifest declares them — so
**the parameter order in each `instantiations` entry must match the
template's parameter order**.

Dozens of entries describe thousands of mangled symbols. This is
where the manifest is genuinely cheaper than checking via headers.

#### Entry shape 3 — `symbol:` (rare; literal exact match)

Reach for this when the promise really is one specific mangled symbol
— a versioned entry point, a dlsym plugin name, a stable C ABI
function. Equality match against `.dynsym`.

```yaml
version: 1
provides:
  - symbol: oneapi_dal_version
    library: libonedal_core.so.1
    optional_provider: false
  - symbol: _ZN6oneapi3dal9train_opsIfNS0_6methodE...
    library: libonedal_core.so.1
    optional_provider: false
```

You generally don't want this for templates — instantiation form is
shorter, demangler-version-independent, and easier to review.

#### Shared fields

Every entry accepts:

- `library` *(optional)* — required when `optional_provider: false`.
  Names a specific library (filename like `libcore.so` or SONAME like
  `libcore.so.1` both work).
- `optional_provider` *(default `true`)* — when `true`, any sibling in
  the bundle can satisfy the promise; when `false`, the symbol must be
  provided by the named `library`. Must be a real boolean (`true` /
  `false`); strings like `"false"` and integers are rejected.

Exactly one of `symbol` / `pattern` / `template` per entry; mixing
raises a `ValueError`.

#### Verdicts

| Manifest entry status in new bundle | ChangeKind | Default verdict |
|---|---|---|
| No matching symbol | `bundle_manifest_instantiation_removed` | BREAKING |
| Matched but at wrong provider (when `optional_provider: false`) | `bundle_manifest_instantiation_removed` | BREAKING |
| Matched in new bundle but not in old bundle | `bundle_manifest_instantiation_added` | COMPATIBLE (addition) |

A malformed manifest aborts the run with a `ClickException`. A failing
`--manifest` is treated as a user error, not an environmental quirk —
unlike the bundle-engine-internal failures, which degrade to per-library
results with a warning.

#### Bootstrapping a manifest

Hand-writing the first manifest is the hard part. abicheck ships a
helper that produces a starting point:

```bash
python scripts/extract_bundle_manifest.py release-2.0/lib/ > manifest.yaml
```

The script walks the release's `.so` files, demangles every exported
symbol, groups by top-level C++ namespace, and emits one `pattern:`
entry per (namespace, library) pair. The result is intentionally
over-broad — every symbol the bundle currently exports is promised.
A curator then narrows it:

- Drop entries for internal namespaces (`detail::`, `impl::`).
- Replace generic `ns::*` patterns with specific `template:` entries
  for explicitly-instantiated classes.
- Mark experimental surface `optional_provider: true`.
- Delete entries for libraries that aren't part of the public contract
  (test fixtures, internal tooling shipped alongside the release).

You don't have to do this all at once. The minimal useful manifest is
one entry per library covering the namespaces you actually want to
freeze.

### `--bundle-system-providers libfoo,libbar`

The bundle layer needs to distinguish *intra-bundle imports* (a sibling
should be providing this symbol) from *external imports* (the symbol
comes from the system loader: libc, libstdc++, libgcc_s, libpthread,
libtbb, libsycl, OpenCL, ...). The built-in allow-list handles the
canonical set; this flag extends it.

When to use it:

- Your bundle uses an external SDK shipped outside the release tarball
  (e.g. a vendor library like `libvpl.so.2` that consumers install
  separately).
- A `--manifest`-free workflow keeps emitting `bundle_intra_dep_removed`
  findings against symbols you know are external.

Example:

```bash
abicheck compare old/ new/ \
    --bundle-system-providers libvpl.so.2,libcuda.so.1
```

These sonames are appended to the built-in allow-list for this run only.

### `--no-bundle-analysis`

Skip bundle analysis entirely. Use this when:

- You're debugging a per-library issue and want to suppress the noise.
- You want **parity output** with the pre-ADR-023 behaviour of a bundle
  `compare` (for instance, comparing a CI run from before the
  bundle layer landed).
- The bundle layer raised a warning ("bundle analysis skipped: ..."),
  you want a clean run, and you've already filed a bug.

This flag is the explicit opt-out. There is no environment variable
equivalent; the flag must appear in the command line.

### `--bundle-facts-out PATH`

Persist the OLD side's bundle facts (per-library snapshots plus the
instantiation manifest, if any) to `PATH` for a later stored-baseline bundle
comparison (G38 Phase 2). See
[Comparing against a stored bundle baseline](#comparing-against-a-stored-bundle-baseline-g38-phase-2)
above. Additive output — it does not change this invocation's own findings
or exit code, and is a no-op when combined with `--no-bundle-analysis`.

## JSON output schema additions

`compare --format json` (on a bundle) adds two top-level keys when bundle
analysis ran:

```json
{
  "verdict": "BREAKING",                  // existing: worst of per-lib × bundle
  "libraries": [...],                     // existing
  "unmatched_old": [],                    // existing
  "unmatched_new": [],                    // existing
  "warnings": [],                         // existing
  "bundle_verdict": "BREAKING",           // new (ADR-023)
  "bundle_findings": [                    // new (ADR-023)
    {
      "kind": "bundle_intra_dep_removed",
      "symbol": "core_mul",
      "consumer_library": "libalgo.so",
      "provider_library": null,
      "description": "libalgo.so imports core_mul, but no library in the new bundle exports it. Runtime load of libalgo.so will fail with undefined symbol.",
      "old_value": null,
      "new_value": null,
      "affected_libraries": ["libalgo.so"]
    }
  ]
}
```

`bundle_findings` is `[]` (empty list) when bundle analysis ran and
found nothing. The keys are **omitted entirely** when
`--no-bundle-analysis` is passed — downstream consumers that need to
distinguish "no findings" from "didn't run" should check for key
presence.

Each finding has:

- `kind` — one of the nine `bundle_*` ChangeKind values
  (see [Change Kinds reference](../reference/change-kinds.md)).
- `symbol` — mangled symbol name (or library name for
  `bundle_library_*` findings).
- `consumer_library` — the sibling whose ABI is affected (nullable).
- `provider_library` — the sibling that caused the change (nullable).
- `old_value` / `new_value` — provider/version migration details when
  applicable.
- `affected_libraries` — list of every library affected by this finding;
  enables fan-out filtering downstream.

## Exit codes

Same as before, but a bundle finding can promote the verdict:

| Exit | Meaning |
|---|---|
| 0 | All clear — no per-library or bundle findings above COMPATIBLE_WITH_RISK |
| 2 | At least one library or bundle finding is API_BREAK |
| 4 | At least one library or bundle finding is BREAKING |
| 8 | Library removed from the bundle (only with `--fail-on-removed-library`) |

If you previously had a green CI on a release and bundle analysis now
flips it red, the finding section in the markdown / JSON tells you what
changed and which consumer is affected. The bisect path depends on which
finding fired: a per-library finding (something in `libraries[].changes`)
can be silenced with a [suppression](suppressions.md) if it's expected; a
`bundle_*` finding cannot be suppressed today (see above) — your options are
to fix the intra-bundle contract, or fall back to `--no-bundle-analysis` /
`--bundle-system-providers` as described below.

## Comparing against a stored bundle baseline (G38 Phase 2)

Every bundle comparison above reopens live `.so` files on both sides. That
means a stored-baseline workflow — the normal `scan --against`/CI pattern
every other surface this tool supports — could not get a bundle-level
verdict at all: there was no persisted form of "what the bundle layer knows
about a release" to compare a live directory against later.

`--bundle-facts-out PATH` on the directory/package `compare` fan-out closes
that gap. It persists the OLD side's per-library snapshots (the same
`AbiSnapshot`s that run already produced) plus the instantiation manifest,
if any, to `PATH` as a `BundleFacts` file — additive output alongside the
ordinary live-vs-live comparison the invocation already performs; it changes
no finding or exit code.

```bash
# Capture release-1.0's bundle facts while doing an ordinary comparison.
abicheck compare release-1.0/ release-2.0/ -H include/ \
    --bundle-facts-out release-1.0.bundlefacts.json

# Later, get a bundle-level verdict for release-1.0 -> release-3.0 without
# ever reopening release-1.0's binaries.
abicheck compare release-1.0.bundlefacts.json release-3.0/ --old-bundle-facts
```

`--old-bundle-facts` is the CLI consumer half of this workflow: it treats
`OLD_INPUT` as a stored `BundleFacts` document (from a prior
`--bundle-facts-out` run) instead of a live directory/package, while
`NEW_INPUT` stays a live release directory or package (extracted the same
way the ordinary release fan-out extracts one). Only `--format json`/
`markdown` are available in this mode, and most of the ~44 flags the live
release fan-out accepts have no channel into a stored-facts comparison and
are rejected explicitly (exit 64) rather than silently ignored — see
`abicheck compare --help-all` for the full, current list. Internally it
routes through `abicheck.bundle_side_input.
compare_release_against_bundle_facts()`, the same driver the paragraph
below describes.

`compare_bundle_from_facts()` reconstructs a live-equivalent
`BundleSnapshot` from the stored per-library `AbiSnapshot.elf` metadata (no
binaries read) and then delegates to the exact same `compare_bundle()` a
live-directory comparison uses — so the two entry points can never
independently drift, and a stored-facts comparison produces byte-identical
findings to a live one for the same underlying facts.

## Platform support

Bundle analysis is **ELF/Linux-only** (ADR-018, ADR-023). Mach-O and
PE/COFF bundles are out of scope for this iteration — the resolution
graph relies on DT_NEEDED edges and `.gnu.version_r` / `.gnu.version_d`
sections that PE and Mach-O don't have direct equivalents for. On
non-Linux runs, `compare` skips bundle analysis silently and
emits per-library results only.

## Programmatic API

The bundle layer is also exposed as a Python module for downstream
tooling:

```python
from abicheck.bundle import (
    build_bundle_snapshot, compare_bundle, load_manifest,
)
from pathlib import Path

old = build_bundle_snapshot({p.name: p for p in Path("old/").glob("*.so")})
new = build_bundle_snapshot({p.name: p for p in Path("new/").glob("*.so")})
manifest = load_manifest(Path("manifest.yaml"))   # optional

# per_library_results is the list of DiffResult returned by
# abicheck.checker.compare() for each library pair.
result = compare_bundle(old, new, per_library_results, manifest=manifest)
print(result.bundle_verdict)        # Verdict.BREAKING / COMPATIBLE / ...
for f in result.bundle_findings:
    print(f.kind, f.symbol, f.consumer_library)
```

For a stored-baseline comparison (G38 Phase 2 — see above), swap the OLD
side for a loaded `BundleFacts` and `compare_bundle_from_facts()`:

```python
from abicheck.bundle import discover_artifact_set
from abicheck.bundle_facts import compare_bundle_from_facts
from abicheck.package import discover_shared_libraries
from abicheck.serialization import load_bundle_facts

old_facts = load_bundle_facts("release-1.0.bundlefacts.json")

# discover_shared_libraries() walks the directory and identifies real ELF
# shared objects by content -- unlike a plain glob("*.so"), it also finds a
# runtime-only versioned DSO (libfoo.so.1, no unversioned dev symlink).
# discover_artifact_set() then canonicalizes each discovered filename the
# same way write_bundle_facts_out() keyed old_facts.per_library_snapshots --
# a plain {p.name: p for p in ...} comprehension would key that same
# libfoo.so.1 as a different library from the persisted "libfoo.so" and
# misreport it as removed.
new = build_bundle_snapshot(
    discover_artifact_set(
        discover_shared_libraries(Path("release-3.0/")), explicit=False
    )
)

# per_library_results still comes from diffing each library's stored
# AbiSnapshot (old_facts.per_library_snapshots[name]) against a freshly
# resolved new-side snapshot, e.g. via abicheck.service.compare_snapshots().
result = compare_bundle_from_facts(old_facts, new, per_library_results)
```

## References

- [ADR-023](../contribute/adr/023-bundle-aware-multi-binary-analysis.md) — design rationale
- [ADR-008](../contribute/adr/008-full-stack-dependency-validation.md) — the resolver/binder engine the bundle layer reuses
- Example cases:
  `case90_bundle_intra_dep_removed` — intra-bundle removed symbol,
  `case91_bundle_intra_signature_drift` — extern-C signature drift,
  `case92_bundle_provider_changed` — provider migration,
  `case93_bundle_manifest_drift` — manifest drift
