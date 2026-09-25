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

**ADR:** [ADR-075](../adr/075-target-ownership-and-extraction-scope.md)
decides Phase 2 — the `.abicheck.yml` keys, the snapshot field
(`SCHEMA_VERSION` bump), the per-entity facts and the comparability rule.
Phase 0 and Phase 1 were additive and landed first.

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

Measured on SVS and oneDAL below, this has two different consequences:

1. **Named third-party dependencies are kept in full.** fmt, spdlog,
   toml++ and robin-map live under `-I` roots, classify as `unknown`, and
   so survive dependency scoping. They are **65–66% of the functions** in
   an SVS core snapshot, with either frontend.
2. **The L2 graph section dominates size.** It is 54–93% of the section
   content of every snapshot measured (M4). It is built from the
   unfiltered AST, so the flat snapshot's dependency filter
   does not reach it.

The first matters for SVS and hardly at all for oneDAL, whose public
headers pull in little beyond the standard library. The second matters for
both. A namespace filter looks like the obvious lever for the first; the
measurements show why it is the wrong one.

## Measurements

All numbers come from `scripts/bench_extraction_scope.py` (this plan's
Phase 0 harness), rerunnable unchanged by every later phase. Peak RSS is
each child process's own `ru_maxrss` (the largest single process in its
tree). Host: 4 CPUs, 15 GB RAM; clang 18.1.3, castxml 0.7.0, g++/libstdc++
13, Python 3.13. Each `abicheck dump` is a cold-cache run against a stub
`.so`, so it measures header extraction, not binary analysis.

| Target | Revision | Translation unit | Flags |
|---|---|---|---|
| SVS runtime | ScalableVectorSearch `main`, 2026-09-23 | the five `bindings/cpp/include/svs/runtime/*.h` (the `libsvs_runtime.so` API) | `-std=c++20`, SVS's own defines, `-fsized-deallocation` |
| SVS core | same | `svs/orchestrators/{vamana,dynamic_vamana,exhaustive}.h` (header-only) | same, plus the pinned FetchContent deps: eve v2023.02.15, fmt 12.1.0, robin-map v1.4.0, spdlog v1.15.3, toml++ v3.3.0 with SVS's patch |
| oneDAL | uxlfoundation/oneDAL `main`, 2026-09-24 | `oneapi/dal.hpp` plus all 26 `oneapi/dal/algo/*.hpp` | `-std=c++17 -Icpp` (host API; no SYCL) |
| DAAL | same | `daal.h` | `-std=c++17 -Icpp/daal/include -Icpp/daal` |

### M1 — frontend output

Time / peak RSS / output size.

| Target | clang full | clang `-ast-dump-filter` | castxml full | castxml `--castxml-start` | ownership closure |
|---|---|---|---|---|---|
| SVS runtime | 1.7 s / 112 MB / 204 MB | 0.4 s / 112 MB / 1.4 MB | 0.6 s / 135 MB / 4.8 MB | 0.5 s / 131 MB / 0.1 MB | 0.2 s / 47 MB / 0.5 MB |
| SVS core | 25.4 s / 478 MB / 2,935 MB | 7.8 s / 473 MB / 188 MB | 12.4 s / 806 MB / 107 MB | 10.4 s / 726 MB / 11.4 MB | 8.3 s / 677 MB / 19.2 MB |
| oneDAL | 4.2 s / 151 MB / 502 MB | 1.6 s / 151 MB / 104 MB | 2.2 s / 234 MB / 20.4 MB | 1.9 s / 220 MB / 2.9 MB | 1.1 s / 144 MB / 3.4 MB |
| DAAL | 4.7 s / 140 MB / 679 MB | 3.2 s / 140 MB / 409 MB | 1.4 s / 162 MB / 10.5 MB | 1.4 s / 159 MB / 5.4 MB | 0.9 s / 84 MB / 5.7 MB |

