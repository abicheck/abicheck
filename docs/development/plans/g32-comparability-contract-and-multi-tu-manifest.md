# G32 — Comparability Contract: Profile/Scope Fingerprints and the Multi-TU Manifest

**Origin:** A review of abicheck's snapshot architecture, prompted by a
real multi-TU/DPC++ scenario (umbrella header + Arrow-derived adapter
header needing its own forced include + SYCL host/device compilation
split), found two unaddressed gaps: `dump()` collapses every requested
header into one synthetic translation unit (no per-TU forced includes, no
required-vs-optional TU semantics), and `checker.compare` has no gate that
proves two snapshots were extracted under a comparable contract before
running a symbol diff — a manifest/flag drift between two extraction runs
today produces a page of false additions/removals instead of a clear
"these two snapshots aren't comparable" result. Most of what the review
also raised (public/private/external classification, deterministic
serialization, content-hash caching, RAM-aware parallel extraction) turned
out to already be shipped under different names — see ADR-050's Context
for the full audit. This plan implements only the genuinely new decisions.

**ADR:** [ADR-050](../adr/050-comparability-contract-and-multi-tu-manifest.md)
(Proposed — records the target model; this plan carries the phased
backlog).
**Type:** Initiative plan (cross-cutting; spans `abicheck/model.py`,
`abicheck/dumper.py`, `abicheck/checker.py`, `abicheck/service.py`,
`abicheck/mcp_server.py`, `abicheck/cli.py`, `abicheck/snapshot_cache.py`,
`abicheck/sycl_metadata.py`, `abicheck/buildsource/source_replay.py`, new
top-level modules).
**Effort:** Phase 0 — S. Phase A — M. Phase B — XL. Phase C — L. Phase D —
L. Phase E — M. Total: XL, phased over multiple PRs.
**Risk:** Phase 0 — low (fixtures only, no production code path changes).
Phase A — low-medium (new gate at a well-defined entry point, additive
fields; ships as a hard default per ADR-050 D2 from day one — risk is
mitigated by Phase 0 fixture coverage and a pre-merge dry run, not by a
runtime soft-default — see Phase A below). Phase B — **high** — changes
`dumper.py`'s single hottest
path (every `dump`/`compare` call goes through it) from one frontend
invocation to N. Phase C — medium (new merge surface, but scoped to
data `dumper.py` produces, no external-tool dependency). Phase D — medium
(needs a real captured DPC++ AST fixture before implementation can proceed
safely — see Phase 0). Phase E — low (extends an already-proven pattern
from `buildsource/source_replay.py`).

---

## Sequencing

Phase 0 first, always — it is what makes Phase D (and to a lesser extent B)
design-by-evidence instead of design-by-assumption, per the originating
review's own strongest procedural point ("don't build a stream parser
against a guessed format; capture the real thing first"). Phase A can ship
and start providing value independently of B–E — it does not require
multi-TU support to be useful, since even today's single-aggregate-TU
snapshots have a real, checkable `profile_fingerprint` — **and it ships
with the hard-blocking gate as its actual, only default behavior**: Phase
A's own section is explicit that there is no soft-launch/report-only mode
(the gate hard-fails `not_comparable` from day one; see Phase A). "Ships
independently" describes *when* Phase A can land relative to the other
phases, not a different (report-only) behavior it has while doing so.
Phase B and Phase D may both start once Phase 0's fixtures exist — Phase
D's parser and `host`-default path don't depend on B, though selecting a
*non-default* context needs Phase B's `frontend_context` field/flag to
request it (see Phase D). Phase C starts only after Phase B lands, since
it operates on the `TuFragment` contract Phase B defines and produces —
it is not a third parallel branch alongside B and D. **E depends on
*both* A and B — not on B alone.** `scope_fingerprint` as a *type* and
*computation* (`ExtractionContract`, `comparability.compute_extraction_contract`)
is Phase A's deliverable; Phase B only adds the manifest-driven *inputs*
(TU names, per-TU ordered includes/forced-includes,
`contributes_to_abi`/`required` flags) that feed that same, already-existing
computation for a manifest-driven dump — Phase B does not invent
`scope_fingerprint` itself. Phase E's cache-key half also concretely
builds on Phase A's own cache-key work, not just its types: Phase E's own
acceptance criteria say outright that "Phase A's own order-sensitivity fix
(dropping `sorted(...)` for `headers`/`includes`) already closes the
remaining gap that mattered for the legacy CLI path" (see Phase E) — a
direct reference to work this same plan places in Phase A, not a
restatement of something Phase E redoes. Starting E after B alone, with A
unmerged, leaves either no `ExtractionContract`/`scope_fingerprint`
machinery to hash at all, or an ad hoc reimplementation that risks
drifting from whatever Phase A's gate actually computes — silently
defeating the whole "cache key must track what the gate checks" premise
this phase exists for. `profile_fingerprint`
is the one that can never be a pre-dump cache-key input, on either path
(see Phase E) — and its scheduling half schedules Phase B's per-TU loop.

```text
                      ┌──▶ Phase A (contract + gate) ──────────┐
                      │                                        │
Phase 0 (fixtures) ───┼──▶ Phase B (manifest/multi-TU) ─┬──────┴─▶ Phase E (scheduling + cache)
                      │                                 │
                      │                                 └─▶ Phase C (compatible merge)
                      └──▶ Phase D (SYCL host/device context)
```

---

## Phase 0 — Regression fixtures and example cases

**Problem.** Every downstream phase needs ground truth to test against, and
two of them (B's merge lattice, D's AST-context selection) are exactly the
kind of thing that goes wrong when designed from a description instead of
real data.

**Goal & acceptance criteria.**
- A real captured DPC++/`clang -ast-dump=json` multi-document output
  fixture exists (from an actual `icpx`/DPC++ invocation, not synthesized),
  alongside a plain single-context clang fixture for contrast.
- A synthetic "ODR-safe" multi-TU fixture pair: one struct forward-declared
  in TU A, fully defined in TU B (must merge cleanly); one function
  declared with different return types across two TUs (must conflict).
- An "external STL noise" fixture: a public function taking
  `std::vector<int>` by value, to exercise D4's supporting-vs-reportable
  entity boundary at the merge layer (this boundary itself is ADR-024's, not
  new — the fixture just needs to exist for the merge tests to use it).
- A "scope drift" fixture pair: same manifest structure, new side adds one
  extra TU — used to assert Phase A's gate hard-fails `not_comparable` on it
  by default, and that the explicit `--diagnostic-comparison` opt-in (D2's
  one sanctioned escape hatch, not a separate always-on report-only mode)
  correctly downgrades it to a tentative, `assurance: "none"`-stamped diff
  instead.

