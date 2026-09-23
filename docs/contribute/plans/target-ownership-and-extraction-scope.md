---
doc_type: contributor
level: advanced
lifecycle: active
---

# Target ownership and extraction scope

**Origin:** a 2026-09-23 investigation into letting a project cut L2
header-AST cost by namespace (the SVS case: a clang AST of 3 GB for a few
hundred public declarations). The investigation started as "pass
`-ast-dump-filter=svs::` to clang" and ended somewhere else. This plan
records what was measured, which designs the measurements rule out, and the
configuration, storage, and documentation the surviving design needs.

**ADR:** needs one before Phase 2 — it adds `.abicheck.yml` keys, a
snapshot field (`SCHEMA_VERSION` bump) and a comparability rule. Phase 0
and Phase 1 are additive and can land first.

**Type:** Initiative plan (`extract/`, `model/`, `storage/`,
`comparability.py`, `buildsource/build_config*.py`, `report/`, docs).

**Relationship to other plans:**
[Evidence entity model](evidence-entity-model.md) Phase 3 ("ownership in
the graph") needs an owner for every entity; this plan defines where that
owner comes from and how it is configured. That plan's Phase 5 (measure
before materializing) is the same discipline applied here to extraction.
[libclang selective AST traversal](libclang-selective-ast-traversal.md)
owns the *mechanism* for a cheaper clang walk; this plan owns *which*
declarations a walk may drop.

**Effort:** L overall (six phases, 0–5: four implementation phases, a measurement harness first and a default decision last; roughly 6–9 PRs). **Risk:** medium —
Phase 3 changes what a dump contains, so it is opt-in and recorded until
measured on oneDAL.

## Problem

Three separate questions are answered today by one value, `ScopeOrigin`
(`public_header` / `system_header` / `unknown`), derived from header paths:

| Question | Today |
|---|---|
| **Owner** — whose declaration is it? | Implied by path: under a `-H` root (or `scope.public_header_dirs`) → ours; under a system prefix → toolchain; everything else → `unknown`. |
| **Contract** — what does the target promise? | `ScopeOrigin.PUBLIC_HEADER` plus `policy.internal_namespaces`, applied at different stages. |
| **Retention** — why is this fact in the snapshot at all? | `dependency_scope=filtered` drops only *system* declarations; nothing else is dropped. |

Two consequences, both measured on SVS below:

1. **Named third-party dependencies are kept in full.** fmt, spdlog,
   toml++ and robin-map live under `-I` roots, classify as `unknown`, and
   so survive dependency scoping. They account for **66% of the functions**
   in the SVS core snapshot.
2. **The L2 graph section dominates size.** 351 MB of the 425 MB of
   snapshot section content is `graph` — built from the unfiltered AST,
   so the flat snapshot's dependency filter does not reach it.

A namespace filter looks like the obvious lever. The experiments show why
it is the wrong one.

## Experiments

