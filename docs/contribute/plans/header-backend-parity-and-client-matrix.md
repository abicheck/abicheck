---
doc_type: contributor
level: expert
lifecycle: active
generated: false
---

# Header-backend parity and the client-compiler matrix

**Type:** Initiative plan. It covers the three L2 header backends (CastXML,
direct Clang, native GCC), the choice of default backend, and checking one
library against several expected client compilers.
**Builds on:** [G34](g34-producer-consumer-compiler-profile-separation.md),
which landed the producer/consumer profile split, per-profile `frontend:`,
per-cell scheduling and cross-profile reconciliation. This plan does not
redo that work. It closes G34's open items and adds what G34 never measured:
whether the backends agree.
**Status:** Experiments done (2026-11-11). Phases 2 and 3 done (extractor
generation 55). Phases 1, 4 and 5 open.

## Problem

Users asked three related questions:

1. **Should CastXML stay the default backend?** Clang is a real compiler
   with current C++ support. CastXML is a Clang fork that emulates another
   compiler. Native GCC (opt-in, #1995) runs the client's actual GCC.
2. **What scanner setup fits a given user?** The answer depends on what the
   user has: binary only, headers, debug info, a compile database, and
   which compilers their clients use. No single page says so.
3. **One library, several kinds of client.** A `.so` built once with GCC may
   be consumed by GCC clients and by Clang clients. A public header can
   depend on the compiler (`#if defined(__clang__)`, `__GNUC__` version
   checks, `_MSC_VER`, `-std=` gates). Each client population can see a
   different ABI. The question is "for each kind of client, did v1 → v2
   break it?", not "does a GCC client match a Clang client?".

## Experiments (2026-11-11)

All runs used GCC 13.3, Clang 18.1 and CastXML 0.7.0 on Linux x86-64. The
native GCC plugins were built with `python -m
abicheck.extract.headers.gcc.build`, after installing `gcc-13-plugin-dev`.

### E1 — the generated edit corpus under each backend

`tests/_edit_corpus_harness.py` pins `header_backend="castxml"`. For the
experiment it read `ABICHECK_EDIT_CORPUS_HEADER_BACKEND` instead. The PR
tier (`tests/test_abi_edit_corpus.py -m integration`, 371 cells) then ran
once per backend:

| Backend | Passed | Failed | Distinct failing operators |
|---|---|---|---|
| CastXML | 368 | 0 | — |
| Clang | 354 | 14 | 7 (each fails under both GCC- and Clang-built libraries) |
| Native GCC | 340 | 28 | 14 |

**Caveat.** The corpus, its expectations and `_edit_corpus_known.py` were
calibrated against CastXML. A clean CastXML column therefore shows that
nothing regressed. It does not show that CastXML is better than the other
backends. The failures are still real gaps in our backends.

Clang backend failures, grouped by cause:

| Cause | Operators | Missing / wrong |
|---|---|---|
| No record alignment | `struct_field_widen`, `rebuild_pack_struct` | `TYPE_ALIGNMENT_CHANGED` / `STRUCT_ALIGNMENT_CHANGED` never reported |
| No access on static data members | `static_member_access_narrowed`, `static_member_access_widened` | `VAR_ACCESS_CHANGED` / `VAR_ACCESS_WIDENED` missing; NO_CHANGE |
| Typedef removal over-reported | `typedef_removed` | an extra `FUNC_RETURN_CHANGED` makes it BREAKING (maximum is API_BREAK) |

Native GCC backend failures:

| Cause | Operators |
|---|---|
| C attributes and qualifiers not projected | `api_deprecated`, `api_undeprecated` (enum/type/field deprecation), `field_became_atomic` (`_Atomic`), `flexible_array_member`, `record_became_opaque` |
| C++ facts not projected | `hidden_friend_added`/`_removed`, `vptr_introduced`, `static_member_access_*`, `field_deprecated_removed`, `param_restrict_removed` |
| Symbol/type join | `const_string_object_grew` (`SYMBOL_SIZE_CHANGED_CONST_OBJECT` missing) |
| Over-reported | `type_lost_abstract` (BREAKING, maximum is COMPATIBLE_WITH_RISK) |

### E1b — the same corpus through the public workflow

The harness calls the internal `abicheck.dumper.dump()`, which never runs
the Clang record-layout pass. The public `run_dump` /
`run_dump_request` (and the CLI) do run it. Rerunning E1 through
`run_dump` (`ABICHECK_EDIT_CORPUS_PUBLIC_DUMP=1`) changes the Clang column:

| Backend | Passed | Failed | Remaining causes |
|---|---|---|---|
| CastXML | 368 | 0 | — |
| Clang | 362 | 6 | static-member access (4), typedef removal over-report (2) |
| Native GCC | 340 | 28 | as in E1 |

So the Clang alignment failures were a harness artifact, not a product gap.
The remaining Clang and native GCC failures were real. The generation-55 fix
brings all three backends to 368/368 through `run_dump`, and native GCC to
162/162 on the example catalog.

### E5 — the example catalog under each backend

`tests/test_example_autodiscovery.py::test_example_pipeline` (232 cases;
70 skip for every backend: 42 with no recognised source layout, 22 consumer
source-smoke cases, 5 build-info cases, 1 `_BitInt`):

| Backend | Passed | Failed |
|---|---|---|
| CastXML | 162 | 0 |
| Clang | 162 | 0 |
| Native GCC | 153 | 9 (a C header parsed as C++ when no language was set) |

The catalog's expectations already allow recorded CastXML/Clang
differences (`frontend_kind_differences` in `catalog/ground_truth.json`,
42 cases), so it does not show what CastXML misses. The traced CastXML
limits recorded there are: a `= delete`d member dropped from the XML,
defaulted template arguments and inline namespaces elided from record
names, no data size (so no tail-padding reuse), `extern template` not
provable, and every implicit special member emitted. Clang's recorded
limit is that it sees only explicitly instantiated specializations.

### E2 — the published fact matrix

From the per-fact tables in
[Header-Backend Capabilities](../../reference/header-backend-capabilities.md),
which are generated and tested against the code:

- **Only CastXML:** `vtable_index`, `is_compiler_generated`,
  `is_noexcept_fact`, `specialization_declared_fact`, several
  `type_identities_fact` rows, and record layout. CastXML is the only
  backend that computes layout itself.
- **Only Clang:** `is_template_pattern`, `has_anonymous_aggregate_fields`,
  `is_standard_layout`, complete `is_noexcept` and `is_trivially_copyable`.

### E3 — one header, three clients

The header used `#if defined(__clang__) long long id; #else int id; #endif`
inside `struct msg`:

| Backend | `struct msg` |
|---|---|
| CastXML (emulating the system `cc`, which is GCC) | 64 bits, `int id` |
| Native GCC | 64 bits, `int id` |
| Clang | 128 bits, `long long id` |

Comparing the GCC-client snapshot with the Clang-client snapshot exits `16`
(`not_comparable`, profile fingerprint mismatch). That refusal is correct,
because the two are different contracts. A scan with one backend sees one
client population only. Nothing in a default run tells the user that a
Clang client would see a different layout.

### E4 — ADR-024 option A reproduction

A public C header has `struct cfg`, `union u` and `enum e`, and no
exported signature names them. v2 adds a member to each. CastXML, Clang
and native GCC all report only `ENUM_MEMBER_ADDED`. Both record size
changes are missed.

The cause is `public_surface_closure._record_is_confirmed_public_seed`,
which requires `qualified_name`. All three backends leave it `None` for a
global-scope record, in C and in C++ alike. Native GCC also reports a
spurious `exported_not_public`/`public_not_exported` pair on the same
library, a binding disagreement that the other two backends do not show.
Option A is fixed separately (generation 55). It is listed here because it
holds for every backend.

## Decision

**Keep CastXML as the default for now.** Switch the default only through an
ADR, once the switch criteria below are met. A default that silently drops
alignment and access findings is worse than one whose gaps are known and
calibrated, whatever the theoretical merits of the parser.

**Measure parity, not reputation.** Every header backend runs the same
oracle corpus, and its results are published as a per-backend column.

**Several clients means several contracts.** A run reports one verdict per
declared client population, plus an aggregate. It never compares across
clients.

### Switch criteria for a new default backend

All of these must hold for the candidate backend:

1. Zero E1 failures on the PR tier, with the same `_edit_corpus_known.py`
   entries as CastXML or a recorded reason for each difference.
2. Nightly-tier parity with CastXML within the documented known gaps.
3. No new `❌ No` rows in the fact matrix where CastXML has `✅ Yes`
   (layout, access and vtable index are the main ones).
4. Cache, generation and wire contracts migrated through the repo scripts.
   A default change is an extractor-generation bump.
5. An ADR amendment to ADR-024 / the backend ADR, with a changelog entry
   stating what changes for an unpinned user.

## Phases

### Phase 1 — a backend axis in the oracle corpus (S/M)

- Make `header_backend` an axis of the edit-corpus `Config`, not an
  environment variable. Each backend gets its own `_edit_corpus_known.py`
  section.
- Run Clang and native GCC in the nightly tier. Native GCC needs the plugin
  build step and `gcc-N-plugin-dev` in CI.
- Publish a per-backend ChangeKind coverage column in the coverage ledger.
- Acceptance: E1's table regenerates from CI artifacts, and a new gap in
  any backend fails the tier.

### Phase 2 — close the Clang backend gaps (M) — done (generation 55)

- Record layout already comes from the built-in layout pass on the public
  workflow (E1b). The internal `dumper.dump()` skips it; Phase 1 moves the
  harness to `run_dump`.
- `access` on static data members (`VarDecl` inside a `CXXRecordDecl`): landed separately in #2001 (generation 54).
- Typedef removal must not add `FUNC_RETURN_CHANGED` when the underlying
  type is unchanged.
- Each fix needs a bug-class regression test, a capability-matrix row
  update and a generation bump.

### Phase 3 — close the native GCC gaps (M/L) — done (generation 55)

- Project the attribute and qualifier set listed in E1: deprecation on
  enum/type/field, `_Atomic`, flexible arrays, opaque records, hidden
  friends, the vptr, static member access and parameter `restrict`.
- Fix the export binding behind E4's spurious pair and
  `const_string_object_grew`.
- Fix the `type_lost_abstract` over-report.
- Native GCC stays opt-in until it meets the switch criteria.

### Phase 4 — client populations as first-class (M, closes G34 open items)

G34 already provides `profiles.<id>.consumer_compile` with `frontend:`
(including `gcc`), bindings, a toolchain probe, per-cell scheduling and
`AggregateResult.finding_matrix`. Remaining work:

- **Baseline side under the consumer context.** G34 Phase 0 still lists
  this as open, but baseline publication already resolves the consumer
  overlay (`buildsource/baseline_extraction_context.py`). Confirm it end to
  end with the fixture below and close the G34 item.
- **Hard fail before extraction** when a binding's probed identity does not
  match `compiler_version` on `dump`/`compare` (G34 Phase A, open).
- **A fixture project with GCC and Clang client profiles** (G34 Phase B,
  open), using E3's header shape, so that a change that breaks only Clang
  clients fails only the Clang cell.
- **A report section that names each client population** with its own
  verdict and the backend that parsed it. A finding seen by one population
  only is labelled with that population.
- **A preflight warning** when a public header has compiler-dependent
  branches and only one client population is declared. E3 should not pass
  silently.

### Phase 5 — documentation (S, part done)

- Done: [Client toolchains](../../learn/client-toolchains.md) (learning
  material: different clients, different ABI) and [Scanner setup by
  toolchain](../../use/scanner-setup-by-toolchain.md) (what you have →
  which setup).
- To do: update both pages as Phases 1–4 land, and point
  `header-backend-capabilities.md`'s "When to pick it" column at the E1
  results.

## Out of scope

- Comparing a GCC-client snapshot with a Clang-client snapshot. That is a
  different-contract comparison, refused as `not_comparable` by design.
- MSVC as a header backend. The MSVC lane stays PDB plus CastXML/Clang.

## Files & surfaces

`tests/_edit_corpus_*.py` (Phase 1), `abicheck/extract/headers/` and
`dumper_clang.py` (Phases 2–3), `abicheck/buildsource/project_targets.py`,
`run_plan.py`, `abicheck/aggregate.py`, `.github/workflows/check-project.yml`
(Phase 4), `docs/learn/client-toolchains.md` and
`docs/use/scanner-setup-by-toolchain.md` (Phase 5).
