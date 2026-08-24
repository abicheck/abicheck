---
doc_type: contributor
level: advanced
lifecycle: active
---

# G38 — Bundle facts model, persisted multi-library graphs, and multibuild-variant comparability

**Origin:** External review of ADR-023 (bundle-aware multi-binary analysis)
against a real oneDAL checkout (`libonedal_core.so` +
`libonedal_thread.so`/`libonedal_sequential.so`/`libonedal_dpc.so`/
`libonedal_parameters*.so` behind one shared `include/oneapi/dal/` header
tree). The review reproduced a real bundle-integrity break (an internal
`_daal_*`-style C symbol renamed in the thread provider, consumer left
unchanged) and confirmed `compare`'s bundle layer (`abicheck/bundle.py`)
catches it correctly from live `.so` files — but found the layer cannot
answer the same question from a **stored dump**, cannot express **build
variants** (CPU vs. `ONEDAL_DATA_PARALLEL`) without silently unioning them,
and treats an internal C boundary's binary-name match as if it proved
signature compatibility. See ADR-023's own "Amendment" block at the top of
that file for the earlier (2026-07-29), narrower correction this plan
builds on.

**ADR:** No new ADR is proposed. This plan is scoped as an amendment/
extension to [ADR-023](../adr/023-bundle-aware-multi-binary-analysis.md)
(bundle layer) and [ADR-050](../adr/050-comparability-contract-and-multi-tu-manifest.md)
(comparability contract, which already owns the "two snapshots must agree
on their extraction recipe before being compared" invariant this plan
extends to bundle-level and multibuild-level comparisons). If Phase 2
below (the persisted `BundleFacts` schema) grows real design disagreement
during implementation, split it into its own ADR at that point — this plan
does not pre-empt that.

**Type:** Initiative plan (cross-cutting; touches `abicheck/bundle.py`,
`abicheck/bundle_models.py`, `abicheck/bundle_manifest.py`,
`abicheck/serialization.py`, `abicheck/model.py`, `abicheck/comparability.py`,
`abicheck/environment_matrix.py`, `abicheck/diff_cxx_rules.py`,
`abicheck/checker_policy.py`, `abicheck/change_registry.py`,
`abicheck/cli_scan_baseline.py`, `abicheck/reporter.py`).

**Effort:** XL (phased — see "Phases" below). **Risk:** medium — Phase 1
(taxonomy) and Phase 4 (finding-severity split) are additive and low-risk;
Phase 2 (persisted `BundleFacts`) is a real schema addition with its own
version bump and round-trip contract; Phase 3 (multibuild pairing) changes
default behavior for any caller that today silently unions variants (none
do today — see "Why this is additive" below — but a future multibuild
consumer must not reintroduce a union by default).

---

## Problem

ADR-023 shipped a real, working bundle layer: `compare_bundle()` builds a
`ResolutionGraph` from live `.so` files (`ProviderEntry`/`ConsumerEntry`
per symbol, DT_NEEDED edges, `gnu.version_r`/`gnu.version_d`) and detects
five real cross-DSO break patterns (intra-bundle removed symbol, signature
drift across a C boundary, cross-DSO type drift, template-instantiation
manifest drift, provider migration). Reproducing this end-to-end against a
real oneDAL build (a modified `libonedal_thread.so` mutating one exported
C symbol name, all other siblings byte-identical) confirms the layer
produces the correct causal chain: provider delta, consumer delta (none),
bundle-level `bundle_intra_dep_removed`. That part of ADR-023 works as
designed.

Four gaps remain, each independently reproducible and each with a distinct
root cause — not one gap wearing four names:

1. **`compare_bundle()` only ever reopens live `.so` files.** `BundleSnapshot`
   is built directly from filesystem paths inside `abicheck/bundle.py`; there
   is no serialized `BundleFacts` object a `dump`-produced snapshot set
   carries, and no `compare --against <stored bundle>` path exists at all.
   `analyze(extract(live))` and `analyze(load(dump))` are not the same
   function today because the second one doesn't exist. This means a stored
   baseline — the normal `scan --against`/CI workflow for every other
   surface this tool supports — cannot get a bundle-level verdict; only a
   live-directory-vs-live-directory `compare` invocation can.
2. **No build-variant (multibuild) model for the bundle layer.** oneDAL
   ships CPU-only and `ONEDAL_DATA_PARALLEL` (SYCL) builds from the same
   source tree. `environment_matrix.py` already models a *build matrix*
   for `compare --env-matrix` (SYCL/CUDA constraints, runtime floors) at the
   per-library level, but nothing pairs two *bundle* snapshots per variant
   and rejects a mismatched pairing — a naive "run the bundle layer once per
   available build" caller would have to invent variant fingerprinting and
   pairing from scratch, and get it wrong the same way a union would (see
   "Why a union is wrong" below).
3. **A C-linkage symbol match is treated as proof of signature
   compatibility, not just proof of binary-name compatibility.**
   `bundle_intra_dep_signature_changed` is keyed off a *provider's own*
   per-library `func_params_changed`/`func_return_changed` finding — which
   already requires DWARF/header evidence for that provider. That part is
   sound. What's missing is the negative case: when neither side has that
   evidence (a stripped provider, or a provider only ever dumped at L0), the
   bundle layer has no way to say "this consumer's import still resolves by
   name, but nothing establishes the signature agrees" — it silently
   reports nothing, which reads as "compatible."