The filters cut output, not the frontend's own cost: RSS barely moves, and
castxml's parse is 1.4–12.4 s either way. The name filters are for a
namespace (`svs`, `oneapi`, `daal`); the closure is seeded from the
target's header root. The closure is the harness's prototype: a separate
Python process that parses castxml's full XML, walks it and writes the
pruned document. Its time and RSS are on top of castxml full's, and are an
upper bound for Phase 3, which would apply the same walk inside the parse
abicheck already does.

### M2 — what each narrowing loses

Target-owned functions and types present in the full castxml parse but
missing after narrowing, counted through abicheck's own castxml parser.

| Target | `--castxml-start` | ownership closure | What `--castxml-start` lost |
|---|---|---|---|
| SVS runtime | 0 | 0 | — |
| SVS core | **81** | 0 | `fmt::formatter<svs::…>` and `std::hash<svs::…>` specializations with their members, global `operator<<` |
| oneDAL | **19** | 0 | oneDAL's `extern "C"` functions: `_onedal_new_mutex`, `_onedal_get_tls_ptr`, `_onedal_lock_mutex`, … |
| DAAL | 4 (not API) | 0 | `__atomic_load_n`, `__atomic_add_fetch`, … — GCC builtins castxml declares implicitly (`artificial="1"`) and attributes to the DAAL header that first uses them |

Both real losses are the cases a name filter cannot express: target code
declared in someone else's namespace (SVS), and a C API at global scope
(oneDAL). `--castxml-start` takes names, and neither case has a name
under the target's namespace to give it. The DAAL row is a classification
finding rather than a loss: an implicitly declared compiler builtin must be
toolchain-owned whichever file first uses it (Phase 1 rule).

clang `-ast-dump-filter` was not run through a parser here; its output is a
stream of unrelated per-declaration documents that abicheck's clang parser
does not accept. On a fixture it lost the same two classes and also dropped
the definitions of referenced types (`mpi::Comm`, the type of a public
field), since it emits no closure — a size change there would be
invisible. Clang takes one filter string, so there is no flag-level repair.

### M3 — dependency declarations the closure would keep

