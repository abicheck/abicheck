# ADR-023: Bundle-aware multi-binary ABI analysis

**Date:** 2026-05-20
**Status:** Proposed
**Decision maker:** Nikolay Petrov

---

## Context

`abicheck compare-release` (ADR-002) and the package extraction layer (ADR-006)
let users point at two release directories and get a per-library verdict table.
The implementation diffs each library against its old self in isolation and
worst-of's the verdicts.

This is insufficient for libraries that actually ship as a *bundle*. Reference
case: oneDAL ships
`libonedal_core.so`, `libonedal_thread.so`, `libonedal_sequential.so`,
`libonedal_dpc.so`, `libonedal_parameters.so`, ... behind one centralized
`include/oneapi/dal/` header tree, with the algorithm libraries having
DT_NEEDED edges into `libonedal_core.so`. The ABI surface that downstream
applications consume is the *union* of what every `.so` exports, plus an
explicit instantiation manifest (`oneapi::dal::train_ops` etc. are
instantiated for a specific list of `(Float, Method, Task)` triples).

Important breakage patterns live only in the relationships between siblings,
not inside any single library:

1. **Intra-bundle removed symbol.** `libonedal_core.so` drops a function that
   `libonedal_thread.so` still imports via DT_NEEDED. Each library viewed in
   isolation can pass (`libonedal_core.so` — symbol removed, but might be in
   an internal namespace; `libonedal_thread.so` — unchanged). At runtime
   `dlopen` of `libonedal_thread.so` fails with `undefined symbol`.

2. **Intra-bundle signature drift across an `extern "C"` or weak boundary.**
   `core_add(int,int)` becomes `core_add(long,long)`. Mangled name is
   identical (C linkage) so the linker is happy; the calling convention is
   wrong. Per-library diff catches the change in `libcore.so`. It does not
   mark `libalgo.so` as affected even though every `libalgo.so` call site
   that touches `core_add` is now miscompiled against the new signature.

3. **Cross-DSO type drift.** Type `oneapi::dal::detail::data_collection` is
   defined in core's headers, used by value in algorithm libraries' public
   types. Changing its layout breaks every algorithm `.so` even when none of
   them changed. ADR-022's `internal_type_leaks_via_public_api` catches the
   per-library leak; it does not propagate the impact across libraries in the
   bundle.

4. **Template instantiation manifest drift.** oneDAL maintains explicit lists
   of which `(Float, Method, Task)` template instantiations are exported.
   Dropping or adding one is an ABI change that is only visible at the
   bundle's symbol-set level (per-library it looks like one more or one fewer
   `func_added`/`func_removed`). The tool today cannot distinguish "expected
   instantiation dropped" from "accidental private symbol unexposed".

5. **Provider migration.** A symbol moves from `libonedal_core.so` to
   `libonedal_parameters.so`. The bundle still exports it once. Downstream
   binaries linked against the old bundle have `DT_NEEDED libonedal_core` but
   not `DT_NEEDED libonedal_parameters`, so `dlopen` still resolves (because
   `libonedal_thread` pulls in `libonedal_parameters` transitively) — or
   doesn't, depending on the link graph. Per-library compare flags
   `func_removed` on core and `func_added` on parameters with no link
   between them.

The user-visible symptom of all five: `compare-release` returns *too
optimistic* a verdict (often `NO_CHANGE` for the affected sibling), and
downstream consumers find out at deployment time.

### What we cannot fix with the existing pipeline

Per-library diff is by construction a function `(snapshot_old,
snapshot_new) -> changes`. The pieces it needs to detect cases 1–5 do not
exist on either side of that function call:

- `snapshot_new_of_libalgo` does not encode "I import `core_add` from
  `libcore.so.1`".
- `snapshot_new_of_libcore` does not encode "my `core_add` is reachable from
  `libalgo.so.1` and `libapi.so.1`".
- Neither encodes the instantiation manifest.

