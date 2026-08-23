---
doc_type: contributor
level: advanced
lifecycle: active
---

# libclang selective AST traversal for the direct-clang L2 backend

**Type:** Investigation / spike proposal, not a committed implementation
plan. Distinct from [G4 — libclang header-AST extractor](g4-header-ast-extractor.md):
G4 justifies adding `clang.cindex` bindings on **feature-completeness**
grounds (concepts, `explicit`, ctor mangling castxml cannot emit at all);
this document evaluates the **same class of new dependency** on
**performance** grounds, for a different existing backend
(`abicheck/dumper_clang.py`, the already-shelled-out
`clang -ast-dump=json` L2 frontend). If G4 ever lands, the two should share
one `abicheck[clang]` optional-dependency story rather than each adding
`libclang` independently — noted here so a future implementer checks G4's
status first.

**Effort:** L for the prototype step (a real, model-object-building
selective walk, per the updated "Recommendation" below — the cursor-count-
only spike this estimate originally covered has now been run); XL if the
prototype recommends proceeding to a real migration. **Risk:** high — a new
native dependency, a structurally different traversal API with its own
parity burden across every one of `dumper_clang.py`'s already-hard-won
extraction facts — but the CPU-cost investigation below (updated 2026-08-23
with a real `clang.cindex` measurement) narrows the risk that was
previously the biggest unknown: the promised win is real and substantial at
the cursor-traversal layer this document can now verify; what remains
unverified is only the per-node semantic-construction cost on libclang's
native API, not whether the avenue is worth pursuing at all.

## Problem statement

`abicheck/dumper_clang.py`'s direct-clang L2 backend shells out to
`clang -Xclang -ast-dump=json`, captures its stdout, and (via
`dumper_clang_errors._parse_clang_ast_result`) `json.load()`s the entire
document into one Python `dict` before `_ClangAstParser` walks it into
`AbiSnapshot` model objects. Measured this session on a synthetic repro (a
tiny 16KB `.so` whose public header pulls in `<vector>`/`<map>`/
`<unordered_map>`/`<functional>`/`<tuple>`, instantiated across a handful of
types, via a trivial umbrella header):

| headers | wall | peak RSS | AST-JSON size |
|---|---|---|---|
| 1 | 24.8s | 945 MB | 67.7 MB |
| 10 | 41.5s | 946 MB | 68.9 MB |
| 20 | 58.0s | 762 MB | 70.3 MB |

A field report against a real product (oneDAL) reproduced the same shape at
much larger scale: a single, trivial 290-symbol library with 14 headers took
over 25 minutes and 5.5 GB peak RSS to dump; a 6-library whole-product bundle
compare took over 2.5 hours and hit a 38.3 GB peak-RSS OOM kill.