Functions from each named dependency in the full castxml parse, and in the
ownership closure (M2's zero-loss column).

| Target | Dependency | Full parse | Closure |
|---|---|---|---|
| SVS core | fmt | 12,105 | 1,989 |
| SVS core | toml++ | 3,449 | 1,171 |
| SVS core | spdlog | 1,013 | 821 |
| SVS core | robin-map | 928 | 619 |
| SVS core | libstdc++ | 235,896 | 31,576 |
| oneDAL | libstdc++ | 48,737 | 5,703 |
| DAAL | libstdc++ | 13,527 | 1,789 |

For SVS the closure keeps 4,600 of 17,495 named-dependency functions
(26%). It still keeps whole referenced dependency classes, methods
included; `referenced` retention (Phase 3) keeps fields and bases and would
keep fewer.

### M4 — abicheck dump today, end to end

| Target | Frontend | Time | Peak RSS | Snapshot file | `graph` share of section content | Functions: target / named deps |
|---|---|---|---|---|---|---|
| SVS runtime | clang | 20.0 s | 515 MB | 44 MB | 91% | 86 / 0 |
| SVS runtime | castxml | 15.8 s | 484 MB | 55 MB | 93% | 187 / 0 |
| SVS core | clang | 295 s | 6.50 GB | 728 MB | 83% | 4,568 / 8,950 |
| SVS core | castxml | 498 s | 7.89 GB | **1,199 MB** | 82% | 9,616 / 17,495 |
| oneDAL | clang | 68.6 s | 1.74 GB | 159 MB | 77% | 3,613 / 0 |
| oneDAL | castxml | 84.3 s | 1.55 GB | 212 MB | 89% | 2,533 / 0 |
| DAAL | clang | 79.4 s | 1.52 GB | 196 MB | 54% | 12,177 / 0 |
| DAAL | castxml | 54.6 s | 1.33 GB | 191 MB | 62% | 10,067 / 0 |

The snapshot file is indented JSON; the `graph` share is measured over the
compact JSON of each section. The two frontends count functions
differently (clang's walk drops some declarations castxml reports), so
compare within a frontend, not across.

What this says:

- **The cost is abicheck's, not the compiler's.** For SVS core the
  frontend emits its output in 12–25 s under 1 GB (M1); the dump takes
  295–498 s and 6.5–7.9 GB. What abicheck materializes is the lever.
- **For SVS, dependency retention is the lever.** Named dependencies are
  65–66% of the functions with either frontend.
- **For oneDAL, the graph is the lever.** No named dependency leaks in;
  the `graph` section is 77–89% of the snapshot's content.
- **A default dump can write a snapshot the default reader refuses.** SVS
  core via castxml writes 1,199 MB, over the 1 GiB snapshot safety limit
  (`ABICHECK_SNAPSHOT_MAX_STORED_BYTES`), so reading it back — and so any
  `compare` against it as a baseline — fails unless the operator raises the
  limit. Phase 3's acceptance criterion includes getting SVS core under it
  with the default settings.

The castxml frontend could complete SVS core only after two parser fixes
that landed in the same PR as this plan. castxml asks the compiler it
emulates for predefined macros, and abicheck passed `-std=c++20` to
castxml's parser but not to that compiler, so libstdc++ saw C++17 and
declared no `std::integral`. Once that parsed, function-local declarations
castxml emits for deduced `auto` return types broke the dump.

## Decisions

1. **Reject clang `-ast-dump-filter` as a user-facing option.** It loses
   owned declarations, keeps foreign ones, and drops referenced type
   definitions with no recovery. The 2,935 → 188 MB saving on SVS core is
   real, but it buys an unsound snapshot (M1, M2).
2. **Do not make `--castxml-start` the retention mechanism.** It is sound
   for a target whose whole API lives in its own namespace (SVS runtime:
   0 lost), but unsound in general: SVS core lost 81 owned declarations
   declared in `fmt`/`std`, and oneDAL lost all 19 of its `extern "C"`
   functions (M2). Keep it as an **optional accelerator** behind the
   ownership model: allowed only when the ownership pass can prove it lost
   nothing (Phase 4).
3. **Ownership comes from files, not names.** A declaration is
   target-owned when its declaring file is under a target header root. A
   namespace list validates and refines contract; it never claims
   ownership. It lost nothing on any of the four targets (M2).
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
6. An implicitly declared compiler builtin (castxml's `artificial="1"`
   `__atomic_*`/`__builtin_*`/`__sync_*` at global scope) is
   toolchain-owned. castxml attributes it to the first file that uses it,
   which on DAAL made four GCC builtins look like DAAL API (M2).
7. A namespace mismatch — a target namespace declared in a dependency
   file, or a dependency namespace declared in a target file (the
   `fmt::formatter<svs::…>` case) — is a **diagnostic, not a
   reclassification**. The file decides.

### CLI surface

None for the keys, following ADR-068 D5's direction and the `compile:`
precedent: ownership is a property of the project, stated once. It must
be identical on both sides of a comparison when either side uses
`dependency_evidence: referenced`; when both use `full`, differing
`ownership_rules` are compared with a warning (see Storage). Two
exceptions to "no CLI spelling":

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
  applies. Comparisons involving `unknown`, stated explicitly:
  - `unknown` vs a side with `dependency_evidence: referenced` or any
    `prefilter`: **refused**. Nothing proves the `unknown` side kept the
    same declarations, and a difference would read as additions or
    removals.
  - `unknown` vs `full` with no prefilter: **compared, with a report
    line** naming the unrecorded side. Every snapshot written before the
    field existed was produced without any narrowing (the capability did
    not exist), which is the precedent `comparability.py` already applies
    to `dependency_scope`: refuse only on two explicit, differing values.
    Refusing here would reject every existing baseline on upgrade, for a
    difference that cannot exist.
  - `unknown` vs `unknown`: compared as today.
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
| `docs/reference/config-file.md` § `scope:` | New keys, the seven precedence rules, and the "`-I` is not ownership" statement. |
| `docs/reference/config-keys-reference.md` | One row per new key (type, default, CLI: none). |
| **New** `docs/use/target-ownership.md` | Task guide: "My library pulls in heavy dependencies". Walks the SVS shape end to end: declare roots, run the preview, read the diagnostics, switch `dependency_evidence` to `referenced`, what the report says afterwards. States plainly why there is no namespace filter, citing M2. |
| `docs/use/dump-compare-flags.md` | The preview output. |
| `docs/reference/snapshot-format.md` | `extraction_scope` and the per-entity facts. |
| `docs/learn/evidence-and-detectability.md` | What `referenced` retention can and cannot detect: a dependency type's *layout* change is still seen; a change to a dependency method no owned declaration calls is not. |
| `docs/use/troubleshooting.md` | The two refusal messages: differing `dependency_evidence`, differing `prefilter`. |
| `changelog.d/` fragment per code PR | Standard. |

## Phases

| Phase | Scope | Size | Behaviour change |
|---|---|---|---|
| **0 — Record the numbers** | Done: `scripts/bench_extraction_scope.py` produced M1–M4 for SVS and oneDAL. Every later phase reruns it and updates those tables. | S | none |
| **1 — Classify and preview** (landed) | `extract/ownership.py`: one pure function, file path + rules → (owner, contract, rule id, diagnostics). Parse `scope.dependencies`/`private_*` in `build_config.py` (not yet applied). The preview output. Property tests: rule-order independence, most-specific-root wins, `-I` never grants ownership, a system prefix never beats an explicit root. | M | none — report only |
| **2 — Persist** | ADR. `extraction_scope` field, per-entity facts, comparability rule, digest fields. `dependency_evidence` accepted but only `full`. | M | new refusal between differently-configured snapshots |
| **3 — `referenced` retention** | Apply the closure at parse time for both backends: the castxml parser (a linear seed-and-follow over the id map it already builds) and the clang streaming pruner (`dumper_clang_streaming.py`, which already skips system declarations at parse time). Same rule for the graph section. Gate: on SVS core and oneDAL, zero owned declarations lost against `full`, and the finding set on a real version pair unchanged except for dependency-internal kinds. | L | opt-in |
| **4 — Prefilter accelerator** | Allow `--castxml-start <target namespaces>` only when Phase 1's classification, run on a cached full parse or on the first dump, shows zero owned declarations outside those namespaces. Otherwise ignore it with a diagnostic. Record `verified_lossless`. | S–M | opt-in |
| **5 — Default** | Decide whether `referenced` becomes the default, using Phase 0 numbers on oneDAL and SVS. | S | possibly default |

### Phase 1 as landed

- Rules: `model/ownership_rules.py`. Classifier: `extract/ownership.py`
  (`resolve_ownership_rules` + `classify`). Config: `scope.dependencies`,
  `scope.private_headers`, `scope.private_namespaces`, parsed by
  `buildsource/build_config_scope.py`. Preview: `workflows/ownership_preview.py`,
  rendered by `dump --dry-run`.
- The preview answers open question 3 for now: it rides `--dry-run` and
  classifies the `-H` headers without parsing. Per-declaration counts
  arrive with Phase 2, where each declaration records its owner, so no
  `--explain-scope` flag was added.
- `dependency_evidence` is not accepted yet. A key the run cannot act on
  would be inert configuration; it arrives with the phase that honours it.
- A dependency or toolchain declaration's contract is `external`, a fourth
  value next to the three above: the target promises nothing about it, which
  is different from "unknown".
- Rule 7's diagnostic fires in one direction only: a target file declaring
  into a namespace named like a configured dependency. The other direction
  needs the target's namespaces, which no key states.

## Tests

- Phase 1: primitive-level property tests for the classifier, per
  `AGENTS.md`. The oracle is an independent table of (path, rules) →
  expected owner, not the function's own matching helpers.
- Phase 3: the fixture behind M2 checked in as a regression corpus,
  covering each trap row: `extern "C"`, a target declaration in a dependency namespace,
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