The data we are missing is the **resolution graph** of the new bundle —
which is exactly what `binder.py` already computes for `stack-check`
(ADR-008). The decision below reuses that engine instead of inventing a new
one.

---

## Decision

Introduce a **bundle layer** between release-directory discovery and
per-library diff. The bundle layer treats the set of libraries shipped
together as a single comparison subject. `compare-release` is the front door;
the bundle layer is **enabled by default** — the user gave us the bundle
and the strictly-weaker per-library-only answer is rarely what they want.
An explicit `--no-bundle-analysis` escape hatch remains for debugging or
for parity runs against the pre-ADR-023 behaviour.

### New types

| Type | Module | Role |
|------|--------|------|
| `BundleSnapshot` | `abicheck/bundle.py` | Collection of `AbiSnapshot`s for the libraries discovered in one release, plus a `resolution_graph` that records which DSO defines each symbol and which DSO imports each symbol (with DT_NEEDED edges and `gnu.version_r` versioning). |
| `BundleDiffResult` | `abicheck/bundle.py` | Per-library `DiffResult`s plus a `bundle_findings: list[BundleFinding]` list and a `bundle_verdict`. |
| `BundleFinding` | `abicheck/bundle.py` | A change that exists only because the bundle is multi-library. Has the same shape as `DiffResult.changes` (kind, severity, evidence, affected libraries) but its `affected_libraries: list[str]` is always ≥1 and the `provider_library` / `consumer_libraries` fields are mandatory. |
| `InstantiationManifest` | `abicheck/bundle.py` | Optional input: a YAML/JSON file listing the symbols (typically mangled, often template instantiations) that the release publicly promises. When present, every promised symbol must exist exactly once across the bundle. |

### New `ChangeKind`s

Added to `ChangeKind` enum in `checker_policy.py` and registered in
`change_registry.py`:

| Kind | Default verdict | Category | Catches |
|------|------------------|----------|---------|
| `bundle_intra_dep_removed` | `BREAKING` | Bundle / link-time | Sibling library still has `DT_NEEDED` + undef `U sym` for a symbol the new bundle no longer provides anywhere (case 1). |
| `bundle_intra_dep_signature_changed` | `BREAKING` | Bundle / link-time | Symbol exists in old and new provider with the same mangled name but the provider's DWARF signature changed. The kind fires on every *consumer* sibling that imports the symbol (case 2). The single-library `func_params_changed` on the provider is preserved and cross-linked via `provider_library`. |
| `bundle_intra_type_changed` | `BREAKING` | Bundle / link-time | A `type_*_changed` on a type that is reachable from a public symbol of a *different* library in the bundle. Fires once per consumer (case 3). |
| `bundle_provider_changed` | `COMPATIBLE_WITH_RISK` | Bundle / link-time | A symbol moved from provider A to provider B inside the bundle. Stays compatible iff the existing DT_NEEDED graph can reach the new provider transitively from every consumer (case 5). Otherwise upgraded to `BREAKING`. |
| `bundle_manifest_instantiation_removed` | `BREAKING` | Bundle / manifest | A symbol listed in the old `InstantiationManifest` is not exported by any library in the new bundle (case 4). |
| `bundle_manifest_instantiation_added` | `COMPATIBLE` (addition) | Bundle / manifest | A symbol listed in the new manifest is not in the old one. |
| `bundle_library_removed` | `BREAKING` | Bundle / structural | A library present in the old bundle is absent in the new bundle and at least one symbol it exported was consumed by a sibling (otherwise the existing `--fail-on-removed-library` path still applies for top-level removal). |
| `bundle_library_added` | `COMPATIBLE` (addition) | Bundle / structural | New library appears in the new bundle. |
| `bundle_intra_dep_resolved_to_different_version` | `COMPATIBLE_WITH_RISK` | Bundle / versioning | Sibling import is now satisfied by a different `gnu.version` entry (e.g. `GLIBCXX_3.4.30` instead of `GLIBCXX_3.4.20`). |