**Files & surfaces.** New fixtures under `tests/fixtures/g32/` (raw AST
captures, not committed as generated `.abi.json` — those are produced by
the tests themselves once Phase A/B land).
**Not every Phase 0 fixture is a `tests/`-only artifact — only the ones
whose *outcome* is genuinely non-verdict-producing are.** The "scope drift"
pair (Phase A's `not_comparable`/`SCOPE_MISMATCH`) and the
conflicting-return-type pair (Phase C's `INCONSISTENT_DECLARATION`/
`HETEROGENEOUS_ABI_CONTEXT`) are extraction-time outcomes, not `ChangeKind`s
or `Verdict` values — `tests/test_validate_examples_unit.py`'s
`_VALID_VERDICTS` frozenset only accepts the five real `Verdict` strings,
so *these two specifically* never become catalogued examples (see Phase
A's and Phase C's own "Example fixtures" sections for the same reasoning).
The **ODR-safe** pair's compatible-merge half is different: it produces an
ordinary, verdict-bearing comparison once Phase C's real merge lands, and
is exactly what becomes `examples/case2xx_multi_tu_compatible_merge/`
(Phase C's own "Example fixtures" section) — it is not swept into this
tests-only rule. The DPC++/plain-clang AST captures aren't diff fixtures at
all (they feed Phase D's parser directly), so the examples-catalog
question doesn't apply to them either way. The external-STL-noise pair is
wired through Phase B's real manifest path (see Phase B's own "Example
fixtures" section) as a `tests/`-level check of the supporting-vs-reportable
merge boundary, with no catalog promotion currently planned for it.

**Tests.** No new production tests yet — this phase is fixture capture and
a short `tests/test_g32_fixtures.py` asserting the fixtures are non-empty
and readable, so a later phase's tests have something real to load without
a live DPC++ toolchain in every CI lane. The multi-document DPC++ capture
is **not** asserted to parse as one JSON value — by design it's a stream
of concatenated documents, the exact shape Phase D's stream parser exists
to handle; requiring single-document JSON-parseability here would reject
the real capture and push Phase 0 toward synthesizing a fake one,
undermining the fixture-first point of this phase. It's validated only for
non-emptiness now, and by the real stream parser once Phase D exists.

**Out of scope.** No production code changes.

---

## Phase A — `ExtractionContract`: profile/scope fingerprints and the comparability gate

Implements ADR-050 D1 and D2.

**Goal & acceptance criteria.**
- `AbiSnapshot.contract: ExtractionContract | None` (`model.py`) carries
  `profile_fingerprint`/`scope_fingerprint` plus the resolved inputs that
  produced them. `scope_fingerprint`'s hashed inputs include each TU's
  `required`/`contributes_to_abi` flags, not just its includes/forced
  includes (ADR-050 D1) — flipping `contributes_to_abi` changes which
  declarations feed the ABI model without necessarily touching a TU's
  includes, so leaving it out of the hash would let that exact class of
  scope drift pass the gate undetected.
- **A profile mismatch confined to target triple/pointer width/endianness
  must not preempt `diff_platform.py`'s existing, more specific
  platform-identity detectors.** `profile_fingerprint` deliberately
  includes target triple/pointer width/endianness (they genuinely affect
  AST extraction — omitting them reopens the under-counting bug this
  design exists to close), but `elf_machine_changed`/`elf_class_changed`/
  `elf_endianness_changed` (and PE/Mach-O equivalents) already detect the
  exact same axis directly from the binaries' own headers, already
  classified `BREAKING` — comparing genuinely different target
  architectures is `profile_fingerprint`'s single most likely mismatch
  source, and unlike every other drift case this gate exists to catch, it
  is not unexplained: the diff pipeline already has a correct,
  artifact-grounded answer. Gating it into a generic `not_comparable`
  before `diff_platform.py` ever runs would downgrade a proven `BREAKING`
  verdict into a strictly less useful "couldn't tell" result. Fix:
  `check_contracts_comparable` inspects *which* resolved fields differ
  (the same per-field data `ExtractionContract` already stores for
  reporting, not just the opaque hash). Any other
  differing field still hard-fails as before, even alongside a
  co-occurring target difference — scoped to platform-identity fields
  alone, not a general loosening. **"Only the profile-fingerprint target
  differs" is necessary but not sufficient — the carve-out must also
  confirm the binaries themselves genuinely differ on that axis, or a
  misconfigured extraction slips through uncaught.** A profile-only
  target mismatch can happen with no real architecture difference at
  all: a cross-compiler flag/`--gcc-prefix` set for only one side while
  both `.so`/`.dll` files are genuinely the same architecture. In that
  case `diff_platform.py` has no `elf_machine_changed`/etc. finding to
  emit — the binaries never differed — so skipping the gate would let a
  misconfigured, actually-non-comparable extraction through as an
  ordinary diff, any AST artifact the wrong target introduced surfacing
  as an apparently real `Change` with nothing flagging the underlying
  problem. The carve-out therefore requires *both*: (1) the only
  differing `profile_fingerprint` fields are target/pointer-width/
  endianness, **and** (2) the snapshots' own binary-derived platform
  metadata (the same header fields `elf_machine_changed` et al. already
  read) shows a genuine difference on that same axis. Only when both hold
  does `compare()` proceed instead of raising; (1) alone, with (2) false,
  still raises `ProfileMismatchError` — an extraction misconfiguration,
  not a legitimate cross-architecture compare.
- **Both fingerprints hash root-relative paths, never raw absolute ones —
  the single highest-priority correctness requirement in this phase — and
  each normalizes against its *own* root, never a root shared across both.**
  `compare`'s side-scoped `--header old=v1/foo.h --header new=v2/foo.h` /
  `--include old=inc1 --include new=inc2` (ADR-040) is the ordinary
  two-checkout compare workflow, and its old/new sides necessarily resolve
  to different absolute paths even for an identical logical surface.
  Hashing absolute paths directly would fingerprint-mismatch and hard-fail
  *every routine compare* as `not_comparable` — breaking the gate's primary
  use case on day one. `scope_fingerprint`'s root (header/TU paths) and
  `profile_fingerprint`'s root (`-I` include-search directories) are
  computed **separately**, from each category's own paths only — an
  earlier revision of this criterion combined them into one shared root,
  which breaks the moment an external dependency `-I` directory (e.g.
  `--include old=/opt/dep --include new=/opt/dep`, commonly identical and
  well outside either checkout) shares no meaningful prefix with the
  project headers: the combined common ancestor collapses to `/`, so the
  header paths normalize right back to their diverging checkout roots
  (`work/v1/foo.h` vs. `work/v2/foo.h`) and `scope_fingerprint` mismatches
  anyway — the exact bug this whole fix exists to close, reintroduced by
  mixing an unrelated path category into the same root computation.
  For the legacy CLI path, `scope_fingerprint`'s root is the common
  ancestor **directory** of that side's header paths' *parent* directories
  only (never `-I` directories). Deriving it from header paths directly
  instead of their parents breaks the single-header-per-side case — the
  common ancestor of a one-element path set is that whole path, so
  `old=v1/foo.h` and `new=v2/bar.h` would both normalize to the same empty
  marker and hash identically despite being different scopes, the opposite
  failure from the one this fix exists to close; taking the parent
  directory first preserves the filename (`v1/foo.h` → root `v1/`,
  normalized `foo.h`).
  **Known, accepted limitation: this only preserves the basename, not the
  header's own subpath.** `old=old/include/foo.h` and
  `new=new/private/foo.h` both normalize to `foo.h` — correct for the
  intended two-checkout case, but identical to what a genuine relocation
  (public `include/` → `private/`) would also produce; the rule cannot
  tell the two apart, since both examples share the same shape (a single
  header, differing parent name) and opposite correct answers — the same
  "undecidable from path shape alone" limitation this document already
  accepts for `-I` directories below, now also true for single-header
  `scope_fingerprint`. Not a new heuristic to chase: the manifest path
  (D3) has no such gap, since a manifest's TU paths are explicit declared
  identities, not inferred from directory shape.
  **`profile_fingerprint`'s `-I` directories are fingerprinted by resolved
  content, not by path shape — three path-shape heuristics were tried and
  rejected in turn (ADR-050 D1 records all three in full); a fourth,
  path-shape-agnostic design is what actually ships.** Rejected attempt
  one: header parent-directory rule applied unchanged — right for
  `--include old=old/include --include new=new/include` (the routine
  two-checkout project-root case), wrong for a lone external dependency
  (`--include old=/opt/dep-v1/include --include new=/opt/dep-v2/include`
  normalizes both to `include`, erasing a real version difference).
  Rejected attempt two: hash each `-I` directory's last two path
  components instead — fixes the dependency case, breaks the routine
  project-root case (hashes `old/include` vs. `new/include` as different).
  Rejected attempt three: revert to the parent-directory rule uniformly
  and accept the dependency-version gap as documented — this breaks a
  *third* direction once a side declares a project include **plus** a
  shared external dependency (`old=/work/v1/include` + `old=/opt/dep`,
  `new=/work/v2/include` + `new=/opt/dep`): each side's common ancestor
  collapses to `/`, so the project include normalizes back to its
  diverging checkout root and an otherwise-identical routine upgrade
  hard-fails `PROFILE_MISMATCH` — the same "combining heterogeneous
  categories under one shared root" mistake `scope_fingerprint`/
  `profile_fingerprint` splitting apart already fixed once, recurring
  *within* `profile_fingerprint` itself. No function of `-I` path shape
  can be made correct, and combining multiple `-I` directories under one
  shared root additionally risks corrupting entries that would have
  normalized correctly alone. `profile_fingerprint` therefore computes no
  root from `-I` path text at all: each `-I` directory (per side, in
  declared order — order is already a hashed input) contributes its own
  digest — the sorted set of (path relative to that `-I` directory,
  content hash) pairs for every file the preprocessor actually opened from
  inside it. **This must be the full transitive include list, not just
  headers that end up owning a declaration** — a header pulled in purely
  for macros/pragmas (an `abi_config.h` defining an ABI-affecting layout
  macro but declaring nothing itself) never appears in
  `dumper_castxml.py`/`dumper_clang.py`'s per-declaration
  `_source_location`/`header_from_location` tracking, so sourcing the
  digest from that data alone would silently miss a genuine
  dependency-content difference expressed only through such a header —
  the same "gap through under-counting" this whole redesign exists to
  close, reintroduced one level deeper. The digest is instead built the
  same way `abicheck/buildsource/include_graph.py`'s existing depfile
  mechanism already builds the L3 include graph (`parse_depfile()`, a pure,
  already-unit-tested Make-rule-depfile parser), **using the same
  system-inclusive flag that module already had to learn to use, for the
  same reason:** the L2 castxml/clang invocation additionally requests a
  depfile via **`-MD -MF <path>`, not `-MMD`** — `-MD` lists
  system-classified headers (reached via `-isystem`/the sysroot/standard
  library) alongside user headers, while `-MMD` silently omits them.
  `include_graph.py:354-356` already documents exactly this precedent ("`-M`
  (not `-MM`) so depfiles include *system*-classified headers"), added
  after an earlier review caught the identical omission there. `-MMD` here
  would reintroduce that same bug on a new path: a header reached only
  through a system/sysroot include (a libstdc++ upgrade changing an
  ABI-relevant macro, for instance) would never appear in the depfile, so
  two dumps that actually parsed different system-resolved headers could
  still match `profile_fingerprint` — the exact under-counting failure this
  redesign exists to close, via a flag choice this time instead of a
  data-source choice. Every listed path is attributed to whichever declared
  `-I` directory contains it — one extra cheap compiler flag per TU,
  reusing a proven parser at a new call site, not a second compiler
  invocation or a directory-tree walk.
  **Not every `-MD`-listed path falls under a declared `-I` directory.**
  `dumper.py` already introduces search paths outside the user-declared
  `includes` list: `--sysroot`, the GNU-toolchain `-isystem` dirs it probes
  and injects automatically (`_probe_gnu_system_includes`), and any
  `-isystem`/`-I` embedded in `--gcc-options`/`--gcc-option` pass-through
  flags. Leaving depfile entries resolved through these unattributed would
  recreate this design's own under-counting bug one layer out — a
  toolchain/sysroot upgrade changing an ABI-relevant system header would
  never affect `profile_fingerprint`. Every depfile path not under any
  declared `-I` directory instead feeds one additional, explicitly-labeled
  **system/toolchain bucket** — a content digest of that unordered set (no
  path-shape normalization attempted, since these paths carry no
  user-declared search-order meaning to preserve).
  **The depfile's own generated driver TU must be excluded before any of
  this bucketing runs, not swept into the system/toolchain bucket as "just
  another unattributed path."** `dumper.py` writes a synthetic aggregate
  `#include` header via `tempfile.NamedTemporaryFile` (`:364,1019`) and
  compiles *that* as the TU's real source; `parse_depfile` (reused here)
  returns the compiled source itself as the first prerequisite, not only
  the headers it pulls in (`tests/test_include_graph.py`:
  `parse_depfile("foo.o: foo.cpp a.h b.h") == ["foo.cpp", "a.h", "b.h"]`).
  That generated `/tmp` file is under no declared `-I` directory, so it
  would otherwise land in the system/toolchain bucket — and its *content*
  embeds the side-specific absolute `#include "..."` paths written for that
  run's own header list, which necessarily differ between old and new sides
  for the ordinary two-checkout case even when the compile environment is
  identical, making `profile_fingerprint` differ on *every* routine
  compare — the worst-case version of the failure mode this whole redesign
  exists to close. The generated driver TU (identified as `dumper.py`'s own
  synthesized source path, not a declared `-I`/`-H` input) is dropped
  before any bucketing runs. `profile_fingerprint`'s
  `-I` component is the hash of the **ordered** sequence of per-directory
  digests, **plus this one system/toolchain bucket appended last** —
  **after excluding every declared `-I` directory that is project-owned,
  in its entirety, not just the specific paths `scope_fingerprint` names.**
  The documented real-world workflow
  (`docs/user-guide/real-world-example.md:61-63`) passes the project's own
  include root as *both* `--header` (the headers being compared) and
  `--include` (so `#include` resolves) — the same directory serves both
  roles, so a depfile for that TU necessarily lists the very header being
  compared alongside its support headers. **Excluding only the named
  header is not enough**: `foo.h` typically `#include`s project-internal
  support headers (a private `detail.h`) that are never individually
  named, only reached because they live under the same declared `-I`
  root — an exclusion scoped to just the explicit `--header`/manifest
  entry points would still hash those unnamed support headers' content, so
  an ordinary internal refactor (renaming `detail_v1.h` to `detail_v2.h`,
  or editing it, with `foo.h` itself untouched) would still flip
  `profile_fingerprint` and hard-fail the gate before the diff ran — a
  routine case, not an edge case. The exclusion therefore applies at the
  **whole `-I` directory** level: a declared `-I` directory is
  **project-owned** when it equals or is an ancestor of any of that side's
  declared `--header`/manifest TU paths — every file under it, named or
  not, is excluded from `profile_fingerprint` entirely. A declared `-I`
  directory unrelated to any declared header is **external** and keeps the
  full per-file content digest — a real dependency, where a change
  anywhere in it is meaningful drift. **The ancestor rule misses a common
  non-nested layout: a support directory declared as a *sibling* of the
  public header root, not underneath it** — a build-generated headers
  directory (`generated/`) or a private-implementation directory (`src/`,
  `config/`) that `include/foo.h` `#include`s from, passed via its own
  `--include` but never an ancestor of any declared `--header`. The
  ancestor rule classifies these as external today, so an ordinary edit to
  a generated or private support header still flips `profile_fingerprint`
  on a routine CMake/Meson-style layout, not an edge case.

  **A separate `--project-include` option cannot carry this at all,
  regardless of its own value grammar — Click doesn't preserve declaration
  order *across* two differently-named repeatable options.** Verified
  against Click's actual parsing model: a `multiple=True` option's
  callback gets that option's own values as one tuple, in that option's
  own declaration order, but Click never records one option's occurrences
  relative to a *different* option's. `--include dep --project-include
  support=src` and `--project-include support=src --include dep` arrive
  at the callback as the identical `(include=('dep',),
  project_include=('support=src',))` — no way to recover which came
  first. Since `profile_fingerprint`'s `-I` ordering is search-precedence
  order (the actual relative position the compiler sees), a second,
  separate option can never feed it correctly, no matter how its value is
  shaped — this sinks the separate-option design before its grammar is
  even considered. **Fix: no second option — the label rides on
  `--include` itself**, the one option Click already keeps in true
  declaration order (the same guarantee the whole `-I`-sequence design
  already relies on for plain `--include`/`--include` pairs).
  `cli_params.py`'s existing `SidedPathParam` (shared today by
  `--include`, `--header`, and other sided-path options) is extended for
  `--include` specifically into a new `SidedIncludePathParam` — `--header`
  and the rest keep the unchanged, 2-tuple `SidedPathParam`, since only
  `--include` needs a label slot. `SidedIncludePathParam` layers an
  optional labeled form on top of the existing `[old=|new=|both=]PATH`
  grammar. **The labeled form requires the literal colon prefix — there is
  no colon-less/bare `LABEL=PATH` variant at all.** `[old:|new:|both:]LABEL=PATH`
  means "one of these three literal prefixes, colon included," never "the
  prefix segment is optional, so a bare value is still tried as
  `LABEL=PATH`." Getting this backwards breaks existing usage:
  `SidedPathParam.convert` (today's `--include` type) only checks
  `s.startswith("old=")`/`"new="`/`"both="`, so an ordinary directory
  containing a literal `=` past that point — `build/config=asan/include`,
  a valid `--include` value today — matches none of the three and falls
  through unchanged to `("both", Path(...))`. A bare-`LABEL=PATH` reading
  would instead reinterpret it as `label="build/config"`,
  `path="asan/include"` — a different compiler argument, breaking a
  currently-valid value that never opted into labeling. Fix: a value is
  only ever split into a label after one of the three literal
  `old:`/`new:`/`both:` prefixes has matched; every other value — bare,
  `old=`/`new=`/`both=`-prefixed, or containing an unrelated `=` — takes
  `SidedPathParam`'s existing, unmodified path, `label=None`
  unconditionally. A two-checkout compare with
  side-specific paths for one shared logical root is declared `--include
  old:support=old/src --include new:support=new/src` — same `support`
  label on both invocations (so the per-slot token matches across sides
  for the same logical root), different paths per side, interleaved with
  any number of ordinary `--include old=/opt/dep` entries in exactly
  their typed order, since it's all one option's accumulated tuple. The
  label is required for this labeled form specifically (a user-supplied
  logical name, not path-derived — the same choice D3's manifest `name`
  field already makes) because a support root with no owned declared
  header has nothing for the ancestor-derived per-slot token below to key
  off of; asking for one avoids yet another path-shape heuristic. This
  labeled `--include` form is legacy-CLI-only. **The manifest path (D3) is
  not automatically exempt from the same gap — an earlier revision of this
  bullet wrongly claimed `forced_includes` already covered it.**
  `forced_includes` is a per-TU list of individual force-included header
  *files*, the manifest equivalent of a single named header — it says
  nothing about a TU's `includes` list, the manifest's own `-I`
  search-path entries. A manifest TU declaring `includes: [../src]` or
  `includes: [generated/]` to resolve a private/generated support header
  has the identical problem: that directory is a *sibling* of the TU's own
  path, not an ancestor, so the ancestor rule classifies it external and
  an ordinary edit inside it still flips `profile_fingerprint`. Fix, in the
  manifest's own idiom: an `includes` entry is either a bare path string
  (unchanged — ancestor rule decides) or a mapping `{path: ...,
  project_owned: true}` explicitly marking it project-owned. No separate
  label is needed here, unlike the CLI form — manifest paths are already
  root-relative/side-normalized by design, so the same relative path
  string on both the old and new manifest already is a stable,
  mount-point-independent per-slot token, the same way a TU's `name`
  already serves as stable identity elsewhere in this design.

  **`--include` is three separate Click registrations today, not one —
  `SidedIncludePathParam` only fixes `compare`'s.** Verified against the
  actual code: `cli_options.py`'s `two_sided_input_options`
  (`:206`, the `SIDED_PATH_PARAM` registration extended above) is applied
  only at `cli.py:1513` (native `compare`). `dump_cmd`'s own `--include`
  (`cli.py:486`) is a *separate*, inline registration — plain, non-sided
  `click.Path` (`dump` has one input, never carried `SidedPathParam` at
  all). `scan_cmd`'s own `--include` (`include_pairs`, `cli_scan.py:487-495`)
  is *also* a separate inline registration, with its own
  `type=SIDED_PATH_PARAM` — not `compare`'s decorator, and not touched by
  extending it. Fixing only `two_sided_input_options` leaves `dump`/`scan`
  with no way to express a label at all: `project_include_labels` stays
  empty for snapshots produced through either command, and a sibling
  support root declared via `dump --include .../src` or `scan --against
  ... --include .../src` stays classified as external — reproducing this
  whole fix's target bug on two more commands, not just leaving them
  unimproved. Both need their own change: `scan_cmd`'s inline `--include`
  switches `type=` to `SidedIncludePathParam` (it already has `old=`/`new=`
  side semantics, current artifact vs. `--against`, identical to
  `compare`'s), and its `split_sided_paths(include_pairs)` call
  (`cli_scan.py:712`) becomes `split_sided_include_paths(include_pairs)`.
  **`dump_cmd` cannot reuse `SidedIncludePathParam` as-is, though — doing
  so silently breaks a currently-valid `dump --include` value.**
  `SidedIncludePathParam` still honors the *unlabeled*
  `[old=|new=|both=]PATH` grammar `SidedPathParam` already implements
  (deliberate, so `compare`/`scan` — which already had this prefix rule —
  don't change behavior). But `dump_cmd`'s `--include` is a plain
  `click.Path` today and has *never* recognized `old=`/`new=`/`both=` as a
  side prefix, so a directory literally named `old=foo/` is a valid,
  existing value there; reusing `SidedIncludePathParam` wholesale would
  newly start stripping that prefix on `dump` too, a real behavior change
  unique to `dump` (unlike `compare`/`scan`, which already had it).
  `dump_cmd` instead gets its own type, `LabeledIncludePathParam`: it
  recognizes **only** the colon-terminated `both:LABEL=PATH` form (colon
  was never meaningful to `dump`'s plain `click.Path` before, so adding it
  is purely additive) and treats every other value — bare, or one that
  happens to literally start with `old=`/`new=`/`both=` — as an ordinary,
  unlabeled path exactly as `click.Path` does today, with no equals-form
  side-prefix stripping at all. `dump`'s labeled form is therefore
  `--include both:support=path` (only `both:` is accepted — there is no
  real side concept, and no `old:`/`new:`, on a single-input command); a
  value like `--include old=foo/` keeps meaning the literal directory
  `old=foo/`, unchanged.
  `scope_fingerprint` owns everything
  under a project-owned root; `profile_fingerprint` owns only external
  roots, in full. **Excluding a project-owned directory's content must not
  also erase its position in the declared `-I` sequence.** `-I` order is
  search-precedence order — `-I project -I dep` and `-I dep -I project`
  over identical files can resolve an ambiguous `#include "config.h"`
  present in both directories to a *different* file depending on which
  flag came first, a real compiled-content difference, not cosmetic
  reordering. Dropping an excluded directory's *slot* entirely (rather
  than just its content) would collapse both orderings to the same
  single-element sequence, since the project root then contributes
  nothing — two extractions with genuinely different `#include`-resolution
  behavior would hash identically, the exact false-match failure mode this
  digest exists to close, reintroduced through the exclusion mechanism
  itself. The fix keeps the sequence **positional**: every declared `-I`
  directory keeps its own slot in declaration order; a project-owned
  slot's content is replaced with a **per-slot logical token**, not one
  shared constant — a single generic sentinel for every project-owned
  slot loses order information again, one level down, once there are two
  or more project-owned roots: `-I include -I generated` vs. `-I
  generated -I include` (both project-owned, byte-identical content,
  swapped declared order) would both hash to the same `[SENTINEL,
  SENTINEL]` sequence under one shared constant, silently losing the same
  order information this fix exists to preserve. The token comes from one
  of two sources depending on *why* the slot is project-owned: an
  **ancestor-derived** root's token is the **sorted set of declared
  `--header`/manifest TU names that directory is an ancestor of** (never
  its path or content) — two ancestor-derived directories owning
  different declared headers get different tokens (so swapping their
  order changes the sequence), while a directory owning the same declared
  header set tokenizes identically regardless of mount point, consistent
  with `scope_fingerprint` already treating declared header *names* as
  legitimate, already-tracked identity, so no new information leaks
  through the token; a labeled `--include old:LABEL=PATH` support root's
  token is its required user-supplied **`label`** instead (it owns no
  declared header by construction — that's exactly why it needs the label
  form), namespaced separately from the ancestor-derived token space so a
  label can never collide with a header name. `-I project -I dep` (project
  ancestor of declared `foo.h`) hashes `[token(foo.h), digest(dep)]`; `-I
  dep -I project` hashes `[digest(dep), token(foo.h)]` — different,
  correctly flagged as non-comparable; `--include both:support=old/src`
  vs. the same directory declared last instead of first is distinguished
  the same way via `token(support)`. **Residual limitation (same class as the
  vendored-nested-dependency gap below):** two separately declared `-I`
  roots that are both ancestors of the *same* declared header (an outer
  directory and one of its own subdirectories, each passed as its own
  `-I` entry) tokenize identically and stay order-indistinguishable from
  each other — an unusual declaration shape, not the routine case this
  fix targets. The system/toolchain bucket stays unordered, since its
  inputs were never part of a declared, precedence-bearing `-I` sequence
  to begin with. **Known, accepted residual gap:** a vendored dependency
  nested *inside* a project-owned root is swept into that exclusion too,
  so a content change confined there is invisible to `profile_fingerprint`
  on the legacy CLI path — the same "can't disambiguate from directory
  shape alone" class of limitation already documented for the mixed-roots
  case, not a new kind of gap; the manifest path (D3) has no such gap,
  since it can express a per-TU forced-include instead of relying on
  directory-tree inference. This is lossless: byte-identical dependency content at different mount points
  normalizes identically (attempt one's routine case, still correct);
  genuinely different content normalizes differently regardless of naming
  (the `dep-v1`/`dep-v2` case both attempt one and two mishandled); a
  side-specific project include alongside a shared external dependency
  normalizes each independently, since no shared-root computation exists
  left to corrupt (attempt three's regression is structurally impossible
  here). If a resolved header's content can't be read at fingerprint time,
  extraction fails outright with a dedicated error rather than folding an
  "unresolvable" sentinel into the hash. `scope_fingerprint` is unaffected
  — it hashes header/TU *paths* (declared naming is part of the compared
  surface), unlike `profile_fingerprint`'s `-I` job of describing *how*
  `#include` resolves, where identity should track content, not the path
  label. For the manifest path (D3), both fingerprints' roots are simply
  the manifest file's own directory — none of these legacy-CLI cases
  exist there.

  Fifteen dedicated tests (numbered 1–14, with 8b alongside 8) are non-negotiable for this phase to be considered
  done: (1) `--header old=v1/foo.h --header new=v2/foo.h` against logically
  identical trees under different roots asserts the resulting
  **`scope_fingerprint`s** match (not `profile_fingerprint` — headers are a
  scope input, and a test that only checks `profile_fingerprint` here can
  pass while `scope_fingerprint` still hard-fails on raw header paths); (2)
  `old=v1/foo.h`/`new=v2/bar.h` (genuinely different header names) produce
  *different* `scope_fingerprint`s; (3) adding an identical, out-of-checkout
  `--include old=/opt/dep --include new=/opt/dep` alongside case (1)'s
  headers still leaves `scope_fingerprint` matching; (4) `--include
  old=old/include --include new=new/include` with byte-identical header
  content on both sides leaves `profile_fingerprint` matching — the
  routine two-checkout case every rejected attempt had to keep working;
  (5) `--include old=/opt/dep-v1/include --include new=/opt/dep-v2/include`
  with **genuinely different** header content produces *different*
  `profile_fingerprint`s — the case attempts one and two got wrong in
  opposite directions, now closed rather than documented as a gap; (6) a
  side declaring a project include **plus** a shared, byte-identical
  external dependency (`old=/work/v1/include` + `old=/opt/dep`,
  `new=/work/v2/include` + `new=/opt/dep`) leaves `profile_fingerprint`
  matching — the specific mixed-roots regression rejected attempt three
  introduced, now a permanent regression test rather than a live bug; (7)
  two dependency trees identical in every declaration-bearing header but
  differing in one macro-only header pulled in transitively (never itself
  the target of any declaration's `source_location`) produce *different*
  `profile_fingerprint`s — proving the digest is sourced from the depfile's
  full resolved-file list, not the narrower per-declaration set, the
  specific under-counting gap this design's own history exists to close;
  (8) the documented real-world shape — `--header old=old/include/foo.h
  --header new=new/include/foo.h --include old=old/include --include
  new=new/include`, the same directory serving both roles — with an
  **ordinary content edit to `foo.h` itself** (e.g. adding a parameter to a
  declared function) between old and new leaves **both**
  `scope_fingerprint` **and** `profile_fingerprint` matching: neither
  fingerprint hashes the declared header's own content — `scope_fingerprint`
  tracks declared TU/header *identity* (names, include structure, flags),
  never content, precisely so an ordinary API/ABI edit is an unremarkable,
  comparable case, not a mismatch; `profile_fingerprint` excludes the whole
  project-owned `-I` root per the bullet above, `foo.h` included. The
  comparison proceeds past the gate and the diff reports the parameter
  addition as an ordinary `Change` — `not_comparable` never fires on the
  routine case of "the thing being compared changed," which is this whole
  tool's primary purpose, not an edge case to special-case around; (8b)
  the same shape, but the edit lands in an **unnamed, project-internal
  support header** `foo.h` `#include`s (a `detail.h` never itself passed to
  `--header`) — renaming it (`detail_v1.h` → `detail_v2.h`) or editing its
  content, `foo.h` itself untouched — still leaves `profile_fingerprint`
  matching, proving the exclusion covers the *whole* project-owned `-I`
  directory, not only the explicitly-named header; excluding only the
  named file would still hash `detail.h`'s change and spuriously
  hard-fail this routine internal-refactor case; (9) a header reached only through a
  system/`-isystem`-classified include path (not a plain user `-I`) with
  genuinely different content between old and new produces *different*
  `profile_fingerprint`s — proving the depfile request uses `-MD`, not
  `-MMD`, since `-MMD` would omit a system-classified header from its
  output entirely and silently miss this difference, the exact regression
  `include_graph.py:354-356` already had to fix once for the L3 include
  graph and this digest must not reintroduce on its own, separate call
  site; (10) a header reached only via a probed toolchain `-isystem` dir or
  `--sysroot` — **not under any declared `-I` directory at all** — with
  genuinely different content between old and new still produces
  *different* `profile_fingerprint`s, proving the system/toolchain bucket
  actually attributes and hashes paths outside every declared `-I`
  directory rather than silently dropping them, the specific gap distinct
  from test (9)'s (which covers a system-*classified* path still reachable
  through a declared `-I`); (11) an old/new pair with byte-identical
  declared headers and `-I` directories, run from two different checkout
  roots (so `dumper.py`'s generated aggregate driver file necessarily
  embeds different absolute `#include` paths on each side), leaves
  `profile_fingerprint` matching — proving the generated driver TU is
  excluded from bucketing entirely, not swept into the system/toolchain
  bucket where its run-specific absolute paths would make every routine
  compare mismatch; (12) two declared `-I` lists with byte-identical
  directory contents but **swapped order** between a project-owned root
  and an external root — `old` declares `--include old=/work/include
  --include old=/opt/dep`, `new` declares the same two directories as
  `--include new=/opt/dep --include new=/work/include` (`/work/include`
  project-owned on both sides via a shared `--header`, `/opt/dep`
  byte-identical on both sides) — produce *different* `profile_fingerprint`s,
  proving the project-owned slot is replaced with a positional per-slot
  token rather than dropped: if the slot were simply omitted, both sides
  would degrade to the same single-element `[digest(/opt/dep)]` sequence
  and spuriously match despite the order swap being a genuine, ambiguity-
  affecting difference in `#include` search precedence; (13) **two**
  project-owned `-I` roots declared in swapped order — `old` declares
  `--header old=old/include/foo.h --header old=old/generated/bar.h
  --include old=old/include --include old=old/generated`, `new` declares
  the identical, byte-identical-content pair but swapped:
  `--include new=new/generated --include new=new/include` (both `foo.h`
  and `bar.h` unchanged) — produce *different* `profile_fingerprint`s,
  proving the per-slot token is derived from each root's own owned-header
  set (`token(foo.h)` vs. `token(bar.h)`), not one shared constant: a
  single generic sentinel for every project-owned slot would hash both
  orderings to the same `[SENTINEL, SENTINEL]` sequence and spuriously
  match, exactly the regression this per-slot-token fix (as opposed to
  the simpler single-sentinel fix test (12) alone would validate) exists
  to close; (14) a sibling support root declared via a labeled `--include`
  entry, not nested under any declared `--header` — `old`/`new` both
  declare `--header .../include/foo.h --include .../include --include
  both:support=.../src` (`src/` a sibling of `include/`, containing a
  private header `foo.h` `#include`s) — with a **content edit confined to
  `src/`'s private header** between old and new leaves `profile_fingerprint`
  matching, proving the labeled form extends project-ownership to a
  sibling directory the ancestor rule alone would otherwise classify as
  external (and thus spuriously flip on this routine internal edit); a
  companion assertion declares `--include old:support=old/src --include
  old=/opt/dep` vs. `--include old=/opt/dep --include old:support=old/src`
  (the labeled support root and an unrelated external root swapped in
  declaration order, same two directories, same label) and asserts
  *different* `profile_fingerprint`s, confirming the label-derived token
  participates in the same order-sensitive sequence as every other slot
  **and** that interleaving a labeled and an unlabeled `--include` entry
  preserves their true relative order — the exact guarantee a second,
  separately-named `--project-include` option could not have provided,
  since Click never records cross-option interleaving (P2 review: verified
  against Click's own parsing model — two differently-named repeatable
  options each get their own accumulated tuple with no relative-order
  record between them); a third assertion parses `--include
  old:support=old/src` directly and asserts the side/label/path triple
  round-trips correctly through `SidedIncludePathParam`, and that a bare
  `--include old=v1/foo.h` still round-trips to `(side="old", label=None,
  path=...)` unchanged; a fourth assertion parses `--include
  build/config=asan/include` (a bare, unprefixed value containing a
  literal `=`, no `old:`/`new:`/`both:` colon prefix) and asserts it still
  round-trips to `(side="both", label=None, path=Path("build/config=asan/include"))`
  — proving the labeled form is only ever triggered by the literal colon
  prefix, never inferred from an ambient `=` in an otherwise-ordinary
  path, the exact regression a bare-`LABEL=PATH` grammar reading would
  introduce (P2 review); a fifth assertion parses `dump --include
  old=foo/` through `LabeledIncludePathParam` (`dump`'s own type, not
  `SidedIncludePathParam`) and asserts it resolves to the literal
  directory `Path("old=foo/")`, unlabeled — proving `dump`'s type never
  strips an equals-form side prefix at all, the specific backward-compat
  break reusing `SidedIncludePathParam` on `dump` would have introduced
  (P2 review), alongside a companion assertion that `dump --include
  both:support=path` still parses to `(label="support", path=Path("path"))`
  through the same `dump`-specific type.
- **Modeling `contract` is not the same as populating it — this phase must
  do both.** `dump()` (`dumper.py`) is the one place that already resolves
  every input both fingerprints need; it calls
  `comparability.compute_extraction_contract(...)` and attaches the result
  to the `AbiSnapshot` it returns, for every dump (not only a manifest-
  driven one — Phase B). Without this, `contract` stays `None` on every
  freshly-produced snapshot, and since the gate below only ever raises when
  **both** sides carry one, two ordinary dumps would silently take the same
  code path as the intentionally-lenient mixed-pair case forever — a fully
  specified, fully inert gate.
- **The whole-snapshot cache is the same bypass by a different route — this
  phase closes it too, not just Phase E's later cache-key work.**
  `service_dump_cache.cached_run_dump` looks up `snapshot_cache` *before*
  calling `dump()`; a warm cache entry from a pre-Phase-A abicheck (no
  `contract` computed) served after upgrading would still come back with
  `contract=None`, defeating the fix above through a different code path.
  `snapshot_cache._SNAPSHOT_CACHE_VERSION` (`:48`, currently `"3"`) is
  bumped in this same phase so every pre-Phase-A cache entry misses once
  and gets rebuilt through the now-`contract`-populating `dump()`. This is
  separate from Phase E's later manifest-driven `scope_fingerprint`
  cache-key work (a different gap: pre-dump-knowable manifest fields the
  existing filesystem-only cache key can't see) and cannot be deferred to
  it without leaving the gate inert for every warm-cache user until Phase E
  ships.
- **A third cache gap, ongoing rather than one-time, also lands here:**
  `_cache_key()` (`snapshot_cache.py:159,168`) hashes `sorted(headers)`/
  `sorted(includes)` — order-*insensitive* — while D1's fingerprints are
  order-*sensitive* for the same inputs (`-I a -I b` vs. `-I b -I a`).
  Reordering flags between two runs can therefore hit the cache under the
  sorted key and return an `AbiSnapshot` whose `contract` fingerprints were
  computed once, for whichever order first populated that entry — never
  recomputed for the new order, since a cache hit skips `dump()` entirely.
  This phase drops `sorted(...)` for `headers`/`includes` in `_cache_key()`
  and hashes them in caller-supplied order instead; deferring this to
  Phase E doesn't help, since Phase E's cache-key work is scoped to a
  different, manifest-only gap (see Phase E) and wouldn't by itself make
  the existing sorted hashing order-preserving. **The `includes` key
  material must carry each entry's *label* too, not just its path — since
  `--include`'s labeled form (`old:LABEL=PATH`) folds the label into
  `includes` itself rather than a separate option, "hash `includes` in
  caller-supplied order" is only correct if that per-entry hash covers the
  full `(side, label, path)` triple.** Both the project-ownership
  predicate and the per-slot logical token depend on which directories are
  marked project-owned and under which label — two dumps with identical
  paths but a different label on the same directory (or the label present
  on one run and absent on the next, the path otherwise unchanged) would
  otherwise hit the same cache entry under a key that only ever hashed the
  path, and `cached_run_dump` would serve back a stale
  `contract.profile_fingerprint` computed under the old labeling — a
  warm-cache bypass of this whole phase's ownership fix, the identical
  class of gap already found and fixed above for a cold `contract=None`
  cache entry. `_cache_key()` hashes each `includes` entry's full
  `(side, label, path)` triple, not `path` alone, in caller-supplied order
  for the same reason it's no longer sorted.
- `serialization.SCHEMA_VERSION` is bumped (11 → 12) in the same change
  that starts writing `contract` — **not** treated as a free additive field
  the way ADR-041's advisory `extractor_passes`/`narrowed_passes` were. The
  bump alone is not sufficient: `snapshot_from_dict`'s existing
  newer-than-supported handling (`serialization.py:556-572`) only calls
  `warnings.warn(...)` and keeps deserializing — it never raises, so an old
  reader would print an easily-missed warning and still produce an ordinary
  verdict on a `contract`-bearing snapshot it can't check. This phase adds a
  real guard alongside the bump: a new
  `_MIN_SCHEMA_VERSION_REQUIRING_HARD_REJECTION = 12` constant (same naming
  convention as the existing `_MIN_SCHEMA_VERSION_FOR_CV_FACTS`) and a new
  `IncompatibleSnapshotSchemaError` (`errors.py`), raised by
  `snapshot_from_dict` *before* the existing warn-only branch when the
  snapshot's `schema_version` is **both greater than the running
  `SCHEMA_VERSION` and at or above the threshold** — not "the running
  version is below the threshold" alone, which stops protecting the
  moment a reader's own `SCHEMA_VERSION` reaches 12 (that reader would
  then silently warn-and-continue on a hypothetical future schema-13
  snapshot instead of correctly hard-rejecting it, moving the exact gap
  this guard closes one bump later rather than eliminating it). Versions
  below the threshold keep today's
  warn-and-continue behavior unchanged — only a jump that crosses a
  hard-rejection threshold, from either direction of the running/
  snapshot version pair, becomes a hard failure (ADR-050 D1).
- `comparability.check_contracts_comparable(old, new)` raises
  `ProfileMismatchError`/`ScopeMismatchError` (`errors.py`) **only when
  both sides carry a `contract`** and the fingerprints differ. A **mixed**
  pair (one side has a `contract`, the other doesn't) is unambiguous, not
  an implementer's judgment call: it takes the exact same code path as a
  pair where neither side has one — never hard-fails, never becomes
  `not_comparable` — so comparing a freshly-produced snapshot against a
  pre-ADR stored CI baseline (a common real workflow) never regresses on
  upgrade — **including under strict severity settings, not only the
  default ones.**
- **`UNKNOWN_PROFILE` is report-level metadata, not a `ChangeKind`/`Change`
  finding — this took two wrong designs to converge on, worth getting
  right the first time here.** Classifying it `RISK_KINDS` (matching
  `SOURCE_FACT_COVERAGE_INCOMPLETE`'s shape) broke under
  `--severity-potential-breaking=error`/`--severity-preset strict`
  (promotes to exit 2). Reclassifying it `COMPATIBLE_KINDS`'s
  `QUALITY_KINDS` instead only relocated the same collision:
  `--severity-quality-issues=error`/`--severity-preset strict` promotes
  `QUALITY_KINDS` too (exit 1) — proving no `ChangeKind` category is
  permanently severity-immune, since severity gating can reach any of them
  by design. `UNKNOWN_PROFILE` is instead a new field on the comparison
  result (e.g. `contract_coverage: "partial"`), alongside the existing
  `assurance` field this same phase adds for `--diagnostic-comparison` —
  never entering the `changes`/findings list any `--severity-*` flag scans,
  so it's structurally unreachable by severity promotion rather than
  merely unreachable by the flags checked so far.
- **The exit code is part of the published contract too, not an
  afterthought — and it must be a pinned, actually-free value, not "a new
  code TBD."** `docs/reference/exit-codes.md` documents two co-existing
  single-library `compare` schemes (legacy: 0/2/4; severity-aware:
  0/1/2/4) where `0` means *compatible* in both, **plus a separate
  release/multi-library table** (directory/package inputs) that already
  uses `0/2/4/8` — `8` is `--fail-on-removed-library`
  (`docs/reference/exit-codes.md:134-139`), not free. `not_comparable`
  must never exit `0` in either single-library scheme — otherwise the
  exact "missing evidence reads as safe" failure this ADR exists to
  prevent reappears at the process-exit boundary, undoing the JSON-level
  fix. This phase reserves exit code **`16`** (not `8` — that collides
  with the release table's existing removed-library code, a mistake an
  earlier draft of this criterion made by checking only the two
  single-library tables) — identical across all three tables, since
  `not_comparable` fires before severity classification or the
  removed-library check ever run — continuing the doubling pattern the
  existing codes already use, and adds it as its own row to all three
  tables in `docs/reference/exit-codes.md`, not folded into any existing
  scheme's numbering.
- **Release-level (directory/package) aggregation gets an explicit
  precedence against *two* existing mechanisms, not one.**
  `cli_compare_release_helpers.py`'s
  `_RELEASE_VERDICT_ORDER` (currently `NO_CHANGE` < `COMPATIBLE` <
  `COMPATIBLE_WITH_RISK` < `API_BREAK` < `BREAKING` < `ERROR`, rank 5 as
  the ceiling) gains `not_comparable` at rank 6, above `ERROR` — a
  correctly-diagnosed `not_comparable` result carries less trustworthy
  information about a library than even a partial `ERROR`, so it
  dominates the release-level "worst verdict wins" rollup over every other
  outcome in the same release, including a genuine crash. It also
  dominates the separate `--fail-on-removed-library` mechanism
  **unconditionally, in both schemes** — unlike that mechanism's own
  existing scheme-dependent precedence against `ERROR`/`2`/`4`: a
  `not_comparable` result means the comparison couldn't establish what
  changed at all, so an apparent "library removed" reading from an
  incomparable pair is an unproven inference, not a real removal finding
  entitled to its own exit code. This is what
  makes the release fan-out fix (below) actually surface at the release
  level instead of being computed per-library and then silently
  outranked.
- The gate is wired at **all seven** entry points in one phase, closing the
  gap AGENTS.md's "Known gaps" section already names for the depth
  contract rather than repeating the CLI-only mistake: `checker.compare`
  (core), `service.py`'s `ScanRequest`/`compare_snapshots`,
  `mcp_server.py`'s MCP compare tools, `cli_compare_release.py`'s
  directory/package fan-out, `compat/cli.py`'s ABICC-compatible
  `compat check` (which calls `checker.compare` directly too — see the
  dedicated bullet below for its own, independent exit-code contract),
  `cli_scan.py`'s `scan --against` (which reaches
  `compare_snapshots` through `cli_scan_baseline._run_baseline_compare` —
  see its own dedicated bullet below for why listing `service.py`'s
  `compare_snapshots` alone doesn't already cover it), **and**
  `stack_checker.py`'s `_run_abi_diff`, driving `abicheck deps compare`
  (which imports `checker.compare` directly too, and today swallows every
  exception — see its own dedicated bullet below). `appcompat.py`'s
  `check_appcompat`/`check_plugin_host_contract` reach the same gate
  (via `compare_snapshots`) but are deliberately **not** among these
  seven: per the dedicated `diagnostic_comparison` bullet below, letting
  the mismatch exception propagate is these two functions' documented
  default behavior (a `diagnostic_comparison` opt-in is added, but no
  outcome-conversion wrapper), not an oversight to close here.
- **The release fan-out needs a dedicated fix, not inherited behavior.**
  `_compare_one_library` (`cli_compare_release.py:180-269`) wraps its
  entire per-library flow in `except (click.ClickException,
  click.UsageError):` / `except Exception:`, both returning
  `{"verdict": "ERROR", ...}` — documented at `:1142` as flooring the
  release's exit code at 4 regardless of severity settings.
  `ProfileMismatchError`/`ScopeMismatchError` are plain exceptions, so
  today's broad `except Exception` would swallow them into that same
  `"ERROR"`/exit-4 bucket — one incomparable library in a release would
  silently report as the *worst possible* classification (an ABI break)
  instead of `not_comparable`, inverting this ADR's purpose on its one
  multi-library surface. `_compare_one_library` gains a dedicated
  `except (ProfileMismatchError, ScopeMismatchError) as exc:` branch,
  ordered before the generic `except Exception`, returning
  `{"verdict": "not_comparable", "reason": ...}`; `_RELEASE_VERDICT_ORDER`'s
  new rank-6 entry (the bullet above) is what makes that verdict actually
  win the release-level rollup instead of being computed correctly per
  library and then silently outranked by a co-occurring `BREAKING`, and
  `docs/reference/exit-codes.md`'s multi-library section documents both
  together.
- **This `"not_comparable"` string entry is a different JSON document from
  the canonical `verdict: null` shape, by design, not a second incompatible
  contract for the same shape.** `_compare_one_library`'s return dict feeds
  `summary.json`'s top-level `verdict` (`worst_verdict`) and its nested
  `libraries` array — both already string-only fields today (the existing
  `"ERROR"` case is exactly this: a non-`Verdict`-enum sentinel string, the
  same class `"not_comparable"` joins). That document was never governed by
  `compare_report.schema.json` — it's `cli_compare_release.py`'s own
  long-standing summary shape, extended in its own established idiom.
  Separately, when `--output-dir` is set, `_compare_one_library`'s success
  path *also* writes a full per-library report (`{stem}.json`) via
  `to_json(result)` — that file **is** governed by
  `compare_report.schema.json`, and for a `not_comparable` library must use
  the canonical `verdict: null` + `reason` shape, assembled the same way
  every other front-end's exception handler assembles it (there is no
  `DiffResult` to call `to_json` on). The two documents disagreeing in
  shape isn't an inconsistency to fix — each already followed its own
  distinct schema before this ADR existed. `aggregate.py`'s not-comparable
  detection (which reads whichever of these two shapes it's actually
  pointed at) must check both: `verdict is None` for a canonical
  `compare_report.schema.json` document, or `verdict == "not_comparable"`
  for a release `summary.json`/per-library entry.
- **A fifth entry point needs its own exit code, not the fourth one's.**
  `abicheck/compat/cli.py`'s ABICC-compatible `compat check` command calls
  `checker.compare` directly (`from ..checker import compare`, the call
  around `:967`) — a separate front-end from native `compare`, with its own
  independent 0–2/3–11 exit-code contract (`_classify_compat_error_exit_code`
  in `compat/_errors.py`) that this phase must not silently break by leaving
  a `ProfileMismatchError`/`ScopeMismatchError` to fall into that function's
  generic fallback code — or, worse, propagate out of the command entirely
  unclassified. **This is a real call-site change, not just a classifier
  update**: verified against the actual code, the `result = compare(old_snap,
  new_snap, ...)` call has no surrounding `try` today, unlike `check`'s other
  operations (descriptor parsing, logging setup, dump, report writing), each
  wrapped in its own narrow `except ...: _compat_fail(...)` block. This phase
  adds `try: result = compare(...) except (ProfileMismatchError,
  ScopeMismatchError) as exc: _compat_fail("comparing snapshots", exc)`
  around that call site so the new exceptions are ever caught at all, not
  only classified correctly once caught. `_classify_compat_error_exit_code` gains an explicit
  `isinstance(exc, (ProfileMismatchError, ScopeMismatchError))` check —
  mirroring its existing `KeyboardInterrupt` special case — returning **`9`**,
  the one integer the current 3–11 range documents no meaning for (3/4/5/6/7/8/10/11
  are all taken; 9 is the sole gap). This is deliberately a different number
  from native `compare`'s `16`: the two commands already use disjoint,
  independently-documented exit-code schemes (native `compare`'s legacy/severity-aware
  0/1/2/4 doubling vs. `compat check`'s ABICC-mimicking 0/1/2/3-11), so reusing
  `16` here would misleadingly imply a shared numbering that doesn't exist.
  `compat/CLAUDE.md`'s exit-code table and a changelog fragment are updated in
  the same phase, per that file's own stated policy that changing the
  exit-code contract "requires a CHANGELOG note and downstream coordination."
- **A sixth entry point reaches `compare_snapshots` through a different
  code path than the ones already named, with its own exit-code contract
  too.** `abicheck scan --against` calls `service.compare_snapshots` from
  `cli_scan_baseline._run_baseline_compare` (invoked from
  `scan_engine.run_scan_core` around `:852`) — `compare_snapshots` itself
  has no exception handling of its own (a thin wrapper over
  `checker.compare`), so `ProfileMismatchError`/`ScopeMismatchError`
  propagate through it cleanly, exactly as intended at the `service.py`
  boundary. The gap is one level up: `cli_scan.py`'s `scan_cmd` wraps its
  `run_scan_core` call in `try`/`except _BudgetOverflow`/`except
  _EvidenceContractError` only — verified against the actual code, neither
  clause catches `ProfileMismatchError`/`ScopeMismatchError`, so today they
  would propagate uncaught out of `scan_cmd` entirely, an unhandled
  traceback rather than any of `scan`'s own documented exit codes
  (`0`/`2`/`4`/`5`/`64`). `scan_cmd` gains a third `except
  (ProfileMismatchError, ScopeMismatchError) as exc:` branch alongside its
  existing two, exiting **`6`** — the next integer after `scan`'s own
  highest documented code (`5`), distinct from both native `compare`'s
  `16` and `compat check`'s `9` since all three commands maintain
  independent exit-code schemes; reusing either of those would imply a
  shared numbering `scan` doesn't have. `docs/reference/exit-codes.md`'s
  `scan` table gains this row.
- **`cli_scan.py`'s `scan_cmd` is not the only front-end wrapping
  `run_scan_core` — the typed Python API and MCP reach the same
  `_run_baseline_compare` call through a separate, unfixed path.**
  `service_scan.run_scan` (`:801-928`) is its own front-end over
  `run_scan_core`, with its own `try`/`except _BudgetOverflow`/`except
  _EvidenceContractError` — the identical gap as `scan_cmd`'s, on a
  different call site: `ProfileMismatchError`/`ScopeMismatchError` would
  propagate out of `run_scan` uncaught today. This matters beyond the
  Python API itself because `run_scan_subprocess`'s worker (`:982-985`)
  calls `run_scan(req).to_dict()` inside `except BaseException as exc:
  q.put(("err", f"{type(exc).__name__}: {exc}"))` — a fully generic
  catch-all that cannot distinguish a deliberate `not_comparable` result
  from any other crash, and `run_scan_subprocess` (`:1108-1135`) turns that
  into a bare `RuntimeError`. `mcp_server`'s `abi_scan` MCP tool goes
  through `run_scan_subprocess`, so an AI agent calling `abi_scan(...
  against=...)` on a mismatched pair would see an opaque worker-crash
  `RuntimeError`, not a structured not-comparable result — losing the
  ADR's semantics entirely on the one surface built specifically for
  agent consumption. `run_scan` gains a fourth `except
  (ProfileMismatchError, ScopeMismatchError) as exc:` branch alongside its
  existing two, returning `ScanResult(verdict="NOT_COMPARABLE",
  exit_code=6, ...)` — reusing `scan`'s own exit `6` rather than inventing
  a second code for the same command. Fixing `run_scan` alone closes both
  gaps: `run_scan_subprocess`'s worker calls `run_scan(...)` directly, so
  a `NOT_COMPARABLE` result now flows through its normal `q.put(("ok",
  ...))` path — no separate `run_scan_subprocess`/`mcp_server.py` change
  needed beyond that.
- **A seventh entry point imports `checker.compare` directly and swallows
  every exception into an undifferentiated `None` today — a different
  failure mode than the previous six, and it needs its own fix.**
  `stack_checker.py:32` imports `compare` from `checker` (not through
  `service.compare_snapshots`), driving `abicheck deps compare`.
  `_run_abi_diff` (`:396-410`) wraps its whole body — both the `dump()`
  calls and the `compare()` call — in one broad `except Exception as exc:
  log.warning(...); return None`. Verified against the actual code: a
  `ProfileMismatchError`/`ScopeMismatchError` from a changed dependency DSO
  would be swallowed into that same `None`, indistinguishable from the
  pre-existing "file unreadable" case a few lines above (`:363-364`, also
  `abi_diff=None`) or a genuine crash — the resulting `StackChange` carries
  no `not_comparable` reason at all, and `cli_stack.py`'s `deps compare`
  reporters/exit-code contract (`0`/`1`/`4`/`64`) read it no differently
  than "nothing to report for this library." `StackChange` (`stack_checker.py`)
  gains an additive `not_comparable_reason: str | None = None` field
  alongside its existing `abi_diff: DiffResult | None`. `_run_abi_diff`
  itself re-raises `ProfileMismatchError`/`ScopeMismatchError` instead of
  swallowing them (only its caller can attach a result to a `StackChange`);
  its caller (the loop building `StackChange` entries) gains a dedicated
  `except (ProfileMismatchError, ScopeMismatchError) as exc:` branch around
  the `_run_abi_diff(...)` call, setting `not_comparable_reason` instead of
  leaving `abi_diff` an unexplained `None`. `deps compare` gains its own
  exit code for "at least one dependency was not_comparable": **`5`**, the
  next integer after that command's own currently-documented ceiling (`4`,
  `FAIL`) — distinct from `scan`'s `6`, `compat check`'s `9`, and native
  `compare`'s `16`, continuing the same disjoint-per-command scheme rule,
  never folded into the existing `FAIL`/`4`.
- Reporting: `reporter.py`/`sarif.py`/`junit_report.py` gain a
  `not_comparable` top-level result distinct from every existing verdict
  value — never coerced into `compatible`/`breaking`.
- **`abicheck aggregate` consumes these reports in CI and has its own blind
  spot this phase must close, not just the commands that produce them.**
  `aggregate.py:589-596`'s `parse_report_verdict` returns `None` whenever
  `verdict` isn't a string — true for `verdict: null` by design, but also
  true for a missing or corrupt report, and today nothing distinguishes
  the two: both become the same `compatibility_verdict=None`/"unavailable"
  `TargetReport`. Verified against the actual code: in **discovered-only**
  mode, `coverage_blocking` is unconditionally `False`
  (`aggregate.py:406-410`, `and not self.discovered_only`), and an
  unavailable target's `gate` is `None`, so it contributes nothing to
  `exit_code()`'s `max(...)` — a `not_comparable` target can silently
  reduce the whole aggregate run to exit `0`, resurfacing the "missing
  evidence reads as safe" failure this ADR exists to prevent, at the one
  consumer surface untouched so far. `aggregate.py` gains a way to tell a
  deliberate `not_comparable` report (its `reason` object is present) from
  a genuinely missing/corrupt one, and folds it into `exit_code()` as an
  unconditionally blocking contribution — regardless of `discovered_only`.
  **The resulting code is pinned to `1`, not left to whatever an
  implementer picks.** `docs/reference/exit-codes.md`'s own `aggregate`
  table already establishes the precedent this fits, not a new one:
  `1` is documented as covering both a coverage gap *and* "a non-verdict
  per-report failure" (its own example being `scan`'s budget-overflow
  exit `5` folding in there) — `not_comparable` is exactly that same class
  of non-verdict per-report failure, so it folds into `aggregate`'s
  existing `1`, the same way `scan`'s `5` already does, rather than
  reserving a fifth disjoint number the way `compare`/`scan`/`deps compare`
  each did for their *own* schemes (`aggregate` never invents a new code
  per producer; it has one shared bucket for "this target isn't a clean
  verdict"). `docs/reference/exit-codes.md`'s `aggregate` table gains an
  explicit `not_comparable` row alongside the existing budget-overflow
  example, and its `## Summary table` cross-command matrix gains a row
  too, so both are updated in the same change, not just the aggregate
  section in isolation.
- **`action/run.sh` is another consumer with the same blind spot, one layer
  further from the Python package, and it compounds into a worse failure
  than a generic misreport.** Verified against the actual script:
  `action/run.sh` maps each command's exit codes to a `VERDICT` string via
  `case` statements ending in an unconditional `*) VERDICT="ERROR"`
  fallback — native `compare`'s exit codes are matched at `:680-698` (no
  `16` case), `scan`'s at `:656-666` (no `6` case), `deps compare`'s at
  `:628-634` (no `5` case), so all three new codes fall through to
  `VERDICT="ERROR"` today. `_maybe_post_pr_comment` (`:897`) then
  unconditionally returns early when `VERDICT == "ERROR"` (`:907`) — so a
  deliberate `not_comparable` result would both misreport as a generic
  internal error *and* silently suppress the one PR comment meant to
  surface it, on the Action's most visible first-party consumer. Each
  `case` statement gains a matching branch (e.g. `16) VERDICT="NOT_COMPARABLE"
  ;;`), and `_maybe_post_pr_comment`'s `ERROR`-only skip is joined by an
  explicit exception that still posts for `NOT_COMPARABLE` — this result
  deserves the comment more than an ordinary pass, not less.
  **Mapping the `VERDICT` string alone is not enough — the script's final
  exit-code section never checks it, so the step still exits `0`.**
  Verified against the actual code: `run.sh`'s final gate (`:1074-1145`)
  starts `FINAL_EXIT=0` and only ever sets it to `1` inside branches keyed
  on `ERROR`, or on `BREAKING`/`API_BREAK`/`FAIL`/`WARN`/`REMOVED_LIBRARY`/
  `SEVERITY_ERROR`/`BUDGET_OVERFLOW` gated by their respective
  `fail-on-*` inputs, across the `compare`/`scan`/`deps-compare`/`dump`
  branches — there is no `NOT_COMPARABLE` check anywhere in that section.
  Adding only the `case`-statement mapping above means `VERDICT` now
  correctly *reads* `"NOT_COMPARABLE"`, but every mode's final-exit branch
  still falls through unmatched and leaves `FINAL_EXIT=0` — a `compare`/
  `scan`/`deps-compare` step whose underlying CLI exited `16`/`6`/`5` would
  still report a **green** composite Action step, silently downgrading a
  result that (per D2/the release-verdict-order fix elsewhere in this
  plan) is supposed to be *more* blocking than an ordinary break, not
  less. `NOT_COMPARABLE` gets its own unconditional check, alongside (not
  gated by) the existing `ERROR` check at the top of the final-exit
  section — not folded into any mode-specific `fail-on-*`-gated branch,
  since (matching `ERROR`'s own treatment) whether a comparison could even
  be attempted is not something a `fail-on-breaking`/`fail-on-api-break`
  toggle should be able to opt out of.
- **`html_report.py`/`service_render.py` are the fourth reporting surface,
  not an optional add-on.** AGENTS.md's own module map groups `html_report.py`
  with `reporter.py`/`sarif.py`/`junit_report.py` under "Reporting," and
  `service_render.py:87-99` routes `--format html` to
  `generate_html_report(result: DiffResult, ...)` the same way it routes the
  other three formats. Since `generate_html_report` requires a real
  `DiffResult`, the `not_comparable` case (no `DiffResult` ever constructed)
  means `render_output` must not call it at all on that path — the
  gate-raising front-end handles HTML the same way it assembles `verdict: null`
  JSON, without `generate_html_report` growing an optional-`DiffResult`
  parameter. The mixed-pair `contract_coverage` case does produce a real
  `DiffResult`, so `generate_html_report` gains a headline-card surface for
  `contract_coverage`, matching the other three reporters — otherwise HTML is
  the one format that can't tell a reader the comparison ran on unequal
  evidence.
- **`verdict: null` is JSON-output shape, not a `checker_types.DiffResult`
  typing change.** `DiffResult` (`verdict: Verdict = Verdict.NO_CHANGE`,
  non-nullable) is never constructed at all for a
  `ProfileMismatchError`/`ScopeMismatchError` case — the gate raises before
  any diff runs, so each front-end's own exception handler assembles the
  `verdict: null` JSON shape; `DiffResult` itself needs no new field for
  that path. The **mixed-pair** case (below) is different: it's an ordinary,
  non-exception comparison that completes and produces real `DiffResult`s,
  just with reduced evidence on one side — so `contract_coverage` is a
  genuinely new field, not report-assembly shape. `checker_types.py` gains
  `contract_coverage: str | None = None` on `DiffResult` itself, and
  `checker.py`'s `compare()` sets it when exactly one side carries a
  `contract`.
- **`assurance` (the `--diagnostic-comparison` stamp) is also a `DiffResult`
  field, not a per-`Change` one.** A forced diagnostic comparison is
  uniformly tentative — the gate failed for the pair as a whole before any
  diff ran, so every finding a tentative diff produces shares one identical
  reduced-assurance reason; there is no per-finding split to encode, and
  `checker_types.Change` gains no new field for this. `checker_types.py`
  gains `assurance: str | None = None` on `DiffResult` itself (alongside
  `contract_coverage`), set to `"none"` only on the `--diagnostic-comparison`
  path.
- The published JSON contract moves with the reporters, in this phase, not
  after: `abicheck/schemas/compare_report.schema.json` currently requires
  `verdict` and restricts it to a fixed string enum with no `null` member.
  It's updated to allow `verdict: null` alongside the new `not_comparable`
  state (and a `reason` object), and `tests/test_report_schema.py` — which
  already validates emitted reports against this exact file — gains a case
  for a `not_comparable` report. Shipping the reporter change without this
  either emits JSON that fails its own published schema, or ships a stale
  schema — not an acceptable outcome for either. The schema's own version
  metadata moves in lockstep, not as an afterthought:
  `abicheck/schemas/__init__.py`'s `REPORT_SCHEMA_VERSION` (currently
  `"2.12"`, emitted in every report as `report_schema_version`) is bumped,
  and the published mirror `docs/schemas/v1/compare_report.schema.json` is
  regenerated via the existing `scripts/publish_schemas.py` — skipping
  either fails `tests/test_report_schema.py::test_docs_mirror_matches_packaged_schema`,
  which already asserts the two stay byte-identical.
- `--diagnostic-comparison` opt-in flag: downgrades the hard-fail to a
  tentative diff, the whole result stamped `assurance: "none"` — a single
  `DiffResult`-level field (see the dedicated bullet below), never a
  per-finding one.
- **This must be a parameter into `compare()`, not a CLI-level catch around
  it — a post-hoc recovery is structurally impossible.** The gate runs at
  the top of `checker.compare`, before any `diff_*` module runs; once it
  raises, no `DiffResult` exists yet for anything to recover. A CLI
  `except (ProfileMismatchError, ScopeMismatchError)` around `compare()`
  has nothing left to downgrade into a tentative diff — it can only report
  the failure. `--diagnostic-comparison` therefore threads to the gate
  check itself: `checker.compare(..., diagnostic_comparison: bool = False)`
  passes the flag to `comparability.check_contracts_comparable(old, new,
  diagnostic=diagnostic_comparison)`, which — only when set — returns a
  mismatch descriptor instead of raising, letting `compare()` run the
  normal `diff_*` pipeline and stamp `assurance: "none"` on the resulting
  `DiffResult` afterward. `service.compare_snapshots` (a thin
  keyword-argument wrapper over `checker.compare`, not a request
  dataclass) gains the same `diagnostic_comparison` keyword.
- **`compare_snapshots` is not the front-end chokepoint — `api_types.CompareRequest`
  is, and it needs the field too, or `CompareRequest`-based front-ends stay
  unable to reach it.** `CompareRequest` (`api_types.py:125`) is, by its
  own docstring, "the single input to `run_compare`" that "every front-end
  (CLI, MCP, `compare-release` fan-out, `appcompat`)" assembles and hands
  to `service.run_compare_request` — the real ADR-037 D1/D2 classification
  chokepoint, one level above `compare_snapshots`, for the front-ends that
  actually use it. **That docstring's own "`compare-release` fan-out" and
  "`appcompat`" claims don't hold up against the actual code, verified
  directly: `mcp_server.abi_compare` and `appcompat.py`'s
  `check_appcompat`/`check_plugin_host_contract` call `compare_snapshots`
  directly, and `cli_compare_release.py`'s `_compare_one_library` →
  `_run_compare_pair` routes through the legacy `run_compare` keyword shim,
  not `run_compare_request` — so a `CompareRequest`/`run_compare_request`
  reachability test proves the flag reaches CLI/MCP `compare`'s own
  chokepoint only, never compare-release or appcompat.** Each of those
  three gets its own dedicated `diagnostic_comparison` parameter and test
  in this same phase (see their respective bullets/tests elsewhere in this
  plan) — narrowing what this bullet's fix actually covers, not extending
  it by assertion. `run_compare_request`
  calls `compare_snapshots(...)` today with a fixed keyword list that has
  no slot for this flag; adding `diagnostic_comparison` only to
  `compare_snapshots` would be unreachable from `CompareRequest`-based
  front-ends specifically. `CompareRequest`
  therefore gains `diagnostic_comparison: bool = False`, and
  `run_compare_request` passes `request.diagnostic_comparison` through.
  The legacy `run_compare` keyword shim gains the same parameter too,
  appended after every pre-existing one — matching the precedent already
  set for `debuginfod_url`, so a positional caller's bindings don't shift.
- **Rollout: the hard gate is the default from the first shipped version of
  this phase — no soft-launch flag, and no second flag with a default that
  contradicts ADR-050 D2.** D2 is explicit that a contract mismatch is a
  precondition failure producing `not_comparable`, never an ordinary
  verdict with a RISK-tier finding attached; a runtime default that quietly
  downgraded that to "warn, still produce a verdict" would ship exactly the
  behavior the ADR forbids. The two things that *do* need to be true before
  this phase merges — real-world fingerprint false positives from an
  overlooked resolved-field gap must not exist in practice — are handled at
  merge-review time, not at runtime: Phase 0's fixture corpus must cover the
  common drift/no-drift cases, and a dry run over a real multi-snapshot
  corpus using the **already-specified** `--diagnostic-comparison` flag
  (D2's one sanctioned escape hatch, not a new one) must show zero
  unexpected mismatches before Phase A is considered done. Backward
  compatibility for every *existing* baseline is unaffected regardless,
  since a snapshot with no `contract` field (everything produced before
  this phase) compares exactly as it does today (see the bullet above) —
  there is no legacy flow this gate could break on day one, unlike
  ADR-041's header-graph flag flip, which changed behavior for an
  already-common default-off-to-on transition.

**Files & surfaces.** `cli_params.py` (new `SidedIncludePathParam` —
`[old:|new:|both:]LABEL=PATH` layered on the existing
`[old=|new=|both=]PATH` grammar, returning `(side, label, path)` with
`label=None` for every unlabeled form — a dedicated param type used only
by `--include`'s own Click option, distinct from the unchanged, 2-tuple
`SidedPathParam` `--header`/`--sources`/etc. keep, see the
sibling-support-root acceptance-criteria bullet above), `cli_options.py`
(`--include`'s existing option switches `type=` from `SIDED_PATH_PARAM`
to the new param type; a new `split_sided_include_paths` function sits
alongside the existing `split_sided_paths` — shared by `--header` and
plain, unlabeled `--include` — and additionally returns a
`dict[Path, str]` of resolved path → label for whichever entries carried
one, while still returning the same flat `(both, old_only, new_only)`
`Path` tuples `split_sided_paths` does, so the compiler-argv-building code
that already consumes those tuples is unchanged). **`--include` has two
more, separate registrations that need the identical change, not covered
by `cli_options.py` alone** (see the dedicated acceptance-criteria bullet
above for why): `cli_scan.py` (`scan_cmd`'s own inline `--include`
(`include_pairs`, `:487-495`) switches `type=` to `SidedIncludePathParam`
too, and its `split_sided_paths(include_pairs)` call (`:712`) becomes
`split_sided_include_paths(include_pairs)`), `cli.py` (`dump_cmd`'s own
inline `--include` (`:486`, plain `click.Path` today) switches to a new,
`dump`-specific `LabeledIncludePathParam` instead — not
`SidedIncludePathParam` itself, which would silently start stripping
`old=`/`new=`/`both=` off a `dump --include` value for the first time
ever, breaking a directory literally named that way; `dump`'s type
recognizes only the colon-terminated `both:LABEL=PATH` label form and
leaves every other value, including one that happens to start with
`old=`/`new=`/`both=`, untouched).
**Getting the label out of Click's parsed value is not enough — it still
needs its own path down to `comparability.compute_extraction_contract`,
the same per-layer threading already required (and already found
missing, twice) for `--dump-manifest`/`--diagnostic-comparison`/
`--frontend-context` in this same phase/plan, and this bullet was the one
surface that skipped that step on the first pass.** `dumper.dump()`'s
`extra_includes: list[Path]` parameter (`dumper.py:1119`, and every
internal per-TU helper it threads through, e.g. `:152,264,453,813,994`)
stays a bare `Path` list, deliberately unchanged — rippling a label into
every one of those internal helpers would be a far wider blast radius
than this fix needs, since only the top-level `compute_extraction_contract`
call actually reads labels. `dump()` instead gains one new, separate,
optional parameter, `project_include_labels: dict[Path, str] | None =
None`, passed alongside `extra_includes` (not folded into it) straight
into `compute_extraction_contract(extra_includes, headers,
project_include_labels=project_include_labels, ...)`. That parameter is
threaded down the same chains already established for `--dump-manifest`
and `--frontend-context` in this phase: `cli_compare_helpers.run_compare`
(gains the parameter itself; `compare_cmd` forwards `**kwargs`, so Click
already passes it through once `run_compare`'s signature names it) →
`cli_resolve._resolve_compare_snapshots` → `cli_resolve._resolve_input` →
`service.resolve_input`/`run_dump` for `compare`; `dump_cmd`/`scan_cmd`
(themselves fixed Click callbacks Click invokes directly, each gaining
the parameter in their own signature) → `cli_dump_helpers`/`scan_engine`
→ `service.resolve_input`/`run_dump` for `dump`/`scan` — terminating in
`dumper.dump()`. Without this, the sibling support-root acceptance tests
(test (14) below) either fail with an "unexpected keyword argument" at
the Click-callback boundary or silently run with the label parsed by
Click but never reaching the predicate — the labeled `--include` form
would parse successfully and do nothing, the same silent-loss failure
mode already caught once for `--diagnostic-comparison` on `run_compare`
in this phase.
`model.py` (new `ExtractionContract`), new
`abicheck/comparability.py` (fingerprint computation, `compute_extraction_contract`
— its project-ownership predicate takes both ancestor-derived headers and
the new `project_include_labels` mapping as inputs, not headers alone —
the gate, and `contract_coverage` metadata computation — no detector, since
`UNKNOWN_PROFILE` is report metadata, not a `Change`; reuses
`abicheck/buildsource/include_graph.py`'s existing `parse_depfile()` to turn
a per-TU `-MD` depfile (system-inclusive, never `-MMD` — see the
acceptance-criteria bullet above) into the per-`-I`-directory resolved-file
lists `profile_fingerprint` hashes), `dumper_castxml.py`/`dumper_clang.py`
(request a `-MD` depfile alongside the AST dump for every L2 invocation, not
only when `buildsource` L3 evidence is also being collected), `dumper.py` (`dump()`
calls `compute_extraction_contract(...)` and attaches it to every returned
snapshot — see the acceptance-criteria bullet above; this is not optional
plumbing), `snapshot_cache.py` (`_SNAPSHOT_CACHE_VERSION` bump and the
`_cache_key()` order-preserving `headers`/`includes` hashing — see the two
warm-cache acceptance-criteria bullets above), `errors.py`
(`ProfileMismatchError`/`ScopeMismatchError`/`IncompatibleSnapshotSchemaError`),
`serialization.py` (`SCHEMA_VERSION` bump,
`_MIN_SCHEMA_VERSION_REQUIRING_HARD_REJECTION` threshold + the
`snapshot_from_dict` hard-rejection branch, `contract` round-trip through
`snapshot_to_dict`/`snapshot_from_dict`), `checker.py` (gate call at the
top of `compare`, new `diagnostic_comparison: bool = False` parameter
threaded to `comparability.check_contracts_comparable`, `contract_coverage`
field on the result), `comparability.py` (`check_contracts_comparable`'s
new `diagnostic` keyword — returns a mismatch descriptor instead of raising
when set),
`checker_types.py` (`DiffResult.contract_coverage: str | None = None` and
`DiffResult.assurance: str | None = None`),
`service.py` (`compare_snapshots`'s new `diagnostic_comparison` keyword,
threaded to `compare()`; `run_compare_request` passes
`request.diagnostic_comparison` into its `compare_snapshots(...)` call —
**not optional**, since `CompareRequest`/`run_compare_request` is the real
front-end chokepoint, not `compare_snapshots` itself, see the
acceptance-criteria bullet above; the legacy `run_compare` keyword shim
gains the same parameter appended last, matching the `debuginfod_url`
precedent), `api_types.py` (`CompareRequest.diagnostic_comparison: bool =
False`), `mcp_server.py` (**`abi_compare`'s inner `_do_compare` calls
`compare_snapshots(...)` directly, bypassing `CompareRequest`/
`run_compare_request` entirely** — verified against the actual code, its
`future.result(...)` is awaited under a narrow `except
_futures.TimeoutError` with a broader `except Exception` catching
everything else into `{"status": "error", ...}` today; exposing
`diagnostic_comparison` as an input parameter is not enough on its own —
`abi_compare` also gains a dedicated `except (ProfileMismatchError,
ScopeMismatchError)` branch, ordered before that generic catch, rendering
`{"status": "not_comparable", "reason": ...}` distinct from
`{"status": "error"}` for the default hard-fail path). **`appcompat.py` is a
third, independent instance of this exact bypass, not covered by fixing
`CompareRequest`/`run_compare_request` or `mcp_server.py` alone — verified
against the actual code:** `check_appcompat` (`:1018`) and
`check_plugin_host_contract` (`:1227`) each call `service.compare_snapshots(...)`
directly (`:1078`, `:1248`), with no surrounding `try` today, no
`CompareRequest` in either call path, and no route through `run_compare_request`
at all — these are themselves public Python-API entry points (documented,
user-callable functions, not internal helpers), so a mismatched pair raises
a raw `ProfileMismatchError`/`ScopeMismatchError` straight out of them,
uncaught, with no `AppCompatResult`/`PluginHostContractResult` to carry a
`not_comparable` outcome (both dataclasses' `full_diff: DiffResult | None`
has nowhere to put a result when the gate raises *before* any `DiffResult`
exists). Both functions gain a `diagnostic_comparison: bool = False`
parameter, forwarded into their own `compare_snapshots(...)` calls exactly
like `run_compare_request`'s equivalent parameter — giving a caller of the
Python API the same tentative-diff escape hatch every other front-end
gets. Raw exception propagation remains the *default* (undiagnosed) behavior
for both functions, same as any other direct `compare_snapshots` caller
that doesn't opt in — this phase documents that explicitly as these two
functions' intended contract (in their docstrings) rather than leaving it
implicit, closing the ambiguity Codex flagged rather than silently
choosing one reading. `cli.py`
(the `--diagnostic-comparison` flag definition and the new, distinct
`not_comparable` exit `16` constant), **`cli_compare_helpers.py`** (the new
`except (ProfileMismatchError, ScopeMismatchError)` branch around the real
`compare_snapshots(...)` call in `run_compare` — verified against the
actual code: `cli.py`'s `compare_cmd` is a thin `**kwargs` forwarder to
`cli_compare_helpers.run_compare`, which is where `compare_snapshots(...)`
is actually called (`:1464`) and where the output/exit-code rendering
(`_finalize_compare_result` etc.) lives; a bare exception there today would
propagate as an unhandled traceback, not the planned `verdict: null`
report + exit `16` — `cli.py` alone has nothing to catch). **The exception
branch alone does not make `--diagnostic-comparison` reachable — `run_compare`
must also accept and forward the flag itself, the same gap already found
and fixed once for `--dump-manifest` on this exact function.** `run_compare`
(`:992-1057`) has its own large, explicit, fixed keyword-argument signature
with no `diagnostic_comparison` slot today; `compare_cmd` forwards `**kwargs`,
so Click happily passes the new dest through, but `run_compare`'s own
`compare_snapshots(...)` call (`:1464`) only passes what its signature
already names — a `--diagnostic-comparison` value that reaches `run_compare`
via `**kwargs` and is never read by name inside the function body is silently
dropped before it ever reaches `compare_snapshots`, not rejected (there is no
`**kwargs`-passthrough to `compare_snapshots` to raise on an unused key), so
this is a silent-loss bug, not a crash — harder to notice than the
`--dump-manifest` version of this same mistake, which at least raised
"unexpected keyword argument." `run_compare` therefore gains a
`diagnostic_comparison: bool = False` parameter, passed by name into the
`compare_snapshots(diagnostic_comparison=diagnostic_comparison, ...)` call
at `:1464`, mirroring how `old_dump_manifest`/`new_dump_manifest` are
threaded through this same function. **`cli_compare_release.py` needs the
`diagnostic_comparison` parameter itself threaded, not just an exception
branch — it does not go through `CompareRequest`/`run_compare_request` at
all, so the `run_compare_request` reachability test elsewhere in this plan
proves nothing about this path.** Verified against the actual code:
`_compare_one_library` calls `_run_compare_pair` (`:91-141`), whose own
docstring states it "routes through the single Tier-2 chokepoint
(`service.run_compare`, ADR-037 D1)" — the *legacy keyword shim*, not
`run_compare_request`/`CompareRequest`. `_run_compare_pair`'s own fixed,
explicit signature has no `diagnostic_comparison` slot today, and its
`service.run_compare(...)` call (`:124-141`) only forwards what it already
names — so even with `service.run_compare` itself gaining the parameter
(above), a value would never reach it through this path without both
`_run_compare_pair` and `_compare_one_library` also naming it explicitly
and threading it through, the identical multi-layer gap already found
(twice) for `--dump-manifest`/`--frontend-context` elsewhere in this
phase. `cli_compare_release.py`
(`_compare_one_library`'s dedicated
`except (ProfileMismatchError, ScopeMismatchError)` branch, ordered before
`except Exception` — see the release-fan-out acceptance-criteria bullet
above; this is not covered by the CLI's own exit-code handling, it is a
separate call path — **plus** `_run_compare_pair`'s and
`_compare_one_library`'s own new `diagnostic_comparison: bool = False`
parameters, threaded into `_run_compare_pair`'s `service.run_compare(...)`
call), `cli_compare_release_helpers.py`
(`_RELEASE_VERDICT_ORDER`'s new rank-6 `not_comparable` entry),
`compat/cli.py` (new `try`/`except (ProfileMismatchError, ScopeMismatchError)`
wrapping the `compare()` call — there is no surrounding `try` there today —
routing to the updated `_classify_compat_error_exit_code` via `_compat_fail`),
`compat/_errors.py`
(`_classify_compat_error_exit_code`'s new `ProfileMismatchError`/
`ScopeMismatchError` branch returning `9`), `compat/CLAUDE.md` (exit-code
table update), `cli_scan.py` (`scan_cmd`'s new third `except
(ProfileMismatchError, ScopeMismatchError)` branch exiting `6`, alongside
its existing `_BudgetOverflow`/`_EvidenceContractError` clauses — no change
needed in `cli_scan_baseline.py`/`scan_engine.py` themselves, since
neither catches these exceptions today and both must keep letting them
propagate), `service_scan.py` (`run_scan`'s new fourth `except
(ProfileMismatchError, ScopeMismatchError)` branch returning
`ScanResult(verdict="NOT_COMPARABLE", exit_code=6, ...)`, alongside its
existing `_BudgetOverflow`/`_EvidenceContractError` clauses — no separate
`run_scan_subprocess`/`mcp_server.py` change needed, since the worker
already calls `run_scan(...)` and forwards whatever `ScanResult` it
returns), `stack_checker.py` (`StackChange.not_comparable_reason`
field; `_run_abi_diff` re-raises `ProfileMismatchError`/`ScopeMismatchError`
instead of swallowing them into its broad `except Exception`; its caller
gains the dedicated `except` branch that sets `not_comparable_reason`),
`cli_stack.py` (`deps_compare_cmd`'s new exit-`5` branch alongside its
existing `0`/`1`/`4`/`64` logic), `docs/reference/exit-codes.md` (a
new row in both the legacy and severity-aware `compare` tables, the
multi-library section, the `compat check` table's `9` row, the
`scan` table's `6` row, the `deps compare` table's `5` row, **and** the
`## Summary table` cross-command matrix — a `not_comparable` row spanning
all six of its columns, or the per-command detail tables gain their new
codes while the one table meant to summarize them across commands goes
stale the same day), `reporter.py`,
`sarif.py`, `junit_report.py`, `html_report.py` (`generate_html_report`'s
`contract_coverage` headline card). **`service_render.render_output`
has five branches that require a real `DiffResult` — `sarif`, `html`,
`junit`, `review`, and the default `markdown` — not just `html`; every
one needs the identical bypass, not only the one this bullet previously
named.** Verified against the actual code (`service_render.py:36-132`):
`to_sarif_str(result, ...)`, `generate_html_report(result, ...)`,
`to_junit_xml(result, ...)`, `to_review_digest(result, ...)`, and the
default `to_markdown(result, ...)` each take `result` as a required,
non-`Optional` `DiffResult` positional argument — on the `not_comparable`
path (no `DiffResult` ever constructed), calling any of them the normal
way crashes or requires inventing a synthetic empty `DiffResult`, neither
of which is what this ADR intends. The front-end's own exception handler
therefore renders the not-comparable outcome directly, per format, the
same way it already assembles `verdict: null` JSON, instead of calling
`render_output` at all on this path: **SARIF** gets one `run` with
`invocations[0].executionSuccessful: false` and a
`toolExecutionNotifications` entry carrying the not-comparable reason —
SARIF's own defined mechanism for "the tool didn't complete analysis,"
so a SARIF consumer (e.g. GitHub Code Scanning) can't misread an empty
`results` array as a clean pass; **JUnit** gets one `<testsuite>` with a
single `<testcase>` wrapping an `<error message="...">` (not
`<failure>` — JUnit's own convention for "the test itself couldn't run,"
distinct from an ordinary reported ABI break, which stays a `<failure>`);
**markdown/review** (human-readable, no schema to satisfy) get a plain
"NOT COMPARABLE: `<reason>`" summary line. `service_render.py`
(`render_output`'s `sarif`/`html`/`junit`/`review`/default-`markdown`
branches are all skipped on the `not_comparable` path, not just `html`),
`abicheck/schemas/compare_report.schema.json`,
`abicheck/schemas/__init__.py` (`REPORT_SCHEMA_VERSION` bump),
`docs/schemas/v1/compare_report.schema.json` (regenerated via
`scripts/publish_schemas.py`, not hand-edited), `aggregate.py`
(`parse_report_verdict`/`GateInfo`/`TargetReport` gain a way to
distinguish a deliberate `not_comparable` report from a missing/corrupt
one, and `exit_code()`/`coverage_blocking` treat it as unconditionally
blocking, independent of `discovered_only`), `action/run.sh` (a new
`VERDICT="NOT_COMPARABLE"` case-branch alongside each of the `compare`/
`scan`/`deps compare` exit-code `case` statements, and an explicit
carve-out in `_maybe_post_pr_comment` so `NOT_COMPARABLE` still posts a
comment despite the existing `ERROR`-only skip).

**Tests.** A `dump()`-level test asserting a real (non-manifest) dump
returns a snapshot with a populated, non-`None` `contract` — the specific
gap that would otherwise leave the gate permanently inert. A **warm-cache**
regression test: seed `snapshot_cache` with a pre-bump-version entry (no
`contract`), call `cached_run_dump` for the same inputs post-bump, and
assert it misses and rebuilds with `contract` populated rather than
serving the stale hit — the cache-layer analogue of the `dump()` test
above, closing the same class of bypass through a different code path. A
**cache order-sensitivity** test: call `cached_run_dump` with
`includes=[a, b]`, then again with `includes=[b, a]` (same set, reordered)
— assert the second call is a cache **miss**, not a hit serving the first
call's `contract.profile_fingerprint`, proving `_cache_key()` no longer
sorts these inputs away. Unit tests for fingerprint stability (same manifest,
independent-TU reordering unaffected; include-order-within-a-TU changes
the fingerprint; flipping one TU's `contributes_to_abi` or `required` flag
with its includes held identical also changes `scope_fingerprint`); a
hard-rejection test asserting a pre-bump reader (a stubbed/patched
`SCHEMA_VERSION` below the threshold) raises `IncompatibleSnapshotSchemaError`
on a schema-12 `contract`-bearing snapshot instead of the pre-existing
warn-and-continue path; a **second** hard-rejection test asserting a
reader whose own `SCHEMA_VERSION` is *already* 12 (at the threshold, not
below it) still raises `IncompatibleSnapshotSchemaError` on a stubbed
future schema-13 snapshot — the specific case a "running version below
threshold" condition would silently stop protecting, per the corrected
`>` running-version comparison above; a regression test pinning that a
schema bump *below* the threshold still only warns (today's lenient
behavior for ordinary additive fields must not become accidentally
stricter);
`tests/test_report_schema.py` gains a `not_comparable` case validated
against the updated `compare_report.schema.json`, and its existing
`test_docs_mirror_matches_packaged_schema` must still pass against the
regenerated `docs/schemas/v1` copy; a **multi-format not_comparable
reporting** test suite asserting `render_output(..., fmt=...)` on a
`not_comparable` path is never reached at all — for **each** of `sarif`,
`html`, `junit`, `review`, and the default `markdown`, not only `html` —
proving the front-end's own exception handler owns every one of those
paths instead of calling `to_sarif_str`/`generate_html_report`/
`to_junit_xml`/`to_review_digest`/`to_markdown` with a missing
`DiffResult`; a dedicated **SARIF not-comparable** test asserting the
emitted SARIF log has `invocations[0].executionSuccessful == false` and a
`toolExecutionNotifications` entry carrying the not-comparable reason,
never an empty `results` array that a SARIF consumer could misread as a
clean pass; a dedicated **JUnit not-comparable** test asserting the
emitted XML has exactly one `<testcase>` wrapping an `<error
message="...">`, not a `<failure>` — proving JUnit's own
couldn't-run-the-test convention is used, distinct from an ordinary
reported ABI break; and a second test asserting a mixed-pair
`contract_coverage` comparison's HTML output surfaces `contract_coverage`
the same way its JSON/Markdown/SARIF/JUnit siblings do; a root-relative-path fingerprint test
(the acceptance-criteria bullet above — same-tree-different-root compare
must not fingerprint-mismatch); an exit-code test
asserting `not_comparable` returns exactly `16`, never `0` and never an
unhandled traceback, from a real end-to-end native `compare` invocation on
a mismatched pair — exercised through the actual `cli_compare_helpers.run_compare`
call site, proving the new `except` clause there catches what `cli.py`'s
thin forwarder has no chance to — from
both the legacy and severity-aware `compare` invocations; a **release
fan-out** test asserting a `not_comparable`-triggering library inside a
directory/package `compare` reports `verdict: "not_comparable"` in its
release-level entry, not `"ERROR"` — the specific inversion (incomparable
reported as the worst-possible classification) this phase must close on
its fourth entry point; a **release-precedence** test asserting a mixed
release (one `not_comparable` library, one `BREAKING`, N `COMPATIBLE`)
reports and exits as `not_comparable` overall, proving
`_RELEASE_VERDICT_ORDER`'s new rank actually wins the rollup rather than
being silently outranked by the co-occurring `BREAKING`; a
**removed-library-precedence** test asserting a release combining a
`not_comparable` library with a separately-removed library (triggering
`--fail-on-removed-library`) exits `16`, not `8`, in *both* the legacy and
severity-aware release schemes — proving `not_comparable`'s precedence
over the removed-library mechanism is unconditional, unlike that
mechanism's own existing scheme-dependent precedence; gate unit tests
for all
seven entry points; a **compat-mode exit-code** test asserting
`compat check` returns exactly `9` (never `10`'s generic fallback, never
`16`, and never an unhandled traceback) for a
`ProfileMismatchError`/`ScopeMismatchError`, exercised both through
`_classify_compat_error_exit_code` directly and through a real end-to-end
`compat check` invocation on a mismatched pair — the latter is what proves
the new `try`/`except` around the `compare()` call site actually catches the
exception, not just that the classifier returns the right code once handed
one; a **scan-mode exit-code** test asserting `abicheck scan --against`
returns exactly `6` (never an unhandled traceback, never `5`'s
budget-overflow code) for a `ProfileMismatchError`/`ScopeMismatchError`
raised from the baseline compare, exercised through a real end-to-end
`scan --against` invocation on a mismatched pair — proving `scan_cmd`'s new
`except` clause actually catches what `_run_baseline_compare` lets through
uncaught today; a **`run_scan` API/MCP** test asserting
`service_scan.run_scan(ScanRequest(..., baseline=...))` on a mismatched
pair returns `ScanResult(verdict="NOT_COMPARABLE", exit_code=6)` rather
than raising, and a second test asserting `run_scan_subprocess` (and, by
extension, `mcp_server.abi_scan`) surfaces that same structured result
rather than a generic `RuntimeError` — proving the fix at `run_scan`
actually reaches the MCP surface without any change needed there; a
**deps-compare exit-code** test asserting `abicheck deps
compare` returns exactly `5` (never folded into `4`'s `FAIL`, never a
silent `None` diff indistinguishable from the pre-existing
"file unreadable" case) for a `ProfileMismatchError`/`ScopeMismatchError`
raised while diffing a changed dependency DSO, and asserting the resulting
`StackChange.not_comparable_reason` is populated rather than `abi_diff`
being left an unexplained `None` — proving `_run_abi_diff`'s caller, not
`_run_abi_diff` itself, is what classifies the exception; a
**platform-identity dominance** test asserting that an old/new pair built
for genuinely different target triples (e.g. `x86_64` vs. `aarch64`),
otherwise identical in every other profile field, does **not** raise
`ProfileMismatchError` and instead produces a real `DiffResult` carrying
`elf_machine_changed` classified `BREAKING` — proving the gate defers to
`diff_platform.py`'s existing detector rather than preempting it with
`not_comparable`; a companion assertion with the *same* target mismatch
**plus** a genuinely different macro definition asserts the gate still
raises `ProfileMismatchError` — proving the carve-out is scoped to
target-only mismatches, not a blanket exemption whenever a target
difference happens to be present; a **misconfigured-extraction** test
asserting that when `profile_fingerprint`'s target component differs but
the old/new binaries' *own* ELF/PE/Mach-O metadata is identical (a
`--gcc-prefix` cross-compile flag applied to only one side, both actual
`.so` files genuinely the same architecture), the gate still raises
`ProfileMismatchError` rather than skipping — proving the carve-out
requires artifact-confirmed platform drift, not merely a differing
compile-context target, the specific misconfiguration this stricter
condition exists to keep catching; a **diagnostic-comparison API** test asserting `checker.compare(old, new,
diagnostic_comparison=True)` on a mismatched pair returns a real
`DiffResult` (never raises) with `assurance == "none"` — proving the flag
reaches the gate check itself, not just a CLI-level catch with nothing to
recover — plus a CLI end-to-end `--diagnostic-comparison` test asserting
the same, and a `service.compare_snapshots(..., diagnostic_comparison=True)`
test proving the Python API exposes the identical parameter, not only
`cli.py`'s flag; a **`CompareRequest` reachability** test asserting
`service.run_compare_request(CompareRequest(..., diagnostic_comparison=True))`
on a mismatched pair also returns the tentative `DiffResult` rather than
raising — proving the flag reaches CLI/MCP `compare`'s own chokepoint;
**this test does *not* cover `compare-release` or `appcompat`, which do
not go through `CompareRequest`/`run_compare_request` at all (see their
own dedicated bullets above) — each needs its own dedicated test instead**:
a **`compare-release` diagnostic-comparison** test asserting
`_run_compare_pair(..., diagnostic_comparison=True)` on a mismatched
old/new pair returns a tentative `(DiffResult, ...)` tuple rather than
raising, proving the parameter actually reaches `_run_compare_pair`'s own
`service.run_compare(...)` call, not just `service.run_compare` itself;
and two **`appcompat` diagnostic-comparison** tests asserting
`check_appcompat(..., diagnostic_comparison=True)` and
`check_plugin_host_contract(..., diagnostic_comparison=True)` each return
a tentative result on a mismatched pair instead of raising, alongside a
companion assertion that *without* the flag, both still raise
`ProfileMismatchError`/`ScopeMismatchError` uncaught — the documented
default this phase deliberately keeps (see the `appcompat.py` bullet
above); an **`mcp_server.abi_compare`
not_comparable** test asserting a mismatched pair through `abi_compare`
(the default, non-diagnostic path) returns `{"status": "not_comparable",
"reason": ...}`, never the generic `{"status": "error"}` its existing
broad `except Exception` would otherwise produce — proving `abi_compare`'s
own direct `compare_snapshots` call (bypassing `CompareRequest` entirely)
got its own dedicated catch, not just the `diagnostic_comparison`
parameter; and a second `abi_compare` test asserting
`diagnostic_comparison=True` on the same mismatched pair returns a real
tentative result instead; a `--diagnostic-comparison` end-to-end test asserting the report's
top-level `assurance` field is `"none"` and that no individual finding
carries its own `assurance` value; a
backward-compat test asserting a contract-less snapshot pair compares
unchanged; a **mixed-pair** test (one side `contract`, one side none)
asserting the comparison never hard-fails and instead carries a
`contract_coverage: "partial"` report field alongside an otherwise-ordinary
verdict — the specific case the ADR calls out as unambiguous, not left to
interpretation; a **severity-neutrality** test asserting a mixed-pair
comparison run under *every* `--severity-*` flag (`--severity-potential-breaking=error`,
`--severity-quality-issues=error`, and `--severity-preset strict`) still
exits successfully — proving `contract_coverage` is structurally outside
the findings list any severity flag scans, not merely untested against the
one flag checked last time; an **aggregate not_comparable** test asserting
`abicheck aggregate` in **discovered-only** mode exits exactly `1` when one
target's report is `not_comparable` — never silently `0`, and not any
other non-zero code either, matching the same bucket `scan`'s
budget-overflow already folds into — and a second
test asserting a `not_comparable` report is never conflated with a
genuinely missing/corrupt one in `aggregate`'s rendered per-target output,
proving the two "unavailable"-shaped states stay distinguishable; an
**Action wrapper** test (`action/run.sh`'s existing bats/shell test
harness) asserting exit `16`/`6`/`5` from `compare`/`scan`/`deps compare`
each map to `VERDICT="NOT_COMPARABLE"`, never the `*) VERDICT="ERROR"`
fallback, plus a `_maybe_post_pr_comment` test asserting a `NOT_COMPARABLE`
verdict still posts a PR comment despite the existing `ERROR`-only skip —
proving the Action doesn't both misreport and silently suppress the one
comment meant to surface a deliberate not-comparable result. A
**separate, dedicated final-exit** test asserting the composite Action
step itself exits non-zero (not just that `VERDICT` reads correctly) for
each of `compare`/`scan`/`deps-compare` given exit `16`/`6`/`5` — proving
the mapping fix alone doesn't already cover this, since `run.sh`'s
final-exit section is a separate code path from the `case`-statement
mapping and, verified against the actual code, has no `NOT_COMPARABLE`
branch of its own; without it, `FINAL_EXIT` stays `0` and the step
reports green despite the underlying CLI call having failed to compare
at all.

**Example fixtures.** The Phase 0 "scope drift" pair stays a `tests/`-level
fixture, not an `examples/case*/` catalog entry: `tests/test_validate_examples_unit.py`'s
`_VALID_VERDICTS` frozenset accepts only the five real `Verdict` strings, and
`not_comparable` is deliberately not one of them (it is a precondition
failure, never a verdict) — the same reasoning Phase C already applies to
`INCONSISTENT_DECLARATION`. Adding it to `examples/` would need a change to
that frozenset and the catalog machinery it gates, which is out of scope
here.

**Out of scope (deferred to later phases or explicitly not planned).**
`expected_public_headers` coverage inventory (ADR-050 non-goals) is not
part of this phase.

---

## Phase B — Manifest and real multi-TU dump

Implements ADR-050 D3. The highest-risk phase — see Risk above.

**Goal & acceptance criteria.**
- New `abicheck/dump_manifest.py`: strict YAML parser (unknown fields
  error), `roots`/`translation_units` schema, `name`-uniqueness,
  `contributes_to_abi=True ⇒ required=True` invariant enforced at parse
  time (a validation error, not a silent coercion). A TU's `includes`
  entries accept either a bare path string (external-by-default, D1's
  ancestor rule decides ownership, unchanged) or a mapping `{path: ...,
  project_owned: true}` explicitly marking a sibling support root
  project-owned — see the dedicated acceptance-criteria bullet above for
  why `forced_includes` alone doesn't already cover this. The base-profile
  section also accepts `frontend_context` (`host` default) — the field
  Phase D's context selector needs an accepted input path for; a manifest
  schema that only carried `roots`/`translation_units` would leave a
  DPC++ flow needing a non-default context with nowhere to request it
  (ADR-050 D3). The legacy, non-manifest CLI path gains a matching
  `--frontend-context host|device` flag (default `host`).
- `dumper.py` gains a manifest-driven `dump()` path: one castxml/clang
  invocation per TU (shared base profile + that TU's own forced includes),
  each producing a `TuFragment`. The existing single-header CLI path
  becomes this path's one-TU special case (`legacy-main`) — same code, not
  a parallel implementation.
- A manifest declaring TUs with different compilers/target triples is
  rejected at parse time — before any extraction runs, cheaper than
  failing after a wasted compile — via a new `ManifestValidationError`
  (`dump_manifest.py`), the same exception type and mechanism as this
  phase's other parse-time schema violations (unknown fields, the
  `contributes_to_abi=True ⇒ required=True` invariant above), not a new
  one invented just for this check. **This is deliberately *not* Phase C's
  `HETEROGENEOUS_ABI_CONTEXT` — that is a `TuMergeError` conflict code
  Phase C defines later, and Phase B ships and lands before Phase C.**
  Reusing that name/type here would force Phase B to either introduce
  Phase C's `tu_merge.py`/`TuMergeError` contract early (a phase landing
  out of order) or leave Phase B's own acceptance criterion referencing a
  type that doesn't exist yet; `ManifestValidationError` is Phase B's own,
  self-contained mechanism, with no dependency on `tu_merge.py`. The two
  checks are conceptually related (both concern mixed compile contexts
  across a manifest's TUs) but fire at different times for different
  reasons: this one is unconditional and parse-time, over the manifest's
  own declared fields, before `dump()` even starts; Phase C's
  `HETEROGENEOUS_ABI_CONTEXT` is merge-time, over what fragments actually
  extracted to, and — per Phase C's own text — not expected to ever fire
  in practice given this parse-time rule already holds.
- A required TU's compile failure is a hard extraction failure for the
  whole snapshot (`IncompleteAttempt`, never silently merged as if it
  succeeded); an optional (`required: false`) TU's failure degrades to a
  diagnostic, and — enforced by the parse-time invariant above — an
  optional TU can never be `contributes_to_abi: true`, so this can never
  produce a false removal.
- **New CLI surface is `--dump-manifest`, not `--manifest` — that spelling
  is already taken.** Native `compare` already registers a `--manifest`
  option today, via `@release_options` (`cli.py:1499-1507`,
  `cli_options.py:883-889`): the ADR-023 release instantiation manifest
  listing symbols a directory/package release publicly promises, visible
  in `compare --help-all`. Reusing the same flag spelling for this ADR's
  extraction/TU manifest would either collide at Click's option-registration
  level or silently reinterpret an existing `compare old_dir new_dir
  --manifest manifest.yml` release workflow as a TU-dump manifest instead —
  a real, user-visible ambiguity, not a naming nitpick. This ADR's manifest
  flag is therefore `--dump-manifest path/to/manifest.yml`, alongside
  `--frontend-context host|device` **options added to the existing
  `dump`/`compare` commands** (not new commands — a new sibling module
  cannot retroactively add options to a command already declared
  elsewhere), plus a genuinely new `abicheck plan --dump-manifest ...`
  diagnostic command (same flag spelling, for consistency — `plan` is a new
  command so `--manifest` wouldn't itself collide there, but using two
  different names for the same concept across sibling commands would be its
  own inconsistency) that prints the normalized manifest and its
  **`scope_fingerprint`** — genuinely computable from the manifest document
  alone, no compiler invocation needed (established in Phase E's cache-key
  discussion) — without running extraction. **It does not print
  `profile_fingerprint`**: that fingerprint's `-I` component is a
  `-MD`-depfile digest (Phase A) that only exists once an L2 castxml/clang
  invocation actually runs, so promising it here would either be
  impossible to compute honestly or require inventing a pre-extraction
  surrogate the compare gate would later have to treat as authoritative —
  exactly the kind of guessed value this ADR's whole design exists to
  avoid. `plan --dump-manifest` is scoped to what's genuinely
  pre-extraction-knowable — manifest validity and `scope_fingerprint` — cheap
  to run in CI before committing to a full dump; a real `profile_fingerprint`
  check still needs an actual `dump`/`compare` invocation.
- **`--dump-manifest` must be side-scoped on `compare`, not a single shared
  path.** `dump` produces one snapshot from one manifest, so a bare
  `--dump-manifest path` is correct there. `compare OLD_INPUT NEW_INPUT` is
  different: it already dumps both sides from independently-rooted
  header/include trees via `--header`/`--include`'s `old=`/`new=` prefix
  convention (`SIDED_PATH_PARAM`, ADR-040) precisely because old and new
  live under different roots. A single unsided `--dump-manifest v1/abi.yml`
  would either apply the same manifest to both sides (unable to express
  the normal two-checkout case) or leave no way to also pass `v2/abi.yml`
  for the new side. `compare`'s `--dump-manifest` therefore reuses
  `SIDED_PATH_PARAM` exactly like `--header`/`--include` do —
  `--dump-manifest old=v1/abi.yml --dump-manifest new=v2/abi.yml` — while
  `dump`'s stays a bare path (it only ever has one side).

**Files & surfaces.** New `abicheck/dump_manifest.py` (strict YAML parser,
and a new `ManifestValidationError` for every parse-time schema violation
it raises — unknown fields, the `contributes_to_abi=True ⇒ required=True`
invariant, and the heterogeneous-compiler/target-triple rejection above —
self-contained to this phase, no dependency on Phase C's later
`tu_merge.py`/`TuMergeError`), `abicheck/dumper.py`
(per-TU invocation loop, `TuFragment` type, and the actual `--dump-manifest`
termination point). `--dump-manifest` is a
shared-concept option across `dump` and `compare` (side-scoped on `compare`
via `SIDED_PATH_PARAM`, bare on `dump` — see the acceptance-criteria bullet
above), added as one decorator in `cli_options.py` and applied at `dump`'s
and `compare`'s existing declarations directly, not merely implied by
registering a new command module — deliberately not named `--manifest`,
which `@release_options` already registers on `compare` for the unrelated
ADR-023 release manifest. `cli_resolve.py` (`_resolve_compare_snapshots`
and `_resolve_input` each gain the same manifest parameter, threaded
through to `service.py`) and `service.py` (`resolve_input`/`run_dump` gain
it too, the actual entry points into `dumper.py`) — see the dedicated
acceptance-criteria bullet below for why every layer needs its own
explicit update, not just `cli_compare_helpers.py`.
**Declaring the Click option is not enough for `compare` — `cli_compare_helpers.py`
needs the actual plumbing, the same gap already fixed once for the
gate's own exception handling on this exact command.** `compare_cmd`
forwards `**kwargs` to `cli_compare_helpers.run_compare`, whose signature
is a large, explicit, fixed keyword list (verified against the actual
code — it already carries `manifest_path` for the unrelated ADR-023
release manifest, confirming the pattern: a new Click option's dest must
also appear here or it either raises "unexpected keyword argument" or
never reaches the per-side dump resolution). `run_compare` gains the new
`old_dump_manifest`/`new_dump_manifest` parameters — but `run_compare`
itself is not the bottom of this chain either: it calls
`cli_resolve._resolve_compare_snapshots`, which has its own large explicit
signature (verified against the actual code — no manifest parameter today)
and calls `cli_resolve._resolve_input` (also explicit, also no manifest
parameter, "a thin CLI wrapper over `service.resolve_input`"), which calls
`service.resolve_input`/`service.run_dump` — the actual entry points into
`dumper.py`. Each of these four layers (`run_compare` →
`_resolve_compare_snapshots` → `_resolve_input` →
`service.resolve_input`/`run_dump`) has its own fixed, explicit
keyword-argument signature with no existing manifest slot, so the new
parameter must be threaded through every one of them individually, not
just declared once at the top and assumed to flow — the exact mistake
this ADR has already made and corrected once for `CompareRequest`
(`run_compare_request` → `compare_snapshots`) earlier in this same phase.
`dumper.dump()` is where it actually terminates, alongside the existing
`except (ProfileMismatchError, ScopeMismatchError)` fix already planned
in `run_compare`.
**`--dump-manifest` reaching native `compare` means directory/package
`compare` accepts it too, on the exact same set-input path already
established for evidence flags — and must reject it, not silently ignore
it, the identical mistake this plan already caught once for
`--frontend-context` (below).** Verified against the actual code:
`run_compare`'s `{old_kind, new_kind} & {"directory", "package"}` branch
(`cli_compare_helpers.py:1155-1168`) dispatches to
`cli._dispatch_release_compare(...)` with its own explicit kwargs list —
no `dump_manifest`/`manifest_path` field in it — so a directory/package
`compare old_dir new_dir --dump-manifest old=... --dump-manifest new=...`
would be accepted by Click, never forwarded into the release fan-out, and
silently do nothing. `--dump-manifest` is conceptually the same kind of
"how do I resolve this side's dump input" flag `_EVIDENCE_SET_INPUT_FLAGS`
(`cli_resolve.py:618-622`) already exists to reject on set inputs
(`--sources`, `--build-info`) — not a compile-context flag — so it gains
its own entry there (the raw, pre-side-normalization Click dest, matching
how `sources`/`build_info` are keyed), and
`_reject_evidence_flags_for_set_inputs` picks it up automatically once
listed, the same way `frontend_context`'s own fix (below) inherits
`_COMPILE_CONTEXT_SET_INPUT_FLAGS`'s existing rejection path rather than
writing a new one.
**`--frontend-context` reaching `compile_context_options` means directory/package
`compare` accepts it too — and must reject it, not silently ignore it.**
The release/set-input fan-out (`cli_compare_release.py`) never threads a
per-library `CompileContext` at all; `cli_resolve.py`'s existing
`_COMPILE_CONTEXT_SET_INPUT_FLAGS` dict is exactly the guard that rejects
every other compile-context flag (`--gcc-path`, `--ast-frontend`, etc.) on
a directory/package input, loudly, instead of silently accepting and then
ignoring it. `frontend_context` gains its own entry in that same dict — the
new option automatically inherits the existing rejection path once listed
there, so `compare old_dir new_dir --frontend-context device` fails fast
with the same clear error every other compile-context flag already
produces on a set input, rather than being accepted and silently dropped
by a backend that was never taught to use it.
**`--frontend-context` is not a `dump`/`compare`-only option — it belongs in
the existing `compile_context_options` decorator (`cli_options.py`), the same
shared L2 compile-context family `dump`, `compare`, *and* `cli_scan.py`'s
`scan_cmd` already apply (`# dump↔scan L2 compile-context parity (ADR-037
D3)`), not a new, narrower decorator applied only to two of those three.**
Leaving `scan` out would strand the one-shot `abicheck scan --against`
workflow on the `host` default with no way to request `device` — the same
SYCL/DPC++ target a `scan`-driven audit can already reach via that command's
side-aware `-H`/`-I` options.
**The decorator alone only makes Click accept the flag — it does not by
itself thread the value anywhere, and this phase must not stop at the
decorator, and it does not stop at `resolve_compile_context` either.**
Verified against the actual code: `dump_cmd` (`cli.py:569-592`) and
`scan_cmd` (`cli_scan.py:634-663`) each have their own fixed, explicit
callback parameter list — every other flag `compile_context_options` adds
(`gcc_path`, `gcc_prefix`, `gcc_options`, `gcc_option_tokens`, `sysroot`,
`nostdinc`, `header_backend`) is already listed there individually, not
gathered via `**kwargs`. Click invokes these callbacks directly with one
keyword per registered option, so adding `--frontend-context` to the shared
decorator without adding a matching `frontend_context` parameter to
*both* callback signatures is what actually raises "got an unexpected
keyword argument" at the Click-callback boundary — `resolve_compile_context`
is never in that call path; it is invoked manually from inside each
callback's body, not by Click. `dump_cmd` gains `frontend_context: str =
"host"`; `scan_cmd` gains the same (with its own existing defaulted-flags
group, since `compile_context_options`' compile-context params already
carry defaults there per `:657-663`). Native `compare`'s path is a third,
separate instance of the identical gap: `compare_cmd` is a thin `**kwargs`
forwarder (so it needs no change itself), but `cli_compare_helpers.run_compare`
— the function Click's kwargs actually land in — has the same kind of
fixed, explicit signature and gains its own `frontend_context: str = "host"`
parameter too. Each of these three callback/helper layers then passes its
new parameter into its own `resolve_compile_context(...)` call
(`cli_dump_helpers.resolve_dump_compile_context` for `dump`, `cli_scan.py:722`
for `scan`, `cli_compare_helpers.py:1258` for `compare`) — which is where
`cli_options.resolve_compile_context`'s own signature and
`CompileContext` construction come in: that function (the single function
the `@compile_context_options` family ultimately resolves to for
`compare`/`dump`/`scan` alike) has a fixed, explicit keyword-argument list
and builds a `service_scan.CompileContext` from exactly those fields —
neither has a slot for a host/device context today. This phase therefore
also adds `CompileContext.frontend_context: str = "host"` and a matching
`frontend_context` parameter to `resolve_compile_context`, threaded into
the `CompileContext(...)` it constructs — necessary, but (per the
callback-layer paragraph above) not sufficient on its own; `dump_cmd`,
`scan_cmd`, and `run_compare` each need the parameter in their own
signature too, or the value never survives the trip from Click's callback
invocation down to this function call. `cli_dump_helpers.resolve_dump_compile_context`
(`:1168-1181`) sits between `dump_cmd` and `resolve_compile_context` and
has the identical fixed-signature gap — it gains the same
`frontend_context` parameter, threaded from `dump_cmd` into its own
`resolve_compile_context(...)` call (`:1198`). Because `scan_engine.run_scan_core`
already passes the *whole* `CompileContext` object through to
`service.resolve_input` (`compile=compile_context`, not individual
unpacked fields — verified at `scan_engine.py:243-254`) the same way
`dump`/`compare` do, `dump()`'s Phase-B addition of a `compile.frontend_context`
read (needed there regardless, for the legacy CLI path) automatically
reaches `scan` too once `CompileContext` carries the field — no
`scan_engine.py`-specific dump-call change beyond that. New `cli_dump_manifest.py`
sibling command module for the genuinely new `plan --dump-manifest` command
only (per the root `CLAUDE.md`'s "larger command → sibling module"
convention) — registering it is not implicit: `cli.py`'s bottom
side-effect `from . import (...)` block gains `cli_dump_manifest`, or
`plan --dump-manifest` never attaches to `main` at all, and `pyproject.toml`'s
`disallow_untyped_decorators = false` override
list gains `abicheck.cli_dump_manifest` alongside the existing per-module
entries, or the typed-decorator mypy lane fails on its `@click` decorators
(both required steps of the root `CLAUDE.md`'s "Adding a new top-level
command" procedure, steps 3–4) — `cli_dump_helpers.py` (extend
`resolve_dump_depth`/`check_requested_depth_satisfied` to operate per-TU),
`service_scan.py` (`CompileContext.frontend_context: str = "host"`),
`cli_options.py` (`resolve_compile_context`'s new `frontend_context`
parameter, threaded into the `CompileContext` it builds — the single
choke point `compare`/`dump`/`scan` all resolve through, so no
`scan_engine.py` dump-call-site change is needed beyond this).

**Tests.** Manifest parser unit tests (the invariant violation, duplicate
TU names, unknown fields, relative-path resolution, `frontend_context`
accepted/defaulted/rejected-when-invalid). A **manifest support-root
ownership** test asserting a TU declaring `includes: [{path: ../src,
project_owned: true}]` (a sibling of the TU's own declared path, not an
ancestor) leaves `profile_fingerprint` matching across an old/new manifest
pair when the edit is confined to a private header inside `../src` —
proving the mapping form extends project-ownership the same way the
legacy CLI's labeled `--include` does — alongside a companion assertion
that a bare `includes: [../src]` (no `project_owned` marker) on the same
layout still classifies `../src` external and flips `profile_fingerprint`
on that same edit, confirming the mapping form is genuinely required, not
redundant with the ancestor rule. `dumper.py` multi-TU
integration tests (`@pytest.mark.integration`, needs castxml/clang) using
Phase 0's fixtures. A `plan --dump-manifest` unit test asserting it never
invokes a compiler and prints `scope_fingerprint` only — never a
`profile_fingerprint` value, which would require one. A `--frontend-context` CLI-flag unit test for the legacy
(non-manifest) path, mirroring the manifest-field test. A **side-scoped
`compare --dump-manifest`** test asserting `compare old.so new.so
--dump-manifest old=v1/abi.yml --dump-manifest new=v2/abi.yml` dumps each
side from its own manifest (not both from one), plus a `dump --dump-manifest
path` test confirming the single-sided form is unaffected. A test asserting
`compare --dump-manifest` and the pre-existing `--manifest` (ADR-023 release
manifest) coexist as distinct, independently-settable options on the same
`compare` invocation — the specific collision this naming choice exists to
avoid; and an end-to-end test asserting `compare old.so new.so
--dump-manifest old=v1/abi.yml --dump-manifest new=v2/abi.yml` actually
dumps each side from its own manifest all the way through
`dumper.dump()` (not just that Click accepts the flags, and not stopping
at a mock of any intermediate layer) — proving `cli_compare_helpers.py`,
`cli_resolve.py`, and `service.py` were all updated together, not just
`cli.py`. A **release
rejects `--frontend-context`** test asserting `compare old_dir new_dir
--frontend-context device` fails fast via `cli_resolve.py`'s existing
set-input guard, the same way it already fails fast for `--gcc-path`/
`--ast-frontend` on a directory/package input — proving the flag can't be
silently accepted and then ignored by the release backend. A companion
**release rejects `--dump-manifest`** test asserting `compare old_dir
new_dir --dump-manifest old=v1/abi.yml --dump-manifest new=v2/abi.yml`
also fails fast via `_EVIDENCE_SET_INPUT_FLAGS`'s guard, the same way
`--sources`/`--build-info` already do on a directory/package input —
proving `--dump-manifest` can't reach `_dispatch_release_compare`'s
kwargs (which never carries it) and silently do nothing on a release
compare. A
**`scan --frontend-context`**
regression test asserting `abicheck scan --against` accepts `device` and
threads it into the L2 header frontend the same way `dump`/`compare` do,
proving `compile_context_options` (not a `dump`/`compare`-only decorator) is
what carries the flag.

**Example fixtures.** Phase 0's external-STL-noise pair only, wired through
the real manifest path end to end — **not** the ODR-safe pair. The
ODR-safe fixture is a forward-declaration-in-one-TU,
full-definition-in-another case, which is exactly a duplicate `entity_key`
across TUs; Phase B's own placeholder merge (below) errors loudly on any
duplicate `entity_key`, so wiring the ODR-safe pair through Phase B "end to
end" would either require Phase C's real merge lattice to already exist
here (collapsing the phase split) or fail outright against the deliberately
strict placeholder. The ODR-safe pair's real end-to-end wiring belongs to
Phase C (see its own "Tests" section), where the merge that actually
handles this trivial-merge case exists.

**Out of scope.** D4 (merge across TUs) is Phase C — Phase B's
`TuFragment`s are produced but not yet merged into one `AbiSnapshot`
usable by `checker.compare`; Phase B ships with a minimal "no conflicts
possible" merge (concatenate, error loudly on any duplicate `entity_key`)
as a placeholder, replaced by Phase C's real compatible-merge lattice.

---

## Phase C — Compatible merge across translation units

Implements ADR-050 D4. Depends on Phase B.

**Goal & acceptance criteria.**
- New `abicheck/tu_merge.py`: for each `entity_key` seen in more than one
  `TuFragment`, classify as trivial-merge (forward-decl + definition,
  declaration + redeclaration, default-argument-only difference — union
  provenance, keep the richer declaration), `INCONSISTENT_DECLARATION`
  (same-context conflict — different return type/layout/calling
  convention), or `HETEROGENEOUS_ABI_CONTEXT` (should Phase B's
  single-profile-per-manifest rule ever be relaxed — not expected in this
  phase).
- **`INCONSISTENT_DECLARATION`/`HETEROGENEOUS_ABI_CONTEXT` are conflict
  codes on a new `TuMergeError` (`errors.py`), not `ChangeKind` enum
  members — despite the naming convention looking identical to one.** They
  fire during extraction/merge, before a snapshot is ever `Complete` enough
  to diff (see the bullet below) — `checker.compare` never runs on a
  conflicted merge, so there is no comparison for a `ChangeKind` to
  describe. Registering them through the four-step `ChangeKind` procedure
  would be a category error: they'd never fire from a detector during
  `compare`, so `changekind-detector`'s orphan check would immediately flag
  them, and severity/`RISK_KINDS`/`QUALITY_KINDS` classification doesn't
  apply to something that blocks a comparison from happening at all.
  `tu_merge.merge_fragments(...)` raises `TuMergeError(code=...)` directly;
  `dumper.py`'s manifest-driven `dump()` lets it propagate as an
  `IncompleteAttempt`, the same shape a required TU's compile failure
  already produces (Phase B).
- `entity_key` excludes return type (kept in `abi_facts`) — reuses the
  ADR-045/048 "prefer specific identity, never fold a mutable fact into the
  key" principle explicitly, not a fresh design.
- A snapshot with unresolved conflicts cannot pass Phase A's comparability
  gate as a clean side — it is not a `CompleteSnapshot`.
- Merge is deterministic regardless of TU-completion order (a required
  property, tested directly — shuffle TU processing order, assert
  byte-identical merged output).

**Files & surfaces.** New `abicheck/tu_merge.py`, reusing
`buildsource/crosscheck.py`'s existing merge/classify shape (not a new
algorithm — see ADR-050 D4), `errors.py` (`TuMergeError`). `dumper.py`'s
manifest path calls this instead of Phase B's placeholder concatenation.

**Tests.** Phase 0's ODR-safe fixture (must merge cleanly) and
conflicting-return-type fixture (must raise `TuMergeError(code="INCONSISTENT_DECLARATION")`
— not produce a `Change`/finding); an order-independence property test
(`tests/test_detector_properties.py` style, per the repo's existing
metamorphic-test convention); a test confirming `TuMergeError` is never
registered as a `ChangeKind` (no `checker_policy.ChangeKind` member, no
`change_registry*.py` entry) — guarding against the exact ambiguity this
phase's own naming otherwise invites.

**Example fixtures.** `examples/case2xx_multi_tu_compatible_merge/` only —
the conflicting-return-type case is an extraction failure, not a
verdict-producing comparison, so it doesn't fit the example catalog's
`ground_truth.json` verdict convention; Phase 0's fixture plus the
`TuMergeError` unit test above are its coverage instead.

---

## Phase D — SYCL/DPC++ host vs. device AST context selection

Implements ADR-050 D5. Independent of Phase C's merge work; depends only
on Phase 0's captured DPC++ fixture for the parser itself. Selecting a
*non-default* context does have a soft dependency on Phase B, though: the
`frontend_context` field (manifest base profile) and `--frontend-context`
CLI flag this phase's selector reads are defined there, not here (Phase B
"Goal & acceptance criteria"). Everything in this phase can be built and
tested against the `host` default without Phase B, but a real DPC++
device-context request has nowhere to come from until Phase B's field/flag
exist.

**Goal & acceptance criteria.**
- New `abicheck/sycl_context.py`: decodes a DPC++ frontend's
  (possibly-multi-document) JSON output as a stream of `{kind, target,
  ast}` contexts — real document-boundary streaming, not a bracket/string
  split; rejects trailing garbage and truncated documents.
- Context selection is by **`kind`**, not by target-triple matching — the
  manifest's/CLI's `frontend_context` (`host` default, Phase B) is matched
  against each decoded context's `kind` field (`"host"`/`"device"`, read
  directly from the compiler's own JSON output), never against the target
  triple (`spir64`, etc.), which is diagnostic-only (ADR-050 D5). Three
  outcomes: exactly one context with the requested `kind` → selected;
  zero contexts with the requested `kind` (e.g. only a `spir64`/`device`
  context when `host` was requested) → `AST_CONTEXT_MISSING`, an
  extraction failure, never a successful snapshot with the wrong target
  silently selected; more than one context sharing the requested `kind` →
  `AST_CONTEXT_AMBIGUOUS`, never resolved by an implicit tiebreaker.
- **The resolved `kind` must be folded into `profile_fingerprint`'s hashed
  fields — selecting the right context is not the same as the gate
  knowing which one was selected.** Phase A's `profile_fingerprint` field
  list predates this phase (`frontend_context` doesn't exist until Phase
  B/this phase), so it was never added there; leaving it un-added here
  would silently reopen the exact under-counting bug Phase A exists to
  close, one field short — two extractions requesting different
  `frontend_context` values (`host` vs. `device`), otherwise identical in
  compiler/target/macros/includes, parse genuinely different ASTs, but
  D2's gate would never see the difference and any resulting AST
  difference would surface as an ordinary reported `Change` instead of a
  pre-diff `not_comparable`. `comparability.compute_extraction_contract`
  gains the resolved `kind` as a hashed input, not just the *requested*
  `frontend_context` string — the two would still agree even when an
  ambiguity-adjacent frontend quirk resolved to a differently-labeled
  context on each side for the same requested value, which the requested
  string alone can't see but the actually-resolved `kind` can.
- `dumper_clang.py`'s existing single-context assumption is generalized to
  call this module when the detected frontend is DPC++-capable; a plain
  (non-SYCL) clang/castxml invocation is unaffected.
- **The legacy single-context fallback is gated on positive non-SYCL
  identification, never on the decoded context count.** "Zero contexts
  with the requested `kind`" (above, → `AST_CONTEXT_MISSING`) and "this
  wasn't a multi-document DPC++ invocation at all" are different
  conditions and must not be conflated: the fallback to the existing
  single-context path fires only when the frontend invocation is
  positively identified as non-SYCL *before* `sycl_context.py` is ever
  invoked (e.g. no DPC++/multi-document toolchain flag was requested, or
  the raw output never contains the module's document-boundary markers at
  all) — never as a recovery path *after* handing DPC++-capable output to
  the decoder and getting back an empty or malformed stream. A
  DPC++-capable invocation that decodes to zero contexts (a broken
  toolchain invocation, truncated output, or any other malformed-data
  case) must still raise `AST_CONTEXT_MISSING` through the three-outcome
  logic above, not silently degrade to the single-context path — that
  degradation is reserved for output that was never a context stream to
  begin with, so missing or malformed DPC++ context data can never result
  in silently selecting the wrong (or an arbitrary) AST.

**Files & surfaces.** New `abicheck/sycl_context.py`, `dumper_clang.py`
(wiring), `sycl_metadata.py` (unaffected — this phase adds frontend-level
context selection, D5 explicitly does not touch the existing binary-symbol
classifier).

**Tests.** Fixture-driven parser tests against Phase 0's real captured
output (multi-document, malformed/truncated variants added once the happy
path is proven — matching the review's own "fixture-first, don't guess the
parser" sequencing advice). Selection tests for all three outcomes: exactly
one context with the requested `kind` selects correctly; zero contexts with
the requested `kind` raises `AST_CONTEXT_MISSING`; two-or-more contexts
sharing the requested `kind` raises `AST_CONTEXT_AMBIGUOUS`. A dedicated
test asserting selection is by `kind`, not target-triple pattern-matching —
a `{kind: "device", target: "spir64"}` context selected when `frontend_context`
is `device`, and *not* rejected as a triple-mismatch, is the specific
regression this criterion exists to prevent. A **fallback-gating** test
asserting a DPC++-capable invocation whose decoded stream comes back empty
(a broken toolchain invocation, truncated output) raises
`AST_CONTEXT_MISSING`, never silently falling back to the single-context
path — proving the fallback distinguishes "genuinely not a multi-document
invocation" from "was one, but decoded to nothing," the specific
conflation this criterion exists to prevent. A **host/device
profile-fingerprint mismatch** test asserting an old/new pair extracted
with `frontend_context=host` on one side and `frontend_context=device` on
the other, otherwise identical compiler/target/macros/includes, produces
*different* `profile_fingerprint`s and raises `ProfileMismatchError` from
D2's gate — proving the resolved `kind` actually reaches
`compute_extraction_contract`'s hash, not just `sycl_context.py`'s
selection logic; a companion assertion with the *same* `frontend_context`
on both sides leaves `profile_fingerprint` matching, the routine case this
fix must not break.

**Example fixtures.** None required beyond Phase 0's captures — this phase
is extraction-layer, not diff-layer; no new `ChangeKind`.

---

## Phase E — Resource-aware frontend scheduling and cache-key extension

Implements ADR-050 D6. The cache-key half targets the manifest's full
computed `scope_fingerprint` (TU names, per-TU ordered includes/
forced-includes, and `contributes_to_abi`/`required` flags together), so
it depends on **both** Phase A and Phase B — not Phase B alone.
`scope_fingerprint` as a type and computation
(`ExtractionContract`/`comparability.compute_extraction_contract`) is
Phase A's deliverable; Phase B only supplies the manifest-driven *inputs*
that feed it for a manifest-driven dump, it doesn't invent the fingerprint
itself. This phase's own acceptance criteria below also build directly on
Phase A's cache-key fix (the `sorted(...)`-dropping order-sensitivity
change), not merely on Phase A's types — so starting this phase with A
unmerged leaves nothing concrete to extend. Not on
`profile_fingerprint`, which can **never** be a pre-dump cache-key input at
all, on either path (see the acceptance-criteria bullet below for why).
`scope_fingerprint` is the one exception: for a manifest-driven dump it's
fully computable by parsing the normalized manifest, no compiler invocation
needed, so it genuinely can feed the cache key pre-dump. The scheduling
half depends on Phase B too (the per-TU loop it schedules).

**Goal & acceptance criteria.**
- The RAM-probing/pool-sizing helper in `buildsource/source_replay.py` is
  factored out into new leaf module `abicheck/process_resources.py`; both
  `source_replay.py` and `dumper.py`'s per-TU loop import it — one
  implementation, not two, per AGENTS.md's own import-cycle guidance ("move
  shared logic to a leaf module both sides can depend on").
- `dumper.py`'s per-TU castxml/clang invocations (Phase B) run under this
  pool instead of a fully sequential loop; a killed/timed-out TU records
  its exit signal and never silently retries as a clean empty TU.
- **`profile_fingerprint` itself cannot be a cache-key input — this phase
  does not attempt it.** `cached_run_dump` looks up `snapshot_cache`
  *before* calling `dump()` (Phase A); `profile_fingerprint`'s `-I`
  component is a depfile digest that only exists *after* an L2
  castxml/clang invocation runs, so using it as a pre-lookup key input
  would require running the very extraction the cache exists to skip —
  circular, not merely undesirable. `_cache_key()` already closes the
  practical gap this phase originally targeted, without either
  fingerprint: it recurses every `-I`/`-H` directory
  (`header_utils.iter_cache_header_files`) and hashes each matched file's
  content and mtime, pre-dump, no compiler invocation needed. This
  deliberately over-approximates (hashes every header-like file reachable
  under the directory, not only ones a given compile would resolve) —
  correct for a cache key, where a false miss just costs a redundant dump
  and a false hit would serve a stale `contract`, unlike
  `profile_fingerprint` itself, which must be exact or the gate spuriously
  fires. Phase A's own order-sensitivity fix (dropping `sorted(...)` for
  `headers`/`includes`) already closes the remaining gap that mattered for
  the legacy CLI path.
  **For the manifest-driven path, the cache key must cover the full
  normalized manifest scope, not a hand-picked subset of it.** An earlier
  revision of this bullet keyed only on a TU's `contributes_to_abi`/
  `required` flags — too narrow: `scope_fingerprint`'s own definition (D1)
  also covers each TU's *name* and its *ordered* `includes`/
  `forced_includes`, and reordering a TU's includes, changing its
  forced-includes, or renaming a TU changes extraction semantics (and
  `scope_fingerprint` itself) without necessarily touching any flag or any
  file's content — a gap the flags-only key would miss exactly like the
  content-hash-only key missed the flags. `scope_fingerprint` is fully
  determined by parsing and normalizing the manifest document — no
  compiler invocation needed — so, unlike `profile_fingerprint`, it
  genuinely is available before `dump()` runs; `snapshot_cache.py`'s
  `_cache_key()` (`:130`) hashes the **full computed `scope_fingerprint`**
  for a manifest-driven dump (TU names, per-TU ordered includes/
  forced-includes, and the `contributes_to_abi`/`required` flags together,
  not any one piece in isolation), closing the whole class of
  pre-dump-knowable manifest drift `iter_cache_header_files`'s
  filesystem-content walk structurally cannot see.

**Files & surfaces.** New `abicheck/process_resources.py`,
`buildsource/source_replay.py` (import from it instead of its own inline
implementation), `dumper.py` (per-TU pool), `snapshot_cache.py`
(`_cache_key` gains the full computed `scope_fingerprint` — TU names,
per-TU ordered includes/forced-includes, and `contributes_to_abi`/
`required` flags together — as an additional key input for manifest-driven
dumps; still never `profile_fingerprint`, which cannot be a pre-dump input
at all, per the bullet above).
**Modifying `_cache_key()`'s own internals is not sufficient — the caller
that decides what to pass it never sees the manifest today.**
`service_dump_cache.cached_run_dump` is what actually builds the pre-dump
key: it computes an `extra` string via `_dump_cache_extra_key(...)` and
calls `snapshot_cache._cache_key(path, _headers, _cache_includes, version,
lang, extra=extra)` *before* `dump()` runs (verified against the actual
code, `:338-348`). Changing `_cache_key()`'s signature/behavior alone has
no effect unless this call site is also updated to compute the
manifest-driven `scope_fingerprint` and fold it into `extra` (or a new
keyword) — otherwise a manifest-only change (a TU rename, reordered
forced-includes) still hits a stale cache entry regardless of what
`_cache_key()` itself is capable of hashing. `service_dump_cache.py` gains
that plumbing: `cached_run_dump` computes the manifest's `scope_fingerprint`
(cheap — no compiler invocation, per the acceptance-criteria bullet above)
before its cache lookup and folds it into the key it builds.

**Tests.** `process_resources.py` unit tests migrated from
`source_replay.py`'s existing RAM-probing tests (same behavior, new import
path — a refactor test, not new coverage). Cache-key tests: identical
manifest TU includes, differing only a TU's `contributes_to_abi` flag ⇒
cache miss; identical TUs and flags, differing only one TU's *include
order* (same files, reordered) ⇒ cache miss; identical TUs and flags,
differing only one TU's *name* ⇒ cache miss — the three independent
components of the pre-dump-knowable gap this phase closes,
distinct from Phase A's already-completed order-sensitivity and
whole-snapshot-cache-version fixes.

**Out of scope.** No new scheduling *policy* — this phase ports the
existing, already-proven `source_replay.py` policy verbatim; a different
policy (e.g. per-manifest-declared memory budget) is future work, not
scoped here.

---

## How to pick up this plan

1. Read [ADR-050](../adr/050-comparability-contract-and-multi-tu-manifest.md)
   in full before starting any phase — it has the authority-boundary rule
   (`The one rule that does not change`) every phase must preserve.
2. Start with Phase 0 regardless of which later phase you're aiming for.
3. Implement against each phase's acceptance criteria above; add a
   `changelog.d/` fragment per AGENTS.md convention for any
   `abicheck/**/*.py` change.
4. Update this doc's Effort/Risk line for a phase once it ships (matching
   the convention `g31`/`g19` already use — "Phase A — S, done").
