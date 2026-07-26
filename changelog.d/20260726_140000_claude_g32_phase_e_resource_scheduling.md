<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **Resource-aware per-TU scheduling and cache-key extension (ADR-050 D6,
  G32 Phase E)**: a manifest-driven `dump`/`compare` (ADR-050 D3, G32 Phase
  B) now runs its per-translation-unit castxml/clang invocations under a
  RAM-aware thread pool instead of a fully sequential loop, sized the same
  way `buildsource/source_replay.py`'s L4 source-replay pool already is
  (`ABICHECK_TU_JOBS`/`ABICHECK_TU_JOB_MEM_GIB`, mirroring `ABICHECK_L4_*`).
  The shared memory-probing/pool-sizing logic (host `/proc/meminfo`,
  cgroup v1/v2 headroom, the oversubscription ceiling) moved to a new leaf
  module, `abicheck/process_resources.py`, so the two pools share one
  implementation instead of two independently-maintained copies;
  `source_replay.py`'s own `ABICHECK_L4_*`-named wrapper functions are
  unchanged. A required TU's failure (including a killed/timed-out
  castxml/clang invocation) still propagates and fails the whole manifest
  dump; an optional TU's failure still degrades to a logged diagnostic —
  fragments are always collected in the manifest's own declared TU order,
  never completion order, since the merge step treats TU order as
  significant.
- The whole-snapshot dump cache (`snapshot_cache.py`/`service_dump_cache.py`)
  now covers a manifest-driven dump instead of unconditionally excluding it:
  `comparability.compute_extraction_contract`'s `scope_fingerprint` gained a
  `translation_units` field (each TU's name, its own ordered
  `includes`/`forced_includes` including `project_owned`, `required`, and
  `contributes_to_abi`) — previously `scope_fingerprint` only tracked a
  manifest's flat `roots`/public-header fields, so two manifests differing
  only in TU structure (a TU renamed, its includes reordered, or a flag
  flipped) could silently fingerprint identically, missing exactly the class
  of extraction-contract drift ADR-050 exists to catch. The cache key folds
  in this same structural identity alongside a content-hash walk over every
  file a manifest-driven dump could actually read, so a manifest edit
  invalidates the cache correctly instead of always forcing a live re-dump.

### Fixed

- A manifest-driven dump served from a cache *miss* inside
  `service_dump_cache.cached_run_dump` used to omit `dump_manifest` from its
  live `run_dump(...)` call entirely — dormant while `dump_manifest` forced
  every call uncacheable, but would have silently fallen back to a legacy
  single-TU dump on every cold cache the moment manifest caching was
  enabled. Fixed alongside enabling that caching, not left latent.
- `comparability.manifest_tu_scope_field` used to relativize every TU
  `forced_includes`/`includes` path against the manifest's `base_dir` with
  `os.path.relpath` after `Path.resolve()`. For a genuinely external
  absolute path (e.g. `/usr/include`), `.resolve()` collapses any `..`
  components and destroys the signal needed to tell "declared relative in
  YAML" from "declared absolute" — the relativized string ends up
  checkout-depth-dependent (`../../../usr/include` at one nesting depth vs.
  `../../../../usr/include` at another), so two manifests with an identical
  absolute include path spuriously mismatched on `scope_fingerprint`. Fixed
  by checking the *unresolved* path string against the *unresolved*
  `base_dir` string prefix before deciding whether to relativize, since
  `dump_manifest.py` always builds a relative-YAML path as a literal
  `base_dir / raw` join (a real string-prefix relationship that survives
  only before `.resolve()` runs).
- `dumper_manifest._run_tu_fragments`'s pooled branch used to observe
  `Future` results in submission order rather than completion order. A
  required TU submitted early but slow could delay observing a
  later-submitted required TU's fast failure, during which the pool kept
  starting newly-queued heavyweight AST work — so a manifest already known
  to fail could burn its full CPU/RAM budget for the slow TU's duration
  before cancellation ever ran. Fixed by switching to
  `concurrent.futures.as_completed` with index-addressed result storage, so
  a required failure is observed (and pending futures cancelled) as soon as
  it completes, while the final fragment list is still assembled in the
  manifest's declared TU order.
- `dumper_manifest._run_tu_fragments`'s pooled branch never re-established
  an active scan deadline (`deadline.deadline_scope`, e.g. a future
  `--budget`-aware caller) inside its `ThreadPoolExecutor` workers —
  `contextvars` don't cross that boundary, so a worker would silently see
  no deadline at all and each TU's clang/castxml invocation would fall back
  to its fixed default timeout regardless of the caller's budget. The same
  class of gap PR #591 already closed for `source_replay.py`'s L4 pool via
  `_deadline_bound_worker`; fixed the same way here by capturing
  `deadline.current_deadline_ts()` on the submitting thread and
  re-establishing it inside each worker with `deadline.with_deadline_ts`.
- `run_tu_loop`/`merge_tu_fragments` never actually consulted a translation
  unit's `contributes_to_abi` flag — a `contributes_to_abi: false` TU (a
  support-only TU that exists purely to satisfy other TUs' compiles, e.g. a
  private header) that parsed *successfully* still had its declarations
  merged into the snapshot exactly like any other TU. Parse-time's own
  `contributes_to_abi=True ⇒ required=True` invariant only guaranteed the
  *failure* half of this flag's contract (a dropped optional TU's failure
  can never hide a real removal); the success half — actually excluding a
  non-contributing TU's declarations from the merge — was never
  implemented, even though `MergedTuFragments`'s own docstring already
  described its input as "every *contributing* TU's `TuFragment`". Fixed by
  filtering `run_tu_loop`'s fragment list (matched by TU name, since a
  dropped optional-TU failure leaves `fragments` not index-aligned with
  `tus`) to only contributing TUs before merging (Codex review).
- `dumper_manifest._run_tu_fragments` wrapped its pool in
  `with ThreadPoolExecutor(...) as pool:` — cancelling not-yet-started
  futures on a required failure happened promptly, but the `with` block's
  own `__exit__` still called the executor's default `shutdown(wait=True)`
  as the exception propagated out of it, blocking the calling thread until
  every already-running (uncancellable) sibling finished anyway. So a
  required TU's failure sat behind a still-running, merely-slow sibling's
  full duration before the caller ever saw it — silently re-imposing the
  exact wait the completion-order/cancellation fix above was meant to avoid.
  Fixed by managing the executor's lifetime manually instead of via `with`,
  calling `shutdown(wait=False)` on a required failure right after
  cancelling pending futures, so the diagnostic surfaces immediately while
  any already-running sibling is left to finish in the background (Codex
  review, PR #636 follow-up).