The dominant cost is template-instantiated **member function** content from
dependency headers (`<vector>`'s constructors, iterators, `push_back`
overloads, allocator machinery — instantiated once per element type) that
`dumper_scoping.scope_snapshot_excluding_dependencies` unconditionally
discards moments later anyway for functions and variables (no
"directly referenced" carve-out applies to those two categories — see that
module's own docstring). That existing filter cannot help with the cost
itself: it runs on the fully-parsed, fully-materialized `AbiSnapshot`, well
after `json.load()` and the entire `_ClangAstParser` walk have already paid
for every excluded declaration.

**What a same-repository fix (this session's companion change,
`abicheck/dumper_clang_streaming.py`) can and cannot buy.** A `json.load()`
`object_pairs_hook` can collapse a dependency-header function/variable
subtree into a placeholder the moment it completes, correctly and
conservatively (see that module's own docstring for the exact rule). But
this was **measured, not assumed**, to hit two hard, structural ceilings:

1. **Clang's own "sticky"/delta location encoding.** A JSON AST node only
   carries an explicit `loc.file` when the file *differs* from the
   immediately preceding sibling in clang's own pre-order emission — most
   nodes deep inside one dependency header's content inherit their file
   from ambient context. A `json.load()` hook fires **bottom-up** (a node's
   entire subtree is already built before its own hook call), which cannot
   see that ambient, top-down context at all — correctly resolving it would
   require replaying clang's own pre-order traversal, which is a
   structurally different order than hook-firing order. Restricting
   pruning to nodes with their *own*, unambiguous, explicit `loc.file` (the
   only sound option available to a bottom-up hook) is correct but
   conservative: on the single-header repro above, only ~1.6% of the
   1,027,346 total JSON nodes qualified.
2. **`object_pairs_hook` itself imposes a real, unavoidable per-object
   cost across the *entire* document**, not just the pruned portion — the
   CPython C-accelerated scanner must materialize a Python list of
   `(key, value)` pairs and invoke a Python-level callback for *every*
   JSON object (a document with over a million nodes means well over a
   million such calls), which the no-hook path never pays. Measured
   directly: on the single-header repro, this pruning made the full
   `dump()` pipeline (`json.load()` + `_ClangAstParser` walk) **slower**
   (5.1s → 7.1s), not faster, despite the model's function count genuinely
   dropping (8,167 → 8,054) — the fixed per-object overhead outweighed the
   modest, conservative pruning win. The same pattern held at larger scale
   (a 20-header repro, ~165,000 functions, ~10.2GB baseline peak RSS):
   pruning cut peak RSS by ~1.2% and functions by ~2.2%, but wall time was
   again worse (114.2s → 129.3s, ~13% slower) — not a small-scale artifact.

The conclusion this document starts from: **within the current
`json.load()`-based architecture, no purely Python-side post-processing
strategy — the hook approach implemented this session, or a hand-rolled
pure-Python top-down parser that could prune more aggressively (see
"Rejected alternative" below) — can reliably deliver a net win**, because
clang's own subprocess-side parse/instantiate/serialize work and the JSON
tokenization cost itself are both paid in full regardless, and any
Python-level interception of the *result* competes against CPython's
C-accelerated scanner on unfavorable terms. Only an approach that changes
**what clang is asked to produce**, or that inspects clang's output through
an API that never serializes the excluded subtrees as JSON text in the
first place, can plausibly avoid this ceiling — which is what this document
evaluates.

### Rejected alternative: a hand-rolled top-down pure-Python parser

Before turning to libclang, a hand-rolled recursive-descent JSON parser
(using `json.decoder.scanstring` for C-accelerated leaf scanning, with
custom Python-level object/array traversal to thread an ambient
"current file" down the way `dumper_clang._node_file`'s existing
top-down walk already does, and true byte-level skipping of a pruned
node's `"inner"` array without building any objects for it) was considered
and **not attempted**, for a reason worth recording rather than
rediscovering: real clang AST-dump JSON puts `"kind"` and `"loc"` before
`"inner"` in each object's key order (confirmed against real clang 18
output), so a true top-down parser genuinely *can* make an early-exit
decision before paying to tokenize a pruned subtree's bytes — but doing so
means abandoning CPython's C-accelerated recursive-descent scanner for the
**entire** document, including the ~90%+ majority that must still be kept
and fully materialized for `_ClangAstParser`. Pure-Python structural JSON
parsing (dict/list construction, bracket/string matching) at this scale
(single-digit millions of nodes and up) is routinely an order of magnitude
or more slower than the C accelerator for the portion it must still fully
build, and there is no way to keep the C accelerator for the "kept" portion
while substituting custom logic only for the "pruned" portion within the
stdlib `json` module's public API. Whether an even-more-aggressive
ambient-aware hand-rolled parser could still net a win despite this penalty
was not measured — building it to find out would be a nontrivial spike
in its own right, and the analysis below suggests libclang is likely to
dominate it on every axis that matters (native tree, no Python-level
per-node cost at all for a skipped cursor) — so it was not pursued instead
of, or before, evaluating libclang.

## What libclang actually buys

`clang.cindex.Index.parse()` builds libclang's own native `CXTranslationUnit`
and exposes it as a tree of `Cursor` objects (`TranslationUnit.cursor`,
`Cursor.get_children()`). A caller can inspect `cursor.location.file` (or
`cursor.kind`) and simply choose **not to call `get_children()`** on a
cursor whose subtree should be excluded — no JSON serialization, no
`object_pairs_hook`, and (critically) no Python-level visitation cost at all
for the excluded subtree's descendants, since `get_children()` is what
would have produced Python `Cursor` wrapper objects for them in the first
place. This closes the second ceiling above completely: unlike
`object_pairs_hook`, which is invoked by the C scanner unconditionally for
every JSON object regardless of what the caller's Python code does with it,
`cursor.get_children()` is called *by the traversal code itself*, so
skipping the call means the excluded subtree's Python-visible representation
is never constructed at all.

**Update — verified with a working `clang.cindex` install (2026-08-23,
post-streaming-pruner session).** The `libclang` PyPI package installs
cleanly against this host's real `libclang-18.so.1` (the same Clang 18
already used for the subprocess-based backend), so the crux question below
is answered with real measurements, not documented-semantics inference —
superseding the "not resolved" framing this section originally carried. The
raw experiment (`Index.parse()` timed alone, then a full `get_children()`
walk vs. a selective one that never recurses into a dependency-header
cursor, against the same synthetic 7-types-x-heavy-STL-headers repro used
throughout this investigation) and a `cProfile` of the *real* `abicheck
dump()` pipeline on the identical input are both reproducible from this
session's own scratch scripts; the numbers below are from those runs.

### The crux: does skipping a cursor's children avoid clang's *own* cost?

**Short answer: no, that specific cost is not avoidable — but it was never
the dominant cost to begin with, which changes the recommendation.**

- **Confirmed: `Index.parse()`'s own cost is insensitive to what happens
  afterward**, exactly as Clang's Sema architecture predicts. For the
  synthetic repro (1 header, then 10 headers — result identical either way
  since the shared dependency dedups within one TU): `Index.parse()` alone
  takes ~0.40s regardless of whether anything is ever visited afterward.
  `TranslationUnit.PARSE_SKIP_FUNCTION_BODIES` genuinely reduces this by
  ~28–31% (0.40s → ~0.29s) and roughly halves the resulting cursor count
  (118,801 → 62,015) — a real, previously-unverified, orthogonal win this
  section only speculated about before. Neither result depends on
  traversal, confirming the "Sema runs before any cursor is visited"
  hypothesis directly rather than by inference.
- **Also confirmed, and this is the load-bearing correction to this
  section's earlier text: `Index.parse()`'s ~0.40s is a *small* fraction of
  the current backend's real total cost, not "probably the single largest
  contributor."** A `cProfile` of the actual `abicheck.dumper.dump()` call
  on the identical single-header repro (10.55s wall total in this run)
  attributes it roughly as: clang subprocess (parse *and* emit JSON text)
  ≈ 1.3s (12%); `json.load()`'s C-accelerated `raw_decode` of the resulting
  ~280MB text ≈ 3.5s (33% — the single largest identifiable chunk);
  `_ClangAstParser.__init__`'s id-map construction (resolving clang's
  string-keyed `id`/`referencedDecl`/`type.qualType` back-references, a
  workaround the JSON representation needs that a native object graph
  would not) ≈ 1.9s (18%); the remaining ~30–35% split across
  `parse_functions`'s own per-declaration walk (a `dict.get()` call was
  made **6.4 million times** in this one run) and per-node semantic work
  like initializer-expression fingerprinting. **None of this is clang's
  own Sema/instantiation cost — every bit of it is Python-side work this
  backend's JSON-based architecture makes structurally necessary**: parsing
  a huge text blob, rebuilding a reference-resolution index a native object
  graph would never need, and walking every declaration (not just the ~10
  the library actually owns out of 118,801+ cursors) through per-node
  Python logic.
