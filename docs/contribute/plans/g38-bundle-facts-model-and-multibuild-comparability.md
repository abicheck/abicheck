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