Each kind takes a single registry entry with the colocated metadata
(`default_verdict`, `impact`, `is_addition`) per the ADR-011 / change_registry
pattern.

### Resolution graph

For each side (old, new) the bundle layer runs the existing
`abicheck.resolver` and `abicheck.binder` modules (built for `stack-check`)
in **library-set mode**: instead of starting from one root binary and
walking DT_NEEDED transitively, it loads every library in the release as a
root, unions the dependency graphs, and computes bindings using each
library as a potential entry point. Outputs:

- `provides: dict[symbol_key, list[ProviderEntry]]` — which library (and which
  version, when `gnu.version_d` is present) exports each symbol.
- `consumers: dict[symbol_key, list[ConsumerEntry]]` — which libraries have
  `U symbol` and (post-binding) which provider they resolve to under the
  bundle's RPATH/RUNPATH + simulated loader policy.
- `unresolved: list[UnresolvedImport]` — imports that *no* library in the
  bundle satisfies. The bundle layer separates "unresolved against the
  bundle but probably satisfied by the system (libc, libstdc++, libpthread,
  libgcc_s, libm, libdl, librt, libgomp, libtbb, libsycl)" from "unresolved
  and looks like a real break". The system allow-list is hard-coded and
  user-extensible (`--bundle-system-providers` flag).

The graph is computed *once* per side and reused for every bundle finding.

### Per-library diff is unchanged

The bundle layer composes with the existing pipeline. After per-library
diffs run, the bundle layer:

1. Reads each per-library diff's changes and the new-side `resolution_graph`.
2. For every `func_removed` / `var_removed` change in a provider library,
   checks the new-side `consumers` map. If a sibling still has the symbol
   in its undef table, emit `bundle_intra_dep_removed` against the
   *consumer*; cross-reference the *provider*'s `func_removed` finding.
3. For every `func_params_changed` / `func_return_changed` / `var_type_changed`,
   if the symbol is consumed by a sibling, emit
   `bundle_intra_dep_signature_changed` against the consumer.
4. For every `type_*_changed` against a type marked
   `internal_type_leaks_via_public_api` (ADR-022) or otherwise reachable
   from another library's public symbol's type closure, emit
   `bundle_intra_type_changed`.