- **Selective traversal via libclang's native `Cursor` API achieves close
  to the theoretical maximum walk-time saving, not merely "some" saving.**
  A full recursive `get_children()` walk over every cursor took ~0.33s; a
  selective walk that stops recursing the moment a cursor's own
  `location.file` is a dependency header — visiting only the ~10 cursors
  that are actually the library's own — took ~0.004s: a **99% reduction**,
  because `get_children()` is called *by the traversal code itself*, so a
  skipped subtree's `Cursor` wrapper objects (and everything downstream
  that would process them) are never constructed at all. This is
  structurally different from — and dramatically better than — the
  streaming JSON pruner's own measured result (13–40% *slower*, ~1% memory
  win): `object_pairs_hook` is invoked by the C scanner unconditionally for
  every JSON object regardless of what the caller does with it, so it could
  never avoid the fixed per-object callback cost the JSON approach is stuck
  paying. `get_children()` has no such unconditional-invocation problem.
- **Net assessment, revised from "real but bounded" to "the majority of the
  current cost is plausibly eliminable":** libclang cannot avoid the ~0.40s
  Sema/instantiation floor, but that floor is a small slice (roughly a
  tenth, in this measurement) of what the current backend actually spends.
  The other ~90% — JSON text parsing, id-map construction, and
  per-declaration semantic processing for content the library doesn't own
  — is exactly the class of cost a native, selectively-traversed object
  graph structurally cannot incur for a skipped subtree. If
  `_ClangAstParser`'s real per-declaration semantic-construction cost
  (type resolution, demangling-adjacent lookups, initializer fingerprinting
  — not just the bare-walk numbers measured here, which only *count*
  cursors) scales similarly with declaration count, a libclang-based
  selective walk could plausibly eliminate close to that same ~90% for a
  header set where dependency content dominates — which is exactly the
  oneDAL-shaped, template-heavy case this whole investigation started from.
  This is a materially more optimistic assessment than this section
  previously offered, and the reason a spike is now better-justified, not
  less: the ceiling looks real and large, not speculative.