4. **The rationale for separate public-ABI and bundle-integrity findings is
   undocumented.** `bundle_library_removed`/
   `bundle_intra_dep_removed` etc. already exist as their own kinds (not
   reusing `BREAKING_KINDS`' public-surface language) — this part of
   ADR-023's design is already correct. What's missing is the reporter/
   policy-facing documentation of *why* an internal, non-public symbol can
   still be `BREAKING` at the bundle level, which is a recurring point of
   confusion when a `--policy` profile scoped to "public API only" doesn't
   suppress a `bundle_intra_dep_removed` finding on an internal symbol (by
   design — see Phase 1).

### Why a union is wrong (multibuild)

If a declaration/export is present in the DPC build and accidentally
missing from the CPU build, unioning the two variants' facts before
diffing reports "present" in both old and new — hiding a real, CPU-only-build
regression. The correct model pairs each variant independently
(`CPU old ↔ CPU new`, `DPC old ↔ DPC new`, ...) and aggregates only after
each pair has its own verdict. A variant present in old with no matching
variant in new is `NOT_COMPARABLE`/a build-coverage regression for that
variant specifically — never silently dropped or paired with the nearest
available build. `comparability.py`'s existing `check_contracts_comparable`
already enforces exactly this discipline for a single old/new snapshot pair
(refusing to compare two snapshots whose `scope_fingerprint`/
`profile_fingerprint` disagree); this plan extends the same discipline one
level up, to a *set* of variant-tagged bundle snapshots, rather than
inventing a second comparability mechanism.

### Why this is additive, not a behavior change

No shipped caller invokes the bundle layer once per build variant today —
`compare`/`compare-release` runs it exactly once, against whatever one
directory pair was given. So there is no existing "silent union" to fix;
the risk this plan calls out is prospective (a naive multibuild extension
would reach for a union), not a regression already shipped. Phases 1-4 add
new fields/kinds/modules; none change the meaning of an existing
`BundleFinding`, `ChangeKind`, or exit code for a single-variant
`compare`/`compare-release` invocation.

---

## Goal & acceptance criteria

Mirrors the acceptance bar the originating review proposed, restated as
this plan's phases so each criterion has an owning phase rather than
floating as an unattached checklist:

1. Removing a sibling-consumed internal C symbol produces one causal
   bundle-integrity finding naming consumer, import, old provider, and
   failed new resolution. **Already true today** (verified against a real
   oneDAL reproduction) — not a gap this plan closes, recorded here only so
   the acceptance list is complete and the "already works" part isn't lost
   in a plan about what doesn't.
2. The same finding reproduces from a **stored bundle dump**, not only from
   two live directories. — Phase 2.
3. An unused private export removal does not produce the same severity as
   (1). **Already true** — `bundle_library_removed`/
   `bundle_intra_dep_removed` only fire when a sibling actually consumes the
   symbol; an unconsumed export removal falls through to the existing
   per-library `func_removed`, whose severity is governed by the ordinary
   public-surface/suppression rules, unaffected by this plan.
4. CPU and DPC variants are evaluated independently, with a missing
   matching variant reported as its own finding rather than silently
   dropped or unioned. — Phase 3.
5. A same-name C symbol with no signature evidence on either side is
   reported as **binary-name-compatible, signature-unverified** — a
   distinct finding from a verified-compatible signature match. — Phase 4.
6. Live-directory and stored-dump bundle comparisons produce identical
   findings and evidence for the same underlying facts (the same parity
   invariant ADR-050/G32 already hold single-snapshot comparisons to). —
   Phase 2's acceptance test.

---

## Design

### Phase 1 — Finding-taxonomy documentation (no code change)

Add a short section to `docs/reference/change-kinds.md` (the curated,
narrative change-kind guide — this topic's existing fact owner per
`docs/_meta/topics.yaml`'s ownership split) explaining, in plain language,
that a `bundle_*` kind answers "does the shipped bundle still work
end-to-end" and is deliberately **not** filtered by a public-surface-only
policy scope the way `BREAKING_KINDS`/`API_BREAK_KINDS` are — an internal,
non-public symbol can still be `bundle_intra_dep_removed` because a sibling
DSO's `dlopen` genuinely fails. This is a documentation-only phase (no
`ChangeKind`/registry change) since the underlying behavior is already
correct; it exists purely to close the confusion the review's item 4
identified. Lowest risk, do first.

### Phase 2 — Persisted `BundleFacts` and `compare --against <bundle dump>`

**Implementation status (2026-08-23): the model, the mandatory parity test,
and the producer (`--bundle-facts-out`) are shipped; the CLI consumer half
is deliberately deferred.** Two real deviations from this section's original
design, both discovered during implementation rather than planned up front:

1. **No `BundleArtifactFacts`/persisted `ResolutionGraph`.** The sketch
   below assumed the resolution graph needed its own serialized form. It
   doesn't: `abicheck/bundle.py` already has
   `build_bundle_snapshot_from_metadata()` — a pre-existing primitive (built
   for a different, still-unshipped snapshot-first product-baseline use
   case) that reconstructs a fully-functional `BundleSnapshot` (cross-DSO
   `DT_NEEDED`/version-table resolution included) from bare `ElfMetadata`
   alone, with no binaries read. Since `AbiSnapshot.elf` already *is* that
   `ElfMetadata` for every ELF `dump`, `BundleFacts.per_library_snapshots`
   alone is sufficient to reconstruct everything `BundleArtifactFacts`/
   `ResolutionGraph` would have stored — persisting them separately would
   only add a second, redundant representation that could drift from
   `_compute_resolution_graph()`'s real behavior. Implemented in
   `abicheck/bundle_facts.py`, not `abicheck/bundle_models.py` (that file
   stays a leaf with respect to `abicheck.bundle`; `bundle_facts.py` is
   its own leaf-with-respect-to-`bundle.py` module, importing it only
   lazily inside function bodies to avoid a real
   `bundle_facts <-> serialization` import cycle the first draft of this
   module hit — see that module's own comment for why the `to_dict`/
   `from_dict` pair had to move to `serialization.py` instead of living
   next to the dataclass).
2. **CLI consumer wiring is not attempted.** `compare_release_cmd`'s
   directory/package fan-out (`_prepare_compare_release_inputs`,
   `_compare_release_libraries`) is built entirely around resolving *live*
   binaries on both sides for the per-library diff pass — substituting a
   `BundleFacts` file for the old side would mean a second, parallel
   per-library comparison loop (`service.compare_snapshots()` against each
   stored `AbiSnapshot` instead of `service.run_compare()` against a live
   path), which is a genuine, separate feature with its own option surface
   (most of `compare`'s ~40 release-fan-out flags — headers, debug-info,
   PDB, jobs — lose their old-side meaning once the old side is already a
   resolved snapshot). This section's own text already anticipated
   deferring this exact decision ("this plan does not re-litigate the
   ongoing CLI-cleanup-phase-two convergence, it plugs into whichever entry
   point that work has converged on"); building it reactively now, without
   that convergence, risked exactly the kind of drive-by CLI-dispatch
   change this codebase's own "known gaps over risky reactive patches"
   convention exists to avoid. `abicheck.bundle_facts.
   compare_bundle_from_facts()` is fully implemented, tested against the
   mandatory dump/live parity invariant, and documented as a Python API in
   [Multi-Binary Releases](../../use/multi-binary.md#comparing-against-a-stored-bundle-baseline-g38-phase-2)
   — only the CLI surface to feed it a stored-facts old side is not yet
   wired.
3. **Three review-driven fixes closed after initial implementation
   (Codex review, same day).** `write_bundle_facts_out()`'s producer only
   ever captured `diff_pairs` (matched libraries), silently omitting a
   library removed in the new release from the persisted baseline — it
   now also captures every unmatched old library directly via
   `parse_elf_metadata()`, matching what a live `build_bundle_snapshot()`
   does for the identical case. `capture_bundle_facts()` gained a
   `library_paths` parameter that probes and persists real filesystem
   soname aliases (symlink target basenames, hard-linked siblings) at
   capture time, and `build_bundle_snapshot_from_metadata()`/
   `_compute_resolution_graph()` gained a matching `extra_aliases`
   parameter to replay them at reconstruction time with no filesystem
   access — closing a gap where a provider without a usable `DT_SONAME`
   could resolve differently from a stored baseline than from a live
   comparison. `bundle_facts_from_dict()` now rejects a `schema_version`
   newer than this reader supports, mirroring `snapshot_from_dict()`'s
   existing hard rejection for `AbiSnapshot`. The alias-probing helpers
   live in the pre-existing `abicheck.bundle_soname` leaf module (not
   `bundle.py` itself, which was already near the AI-readiness file-size
   hard cap).

The rest of this section is kept as originally written (this plan's own
amendment convention appends corrections rather than retconning the
original text); read `BundleArtifactFacts`/`resolution_graph` below as the
originally-proposed shape, superseded by the simpler, already-shipped one
described above.

**New model, additive to `abicheck/bundle_models.py`:**

```python
@dataclass
class BundleFacts:
    """Serializable projection of everything compare_bundle() derives from
    live .so files, decoupled from filesystem paths — the bundle-level
    counterpart to AbiSnapshot for a single library."""
    schema_version: int
    variant_fingerprint: str  # see Phase 3 — always present, "default" for a non-multibuild bundle
    artifacts: list[BundleArtifactFacts]  # per-DSO: soname, aliases, build-id/hash, path label (ADR-032 D7 redaction rules apply)
    resolution_graph: ResolutionGraph  # already exists — just needs schema_version + serialization
    per_library_snapshots: dict[str, AbiSnapshot]  # one per bundle member — see below for why this is mandatory, not optional
    manifest: InstantiationManifest | None
```

`BundleArtifactFacts` carries exactly what `ResolutionGraph`'s
`ProviderEntry`/`ConsumerEntry` already compute per-library (exported
symbols, undefined/imported symbols with `gnu.version_r`, DT_NEEDED,
RPATH/RUNPATH) — this phase does not add new *extraction*, it adds
*serialization* of facts `bundle.py` already derives in memory and
discards after `compare_bundle()` returns.

**`per_library_snapshots` is required, not a nice-to-have, and its absence
from an earlier draft of this schema was a real gap in the parity
invariant itself** (caught in review — see the PR that introduced this
plan). `compare_bundle()`'s cross-DSO findings are not derived from the
resolution graph alone: `bundle_intra_dep_signature_changed`,
`bundle_intra_type_changed`, and `bundle_provider_changed` are each keyed
off a *per-library* `DiffResult` (`func_params_changed`/
`func_return_changed`/`type_*_changed`/`func_removed`+`func_added` pairs —
see ADR-023's "Per-library diff is unchanged" section, steps 3-5). A
`BundleFacts` carrying only artifact metadata and the resolution graph has
nowhere for `compare_bundle_from_facts()` to get those per-library diffs
from when the *old* side is a stored dump rather than a live directory —
it would have to re-derive them from `AbiSnapshot`s it doesn't have, which
defeats the entire point of Phase 2. Each entry in
`per_library_snapshots` is exactly the `AbiSnapshot` `dump` already
produces for that library today (no new extraction), keyed by the same
library identity `ResolutionGraph`'s provider/consumer entries use, so
`compare_bundle_from_facts()` can run the existing per-library diff between
`old_facts.per_library_snapshots[lib]` and a freshly-dumped new-side
snapshot before applying the same cross-DSO rules `compare_bundle()`
already implements — one shared per-library-diff-then-bundle-rules code
path for both entry points, not two.

**Wiring:**

- `serialization.py` gains `save_bundle_facts`/`load_bundle_facts`,
  mirroring `save_snapshot`/`load_snapshot`'s existing envelope
  (`snapshot_io.py`'s plain/gzip/zstd detection, atomic writes) rather than
  inventing a second I/O layer.
- **There is no existing directory-level dump mechanism to attach a
  `BundleFacts` producer to — a real gap in an earlier draft of this
  plan, caught in review.** `dump` (`cli.py`'s `dump_cmd`) produces exactly
  one snapshot from one binary; its `--dump-manifest` option is an *input*
  (a YAML describing translation units to merge into that one snapshot),
  not a directory fan-out or an output mechanism. The only existing
  directory/package fan-out lives in `compare`'s release path
  (`cli_compare_release.py`), and it does not persist each library's
  generated snapshot today — it discards them after diffing. Phase 2
  therefore needs a genuine new producer, not a flag bolted onto something
  that already walks a directory in the wrong shape. **This is deliberately
  scoped as a new flag on the existing `compare` release fan-out, not a new
  root command** — a bare `abicheck dump-bundle` would need to separately
  clear the root's own admission bar (root `AGENTS.md`'s "Adding a new
  top-level command" criteria; a directory-of-libraries operand a user
  already thinks of as a `compare-release`-shaped input, with a real usage
  scenario beyond one PR, is exactly the class of thing that bar exists to
  keep off the root surface unless it independently earns a place there).
  `compare`'s release fan-out (`cli_compare_release.py`) already walks
  every library in a directory and already produces each side's
  `AbiSnapshot` in memory before diffing and discarding it — an opt-in
  `--bundle-facts-out <path>` flag on that existing command persists what
  it already computes (`per_library_snapshots` plus the resolution graph)
  into one `BundleFacts` file for the *old*-side directory, rather than
  inventing a second directory-walking entry point. `compare release-1.0/
  release-2.0/ --bundle-facts-out old.bundlefacts` is therefore both the
  producer and, in the same invocation, a normal live-vs-live comparison —
  the flag is additive output, not a new mode.
- `compare --against <old-dir-or-bundle-facts> <new-dir>` (or the
  `compare-release`-shaped equivalent, per whatever `cli_compare_release.py`
  looks like when this lands — this plan does not re-litigate the ongoing
  CLI-cleanup-phase-two convergence, it plugs into whichever entry point
  that work has converged on by the time this phase starts) accepts either
  a live directory (today's behavior, unchanged) or a `BundleFacts` file for
  the *old* side. The *new* side may still be live (the common `scan`-style
  workflow: compare a stored baseline bundle against what's on disk today).
- `checker_policy`/`comparability.py`: a `BundleFacts.variant_fingerprint`
  mismatch between old and new is refused the same way a single-snapshot
  `scope_fingerprint` mismatch already is (`ScopeMismatchError`'s bundle-level
  sibling), rather than silently comparing incompatible bundles.

**Acceptance test (the mandatory parity invariant, restated executably):**

```text
compare_bundle(old_dir, new_dir).bundle_findings
==
compare_bundle_from_facts(load_bundle_facts(dump_bundle(old_dir)), new_dir).bundle_findings
```
(`dump_bundle` names the new directory-level producer above, not the
single-artifact `dump` command — see the "Wiring" note on why the latter
cannot produce this.)
including evidence and `affected_libraries`, not just `ChangeKind` values —
mirroring the existing single-snapshot dump/live parity tests this repo
already runs for `dump`/`scan --against` (see `tests/test_dump_scan_l3_
comparability.py` for the established pattern this test follows).

**What is deliberately NOT attempted in Phase 2:** a fully lossless,
extractor-agnostic bundle archive format (the review's §9 `bundle-dump-
vNext.tar.zst` sketch — content-addressed shared headers, per-artifact
`.json.zst`, optional raw-binary retention). That is a real, separate
storage-architecture project on the scale of ADR-059 (snapshot compression)
or the `docs/contribute/plans/g32-comparability-contract-and-multi-tu-
manifest.md` multi-TU manifest work, not a sub-step of making the bundle
layer stored-data-capable. `BundleFacts` above is scoped to "enough to
reproduce `compare_bundle()`'s existing analysis without live binaries" —
not to "a general-purpose reanalysis substrate for extractors that don't
exist yet." If a future need for the latter materializes, it gets its own
plan, informed by whatever `BundleFacts` looked like in production by then.

### Phase 3 — Multibuild variant pairing

**Implementation status (2026-08-23): the pairing primitive, the
`ChangeKind`, and its finding-construction helper are shipped; the CLI/config
surface that discovers real per-variant `BundleFacts` and feeds them to
`pair_variants` is deliberately deferred, the same posture Phase 2's own
implementation-status note already took for its CLI consumer half.**

- `abicheck/bundle_multibuild.py` implements `variant_fingerprint`,
  `VariantOutcome`, `VariantComparison`, `pair_variants`, and
  `coverage_regression_findings` per this section's design below, with two
  real deviations. First: `variant_fingerprint` takes explicit, named
  coordinates (`target_triple`, `compiler_family`, `feature_toggles`)
  rather than a raw `BuildEvidence | None` / `EnvironmentMatrix | None`
  pair. Telling a genuine logical-identity feature toggle
  (`ONEDAL_DATA_PARALLEL`) apart from build state that legitimately drifts
  release to release (an ABI-relevant `-D` define, a raised `-std=`) cannot
  be done reliably from raw build evidence alone — both can appear as an
  indistinguishable `BuildOption`/`CompileUnit` entry — so that judgement
  call is pushed to the caller instead of embedded as a heuristic parse,
  which would risk silently reintroducing the union failure mode from
  either direction (see the function's own docstring for the full
  reasoning). Second: `compiler_version` is **not** a parameter at all,
  unlike the design sketch below — a real gap in that sketch, caught in
  review (Codex): a routine toolchain upgrade between releases (GCC 13 ->
  14 building the identical variant) is the same class of legitimately-
  drifting build state as an ABI-relevant define or a raised `-std=`, not
  variant identity, so fingerprinting it would make `pair_variants` read
  an ordinary compiler bump as two different, unmatched variants —
  `OLD_ONLY` + `NEW_ONLY` — silently skipping every real per-library
  comparison for that variant and replacing it with a spurious
  `bundle_variant_coverage_regressed` finding. `target_triple`/
  `compiler_family` stay in the fingerprint (a target or compiler-family
  switch is a real, deliberate distribution-channel decision, not routine
  drift the way a version bump is). The function's own contract (which
  coordinates are fingerprinted, which are deliberately excluded and why)
  is otherwise exactly as designed below.
- `ChangeKind.BUNDLE_VARIANT_COVERAGE_REGRESSED` /
  `"bundle_variant_coverage_regressed"` is registered in `checker_policy.py`
  / `change_registry_buildsource.py` (not `change_registry.py`, which is at
  the AI-readiness 2000-line hard cap — default verdict `RISK`, per this
  section's design), classified in `tests/canonical_identity_contract.py`'s
  `UNVERIFIED` bucket (matching every other pre-existing `bundle_*` kind —
  none of them have had their construction call sites individually verified
  against the `TYPE_BEARING`/`VALUE_INSENSITIVE` criteria yet), and covered
  by `tests/test_bundle_multibuild.py` (determinism/sensitivity cases, the
  never-union Hypothesis property, the missing-variant case, and
  `coverage_regression_findings`'s own finding construction).
- **Not shipped**: `pair_variants`' *new-only* coverage-expansion outcome is
  modelled (`VariantOutcome.NEW_ONLY`) but, per this section's own design,
  deliberately never produces a `ChangeKind` — nothing renders it into the
  reporter's `bundle.json`/`bundle.md` yet (that's this phase's own
  "Reporter" row in "Files & surfaces", still open). Also not shipped: any
  CLI/config surface that discovers a release's real build variants,
  extracts `BundleFacts` per variant, and calls `pair_variants` — that needs
  the same real, separate design Phase 2's CLI consumer half deferred for
  (most of `compare`'s release-fan-out option surface loses its per-variant
  meaning once there is more than one old/new directory pair per release),
  and `comparability.py`'s bundle-level fingerprint-mismatch refusal is
  likewise not yet wired to this module's `variant_fingerprint`.

**New module, `abicheck/bundle_multibuild.py`:**

**Kept as originally written, per this plan's own amendment convention (see
Phase 2's identical note above)** — the shipped signature is the explicit-
coordinate one this section's own "Implementation status" note above
describes (`target_triple`/`compiler_family`/`feature_toggles` —
deliberately no `compiler_version`, see that note), not the
`(evidence, env)` sketch below.

```python
def variant_fingerprint(evidence: BuildEvidence | None, env: EnvironmentMatrix | None) -> str:
    """Stable fingerprint over LOGICAL VARIANT IDENTITY only — which
    distinct build configuration this is (target triple, feature toggles
    such as ONEDAL_DATA_PARALLEL that mean "this is the DPC build, not the
    CPU build") — never over versioned build STATE that legitimately
    drifts release to release. Deliberately EXCLUDES four things:

    1. Artifact membership (which libraries actually shipped) — see below.
    2. EnvironmentMatrix.runtime_floors (a deployment/comparison-policy
       input used to classify symbol-version and deployment findings).
    3. Within SyclConstraints/CudaConstraints, the declared-deployment-
       policy fields those dataclasses also carry
       (SyclConstraints.min_pi_version, CudaConstraints.driver_range).
    4. C/C++ standard and ABI-affecting flags/defines — a THIRD, and the
       most consequential, instance of the same "policy/state leaking into
       an identity key" class of bug the two exclusions above already fix,
       caught in yet another review round: an earlier draft of this
       docstring listed these as fingerprinted fields. That is wrong for a
       different reason than (2)/(3) — these genuinely are build facts,
       not policy — but `comparability.py`'s own machinery
       (`_unexplained_profile_fields`, `language_standard_probe_upgrade_
       corroborated`, `language_standard_content_divergence_corroborated`)
       exists specifically to let a *corroborated* language-standard or
       macro-defines change between old and new be compared and classified
       (`cxx_standard_floor_raised`, `abi_relevant_build_flag_changed`),
       not to refuse the comparison. Fingerprinting these fields for
       *pairing* would make `pair_variants` reject exactly the same-variant,
       drifted-build-state comparisons the existing engine is already
       designed to run — the same CPU variant that raised its `-std=`
       between releases would read as two different, unmatched variants,
       replacing a real, classified finding with a generic coverage
       regression. `variant_fingerprint` therefore reads only what
       distinguishes one *logical* variant from another (which feature
       build this is), and leaves everything about how that variant was
       compiled — which can and does change release to release — to the
       ordinary per-library comparability/diff layers to classify once
       `pair_variants` has matched the pair."""

def pair_variants(
    old: dict[str, BundleFacts], new: dict[str, BundleFacts]
) -> list[VariantComparison]:
    """Pairs by fingerprint equality (not nearest-match). A variant present
    on both sides is diffed normally. A variant present only in OLD is a
    real coverage regression (`bundle_variant_coverage_regressed`) — the
    release stopped building a variant a consumer may still depend on. A
    variant present only in NEW is coverage EXPANSION, not regression — a
    real gap in an earlier draft of this function, caught in review: an
    added DPC build on a previously CPU-only release is new coverage, not
    a build that "went missing," and treating it identically would emit a
    RISK-classified regression finding for what is actually good news. New-
    only variants get their own, differently-named outcome (no regression
    finding; recorded as an addition in the `VariantComparison` list so the
    reporter can still show "3 variants compared, 1 newly added," but nothing
    here reads as a regression). Neither shape is ever dropped or paired
    with a mismatched variant."""
```

**Artifact membership must not be part of the fingerprint** (a real design
error in an earlier draft of this plan, caught in review). If the set of
libraries a build produces were folded into `variant_fingerprint`, an
ordinary library addition/removal/rename between old and new would change
the CPU (or DPC) variant's own identity, so `pair_variants` would see two
*different* fingerprints and treat them as two non-comparable singletons —
exactly the failure mode Phase 3 exists to prevent, just reached from a
different direction than a union. That would silently replace
`bundle_library_removed`/`bundle_library_added` and any real cross-library
regression inside that variant with a generic coverage-regression finding,
for the ordinary case of a library being added or dropped from a release.
`variant_fingerprint` is scoped to stable build-axis coordinates only (what
*compiles* the variant); which libraries that build actually produced is
versioned output to be diffed *inside* the matched pair (that's exactly
what `bundle_library_removed`/`bundle_library_added` already do), never a
component of whether two variants are "the same variant."

`pair_variants` is the enforcement point for "never union" — it has no
code path that merges two variants' facts before diffing; it only ever
returns one-to-one pairs or explicit non-comparable singletons. Each pair's
own `compare_bundle()` call and finding set stay completely independent;
aggregation (a release-wide worst-of verdict across variants) is a
reduction over already-computed per-variant results, done by the caller
(the same "worst-of, computed by the caller, never inside the comparison
primitive" shape `compute_verdict`/`compute_exit_code` already use for
per-library verdicts).

New `ChangeKind`: `bundle_variant_coverage_regressed` (category: Bundle /
structural, default verdict: `RISK`, not `BREAKING` — a missing variant is
a build-coverage gap the user needs to see, not by itself proof the
missing variant's ABI broke, since it may simply have been dropped from
the release intentionally; a real per-variant ABI break inside a matched
pair still uses the existing `bundle_*` kinds unchanged). Fires only for an
**old-only** variant (per `pair_variants`' asymmetric handling above) — a
**new-only** variant is coverage expansion, not a `ChangeKind` at all;
it is recorded only in the `VariantComparison` list the reporter renders,
never emitted as a finding.

### Phase 4 — C-boundary signature-evidence gate

**Implementation status (2026-08-23): the detector, the `ChangeKind`, and
its registry/test-completeness wiring are shipped, as a standalone
companion module (the same posture Phase 3's `bundle_multibuild.py` took),
with two real deviations from the design below.**

- `abicheck/bundle_signature_evidence.py` implements
  `find_unverified_signature_findings(old, new, per_library_results,
  old_snapshots, new_snapshots) -> list[BundleFinding]` (signature widened
  2026-08-24, see the "Update" note below) as a leaf module —
  it is **not** wired into `bundle.compare_bundle()` itself, because
  `abicheck/bundle.py` is exactly at the AI-readiness 2000-line hard cap
  (confirmed via `wc -l`) and cannot accept new code without an offsetting
  removal. A caller invokes this function separately, alongside
  `compare_bundle()`, and merges the two `list[BundleFinding]` results —
  the same standalone-companion shape `bundle_multibuild.py` established
  for Phase 3's `coverage_regression_findings`. Deliberately does not
  import `abicheck.bundle` (a small, 3-member
  `_CONFIRMED_SIGNATURE_CHANGE_KINDS` frozenset is duplicated locally
  rather than imported, to stay a strict leaf module `bundle.py` — or a
  future caller — imports, never the reverse).
- **Deviation from the design text below**: the evidence check is scoped to
  the **provider's own snapshot only**, not "the consumer, where
  applicable" as this section's design sketch says. A consumer has no
  DWARF/header declaration of its own for a symbol it only imports (calls)
  rather than defines — its own `AbiSnapshot.function_map`/`variable_map`
  has no entry for an externally-defined symbol at all, so "the consumer's
  evidence for this symbol" is not a fact that exists to check. The design
  sketch's parenthetical was read as anticipating a shape this codebase's
  actual per-library `AbiSnapshot` construction doesn't produce, rather than
  as a requirement to build a new evidence source; the check below
  implements the well-founded half (the provider's own declaration
  evidence) and treats the consumer-side clause as inapplicable rather than
  approximated.
- Registered as `ChangeKind.BUNDLE_INTRA_DEP_SIGNATURE_UNVERIFIED` /
  `"bundle_intra_dep_signature_unverified"` in `checker_policy.py` /
  `change_registry_buildsource.py` (not `change_registry.py`, at the same
  2000-line cap Phase 3's kind avoided — default verdict `RISK`, per this
  section's design), classified in `tests/canonical_identity_contract.py`'s
  `UNVERIFIED` bucket (matching every other `bundle_*` kind), tiered `L0`
  in `scripts/evidence_tiers.py` (the detectable-at signal is the same
  C-linkage resolution match every other `bundle_*` kind uses — the
  finding's own *content* records that deeper evidence was unavailable,
  which is a fact about the finding, not about the minimum tier needed to
  produce it), and covered by `tests/test_bundle_signature_evidence.py`
  (both-sides-ELF-only fires; sufficient-evidence-both-sides doesn't;
  one-side-insufficient still fires; no consumer / symbol absent from old
  (addition) / snapshot missing skip; a confirmed diff-level signature
  change takes precedence over this kind firing on the same symbol;
  variable, not just function, symbols; a single unresolved *parameter*
  type is sufficient insufficiency even with a known return type; one
  finding per consumer library; no crash on an entirely empty snapshot).

**Update (2026-08-24): wired into the real `compare --release` CLI path —
superseding the "Not shipped: reporter wiring / any real caller" line this
note replaces.** `find_unverified_signature_findings` previously had no
caller outside its own test module — the standalone-companion posture
above described where the detector *lived*, not that anything invoked it.
`_run_bundle_analysis`/`_collect_bundle_result` (`cli_compare_release_
helpers.py`, the real `compare_bundle()` call site for `compare --release`,
bundle analysis on by default) now accept `old_snapshots`/`new_snapshots:
dict[str, AbiSnapshot]` and, when both are non-empty, call the detector and
fold its output into the same `bundle_findings` list `compare_bundle()`
already populates — the pre-existing `BundleFinding.to_change()`/
`render_bundle_findings_markdown()` rendering already handles it
generically, no reporter changes needed (an earlier accounting of this
plan's own remaining gaps had incorrectly listed reporter wiring as a
separate missing piece; it was not — see this note). The maps are built
from a new per-library stash: `_compare_one_library` (`cli_compare_
release.py`) now also captures the *new*-side `AbiSnapshot` (alongside the
pre-existing old-side one) and each library's own bundle-canonical key
under `_new_snapshot`/`_bundle_key`, gated behind the same `collect_diff_
results` flag the old-side stash already used — now triggered whenever
bundle analysis is enabled (the default), not only for `--bundle-facts-
out`/`--format junit`. Accepted tradeoff, stated in that gate's own
docstring: both sides' `AbiSnapshot`s are now held in memory for every
default release compare, not only the old side for the narrower
pre-existing cases — the same memory-conscious gate mechanism, now paying
that cost more often because the feature this phase describes needs it.
Regression coverage: `tests/test_cli_compare_release_bundle_signature_
wiring.py` (both `_run_bundle_analysis` and `_collect_bundle_result`
directly, with a monkeypatched `build_bundle_snapshot` mirroring
`tests/test_bundle.py`'s own established pattern; confirmed to fail
against the pre-wiring code).

**A CodeRabbit review on the wiring PR caught a real, pre-existing key-
mismatch bug this new caller made reachable for the first time.**
`_confirmed_provider_symbols` (the "a real, diff-confirmed change outranks
an unverified one" precedence check) keyed its set by `Path(result.library
).name` — `DiffResult.library`'s raw on-disk basename — while the main
loop's own `provider_lib` comes from `new.resolution.provides`, keyed by
the bundle-canonical name (`libfoo.so`, `binary_utils._canonical_library_
key`, version-stripped). For any normally-versioned real SONAME (e.g.
`libfoo.so.1.2.3`) the two never match, so the precedence check silently
never fired — invisible in every existing unit test, which deliberately
uses matching bare names throughout (`"libcore.so"` everywhere), but live
for the very first real caller this update introduces. Fixed by widening
the function's signature to `(old, new, per_library_results, old_
snapshots, new_snapshots)` and resolving each `DiffResult`'s basename back
to its bundle-canonical key via a new `_basename_to_bundle_key(old)`
helper (built from `old.libraries`) before comparing. Regression coverage:
`tests/test_bundle_signature_evidence.py::TestFindUnverifiedSignature
Findings::test_no_finding_when_confirmed_change_present_for_a_versioned_
library` (a genuinely versioned on-disk path alongside a bare-name
`DiffResult.library`, mirroring what a real `compare --release` stashes;
confirmed to fail — reproducing the spurious duplicate finding — against
the pre-fix code).

**Two further Codex review findings, both fixed.** (1) The exact-equality
check for the recursion-depth-cap sentinel (`spelling == "..."`) missed
composite wrapped forms (`"... *"`/`"... &"`/`"... &&"`, from
`pdb_parser.py`/`dwarf_snapshot.py` wrapping a depth-capped inner type in a
pointer/reference) — fixed by switching to the same substring check
`"?"` already uses. (2) `consumer_libs` was computed from a bare,
name-only, set-wide `consumers_of(symbol)` lookup, the same limitation
`bundle._detect_unresolved_intra_dependency`'s own docstring documents for
its own naive alternative — two unrelated libraries sharing a same-named
export could pair a consumer with a provider it has no real `DT_NEEDED`
path to. Fixed by restricting to reachable consumers, via a new shared
leaf module, `abicheck/bundle_resolution_reachability.py` (the `DT_NEEDED`
BFS extracted out of `bundle.py`, which both modules now import — this
also dropped `bundle.py` from exactly the 2000-line hard cap to 1975,
creating headroom rather than costing it). Deliberately narrower than
`_detect_unresolved_intra_dependency`'s full contract: symbol-version/
default-binding matching is not attempted, since that needs a
per-consumer resolution shape (iterate consumers, resolve each one's own
specific requirement) rather than this function's provider-centric one
(iterate providers, gather their consumers) — folding it in would be a
real restructuring of the main loop, left open rather than attempted as
an extension of the same review-driven patch. Both regressions confirmed
to fail against the pre-fix code.

**A third finding on the same recursion-sentinel area, correcting the
previous fix's own reasoning.** That fix's substring check on `"..."` was
itself unsafe, unlike the sibling `"?"` check it was modeled after
(Codex review, fresh evidence): a real, complete C/C++ type spelling can
legitimately contain the literal substring `"..."` — a variadic
function-pointer parameter type like `"void (*)(int, ...)"` is
fully-resolved evidence, not truncated. Fixed by matching only the
sentinel's own finite shape via an anchored regex (the bare sentinel,
optionally followed by one or more space-prefixed `*`/`&`/`&&` wrapper
suffixes for nested pointer/reference wrapping) instead of a blanket
substring check. Confirmed to fail against the pre-fix substring-check
code.

**A fourth finding, on a gap in `_symbol_evidence_sufficient()` unrelated
to type-spelling parsing: unknown variadicness read as sufficient
evidence** (Codex review, fresh evidence). `Function.is_variadic` is a
real tri-state field (`bool | None`), and `diff_symbols._check_variadic_
change()` itself skips (`skip_none=True`) whenever either side is `None`
— an older snapshot/dumper that never populated it is indistinguishable
from one that positively determined "not variadic". Without this module
also treating unknown variadicness as insufficient, a real fixed-arity/
variadic transition landing on an unknown side produced neither a
confirmed diff-level finding nor this module's own risk finding — total
silence on a real, calling-ABI-relevant unknown. Fixed by also requiring
`is_variadic is not None`. Confirmed to fail against the pre-fix code.

**A fifth finding, the identical shape as the fourth for a different
tri-state field.** `Function.contract_attributes` (calling-convention
attributes such as `stdcall`/`ms_abi`/`vectorcall`, `list[str] | None`)
had the same gap: `diff_symbols._check_contract_attributes_change()`
itself skips whenever either side is `None`, so a real calling-convention
transition landing on an unknown side produced neither a confirmed
diff-level finding nor this module's own risk finding. Fixed by also
requiring `contract_attributes is not None`. Confirmed to fail against
the pre-fix code.

**A sixth finding, back on the recursion-sentinel regex -- two more real
composite forms, plus a wholly separate, unconditional placeholder**
(Codex review, fresh evidence). `pdb_parser.py`'s qualifier wrapping
renders the depth-capped sentinel with a *prefix*, not a suffix
(`"const ..."`), and its array wrapping appends `"[]"` (`"...[]"`,
possibly further wrapped, e.g. `"...[] *"`) -- neither matched the
regex from the third finding. Separately, `dwarf_snapshot.py`'s
`DW_TAG_subroutine_type` handling and `pdb_parser.py`'s procedure/
member-function branches both render *any* function/subroutine type as
the fixed literal `"fn(...)"`, unconditionally, regardless of recursion
depth -- a placeholder the sentinel-only regex could never match by
construction, since it isn't a wrapped sentinel at all. Fixed by
widening the regex to accept an optional `const `/`volatile ` prefix
and `[]` among the suffix forms, and by separately recognizing the
exact `"fn(...)"` literal. Confirmed all four new cases fail against
the pre-fix code.

**A seventh finding closed the module's own previously-documented
"deliberately narrower" residual gap: symbol-version/default-binding
matching, which turned out not to need the feared restructuring**
(Codex review, fresh evidence). `consumers_of(symbol)` matches by bare
name only, so a consumer requiring `foo@V2` could still pair with a
`ProviderEntry` whose only definition is `foo@V1` -- a provider that
cannot actually satisfy that consumer at all (a real resolution
failure, not a signature-mismatch risk this module exists to flag). An
earlier revision of this docstring assumed closing this needed
`_detect_unresolved_intra_dependency`'s own per-consumer resolution
shape (iterate consumers, resolve each one's own specific requirement)
rather than this module's provider-centric one; on closer look it does
not -- a new `_consumer_matches_provider()` predicate, evaluated per
(consumer, provider_entry) pair inside the existing provider-centric
loop, mirrors the sibling function's version/`version_soname`/
`is_default` rules without restructuring anything. Confirmed to fail
against the pre-fix code, using the exact `foo@V2`-vs-`foo@V1` example
from the review comment.

**An eighth finding closed the last gap the fourth/fifth findings'
`is_variadic`/`contract_attributes` sufficiency checks opened without
noticing** (Codex review, fresh evidence). `_CONFIRMED_SIGNATURE_
CHANGE_KINDS` still only covered `FUNC_PARAMS_CHANGED`/`FUNC_RETURN_
CHANGED`/`VAR_TYPE_CHANGED` -- so a symbol with a real, diff-confirmed
`FUNC_VARIADIC_ADDED`/`FUNC_VARIADIC_REMOVED`/`CALLING_CONVENTION_
CHANGED` that also happened to carry an unrelated unresolved field
still produced a redundant, contradictory "cannot be confirmed or
denied" finding alongside the already-proven break. Fixed by adding all
three kinds to the set. `bundle._detect_intra_dep_signature_changed`'s
own `relevant_kinds` does not (yet) include these two either -- noted
as a pre-existing, narrower gap in that sibling function's own
docstring update, not something this fix needed to wait on. Confirmed
to fail against the pre-fix code, parametrized over all three kinds.

**A ninth finding closed a version-blindness gap in the old-side
retained-export check itself** (Codex review, fresh evidence, filed once
this Phase 4 detector had a real caller on `compare --release` and was
exercised against realistic versioned-symbol scenarios for the first
time). `_symbol_was_exported(symbol, old_snap)` reads only
`AbiSnapshot.function_map`/`variable_map` -- both keyed by bare symbol
name, with no per-GNU-version distinction (the same limitation this
repo's own root `AGENTS.md` already documents for `ElfMetadata.
symbol_map`'s "last-entry-wins" collapse of versioned aliases). So when a
provider previously exported only `foo@V1` and the new release adds
`foo@V2` for a consumer requiring exactly V2, the old-side check answered
"yes, `foo` was exported" purely from the unrelated `foo@V1` entry, and
the detector reported the brand-new `foo@V2` as a retained-signature risk
even though V2 has no old-side counterpart to be uncertain about at all.
Fixed by adding `_provider_entry_retained_from_old()`, which checks
`old.resolution.provides[symbol]` -- the bundle-resolution layer, built
from real per-symbol GNU version data, unlike the `AbiSnapshot`-layer
check -- for a same-library, same-`ProviderEntry.version` old-side
provider before treating the new one as retained. Two regression tests:
one confirming the false positive is gone for a genuinely fresh version,
one confirming the finding still fires when the version genuinely was
retained from the old side. Confirmed the positive-control test fails
against the pre-fix code.

**A tenth finding, from the same Codex review round as the ninth, widened
the confirmed-kinds allowlist rather than continuing to add one kind at a
time.** `_CONFIRMED_SIGNATURE_CHANGE_KINDS` (the eighth finding's own set)
still omitted every `Function`-level fact `diff_symbols.py` can confirm
independently of the four fields `_symbol_evidence_sufficient` itself
inspects (`return_type`/`params`/`is_variadic`/`contract_attributes`) --
concretely, a real, diff-confirmed `FUNC_NOEXCEPT_ADDED` on a symbol that
also carried an unrelated unresolved field still produced a redundant,
contradictory "cannot be confirmed or denied" finding alongside the
already-proven break, the identical shape the eighth finding fixed for
`FUNC_VARIADIC_ADDED`/`FUNC_VARIADIC_REMOVED`/`CALLING_CONVENTION_
CHANGED`. Rather than adding one more kind reactively (what the eighth
finding's own review round was, by the reviewer's own framing, at risk of
becoming), the fix was widened to every kind in that same shape:
`FUNC_NOEXCEPT_ADDED`/`FUNC_NOEXCEPT_REMOVED` (`is_noexcept`, a plain
bool, always confidently comparable), `FUNC_EXCEPTION_SPEC_CHANGED`/
`CTOR_EXPLICIT_ADDED`/`CTOR_EXPLICIT_REMOVED` (`exception_spec`/
`is_explicit`, tri-state fields whose own `diff_symbols` checks already
skip on `None` the identical way `is_variadic`/`contract_attributes` do),
`FUNC_REF_QUAL_CHANGED` (`ref_qualifier`, a plain string field), and
`FUNC_VIRTUAL_ADDED`/`FUNC_VIRTUAL_REMOVED` (`is_virtual`, a plain bool).
Deliberately excluded, with the reasoning recorded in the module's own
docstring: `FUNC_LANGUAGE_LINKAGE_CHANGED` (an `extern "C"` transition
changes the mangled name itself, so old and new sides can't share the
`symbol` key this module matches on in the first place) and the
vtable-slot/inline-transition kinds (facts about virtual-dispatch layout
and definition placement, not the calling-signature-agreement question
this module exists to answer). Nine regression tests (parametrized over
all eight added kinds), each confirmed to fail against the pre-fix
six-kind set.

**An eleventh finding, from the same review round, closed a deeper
version-blindness gap one layer past the ninth finding's own fix.** Even
a symbol the ninth finding correctly identifies as *retained* from the old
side can have multiple co-existing GNU versions live on one or both sides
(`foo@V1` and `foo@@V2` both still exported -- an entirely ordinary shape
for a provider that has never broken ABI compatibility across a versioned
release) -- and `AbiSnapshot.function_map`/`variable_map` keep exactly one
bare-name-keyed `Function`/`Variable` entry regardless of how many real
GNU-versioned definitions exist. `_symbol_evidence_sufficient()` evaluates
that single entry without any way to know which version it actually
reflects, so a consumer requiring specifically V1 could be told evidence
was fully sufficient purely because the collapsed entry happened to look
complete -- even though no V1-specific signature was ever actually
captured; the "sufficient evidence" answer could silently be borrowed from
an entirely different version. Fixed by adding
`_bare_name_version_collapsed()`, which detects the collapse using the
bundle-resolution layer's own per-version `ProviderEntry` list for that
`(provider_lib, symbol)` pair (a signal `AbiSnapshot` itself does not
carry) and forces both sides' sufficiency to `False` when detected, so the
"unverified" finding correctly fires (fail-closed) rather than trusting
ambiguous evidence. Two regression tests: one confirming the finding fires
when the old/new bare-name entry is version-collapsed (confirmed to fail
against the pre-fix code), one confirming a genuinely single-version
provider (even one flagged `is_default=True`) is unaffected.

**A twelfth finding, from the same review round as the tenth and
eleventh, found the eleventh finding's own fix was reachable only some of
the time -- the confirmed-change precedence check one step earlier in the
same loop could suppress a provider entry before the version-collapse
guard ever ran.** `find_unverified_signature_findings()`'s main loop
checks `(provider_lib, symbol) in confirmed` and `continue`s past the
entire rest of the per-provider-entry body -- including the eleventh
finding's own `_bare_name_version_collapsed()` check -- the moment ANY
diff-confirmed change exists for that bare-name pair. But `confirmed` is
itself built from `DiffResult.changes`, which are computed against the
identical version-blind, bare-name-keyed `AbiSnapshot` entries the
eleventh finding's fix already distrusts for evidence-sufficiency
purposes -- so a diff-confirmed `FUNC_PARAMS_CHANGED` on a collapsed
bare name `foo` (retaining both `foo@V1` and `foo@@V2`) is itself only
ever describing whichever version the model happened to keep, not
necessarily both. Pre-fix, this silently suppressed the unverified
finding for *every* consumer of that bare name, version-blind, even
though only one version's evidence was ever actually confirmed. Fixed by
computing `version_collapsed` once per `provider_entry` before the
confirmed-precedence check, and gating that check on `not
version_collapsed` -- confirmed-change precedence still applies normally
to the overwhelming majority (non-collapsed) case, and only yields to the
fail-closed "unverified" treatment when the bare name is genuinely
ambiguous. Two regression tests: two consumers pinned to each of two
collapsed versions with one confirmed change on the shared bare name
(confirmed to fail against the pre-fix code -- zero findings for either
consumer, when two were expected), and a non-collapsed sibling control
confirming precedence is otherwise unaffected.

**A thirteenth finding, from the same review round, closed a gap in
`_type_spelling_is_unresolved()` unrelated to symbol versioning.**
`dwarf_snapshot.py`'s `_compute_type_name` fallback branch -- reached for
any DWARF type-DIE tag with no dedicated handling (e.g.
`DW_TAG_ptr_to_member_type`) -- returns `name or tag or "unknown"`. When
the DIE carries no `DW_AT_name` (the common case for an obscure,
unhandled tag), this leaks either the bare literal `"unknown"` or the raw,
unresolved DWARF tag spelling itself into `Function.return_type`/
`Param.type`/`Variable.type` as though it were a genuine type name --
neither is one, and the previous sentinel set (`"?"`, the recursion-cap
`"..."`, `"fn(...)"`) recognized none of them. Since both leaked forms
pass through the identical `_resolve_inner_info`/`_resolve_inner_name`
wrapping layer the recursion-cap sentinel already accounts for, they can
also appear composited with pointer/reference/array suffixes and
qualifier prefixes (`"unknown *"`, `"DW_TAG_ptr_to_member_type[]"`).
Fixed by widening the existing recursion-cap regex -- renamed
`_UNRESOLVED_WRAPPED_SENTINEL_RE` to reflect its now-broader scope -- to
alternate over `\.\.\.|unknown|DW_TAG_\w+` as the wrapped base, rather
than only `\.\.\.`. Four new parametrized regression cases (the two bare
forms and one wrapped instance of each), confirmed to fail against the
pre-fix regex.

**A fourteenth finding, from a further Codex review round, showed
retention is not actually a uniform, per-`ProviderEntry` fact -- it can
vary by *which consumer* is asking.** `_provider_entry_retained_from_old`
(the ninth finding) matches purely on `ProviderEntry.version`, which is
correct for "does this exact version have any old-side counterpart at
all" but says nothing about whether a *specific* consumer could actually
have reached that old-side counterpart. Concretely: a provider previously
exporting only `foo@V1` (`is_default=False`) that marks the identical
`foo@@V1` default in the new release presents a genuinely new capability
to an *unversioned* consumer (which binds only to a default definition,
per `_consumer_matches_provider`'s own rule) -- that consumer could not
have resolved `foo` from this provider before at all, so there is no
old-side signature for it specifically to be "unverified" against, even
though the bare version string "V1" checks out. A version-specific
consumer requiring `foo@V1` explicitly is unaffected either way, since
its own match rule never inspects `is_default`. Fixed by adding
`_consumer_retained_from_old()`, folded into the `consumer_libs` filter
alongside `_consumer_matches_provider` rather than into the existing
per-provider-entry check -- deliberately additive, not a replacement:
removing the original check in favor of only the per-consumer one would
reopen the ninth finding's own bug, since an unversioned consumer's match
rule ignores symbol version entirely and would treat *any* old default
entry (of a completely different version) as satisfying retention for a
provider entry whose version never existed in old at all. Both checks
answer genuinely different questions and both must hold. Regression test
with two sibling consumers (unversioned, version-specific) against the
identical default-binding-flip scenario, confirmed to fail against the
pre-fix code (the unversioned consumer's library appeared in the findings
when it should not have).

**A fifteenth finding, from the same review round, was investigated and
deliberately declined rather than fixed reactively.** `_compare_one_
library`'s `collect_diff_results` gate (widened earlier in this same PR
to also trigger whenever bundle analysis is enabled -- the default) means
every ordinary, non-JUnit release comparison now retains both full
`AbiSnapshot` objects for every library until the whole release finishes
and bundle reporting completes -- changing peak memory from roughly the
active worker set to the sum of every old/new snapshot pair, a real
concern for a release with many header-backed libraries. This was already
called out as an accepted tradeoff in this PR's own first changelog entry
(see this fragment's "Added" section), and investigating a fix confirmed
why it isn't a same-pass patch: `entry["_old_snapshot"]` is shared with a
*pre-existing* JUnit rendering path in `cli_compare_release.py` (predating
this PR, per PR #798) that requires a real `AbiSnapshot`
(`isinstance(old_snap, AbiSnapshot)`-gated) -- it cannot simply be
replaced with the compact `function_map`/`variable_map`/`elf_only_mode`
structure `find_unverified_signature_findings` actually needs. A safe fix
needs the stashing code in `_compare_one_library` to know *why*
`collect_diff_results` was triggered for a given run (JUnit vs.
bundle-analysis-only vs. both) and store a full snapshot only when JUnit
genuinely needs one -- a real control-flow change to already-reviewed,
working code, not something to attempt under continued review pressure
in this pass. Left as a known, accepted gap per this file's own "known
gaps over risky reactive patches" convention.

`bundle_intra_dep_signature_changed` already fires correctly when a
provider's DWARF/header evidence shows a real signature change. This phase
adds the missing negative case: a new, dedicated `ChangeKind`,
`bundle_intra_dep_signature_unverified` — **required, not a
field-vs-kind choice left open for implementation time.** A bare field on
`BundleFinding` cannot be independently suppressed, promoted, or exit-coded
by a `--policy` file the way a `ChangeKind` can (`policy_file.py`'s
`overrides:` block, `checker_policy.py`'s severity/verdict registry, and
`severity.py`'s exit-code mapping all key off `ChangeKind`, not an
ad-hoc finding field) — so only the dedicated-kind form actually satisfies
this plan's own acceptance criterion 5 ("a distinct finding... so a
`--policy` file can suppress or promote it independently"). Registered in
`change_registry.py` exactly like every other `bundle_*` kind, with its own
report/JSON representation, registry entry, and acceptance test pinning its
default `RISK` verdict plus a policy-override case — the same bar every
other new `ChangeKind` in this codebase clears (see the root `AGENTS.md`'s
"Adding a new ChangeKind" checklist).

The kind fires per **consumer/provider symbol pair**, evaluated with
symbol-level evidence, not a report-level signal:

1. the consumer's undefined symbol resolves by name to a provider's export
   (the C-linkage match `compare_bundle()` already establishes), **and**
2. *that specific symbol's* evidence — on the provider side, whether its
   `Function`/`Variable` entry in the provider's `AbiSnapshot` carries real
   DWARF/header-derived type information at all, vs. being visible only as
   a bare ELF export with no corroborating declaration (an L0-only bundle
   member, or a provider whose header crosscheck never matched this
   symbol) — is insufficient to confirm or deny a signature change on
   *either* side. `evidence_status_for_result`'s existing
   `ARTIFACT_PROVEN`/`UNATTRIBUTED` distinction is report-level (see
   AGENTS.md's own "Evidence-provider model" known-gap entry) and is
   explicitly **not** reused here for exactly that reason — a report-level
   signal would make this finding's outcome depend on unrelated symbols
   elsewhere in the same comparison, which defeats the point of a
   per-symbol gate. **Checking `Function.params`/`Function.return_type`/
   `Variable.type` for non-`None` does not actually work as this gate's
   check** (caught in review — `Function.return_type`/`Variable.type` are
   required `str` fields, never `None`, and `Function.params` defaults to
   an empty list, so an L0-only symbol has *the same shape* as a real,
   evidenced zero-argument function: `dumper_elf_fallback.py`'s ELF-only
   path fills the unknown type in explicitly as the literal sentinel
   string `"?"` with an empty parameter list, and stamps
   `visibility=Visibility.ELF_ONLY` on the entry rather than leaving any
   field empty or absent). The real, symbol-scoped check this phase needs
   is therefore: does the resolved provider (and consumer, where
   applicable) `Function`/`Variable` for this specific symbol have
   `visibility != Visibility.ELF_ONLY`, a return/variable type other than
   the `"?"` sentinel, **and every parameter's own type also other than
   `"?"`**, on both old and new. That last clause is not redundant with the
   return-type check — a second review round caught a DWARF-specific gap
   in an earlier draft that checked only the return/variable type: a
   function can have full DWARF coverage overall (so it is never
   `ELF_ONLY`) while one *parameter's* own `DW_AT_type` is absent —
   `dwarf_snapshot.py`'s `_process_param` explicitly emits
   `Param(type="?")` for exactly this case — so a signature with a known
   return type but an unresolved parameter type would otherwise read as
   fully verified when it is only partially so. The check must walk every
   parameter, not just the return/variable type, i.e. recognize the actual
   unknown-evidence marker this codebase already uses everywhere it can
   appear, not merely at the top level of a signature. This is deliberately
   much narrower than the full
   per-finding evidence-provider model AGENTS.md's known-gap entry
   describes out of scope — it answers only "does *this* symbol have a
   corroborated signature," not "which tier produced every finding in the
   report."

Default verdict: `RISK`, distinct from both "no change" and the confirmed
`BREAKING` `bundle_intra_dep_signature_changed` — this is the "binary-name
compatible, signature unverified" language the review's finding-taxonomy
table asks for.

---

## Files & surfaces

| File | Change |
|---|---|
| `abicheck/bundle_facts.py` | **Shipped (Phase 2).** New leaf module (not `bundle_models.py` — see the implementation-status note above): `BundleFacts`, `capture_bundle_facts()`, `bundle_snapshot_from_facts()`, `compare_bundle_from_facts()` (a thin wrapper delegating to `bundle.compare_bundle()` unchanged, so the parity test holds two calls to one implementation equal) |
| `abicheck/bundle_manifest.py` | **Shipped (Phase 2).** `manifest_to_dict`/`manifest_from_dict`/`manifest_entry_to_dict`/`manifest_entry_from_dict` — round-trip serialization for `InstantiationManifest`, reusing `_parse_manifest_entry`'s existing validation rather than a second parser |
| `abicheck/bundle_multibuild.py` | **Shipped (Phase 3, pairing primitive only).** `variant_fingerprint`, `pair_variants`, `VariantOutcome`, `VariantComparison`, `coverage_regression_findings` |
| `abicheck/bundle_signature_evidence.py` | **Shipped (Phase 4, detector only).** `find_unverified_signature_findings` — standalone leaf module, not wired into `bundle.compare_bundle()` (see the Phase 4 implementation-status note above) |
| `abicheck/serialization.py` | **Shipped (Phase 2).** `save_bundle_facts`/`load_bundle_facts` plus `bundle_facts_to_dict`/`bundle_facts_from_dict` (the latter two live here, not in `bundle_facts.py`, to avoid the import cycle noted above) |
| `abicheck/comparability.py` | Bundle-level fingerprint-mismatch refusal, mirroring the existing single-snapshot `ScopeMismatchError` (Phase 3, once `variant_fingerprint` carries real per-variant identity — Phase 2's field is always `"default"`); no change to single-snapshot behavior |
| `abicheck/checker_policy.py` / `abicheck/change_registry_buildsource.py` | **Shipped (Phase 3):** `bundle_variant_coverage_regressed` registry entry. **Shipped (Phase 4):** `bundle_intra_dep_signature_unverified` registry entry, in `change_registry_buildsource.py` alongside its Phase 3 sibling |
| `abicheck/cli_options.py` / `abicheck/cli_compare_release*.py` | **Shipped (producer half only).** `--bundle-facts-out <path>` on the existing `compare` release fan-out (`release_options()` in `cli_options.py`, threaded through `run_compare()`/`_dispatch_release_compare`/`compare_release_cmd`, written via `cli_compare_release_helpers.write_bundle_facts_out()`) — an additive output flag, not a new root command. **Not shipped:** `compare --against <bundle facts>` consumer wiring (deferred — see the implementation-status note above) and whichever multibuild CLI surface Phase 3 needs |
| `abicheck/reporter.py` / `abicheck/report_summary.py` | Render the two new finding shapes; extend `bundle.json`/`bundle.md` (Phases 3-4) |
| `docs/reference/change-kinds.md` | Phase 1 taxonomy note; new-kind entries for Phases 3-4 |
| `docs/contribute/adr/023-bundle-aware-multi-binary-analysis.md` | Amendment block linking to this plan (see below) |
| `tests/canonical_identity_contract.py` | **Shipped.** Both new kinds (`bundle_variant_coverage_regressed`, `bundle_intra_dep_signature_unverified`) classified into `UNVERIFIED` — required by the root `AGENTS.md`'s "Adding a new ChangeKind" step 5; `tests/test_canonical_finding_id_completeness.py` passes for both |

---

## Tests

- **Shipped:** `tests/test_bundle_facts.py` (new file, not an extension of
  `test_bundle.py`) — `BundleFacts` round-trip (`save_bundle_facts` →
  `load_bundle_facts` → identical `compare_bundle_from_facts` output,
  covering the manifest's three entry shapes and both compressed/
  uncompressed storage), and the mandatory dump/live parity test from
  Phase 2's acceptance criterion 6 (a graph-native finding, a diff-derived
  finding, a negative/no-change control, and manifest override precedence
  — `compare_bundle_from_facts()`'s findings/verdict compared field-for-field
  against a live `compare_bundle()` call on the identical underlying facts).
- **Shipped:** `bundle_variant_coverage_regressed`'s positive/negative cases
  (Phase 3) — see `tests/test_bundle_multibuild.py` below.
- **Shipped:** `bundle_intra_dep_signature_unverified`'s positive/negative
  cases (Phase 4) — see `tests/test_bundle_signature_evidence.py`, listed in
  the Phase 4 implementation-status note above.
- New `tests/test_bundle_multibuild.py` — `variant_fingerprint` determinism
  and sensitivity: two builds differing only in an ABI-irrelevant flag, or
  only in `-std=`/build-derived defines, fingerprint **identically** (Phase
  3's own point — that drift is corroborated-comparable build state, not
  variant identity, so it must pair and let `cxx_standard_floor_raised`/
  `abi_relevant_build_flag_changed` classify it); two builds differing in a
  logical-identity coordinate (target triple, or a feature toggle like
  `ONEDAL_DATA_PARALLEL`) fingerprint **differently**. `pair_variants`'
  never-union guarantee as a
  Hypothesis property (mirroring this repo's existing "Primitive-level
  property tests" convention for a reusable merge/pairing primitive — see
  AGENTS.md's own guidance on why a primitive this shape needs a
  property-test class, not only hand-picked examples), and the missing-
  variant-produces-its-own-finding case.
- Extend the FP-rate/tier-accuracy corpora with one oneDAL-shaped case per
  new `ChangeKind` (mirroring how `bundle_soname_skew` already has
  `examples/case84_bundle_soname_skew/`).

## Example fixtures

- `examples/case<N>_bundle_dump_comparability/` — a real, small multi-.so
  bundle (three tiny libraries, one importing from another) with a stored
  `BundleFacts` baseline compared against a live "new" directory
  reproducing the same internal-symbol-removed break the review's oneDAL
  repro used, at a fixture scale this repo's example corpus can actually
  check in.
- `examples/case<N>_bundle_multibuild_coverage_gap/` — two variants old
  side, one variant new side, pinning `bundle_variant_coverage_regressed`.
- `examples/case<N>_bundle_c_boundary_unverified/` — an L0-only bundle
  member (stripped, no DWARF) whose C-linkage import still resolves by
  name, pinning the unverified-signature finding.

---

## Stabilization phases (post-Phase-4 external review)

An external review of the state after Phase 4 landed (PR #842/#844, main at
`c370aed`/`42da2d2`) found Phases 1–4 to be good foundational work that is
*not yet* a complete, safe-by-default product feature: Phase 2's stored
facts have no CLI consumer, Phase 3's pairing primitive has no production
caller, and Phase 4 — now wired into the live `compare --release` path —
carries a real correctness bug, a policy/severity inconsistency, a
stored-baseline parity gap, and a memory regression. That review is the
origin of the phases below; each restates one of its numbered findings as
a scoped, independently-landable change rather than one large PR. Numbering
continues from Phase 4 rather than restarting, since these are direct
continuations of the same initiative, not a new one.

### Phase 5 — Confirmed vs. promotable boundary-break kind sets (shipped)

**Finding:** `bundle._detect_intra_dep_signature_changed`'s promoted-kinds
set (3 kinds: params/return/var-type) and
`bundle_signature_evidence`'s suppression set (12 kinds) were two
independently maintained lists that had already drifted apart — a confirmed
`CALLING_CONVENTION_CHANGED` (and eight other kinds) correctly suppressed
`BUNDLE_INTRA_DEP_SIGNATURE_UNVERIFIED` but was never promoted to a
consumer-attributed `BUNDLE_INTRA_DEP_SIGNATURE_CHANGED`, silently losing
cross-library causality for exactly the kinds Phase 4 added confirmed-change
detection for.

**First fix attempt, reverted the same PR after a Codex review round (fresh
evidence): sharing one 12-entry set for both purposes is itself wrong.**
"Confirmed, so don't claim total ignorance about this symbol" (suppression)
and "confirmed severely enough to fabricate a consumer-attributed BREAKING
bundle finding" (promotion) are two different bars — `change_registry.py`'s
own entries prove it: `FUNC_NOEXCEPT_ADDED` has `default_verdict=COMPATIBLE`,
and `FUNC_NOEXCEPT_REMOVED`/`FUNC_EXCEPTION_SPEC_CHANGED` are
`COMPATIBLE_WITH_RISK` with an explicit "not a binary break" rationale in
their own registry comments. A single shared 12-entry set would have made
`_detect_intra_dep_signature_changed` promote any of those three to a
BREAKING `BUNDLE_INTRA_DEP_SIGNATURE_CHANGED` — fabricating a
release-blocking cross-library finding out of a change the tool's own policy
layer says is not a binary break at all. `FUNC_VIRTUAL_ADDED`/
`FUNC_VIRTUAL_REMOVED` are genuinely `BREAKING` but describe vtable-slot
layout, not a direct calling-boundary mismatch — a different failure mode
than `BUNDLE_INTRA_DEP_SIGNATURE_CHANGED`'s own "calling convention is now
mismatched" description.

**Fix (shipped):** two sets in `bundle_models.py`, one a strict subset of
the other (asserted at import time and pinned by
`tests/test_bundle.py::TestIntraDepSignatureChanged::
test_promotable_kinds_are_a_strict_subset_of_confirmed_kinds`, which also
asserts every promotable kind's own `default_verdict` is `BREAKING` via
`change_registry.REGISTRY`):

- `CONFIRMED_C_BOUNDARY_SIGNATURE_BREAK_KINDS` (broad, 12 kinds) — used only
  by `bundle_signature_evidence.py` for suppression, unchanged from the
  first attempt.
- `PROMOTABLE_C_BOUNDARY_SIGNATURE_BREAK_KINDS` (narrow, 6 kinds: the
  original params/return/var-type plus `FUNC_VARIADIC_ADDED`/
  `FUNC_VARIADIC_REMOVED`/`CALLING_CONVENTION_CHANGED`) — used only by
  `bundle._detect_intra_dep_signature_changed` for promotion. This is the
  actual fix for the finding above: it closes the real gap
  (`CALLING_CONVENTION_CHANGED` and the two variadic kinds now promote)
  without also promoting the six kinds that are correctly suppression-only
  evidence.

Regression coverage: the subset/verdict test above, plus
`test_does_not_promote_noexcept_added_to_a_breaking_finding` (an end-to-end
case pinning the exact fabrication the first attempt would have shipped)
and `test_promotes_calling_convention_change_to_consumer` (the genuine gap
this phase closes).

**Lesson for future phases in this sequence:** "these two consumers read
the same underlying fact, so they should share one constant" is not
sufficient justification on its own — check whether the two consumers are
actually answering the *same question* at the *same confidence bar* before
unifying. Phase 7 (bundle policy/severity threading) will face a structurally
similar temptation (one policy object feeding multiple decision points) and
should verify each consumer's bar independently rather than assuming a
single resolved object is automatically correct for all of them.

**Known residual gap, not fixed in this phase (Codex/CodeRabbit review,
fresh evidence, filed rather than rushed):**
`_detect_intra_dep_signature_changed`'s consumer lookup
(`new.resolution.consumers_of(change.symbol)`) is a bare, name-only match
with no reachability or version/default-binding filtering — unlike
`bundle_signature_evidence.find_unverified_signature_findings`, which
already gates on `reachable_intra_libraries()` (the consumer must actually
be able to load the provider through a real `DT_NEEDED` path) and
`_consumer_matches_provider()` (a GNU symbol-version match, not just a bare
name match) before attributing a finding. This predates Phase 5 — widening
`relevant_kinds` from 3 to 6 kinds increases how often the pre-existing
imprecision is reached, but does not introduce the imprecision itself. A
related concern raised in the same review round: `diff_by_library`'s keys
(`Path(result.library).name`, a "canonical basename" per that code's own
comment) are asserted, not verified, to always agree with the resolution
graph's own library-naming convention for every caller of
`compare_bundle()` — plausible for the wired `compare --release` path
(both derive from one directory-discovery pass) but not independently
confirmed for every caller. Not fixed here: `bundle.py` is at the
AI-readiness 2000-line hard cap (1992/2000 after Phase 5's own docstring
additions), so borrowing `bundle_signature_evidence.py`'s reachability/
version-matching logic needs either a shared leaf-module extraction (the
two private helpers would need to become public, tested primitives with
their own home) or an equal-or-greater removal elsewhere in `bundle.py` —
a real, separately-scoped change, not a follow-up edit to the same
function under review pressure. Until then, a `compare --release` bundle
report can attribute a promoted finding to a provider a consumer cannot
actually reach, or across a version mismatch, for the six promotable kinds
— the same class of imprecision the pre-existing three kinds already
carried, now reachable by twice as many kinds.

### Phase 6 — Compact per-library signature evidence (memory regression fix)

**Finding:** wiring Phase 4 into the live `compare --release`/bundle-
analysis path (the "Phase 4" changelog entry above) made
`collect_diff_results=True` the default for *every* directory/package
comparison, not only when `--bundle-facts-out`/JUnit was requested — so
every completed library's full old+new `AbiSnapshot` (functions, types,
layouts, source graph, build-source evidence, everything) is now retained
until the whole release finishes and bundle analysis runs.
`_collect_bundle_result()` then builds complete old/new snapshot maps from
those retained objects. For an N-library release, peak memory approaches
the sum of every completed library's full snapshot pair plus whatever
active parallel workers are still extracting — a real regression relative
to the pre-Phase-4 default, where only JUnit/`--bundle-facts-out` paid that
cost.

**Planned fix:** a new, frozen `BundleSignatureEvidence` projection
(`library_key`, `exported_symbols`, per-symbol function/variable signature
evidence, confirmed-boundary-change keys) built immediately after each
per-library comparison finishes and immune to the source `AbiSnapshot`'s own
size — the full snapshot is then released unless something *else* still
needs it (JUnit rendering, `--bundle-facts-out`). Three independent
retention reasons currently collapse into one `collect_diff_results` flag;
they need to become three (JUnit, `--bundle-facts-out`, compact evidence),
so "bundle analysis is enabled" stops implying "retain every full
snapshot." Acceptance criterion: a default `compare --release` over N
libraries retains zero full old/new `AbiSnapshot` objects once each
library's own comparison completes, verified by asserting retained-object
counts (not just wall time) in a dedicated regression test. **Not yet
implemented** — filed here as the top-priority next phase rather than
attempted in the same change as Phase 5, since it touches the release
fan-out's own memory-lifetime contract and needs its own careful,
independently-verified design (per this repo's "known gaps over risky
reactive patches" convention in the root `AGENTS.md`).

### Phase 7 — Bundle-finding policy/severity/exit-code consistency

**Finding:** `BundleDiffResult.bundle_verdict` is computed from a bare
policy-profile string (`checker_policy.compute_verdict`), so a built-in
profile name (`strict_abi`/`sdk_vendor`/`plugin_abi`) reaches bundle
findings but a custom `--policy-file`, a `kind: policy` pack override, or
direct suppression of a `bundle_*` kind does not — and the severity
exit-code fold converts bundle findings to `Change`s and calls
`compute_exit_code()` without threading the resolved policy/severity
config through at all. The displayed verdict and the process exit code can
therefore disagree for any non-default policy/severity combination.

**Planned fix:** resolve bundle policy once into a typed
`ResolvedBundlePolicy` (profile, `PolicyFile | None`, per-kind pack
overrides, suppression, severity config) and thread that single object
through bundle-finding classification, the aggregate bundle verdict,
JSON/Markdown rendering, and both the severity-aware and legacy exit-code
paths — so a custom policy demoting `bundle_intra_dep_signature_unverified`
changes the report and the exit code together, never just one. **Not yet
implemented.**

### Phase 8 — Structured bundle-analysis coverage/degradation

**Finding:** bundle snapshot construction failures, `find_unverified_
signature_findings` exceptions, and a provider missing from either
snapshot map are all caught and reported as stderr warnings only — a
report's `"bundle_findings": []` cannot be distinguished from "analysis
ran cleanly and found nothing" versus "analysis partially failed."

**Planned fix:** a structured `bundle_analysis` coverage block in the JSON
report (mirroring the existing `contract_coverage_ledger`/`analysis_
assurance` pattern already used for the contract-evaluation axis), naming
per-sub-analysis status (`complete`/`partial`/`not_requested`) and any
missing libraries/errors, with a strict policy able to escalate incomplete
bundle coverage to `NOT_COMPARABLE` rather than silently accepting a clean
result built on partial evidence. **Not yet implemented.**

### Phase 9 — Live/stored Phase-4 parity (one bundle-analysis orchestrator)

**Finding:** `compare_bundle_from_facts()` (the stored-baseline path)
delegates only to `compare_bundle()`; `find_unverified_signature_findings()`
is a separate companion the live `compare --release` CLI path calls
directly (Phase 4's own wiring). A stored-facts comparison therefore never
runs the C-boundary signature-evidence gate at all, so "live vs. live" and
"stored old vs. live new" bundle analysis can disagree on findings for the
identical underlying evidence — the parity Phase 2's own design section
promised does not (yet) extend to Phase 4.

**Planned fix:** one `analyze_bundle()` orchestrator both the live release
path and `compare_bundle_from_facts()` call, taking optional per-library
signature-evidence maps (Phase 6's compact projection) so a stored side
with no retained `AbiSnapshot` can still participate. `compare_bundle()`
stays the core graph-native/diff-derived detector implementation; it is no
longer presented as the complete bundle-analysis surface. **Not yet
implemented** — depends on Phase 6's compact evidence existing first, since
a stored `BundleFacts` side has no full `AbiSnapshot` to build one from
otherwise.

### Phase 10 — Stored-facts CLI consumer and multibuild wiring

**Finding:** Phase 2's `BundleFacts` are producible (`--bundle-facts-out`)
but not consumable — there is no `compare old.bundlefacts.json new-release/`
CLI path, only a documented programmatic API. Phase 3's `pair_variants()`
has no CLI/config caller and no producer populates a real (non-`"default"`)
variant fingerprint, so two same-side captures collide as identical
identity today.

**Planned fix:** a `BundleSideInput` abstraction (`LiveBundleInput` |
`StoredBundleFactsInput`) resolving into one `ResolvedBundleSide`, so live/
live, stored/live, and stored/stored share one comparison pipeline instead
of a second hand-written loop; a declarative `bundle_variants:` config
block naming each variant's identity coordinates explicitly (target,
compiler family, feature toggles) rather than inferring it; and a
`required: true/false` distinction so a missing required variant can gate
a release rather than only demoting to `COMPATIBLE_WITH_RISK`. **Not yet
implemented** — deliberately sequenced after Phases 6–9, since a stored-
baseline CLI surface that skips Phase 4 (Phase 9) or retains full snapshots
for every variant (Phase 6) would ship the same gaps this stabilization
sequence exists to close, just on a wider surface.

The original multi-binary performance problem (repeated header/AST
extraction across sibling DSOs sharing one source tree) is explicitly out
of scope for all of Phases 6–10 above — Phase 6 stops a *new* regression
Phase 4's wiring introduced, it does not address the pre-existing
per-binary extraction cost. That remains its own, separately-scoped
initiative (shared/content-addressed evidence storage, memory-aware
scan scheduling), not additional G38 phase surface.

---

## Real-world validation and further phases (napetrov/abicheck-bazel-lab, real oneDAL)

An external contributor ran a full real-oneDAL validation pass (`daal`,
`oneapi::dal`, and `dpc` scans; three `dump` baselines) against a pin bump
from `7cf8adf83` to `c370aed07a` (101 commits). This is the first evidence
in this plan sourced from a real, at-scale binary rather than a synthetic
fixture, and it both confirms Phase 4 works correctly in production and
surfaces real gaps this section turns into phases. Headline results:

- **Cost-neutral pin bump.** All three scans and three dumps landed within
  noise of the old pin (22m40s → 22m44s total scan time across the three
  libraries; snapshot sizes unchanged). All three scans exit 0,
  `COMPATIBLE_WITH_RISK`, with identical risk scores (78/27/47) to the old
  pin — the bump changed correctness, not behavior, for this codebase.
- **A real dumper correctness fix, already shipped, with an operational
  lesson for this plan.** The bump also picked up a fix for an enumerator-
  initializer value getting lost when folded on an intermediate clang AST
  wrapper node (a positional auto-increment silently replaced the real
  value — e.g. `csrArray = 1 << 4` recorded as `3` instead of `16`). This is
  a general dumper bug, not specific to bundle analysis, and is not part of
  G38 — but re-running the *same* source tree against stale, pre-fix
  baselines produced 280 false `enum_member_value_changed` `BREAKING`
  findings (155 `daal` + 125 `dpc`) with `schema_version=25` on both sides
  and *nothing in the report naming which baseline caused it*. Re-dumping
  the baselines at the new pin took all 280 to zero. The operational
  lesson — "`schema_version` unchanged does not imply a baseline is still
  valid across a core pin bump; any pin bump needs baselines re-dispatched"
  — belongs in the repo's release/CI process documentation, not this plan,
  but is recorded here since it was found by exercising this plan's own
  Phase 2 stored-facts model at scale. A structured coverage block (Phase 8)
  naming *which* baseline a mismatch traces to would have made this
  diagnosable without a bisection.
- **Phase 4 confirmed correct at production scale.** The cheap, headerless
  bundle path (34s / 240MB for the full oneDAL bundle) correctly produced
  `bundle_verdict=COMPATIBLE_WITH_RISK` with 156 advisory
  `bundle_intra_dep_signature_unverified` findings — not spurious
  `BREAKING` findings — validating both the C-boundary signature-evidence
  gate's design and (retroactively) the Phase 5 promotion/suppression
  split above: a headerless scan has essentially no DWARF/header evidence
  for its stripped internal C symbols, so every one of the 156 correctly
  landed as the advisory "unverified" finding rather than a fabricated
  break.
- **Bundle blockers: three of four now understood, two already fixed.**
  Independently confirms `#831`'s two landed fixes (header analysis in
  directory-scoped bundle compare; SONAME-stem matching eliminating 379
  false `bundle_intra_dep_removed` findings on external providers, → 0) and
  identifies two more, detailed as Phases 11–13 below.

### Phase 11 — Headerless-bundle public-surface scoping

**Finding:** a headerless directory/package bundle compare has no header
evidence to scope by, so `FilterNonPublicSurface` (or its bundle-analysis
equivalent) has nothing to restrict findings to the library's actual public
API — every ELF-visible symbol, including ones with no public header at
all, is treated as in-scope. Measured on real oneDAL libraries: 1414/237/272
unscoped `BREAKING` findings across the three headerless scans, an order of
magnitude more than a header-scoped compare of the same libraries would
report. An earlier upstream attempt to scope this from ELF visibility alone
(no headers) was reverted — ELF visibility (`GLOBAL`/hidden) answers "is
this symbol exported," not "is this symbol part of the documented public
API," and the two diverge for exactly the internal-but-exported C symbols
this whole initiative's own oneDAL repro (ADR-023's own origin story) is
built around.

**Not yet designed.** A correct fix needs a public-surface signal that
does not depend on parsing headers — candidates worth evaluating rather
than assuming one is right: (a) a version-script/export-map-derived
allowlist, when the library's build already produces one (oneDAL's own
CMake does, for several of its libraries); (b) `--public-header-dir`/an
explicit `-H`/include-scoping flag threaded through the headerless bundle
path specifically so a *cheap* partial header set (just the public
umbrella headers, not the full transitive include graph a header-scoped
compare pays for) can scope without paying Phase 13's full cost; (c) a
documented, opt-in "no public-surface scoping available" flag on the
finding set itself (mirroring Phase 8's structured coverage idea) so a
headerless bundle report is honest about running unscoped rather than
silently over-reporting. Given the ELF-visibility attempt's own revert,
whichever design is chosen needs validation against the same real oneDAL
corpus before landing, not just synthetic fixtures.

### Phase 12 — Audit-mode (`scan --artifact-set`) system-provider coverage and friction

**Finding, part 1 (additive, low-risk):** `scan --artifact-set`'s cheap
audit path (10.8s / 383MB for the full six-library oneDAL set, no baseline
required) is blocked in practice by `bundle_models.DEFAULT_SYSTEM_PROVIDERS`
having no entries for the Intel oneAPI/TBB/MKL runtime libraries oneDAL
links against — 862 audit findings collapsed to 58 once the missing
sonames were supplied via the existing `--bundle-system-providers` escape
hatch. **Not fixed in this pass**: the exact SONAMEs (`libtbbmalloc.so.2`,
`libiomp5.so`, the MKL runtime family, the oneAPI Level Zero/Unified
Runtime loaders, ...) were not independently re-verified against a real
Intel oneAPI install in this environment, and this codebase's own
"known gaps over risky reactive patches" convention argues against
committing unverified SONAME strings to a shared, curated default list —
a wrong entry here silently under-reports, which is worse than the
current, honest "you must name it yourself" default. The actionable next
step is for whoever ran this validation to supply the exact SONAME list
(from `ldd`/`readelf` output against the real libraries) so it can be
added to `DEFAULT_SYSTEM_PROVIDERS` with real provenance, mirroring how the
existing entries (`libtbb.so.12`, `libsycl.so`, ...) already cover the
same product family.

**Finding, part 2 (a documented design tradeoff, re-examined, not
reversed):** even after naming every system provider, 58 residual audit
findings remained, traced to `_detect_unresolved_intra_dependency`'s own
docstring — it deliberately has **no** `_looks_system_symbol` name-shape
fallback, unlike its diff-driven sibling `_detect_intra_dep_removed`,
specifically so a legitimate, non-system-shaped custom export (the
docstring's own example: `vendor_init`) is never silently swallowed by a
shape heuristic. That design choice is sound in the abstract and is not
reversed here — but real usage shows it creates real friction: a fully-
correct `--bundle-system-providers` list still needs to separately name
every Intel-runtime-internal mangled symbol the loader resolves, since
`DEFAULT_SYSTEM_SYMBOLS`/`_looks_system_symbol` are consulted only by the
diff-driven detector, not the audit one. Worth a scoped follow-up (not
attempted here, since it needs the same real Intel runtime evidence Part 1
does): let `--bundle-system-providers` optionally accept a symbol-name
*pattern*, not only a SONAME, so a real distinguishing signal for exactly
the Intel-runtime-internal symbols this audit path can't otherwise resolve
is available without reopening the `vendor_init` false-negative risk the
original design decision correctly avoided.

### Phase 13 — Cross-pair header/source-context cache for the bundle layer

**Finding:** the fourth, most expensive blocker — a header-scoped
directory/package bundle compare independently re-parses the shared header
tree for every library pair, rather than once per unique compile context —
measured at 2.5+ hours / 38.3GB peak RSS for a 12-union-header-parse oneDAL
bundle compare (6 libraries × old+new), a cost that makes the header-scoped
path effectively unusable for a bundle this size in CI. This is the same
"original multi-binary performance problem" this plan's own Phases 6–10
section already declares out of scope for G38 proper — this finding does
not change that scoping decision, it sharpens it with a real, measured
number rather than a hypothetical one, and confirms (independently of this
plan) that the streaming JSON pruner AGENTS.md documents as a negative
result (~1.2% peak-RSS reduction, ~13% slower) is not a viable point fix
for this cost. The real fix — a shared, content-addressed header/AST cache
keyed by compile context rather than by library — remains its own,
separately-scoped initiative per the existing "Out of scope" text above,
not additional G38 phase surface; this entry exists so that initiative
inherits a concrete, real-world acceptance target (12 union header parses
→ however many *unique* compile contexts the bundle actually has, which
for a single-source-tree product like oneDAL should be closer to 1–2 than
12) instead of starting from nothing.

### Deferred CLI/API surface asks (not yet filed as phases)

Three smaller, concrete asks came out of the same validation pass that
don't yet have a phase of their own, each blocking a specific workflow
rather than correctness:

- **`dump --public-header-dir`** and **per-library header roots on the
  CLI** — both needed for Phase 11's option (b) above (a cheap, partial
  header set for headerless-bundle scoping) and for a cleaner Phase 10
  stored-baseline producer invocation against a multi-library release
  whose libraries don't all share one umbrella header directory.
- **`--bundle-facts-out`'s consumer half** — already tracked as Phase 10
  above; this validation pass is independent confirmation that it's the
  single highest-value remaining ask, since it's the one gap keeping
  `scan --artifact-set`'s otherwise-working 10.8s/383MB cheap audit path
  from producing a real baseline-comparable exit code instead of an
  audit-only one.

---

## Out of scope

Restated from the originating review, explicitly deferred rather than
silently dropped:

- **Reverse impact analysis against an external consumer application** —
  stays in `appcompat`/`stack-check`, unchanged by ADR-023 and unchanged by
  this plan.
- **`dlopen`/`dlsym` plugin-style dynamic dependency edges** — invisible
  from DT_NEEDED; needs source/manifest evidence this plan does not add
  (ADR-008's own follow-up work already tracks this, and G5's
  `plugin-check` covers a related but distinct host↔plugin contract).
- **Mach-O/PE bundle equivalents** — Phase 2's `BundleFacts` schema is
  written to be platform-neutral in shape (artifact identity + resolution
  graph + evidence), but populating `BundleArtifactFacts` from a
  `.dylib`/`.dll` set, and the loader-graph specifics (load commands vs.
  import tables vs. DT_NEEDED), is real per-platform work left for a
  follow-up once the ELF path is proven out.
- **A general-purpose, content-addressed bundle archive format** (the
  review's §9 sketch) — see Phase 2's own "deliberately not attempted"
  note above.
- **Full per-finding evidence-provider model** (which extractor/tier
  produced each individual finding, not just each `AbiSnapshot`) — already
  tracked as its own, larger, cross-cutting gap in the root `AGENTS.md`'s
  "Evidence-provider model" known-gap entry; Phase 4 above deliberately
  does **not** reuse the existing report-level `evidence_status_for_result`
  signal (see Phase 4's own design section for why a report-level signal
  is wrong for a per-symbol gate) and instead adds one narrow,
  symbol-scoped check, rather than attempting that larger per-finding
  project as a side effect of this plan.
- **A genuine toolchain-identity probe validating a resolved compiler
  binding's real family/version against a declared multibuild-variant
  constraint** — `bundle_multibuild.variant_fingerprint` records the
  *declared* logical-identity toolchain facts (target triple, compiler
  family/version string); it deliberately excludes standard/flags (see
  Phase 3's own design section for why) and does not independently verify
  the resolved binary at `compile.binding` actually matches even the
  fields it does record. That verification is already
  tracked as its own gap (root `AGENTS.md`'s "Toolchain-profile compiler-
  family rendering" entry, and G34 Phase A's toolchain-binding probe) and is
  not duplicated here.