5. For every `func_removed` in library A paired with `func_added` for the
   same mangled symbol in library B, emit `bundle_provider_changed`. Cancel
   both per-library findings (they're now subsumed) unless `--keep-raw-changes`.
6. If a manifest is provided, intersect old vs new exported-symbol sets
   against the manifest.

### CLI

`compare-release` learns the following surface:

```text
abicheck compare-release OLD NEW
    [-H include/]                     # already exists
    [--manifest manifest.yaml]        # new: instantiation contract
    [--bundle-system-providers libfoo,libbar]   # new: extend system allow-list
    [--no-bundle-analysis]            # new: opt out (debugging / parity)
    [--output-dir reports/]           # already exists, extended below
```

The summary table grows one column:

```text
| Library     | Verdict       | Breaking | Source | Risk | Additions | Bundle |
| libalgo.so  | ❌ BREAKING   | 0        | 0      | 0    | 0         | 2      |
| libapi.so   | ❌ BREAKING   | 1        | 0      | 0    | 1         | 0      |
| libcore.so  | ❌ BREAKING   | 3        | 0      | 0    | 2         | 0      |
+ Bundle verdict: ❌ BREAKING (2 intra-bundle findings)
```

`--output-dir reports/` gains:

- `reports/bundle.json` — `BundleDiffResult` serialized
- `reports/bundle.md` — human summary of bundle findings, grouped by consumer

JSON output of `compare-release --format json` gains top-level fields:

```json
{
  "verdict": "BREAKING",
  "libraries": [...],
  "bundle_findings": [
    {
      "kind": "bundle_intra_dep_removed",
      "consumer_library": "libalgo.so",
      "provider_library": "libcore.so",
      "symbol": "core_mul",
      "severity": "error",
      "evidence": ["elf"]
    }
  ],
  "bundle_verdict": "BREAKING",
  "manifest": { "supplied": false }
}
```

The `verdict` is the worst of `bundle_verdict` and the worst per-library
verdict. Existing `compare-release` exit codes (0/2/4/8) are unchanged in
their meaning; the bundle findings can promote 0 → 4. Bundle analysis is
**enabled by default**; users do not need to do anything to opt in.
`--no-bundle-analysis` is the documented opt-out.

### `--manifest` format (**Experimental** — schema may change in minor releases)

The manifest exists for promises that headers cannot express:
explicit template instantiation lists, `dlopen`/`dlsym` plugin
contracts, internal-but-stable APIs, and symbol-version guarantees.
For the common case (headers + bundle resolution), no manifest is
needed.

Hand-listing every mangled symbol is infeasible for libraries with
thousands of exports and unstable under compiler-ABI shifts. The
schema therefore accepts three entry shapes; the matcher works
against demangled symbol names where possible.

```yaml
# manifest.yaml — versioned ABI promises for a bundle
version: 1
provides:
  # 1. pattern: glob (fnmatch) against the demangled form. Most common.
  - pattern: "oneapi::dal::train_ops<*>*"
    library: libonedal_core.so.1
    optional_provider: false

  # 2. template + instantiations: the right shape for template libs.
  #    abicheck expands each entry into "Template<v1, v2, ...>" and
  #    matches as substring against demangled exported symbols.
  - template: oneapi::dal::train_ops
    instantiations:
      - {Float: float,  Method: "method::dense",  Task: "task::train"}
      - {Float: double, Method: "method::sparse", Task: "task::train"}
    library: libonedal_core.so.1
    optional_provider: false

  # 3. symbol: literal mangled-name equality. Rare; reserve for
  #    versioned entry points / dlsym plugin contracts.
  - symbol: oneapi_dal_version
    library: libonedal_core.so.1
    optional_provider: false
```

Exactly one of `symbol` / `pattern` / `template` per entry. `library:`
is enforcement of provider when `optional_provider: false`;
`optional_provider: true` accepts any sibling provider (lets bundles
reshuffle internal hosting without breaking the contract).
`optional_provider` must be a real boolean — strings and integers are
rejected to prevent silent contract weakening.

The `scripts/extract_bundle_manifest.py` helper emits a baseline
manifest from an existing release directory (one coarse `pattern:`
entry per namespace+library); users then curate it.

**Why "Experimental":** the schema mixes three matching modes and the
template-form matcher uses substring matching against demangled names,
which works in practice but isn't a complete equivalence to Itanium
mangling. We expect to refine the entry shapes (especially how
default template arguments and SFINAE-pruned overloads are expressed)
based on usage feedback from oneDAL-shaped consumers.

### Library identity within a bundle

Filename-stem matching (ADR-002) is kept as the *baseline* match strategy.
The bundle layer additionally maintains SONAME identity:

- Old `libfoo.so.1` and new `libfoo.so.2` are matched by stem `libfoo.so`
  but their SONAMEs differ. The bundle layer flags this as
  `soname_bump_recommended` if there are breaking per-library changes (we
  already have this kind), and the resolution graph treats them as
  distinct providers when computing whether downstream binaries built
  against `libfoo.so.1` will continue to resolve in the new bundle.

### What is intentionally NOT in scope

- **Reverse impact analysis against an external application.** That stays in
  `appcompat` / `stack-check`. The bundle layer answers "is this release's
  internal ABI consistent and how did the public surface change", not
  "will my customer's binary X load".
- **Per-symbol calling-convention modeling beyond DWARF parameter types.**
  Architecture-specific calling-convention shifts (e.g. SystemV → MS, soft-
  float → hard-float, AVX-512 ABI changes) are out of scope; ADR-018 covers
  cross-platform; this ADR assumes same target triple.
- **Dynamic plugin contracts.** A `dlopen("libplugin.so")` consumer that
  reads symbols via `dlsym` is invisible from DT_NEEDED. ADR-008's
  follow-up work covers this; the bundle layer ignores plugin-style usage.

---

## Consequences

**Pro:**
- Closes the headline gap: oneDAL-style bundles get correct verdicts.
- Reuses `resolver.py` and `binder.py` already shipped for `stack-check`;
  no new graph engine.
- The new ChangeKinds plug into the existing registry, suppression, policy,
  severity, exit-code, and reporter machinery without special cases.
- `--manifest` gives downstream projects a way to encode "this is what we
  promise", which is the missing artifact for instantiation-based libs.

**Con:**
- Bundle analysis multiplies the work per release: O(libs) DWARF/ELF
  parsing happens already; the binder pass adds O(libs²) in the worst
  symbol-fanout case. Mitigation: the binder already runs at sub-second
  scale for sysroots in `stack-check`; release bundles are smaller.
- The new `bundle_intra_dep_signature_changed` finding can be noisy when a
  bundle has heavy use of `extern "C"` headers across siblings (e.g. one
  signature change can fire on N consumers). The reporter groups consumer
  findings under one provider entry, and the existing suppression layer
  applies.
- Bundle findings can promote an otherwise-`COMPATIBLE` verdict to
  `BREAKING`. CI users who relied on per-library compatibility will see
  newly-failing runs. Mitigated by:
  - The bundle finding's `evidence` and `affected_libraries` make the
    failure self-documenting.
  - `--no-bundle-analysis` exists as a debug/parity escape hatch.

**Migration:**
- New ChangeKinds default to BREAKING / COMPATIBLE_WITH_RISK; users can
  downgrade via `--severity-*` flags or policy file per ADR-009/010.
- Existing `compare-release` users get bundle findings automatically.
  Where a release was previously called "compatible" but had latent
  intra-bundle breakage, the new verdict is the truth — there is no
  migration to do, just acknowledgement.

---

## Implementation plan

1. **`abicheck/bundle.py`** — `BundleSnapshot`, `BundleDiffResult`,
   `BundleFinding`, `compare_bundle()`. Pulls libraries via existing
   `dump.py`, resolution via `resolver.py` + `binder.py` in library-set
   mode.
2. **`ChangeKind` + `change_registry`** — add the 9 new kinds.
3. **`abicheck/cli.py`** — wire bundle layer into `_compare_release_libraries`;
   add `--manifest`, `--bundle-system-providers`, `--no-bundle-analysis`.
4. **Reporter changes** — `reporter.py`, `report_summary.py` learn to
   render `bundle_findings`. JSON output adds top-level keys. `--output-dir`
   writes `bundle.json` / `bundle.md`.
5. **Examples** — extend `abicheck_add_case` CMake macro and
   `ground_truth.json` schema to support `multi_binary` cases. Add four
   bundle scenarios covering ChangeKinds 1, 2, 3 (cross-DSO type), and 5
   (provider migration). Case 4 (manifest drift) covered by a manifest
   fixture.
6. **Tests** — `tests/test_bundle.py` (unit tests for resolution graph
   + diff), and bundle entries in `test_example_autodiscovery.py` driven by
   ground_truth.json.
7. **Docs** — update `compare-release` doc + add a guide for bundle
   analysis with the manifest format.

---

## References

- ADR-002: Multi-binary / release compare UX
- ADR-006: Package-level comparison
- ADR-008: Full-stack dependency validation (`resolver.py`, `binder.py`)
- ADR-009: Verdict system and exit-code contract
- ADR-010: Policy profile system
- ADR-011: ABI change classification taxonomy
- ADR-022: `internal_type_leaks_via_public_api` (oneDAL detail-namespace pattern)