## Migration shape

**Not** a wholesale replacement of `dumper_clang.py`. Proposed as a third
mode alongside castxml and the current subprocess-clang backend — e.g.
`--ast-frontend clang-libclang` (or, if G4 lands first and already
establishes `clang.cindex` as an optional dependency, folding into
whatever frontend name that work settles on rather than inventing a second
one). Reasons to keep it opt-in/alternative rather than default:

- **This repository's own stated design values** (`AGENTS.md`: "Pure Python
  (3.10+)... except pyelftools/click" per the top-level description, and
  `dumper_clang.py`'s own precedent of avoiding an external demangler
  dependency specifically "so this works identically on Linux, macOS, and
  Windows and never shells out" — see the `_symbol_candidates`/`c++filt`
  discussion elsewhere in `AGENTS.md`'s "Known gaps"). `libclang` bindings
  are a **native** dependency (a compiled `libclang.so`/`.dylib`/`.dll`),
  a materially different commitment than shelling out to a `clang` binary
  the user already has on `PATH` — the current backend needs no
  Python-visible native extension at all.
- **Cross-platform installability is a real, not theoretical, risk.**
  Prebuilt `libclang` Python wheels exist (the `libclang` PyPI package
  ships prebuilt binaries for common platforms) but their *version* is
  independent of whatever `clang`/`gcc`/`castxml` toolchain a given host
  actually resolves via `--gcc-path`/`--compiler-option`. `castxml_policy.py`
  already establishes the precedent this migration would need to follow:
  an explicit supported-version *range* (there, `>=0.6.11,<0.8.0` for
  castxml itself) with a clear, actionable error when the resolved tool
  falls outside it — a libclang-bindings mode would need the equivalent
  policy for "the `libclang` Python package's bundled/linked LLVM version
  vs. whatever the user's system clang/gcc actually is," which is a
  materially harder version-matching problem than castxml's own (castxml
  bundles a fixed Clang and doesn't attempt to track an arbitrary host
  compiler's exact version at all).