All runs: SVS `main` (intel/ScalableVectorSearch, 2026-09-23), its pinned
FetchContent dependencies (eve v2023.02.15, fmt 12.1.0, robin-map v1.4.0,
spdlog v1.15.3, toml++ v3.3.0 with SVS's own patch), clang 18.1.3,
castxml 0.7.0, libstdc++ 13, `-std=c++20`. Two translation units:

- **runtime** — the five `bindings/cpp/include/svs/runtime/*.h` headers
  (the real `libsvs_runtime.so` public API).
- **core** — `svs/orchestrators/{vamana,dynamic_vamana,exhaustive}.h` (the
  header-only library; the heavy case).

Peak RSS is the child process's `ru_maxrss`.

### E1 — raw frontend output

| TU | Mode | Time | Peak RSS | Output |
|---|---|---|---|---|
| runtime | clang `-ast-dump=json` | 1.6 s | 112 MB | 205.5 MB |
| runtime | clang `+ -ast-dump-filter=svs::` | 0.6 s | 112 MB | 1.4 MB |
| runtime | castxml | 1.2 s | 135 MB | 4.8 MB |
| runtime | castxml `--castxml-start svs` | 0.5 s | 131 MB | 0.1 MB |
| core | clang `-ast-dump=json` | 36.1 s | 479 MB | 3,019 MB |
| core | clang `+ -ast-dump-filter=svs::` | 10.5 s | 473 MB | 208 MB |
| core | castxml | 16.6 s | 805 MB | 107 MB |
| core | castxml `--castxml-start svs` | 13.1 s | 725 MB | 11.5 MB |

The frontend's own parse is a fixed cost: `--castxml-start` saves 3.5 s of
16.6 s and barely moves RSS. **The win of every filter is output size,
which is what abicheck's Python side pays for.**

### E2 — abicheck today, end to end

`abicheck dump` (clang frontend, default dependency scoping) over the core
TU against a stub `.so`:

| Time | Peak RSS | Snapshot | functions / variables / types |
|---|---|---|---|
| 370 s | 6.66 GB | 733 MB | 13,518 / 1,066 / 707 |

Where the kept functions come from: SVS 4,568; fmt 4,204; toml++ 2,370;
robin-map 1,457; spdlog 919. Section sizes: `graph` 351 MB,
`declarations` 54 MB, `semantic_ir` 19 MB, `types` 2 MB.

The castxml frontend could not complete the same dump: under
`--castxml-cc-gnu g++` emulation castxml rejects libstdc++-13's
`std::greater<>` in SVS's `type_traits.h`. Invoked without the emulation
flag it parses cleanly (E1). This is tracked separately; it is a
compiler-emulation issue, not a scoping one.

### E3 — what a name filter keeps and loses

A synthetic fixture with deliberate traps, then real SVS.

**clang `-ast-dump-filter`** matches a *substring of the qualified name*:

| Declaration | `svs::` | `svs` |
|---|---|---|
| `other::svs::nested_trap` (not ours) | kept | kept |
| `svs_extra::not_ours` | dropped | kept |
| `extern "C" svs_c_entry` (ours) | **dropped** | kept by accident of spelling |
| `mpi::injected_by_svs` (ours, in a dependency namespace) | **dropped** | kept by accident of spelling |
| definition of `mpi::Comm`, a public field's type | **absent** | **absent** |

It also emits one JSON document per matching declaration with no closure:
a public struct's fields still name `mpi::Comm`, but its layout is gone. A
size change there would be invisible — a silent false negative. Clang
accepts one filter string, so there is no flag-level repair.

**castxml `--castxml-start`** matches exact qualified names and emits the
**closure of referenced types**. On the fixture it kept `mpi::Comm` and
`CGlobalCfg` with their fields and dropped all three look-alike traps.

On real SVS, parsed through abicheck's own `_CastxmlParser` and bucketed by
declaring file:

| Declaring file | runtime: full → start | core: full → start |
|---|---|---|
| SVS headers (functions) | 187 → **187** | 9,604 → **9,533** |
| SVS headers (types) | 21 → **21** | 1,275 → **1,251** |
| fmt (functions) | — | 11,831 → 1,293 |
| toml++ (functions) | — | 3,418 → 813 |
| libstdc++ (functions) | 11,494 → 46 | 233,434 → 14,691 |

The runtime API survives intact. The core does not: **71 SVS-owned
functions and 24 SVS-owned types are lost**, and every one is SVS code
declared in someone else's namespace:

- `fmt::formatter<svs::DataType>`, `fmt::formatter<svs::lib::Version>`, …
  (8 specializations and their `parse`/`format` members)
- `std::hash<svs::float16::Float16>`, `std::hash<svs::bfloat16::BFloat16>`
- global `operator<<` overloads

Those are exactly the declarations whose ABI matters to a consumer that
formats or hashes SVS types. `--castxml-start` takes a list of names, but
a specialization of a dependency template cannot be named in advance
without already having parsed the TU.

### E4 — ownership-rooted pruning of the full castxml output

A prototype pruner over the full core XML: seed with every element
declared in a file under the target's header root, then follow `type`,
`returns`, `context`, `members`, `bases` and argument references to a
fixed point.

| | Elements | XML | SVS functions kept | SVS types kept |
|---|---|---|---|---|
| full | 526,933 | 107 MB | 9,604 | 1,275 |
| `--castxml-start svs` | — | 11.5 MB | 9,533 (−71) | 1,251 (−24) |
| ownership-rooted closure | 95,557 | 19.3 MB | **9,604** | 1,261 (−14) |

Zero owned functions lost, including every `fmt::formatter`/`std::hash`
specialization. The 14 missing types are parser-synthesized `type`
records inside owned templates. That is a prototype gap to close in Phase 3
(most likely a reference kind the walk does not follow yet), not a property
of the approach. The closure is 1.7× larger than `--castxml-start`'s,
because following `members` of a referenced dependency class pulls in all
its methods. Phase 3's `referenced` retention mode would keep fields and
bases but not methods.

The prototype ran in 297 s because it removed elements from a list one at
a time (quadratic). A real implementation is one linear pass during the
parse the dumper already does.

## Decisions

1. **Reject clang `-ast-dump-filter` as a user-facing option.** It loses
   owned declarations, keeps foreign ones, and drops referenced type
   definitions with no recovery. The 3,019 → 208 MB saving is real, but
   it buys an unsound snapshot.
2. **Do not make `--castxml-start` the retention mechanism.** It is sound
   for a target whose whole API lives in its own namespace (SVS runtime:
   0 lost), but unsound in general (SVS core: 95 owned declarations lost,
   all of them the "our code in their namespace" case the original design
   warned about). Keep it as an **optional accelerator** behind the
   ownership model: allowed only when the ownership pass can prove it lost
   nothing (Phase 4).
3. **Ownership comes from files, not names.** A declaration is
   target-owned when its declaring file is under a target header root. A
   namespace list validates and refines contract; it never claims
   ownership. This is what kept all 9,604 core functions in E4.
4. **Dependency evidence is retained by reference, not by origin.** A
   dependency type stays when an owned declaration references it (field,
   base, parameter, return, template argument). The rest of the
   dependency does not. This is today's system-header rule
   (`dumper_scoping.py` already retains referenced system types) extended
   to *named* dependencies.
5. **Everything that narrows a dump is recorded and compared.** Same
   discipline as `scope.exclude_headers`: two sides narrowed differently
   are refused, not compared.

## Configuration

### Where it lives

In `.abicheck.yml`, extending the existing `scope:` block — not a new
top-level `surface:` block. `scope:` already owns the neighbouring inputs
(`public_header_dirs`, `exclude_headers`, `public_symbols`), and
`buildsource/build_config_schema.py`'s strict loader already enforces its
key set. Adding a second block would give the same concept two homes.

```yaml
scope:
  # EXISTING. Becomes the target's ownership roots (plus any -H directory).
  public_header_dirs:
    - include/svs/

  # NEW. Named dependency roots. A header under one of these is owned by
  # that dependency even when it is reached through a -I, and even when it
  # sits under a public_header_dirs root (vendored copies: svs/third-party/).
  dependencies:
    - name: fmt
      header_roots: [third-party/fmt/include/]
    - name: toml++
      header_roots: [third-party/tomlplusplus/include/]

  # EXISTING. Still drops a header from the parse entirely.
  exclude_headers: []

  # NEW. Target-owned but not promised. Parsed and kept; contract=private.
  private_headers:
    - include/svs/*/detail/**
  private_namespaces:          # merges with policy internal_namespaces
    - svs::detail

  # NEW. What to keep of dependency declarations.
  #   full        — today's behaviour for named deps (default in Phase 2)
  #   referenced  — only dependency types an owned declaration references,
  #                 with fields/bases/size, not their methods
  dependency_evidence: full
```

Precedence rules (the ADR states these as normative):

1. An explicit ownership root (target or dependency) beats the system-path
   heuristic. An installed target under `/usr/include/svs/` stays
   target-owned.
2. The most specific root wins. `third-party/fmt/include/` inside
   `include/svs/` makes fmt's headers fmt-owned.
3. A `-I` directory is compile context. It never makes anything
   target-owned.
4. `private_*` narrows contract for target-owned declarations only. It
   never drops an observed export, and it never removes a dependency type
   a public declaration exposes.
5. A declaration in a file no root claims stays `owner=unresolved`. It is
   kept, reported, and never silently treated as private.
6. A namespace mismatch — a target namespace declared in a dependency
   file, or a dependency namespace declared in a target file (the
   `fmt::formatter<svs::…>` case) — is a **diagnostic, not a
   reclassification**. The file decides.

### CLI surface

None for the keys, following ADR-068 D5's direction and the `compile:`
precedent: ownership is a property of the project, stated once, and must
be identical on both sides of a comparison. Two exceptions:

- `-H` directories keep folding into the target roots, as today.
- A preview is needed before anyone trusts the config (Phase 1). It rides
  an existing command rather than a new root: `abicheck dump … --dry-run`
  gains the ownership table when `scope.dependencies` or `private_*` is
  set. Whether it needs a real parse (it does, for per-declaration counts)
  and so belongs behind a separate `--explain-scope` flag is the one open
  CLI question.

### Typed API

`InputSpec`/`DumpRequest` get the same fields, resolved through the
ADR-049 D7 resolver so CLI, API and Action runs agree
(`cross_front_end_differences()` must stay empty). The resolved values
feed `effective_config_fields` as `surface.ownership.*`, so they are part
of the configuration digest.

## Storage

One new snapshot field, `AbiSnapshot.extraction_scope` (schema bump):

```json
{
  "ownership_rules": {
    "target_roots": ["include/svs/"],
    "dependencies": [{"name": "fmt", "header_roots": ["third-party/fmt/include/"]}],
    "private_headers": ["include/svs/*/detail/**"],
    "private_namespaces": ["svs::detail"]
  },
  "dependency_evidence": "referenced",
  "prefilter": {"kind": "castxml_start", "names": ["svs"], "verified_lossless": true},
  "fingerprint": "sha256:…"
}
```

- Roots are stored **relative to the project root**, so a baseline made on
  one machine compares against a CI dump on another. The fingerprint is
  over this normalized form.
- **Comparability** (`comparability.py`, next to
  `_check_header_exclusions_comparable`): refuse with
  `ScopeMismatchError` when both sides carry the field and
  `dependency_evidence` or `prefilter` differ, **and** when
  `ownership_rules` differ while either side uses
  `dependency_evidence: referenced`. Under `referenced` the closure is
  seeded from owned declarations, so a changed root changes which
  dependency declarations exist in the snapshot. That would surface as
  false additions or removals, or hide a dependency layout change.
  Re-scoping such a project requires a new baseline. Only when both sides
  use `full` does a differing `ownership_rules` change classification
  alone, not presence. That case is a warning plus a report line showing
  which findings moved.
- A snapshot without the field loads as `unknown` — never as `full` — the
  same "unprovable is not native" rule `header_exclusion_record.py`
  applies.
- **Per entity**: `owner` (`target` / `dependency:<name>` / `toolchain` /
  `unresolved`) and `contract` (`public` / `private` / `unresolved`) as
  `Fact[...]` fields next to `model/surface_facts.py`'s three existing
  facts, with the matched rule id. `ScopeOrigin` stays as a derived
  compatibility reading until its readers migrate.
- The L2 graph and `semantic_ir` apply the **same** resolved
  classification. Evaluation happens once, in `extract/`, and every later
  stage reads it. Without that, the flat snapshot can shrink while the
  351 MB graph section does not.

## User documentation

| Page | Change |
|---|---|
| `docs/reference/config-file.md` § `scope:` | New keys, the six precedence rules, and the "`-I` is not ownership" statement. |
| `docs/reference/config-keys-reference.md` | One row per new key (type, default, CLI: none). |
| **New** `docs/use/target-ownership.md` | Task guide: "My library pulls in heavy dependencies". Walks the SVS shape end to end: declare roots, run the preview, read the diagnostics, switch `dependency_evidence` to `referenced`, what the report says afterwards. States plainly why there is no namespace filter, and links E3. |
| `docs/use/dump-compare-flags.md` | The preview output. |
| `docs/reference/snapshot-format.md` | `extraction_scope` and the per-entity facts. |
| `docs/learn/evidence-and-detectability.md` | What `referenced` retention can and cannot detect: a dependency type's *layout* change is still seen; a change to a dependency method no owned declaration calls is not. |
| `docs/use/troubleshooting.md` | The two refusal messages: differing `dependency_evidence`, differing `prefilter`. |
| `changelog.d/` fragment per code PR | Standard. |

## Phases

| Phase | Scope | Size | Behaviour change |
|---|---|---|---|
| **0 — Record the numbers** | Commit the E1–E4 harness as `scripts/bench_extraction_scope.py`, parameterized over a TU and include flags, so every later phase reruns the same measurement. Run it on oneDAL. | S | none |
| **1 — Classify and preview** | `extract/ownership.py`: one pure function, file path + rules → (owner, contract, rule id, diagnostics). Parse `scope.dependencies`/`private_*` in `build_config.py` (not yet applied). The preview output. Property tests: rule-order independence, most-specific-root wins, `-I` never grants ownership, a system prefix never beats an explicit root. | M | none — report only |
| **2 — Persist** | ADR. `extraction_scope` field, per-entity facts, comparability rule, digest fields. `dependency_evidence` accepted but only `full`. | M | new refusal between differently-configured snapshots |
| **3 — `referenced` retention** | Apply the closure at parse time for both backends: the castxml parser (a linear seed-and-follow over the id map it already builds) and the clang streaming pruner (`dumper_clang_streaming.py`, which already skips system declarations at parse time). Same rule for the graph section. Close E4's 14-type gap. Gate: on SVS core and oneDAL, zero owned declarations lost against `full`, and the finding set on a real version pair unchanged except for dependency-internal kinds. | L | opt-in |
| **4 — Prefilter accelerator** | Allow `--castxml-start <target namespaces>` only when Phase 1's classification, run on a cached full parse or on the first dump, shows zero owned declarations outside those namespaces. Otherwise ignore it with a diagnostic. Record `verified_lossless`. | S–M | opt-in |
| **5 — Default** | Decide whether `referenced` becomes the default, using Phase 0 numbers on oneDAL and SVS. | S | possibly default |

## Tests

- Phase 1: primitive-level property tests for the classifier, per
  `AGENTS.md`. The oracle is an independent table of (path, rules) →
  expected owner, not the function's own matching helpers.
- Phase 3: the E3 fixture checked in as a regression corpus, covering each
  trap row: `extern "C"`, a target declaration in a dependency namespace,
  a dependency-template specialization for a target type, a look-alike
  namespace, a public field of a dependency type whose layout changes
  between old and new. The last must still produce a layout finding under
  `referenced`.
- A differential test between `full` and `referenced` on the same input
  that proves both configurations ran (distinct cache roots, per the
  "differential test must prove both of its configurations actually ran"
  rule).

## Open questions

1. Should `referenced` keep a dependency class's virtual methods? The
   vtable layout of an exposed polymorphic dependency type is ABI. The
   likely answer is yes for virtual methods, no for non-virtual ones.
2. Inline dependency functions that an owned inline function calls are
   baked into consumers. Do they count as referenced? L2 has no call edges
   without L5, so Phase 3 likely leaves this `unknown` and says so.
3. The preview's form (`--dry-run` vs `--explain-scope`), above.

## Out of scope

- A namespace-based ownership filter in any form.
- Release fan-out ownership. `model/release_surface.py` already owns
  per-provider acquisition. This plan feeds its acquisition identity (the
  rules fold into `SurfaceAcquisitionIdentity`) and does not replace it.
- The castxml compiler-emulation failure noted in E2.