- Because of both points, this should be opt-in and gracefully absent-safe
  — the same posture G4's own plan already commits to for `clang.cindex`
  ("The extractor is **opt-in** and degrades gracefully... preserving the
  'lightweight, pure-Python core' non-goal posture").

## What breaks / needs re-verification

`dumper_clang.py` is not a thin JSON-to-model mapper — it carries many
individually hard-won, previously-litigated extraction facts, each fixed
against specific real-world failures documented in `AGENTS.md`'s "Known
gaps" section. A libclang-cursor-based walk is a **structurally different**
traversal (native `Cursor`/`Type` objects with their own APIs, not a JSON
dict tree with clang's own particular key/shape conventions), so **none**
of the following can be assumed to transfer without independent
re-verification against the identical regression cases already in the test
suite:

- **`Param.is_va_list` detection** (`dumper_clang_qualifiers.
  _clang_param_is_va_list`) — deliberately scoped to one verified ABI
  (x86-64 System V) with a snapshot-level reliability flag
  (`AbiSnapshot.clang_va_list_facts_reliable`); a libclang walk would need
  to re-derive this from `Cursor`/`Type` APIs and re-verify the same
  target-scoping caveat (see `AGENTS.md`'s own note on this flag not yet
  recording *which* target it was verified against).
- **Forced-include (`-include`/`-imacros`/MSVC `/FI`) recognition and
  rendering** — currently reached through `header_utils.
  forced_include_operands`/`_forced_include_flags`, driven off raw
  `argv`/`CompileUnit` tokens, not the JSON AST itself; unaffected by this
  migration directly, but any future consolidation must not accidentally
  route it through the new backend's argv handling and reintroduce the
  double-inclusion hazard `AGENTS.md`'s L3→L2-fold entry already documents
  as a "rejected fix" for a related, adjacent primitive.
- **SYCL host-only mode** (`dumper._needs_sycl_host_only`, the
  multi-document host+device `-fsycl` decode path,
  `sycl_context.decode_and_select_frontend_context_from_path`) — this
  entire mechanism exists because `-ast-dump=json` under a bare `-fsycl`
  emits two concatenated JSON documents (device + host) on one stdout
  stream; libclang's `Index.parse()` has no equivalent multi-document
  output shape (a single `CXTranslationUnit` per `parse()` call), so this
  problem may not even exist under libclang — but the flag/heuristic logic
  that decides *whether* SYCL is in play (`_needs_sycl_host_only`'s own
  `-fsycl`/`-fno-sycl`/legacy-`dpcpp`-default handling) still needs to be
  re-expressed against libclang's own compiler-argument acceptance, and the
  DPC++/host-vs-device *selection* concept itself needs a libclang-native
  equivalent if one is still needed at all.
- **Standard-layout / trivially-copyable traits**
  (`_clang_record_type_traits`, `RecordType.is_standard_layout`/
  `is_trivially_copyable`) — currently read from specific JSON keys clang's
  dumper emits on a `CXXRecordDecl`'s `definitionData` object; libclang's
  `Cursor`/`Type` API surface for these traits (if any is exposed at all
  through `clang.cindex` rather than only through clang's internal C++ API)
  needs to be located and verified fact-for-fact against the existing
  `test_dumper_clang.py::test_parse_types_populates_standard_layout_and_trivially_copyable`
  regression case.
- **The C→C++ self-heal retry, `--lang c`/`--lang c++` explicit-vs-auto
  resolution, and the `lang_explicit` plumbing** (`_resolve_clang_langmode`,
  the whole `lang_explicit: bool` mechanism `AGENTS.md`'s own "Known gaps"
  documents at length, including the ambiguous-header regression it closed)
  — all of this is argv-level clang-driver behavior tied to the specific
  `clang -x c++` / `-std=` command line the current backend constructs;
  libclang's `Index.parse()` takes an argument list too, so the same
  *inputs* likely transfer, but the retry-on-failure control flow
  (catching a specific stderr signature and re-invoking) needs to be
  re-expressed against libclang's own diagnostic API
  (`TranslationUnit.diagnostics`), which has a different shape than
  parsing `subprocess.CompletedProcess.stderr` text.
- **Every AST-cache-key/memoization concern** (`dumper_ast_config._cache_key`,
  `dumper_cache.store_cached_ast`/`load_cached_ast`, the whole
  `_attach_header_graph` in-process memo reuse) is built around caching a
  **JSON document** (a `dict`, or a raw file for the disk cache). A
  libclang mode has no equivalent serializable artifact to cache the same
  way — either a new cache representation is needed (re-parsing from
  source each time, relying on libclang's own — if any — precompiled-header
  support instead) or the whole caching story for this mode needs its own
  design, not a reuse of the existing one.
- **Everything currently reached via the `AbicheckPrunedDependencyDecl`
  placeholder mechanism this session's companion change introduces**
  becomes moot under a genuine libclang selective walk (a skipped cursor
  needs no placeholder at all, unlike a JSON tree consumed by a downstream
  parser expecting a recognizable node shape) — but the *policy* the
  placeholder encodes (which declarations are safe to skip, and why
  type/enum/typedef declarations never are — see
  `abicheck/dumper_clang_streaming.py`'s docstring) transfers directly and
  should be reused rather than re-derived, since it is already correctness-
  reviewed against `dumper_scoping.py`'s exact contract.

## Recommendation

**Updated (2026-08-23): proceed to a real prototype, still short of a
committed migration.** Step (1) of the spike this section originally
proposed — measure whether `Index.parse()`'s own cost is traversal-
sensitive, using a real `clang.cindex` install — has now been run (see "The
crux" above). The answer is more favorable than this section originally
hedged: `Index.parse()`'s own Sema/instantiation floor (~0.40s) is real and
not avoidable by traversal, but it is a **small fraction** (roughly a
tenth, measured) of the current backend's actual cost, not the dominant
contributor this section previously guessed it probably was. The dominant
costs — JSON text parsing (~33%), the id-map construction the JSON
representation's string-keyed references force (~18%), and per-declaration
semantic processing walking every node rather than just the ~10 the library
owns — are all Python-side, all structurally tied to this backend's
JSON-intermediate architecture, and all things a native, selectively-walked
`Cursor` graph does not need to pay for a skipped subtree (confirmed: a
selective walk visiting only kept cursors measured ~99% faster than a full
walk of the same tree, a categorically better result than the streaming
JSON pruner's own measured *negative* result).

This changes the gate from "is there plausibly a win at all" (unresolved,
as this section originally left it) to "how much of the ~90% non-Sema cost
does a real, full `_ClangAstParser`-equivalent selective walk actually
recover, once semantic model-construction work — not just cursor counting —
is included." That is the one thing this session's spike did **not**
measure: every number above for "selective walk" only *counts* cursors: it
does not build `Function`/`RecordType`/`Param` model objects, resolve
types, or fingerprint initializers the way the real backend must. Given
`_ClangAstParser`'s own per-node work is substantial (6.4M `dict.get()`
calls attributed to it in this single profiled run), there's no guarantee
its *libclang-native* equivalent is cheap per node — only that it would run
on ~99% fewer nodes for a dependency-content-dominated header set.

Proposed prototype step, still bounded and not touching any shipped code
path, replacing the original step (1)/(2) pair now that (1) is answered:

1. Build a genuine (if minimal) libclang-based equivalent of
   `_ClangAstParser.parse_functions`/`parse_types` — enough to construct
   real `Function`/`RecordType` model objects (not just count cursors) for
   the library's own declarations, using `Cursor.type`/`Cursor.spelling`/
   `Cursor.mangled_name` and skipping `get_children()` recursion into any
   cursor whose `location.file` is a dependency header per the exact
   criteria `provenance.is_dependency_header` already implements (reuse,
   don't re-derive — see the "What breaks" list's closing bullet on the
   `AbicheckPrunedDependencyDecl` policy transferring directly).
2. Measure wall time and peak RSS for that real prototype end-to-end
   against the current subprocess-clang backend, on the same synthetic
   repro *and* on a header set more representative of the field report this
   investigation started from (heavier, more distinct per-header dependency
   content, less `#pragma once`-deduped sharing than this session's
   synthetic repro exhibits) — the synthetic repro's dependency content is
   fully shared and deduped across headers, which may understate how much
   semantic work a real multi-header case forces even under selective
   traversal.
3. Only if (1)/(2) confirm the per-node semantic-construction cost is
   genuinely cheap enough on libclang's native API (not just the cursor
   *count* reduction measured here) should this proceed past prototype
   stage — and even then, as a new opt-in `--ast-frontend` mode maintained
   alongside (not replacing) the current two backends, with the "What
   breaks" list above treated as a mandatory, fact-by-fact re-verification
   checklist (each item needs its own regression test proving parity with
   the existing backend, mirroring how
   `test_clang_and_castxml_snapshots_agree_on_public_surface` already holds
   castxml and the current clang backend to the same bar) rather than an
   assumed drop-in replacement.

The fallback this section originally offered — if the instantiation cost
turns out to dominate, recommend tighter header scoping instead of a new
backend — no longer applies as stated, since the instantiation cost was
measured and does **not** dominate. The live open question is now narrower
and mechanical (per-node semantic-construction cost on the native API),
not architectural (whether the whole avenue is worth pursuing at all).
