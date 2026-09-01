---
doc_type: contributor
level: advanced
lifecycle: active
depends_on:
  - docs/learn/abi-api-handling.md
  - docs/_meta/topics.yaml
  - mkdocs.yml
---

# Learning series — structure review and proposed curriculum

**Origin:** a review of the published learning series
(`docs/learn/abi-api-handling.md` and everything it navigates) asked one
question: does the series take a reader from "what is an ABI" to "I can run
this scanner on a large product, at PR level, and read what it tells me" —
in an order of steadily growing complexity, covering the features, examples
and scenarios the scanner actually has? This page records what was found
and proposes the target shape. It is an assessment and plan, not a rewrite;
nothing under `docs/learn/` changes with this page.

**Scope reviewed:** all 39 pages under `docs/learn/` (the hub, the numbered
Parts 0–7 plus the detection capstone, the five-minute on-ramp, glossary,
cheat sheet, and 26 deep dives — ~10,400 lines), their `mkdocs.yml` nav
placement, `docs/_meta/topics.yaml` ownership, and — for the coverage
matrix — the tool track (`docs/use/`, `docs/integration/scenarios/`,
`docs/reference/`), the example catalog (`examples/ground_truth.json` owns
its count), and `docs/contribute/usecase-registry.yaml`.

---

## 1. What exists today

The learning material is physically one directory but is navigated as two
tabs (`docs/AGENTS.md` "Layout"):

| Tab | Pages | Role |
|---|---|---|
| **ABI/API Compatibility** (educational) | hub, on-ramp, cheat sheet, Parts 0–7, capstone (`08-detection.md`), glossary, and 15 deep dives grouped as Define Your Contract / ABI Mechanics / Beyond ABI / Platforms & Toolchains / Designing Stable Interfaces / Verification & Assurance | "understand the problem" — tool-independent |
| **Concepts** (tool track) | `verdicts.md`, `contract-aware-compatibility.md`, `evidence-and-detectability.md`, `what-each-level-sees.md`, `architecture.md`, `build-source-data.md`, `graph-coverage.md`, `impact-analysis.md`, `environment-drift.md`, `elf-symbol-filtering.md`, `limitations.md` | "how abicheck models it" |

Declared reader level (front matter `level:`), where a page declares one:

| Level | Pages |
|---|---|
| beginner | `verdicts.md` only |
| intermediate | 19 pages (every other deep dive that has front matter) |
| advanced | `class-layout-abi.md`, `exception-unwinding-abi.md`, `modern-cpp-toolchain-hazards.md` |
| *(none)* | the hub, cheat sheet, all numbered Parts except 06, glossary, on-ramp, `build-source-data.md`, `dependency-floors.md`, `environment-drift.md`, `graph-coverage.md` |

Two things about the current shape work well and should be kept:

- **The numbered spine is coherent.** Every Part carries the same
  "Series navigation" breadcrumb and an explicit next link; Part 1 →
  Part 7 genuinely builds, with prerequisites stated per page and a
  single governing idea ("the compiler bakes the library's facts into the
  caller and never re-checks them") threaded through.
- **Mechanism → verdict → runnable case is the right pedagogy.** The break
  families table on the hub, the per-case "Real Failure Demo" pages, and
  the `ChangeKind` mapping on `class-layout-abi.md` are exactly what
  distinguishes this series from a generic ABI primer.

Everything below is about the material *around* that spine.

---

## 2. Findings

### F1 — Two competing reading orders, and no forward path off the spine

The in-page breadcrumb says "read 0 → 7 in order"; the sidebar groups the
same pages by *question asked* and interleaves deep dives between Parts
(`class-layout-abi.md` sits between Parts 4 and 5; `compatibility-direction.md`,
`consumer-models.md`, `build-profile-comparability.md`, `abi-surface.md` sit
between Part 0 and Part 1). `mkdocs.yml` documents this as deliberate. The
cost is that the 26 deep dives have *no* previous/next links at all — only
trailing "See also" lines — so a reader who steps off a Part into
`class-layout-abi.md` has no way forward except back. Three concrete
navigation faults:

- `07-designing-for-stability.md` ends with two different "next" targets
  (the capstone and the hub).
- `what-each-level-sees.md`'s only "Next" points *back* to
  `evidence-and-detectability.md` (which has no "Next" of its own), so the
  trio's worked example returns the reader to the model instead of sending
  them on to the flag reference or the practice material.
- `08-detection.md` opens with an admonition explaining why it is not
  "Part 8" — a symptom of the two orders disagreeing.

### F2 — The entry page tells newcomers not to start there

`abi-api-handling.md` is the URL the series is published and linked under,
and its first admonition is "New to the topic? Don't start here." It is a
390-line *navigator* (a role table, a role-path table, a 23-row break-family
index, three "deeper" sections on scan/L5/CI) rather than a curriculum. The
two pages a novice most needs — the five-minute on-ramp and the glossary —
have exactly one inbound link each across the whole docs tree.

### F3 — Complexity does not grow monotonically

- 19 of the 23 pages that declare a level say `intermediate`, and 16
  declare none. The vocabulary for a ladder exists (`beginner` /
  `intermediate` / `advanced` / `expert`, enforced by
  `scripts/check_docs_contract.py`) but is barely used, so neither the
  front matter nor the nav expresses one today.
- `verdicts.md` is declared `beginner` but its sections on contract
  evaluation and the two exit-code schemes are advanced material.
- Part 1 (505 lines) is the longest Part and is the *first* one a
  mechanics-first reader hits; it also ends with its own §8 Glossary that
  duplicates `glossary.md`.
- Part 4 (473 lines) keeps inline summaries of three sections that were
  already split out (`exception-unwinding-abi.md`,
  `modern-cpp-toolchain-hazards.md`, `class-layout-abi.md`), so a reader
  meets each hazard twice at different depths with no signal which is the
  full treatment.
- Practice material (baselines, CI, PR gating) appears only as short
  sections at the end of Parts 5, 6, 7 and in tool-track pages; there is no
  place in the ladder where "now run it on your project" is *taught*
  rather than linked.

### F4 — The same topic is taught several times

`docs/_meta/topics.yaml` already resolves several look-alike pairs: a
registered `allowed_summaries` page (`architecture.md` for the evidence
model, `limitations.md` for symbol filtering, Part 4 for two of the three
hazard pages split out of it — `class-layout-abi.md` owns no topic yet) is
a permitted short summary, not a duplicate, and
`docs/contribute/documentation.md` records those fixes. The rows below are
the ones *not* covered by a registration, or where the second treatment is
a full re-explanation rather than a summary:

| Topic | Taught in full on | Also (re)explained on |
|---|---|---|
| L0–L5 evidence model | `evidence-and-detectability.md` (model) + `what-each-level-sees.md` (worked example) | `08-detection.md` §2 (a per-family evidence table) and `build-source-data.md` §Evidence layers (a second L0–L5 ladder) — neither registered as a summary of `evidence-model` |
| "a static comparer structurally cannot decide this" | — | `behavioral-compatibility.md`, `data-wire-compatibility.md`, `ownership-and-lifetime.md`, `concurrency-and-initialization.md` each make the same argument in their own words (so the 40-word duplicate-block warning does not fire); `assurance-methods.md` summarises all four |
| Struct/class layout | `03-type-layout.md` | `04-cpp-abi.md` §7, `class-layout-abi.md` (which calls itself "the single place") — no registered owner |
| Verdict semantics and the numeric verdict → exit-code table | `verdicts.md` | `architecture.md` (the numeric table again, unregistered — it becomes a link, page specs C7); `abi-cheat-sheet.md` and `07-designing-for-stability.md` (verdict-meaning tables, unregistered) |
| Glossary | `glossary.md` | `01-foundations.md` §8 |
| C++ hazards split out of Part 4 | `exception-unwinding-abi.md`, `modern-cpp-toolchain-hazards.md`, `class-layout-abi.md` | Part 4 keeps an inline summary of each — registered, but long enough that the reader meets each hazard twice with no signal which is the full treatment (F3) |

### F5 — Contributor material sits inside the learning tree

`impact-analysis.md` opens with "slice 1 of G29 Phase 3 (ADR-052)" and links
`contribute/plans/`; `graph-coverage.md` uses internal pass-state field
names and "G31 Phase B"; `build-source-data.md` (810 lines, the longest page
in the tree) cites a dozen ADRs and carries schema/storage/redaction
detail; `architecture.md` lists `contributor` among its audiences; `evidence-and-detectability.md`
ends with an appendix cataloguing *removed* scan flags and carries a
self-correction about a previously over-claimed coverage figure. A user
reading "Concepts" top to bottom crosses from mental model into
release-engineering vocabulary without a marker.

### F6 — The practice track is missing from the learning tab

Measured by where a topic is *taught* (not merely mentioned), six topics
have a rich tool-track home and no learning-tab treatment at all: what a
baseline is and why a project needs two; PR-level gating; reporting surface
*growth* rather than only breaks; multi-binary products; rollout and
governance; and packaging/consumer inputs (deb/rpm/conda/wheel, `abi3`,
FFI). The coverage matrix in §3 lists each with its owner; §4.5–4.6 say
what a learning page would teach.

### F7 — Concept pages with nothing to run

`02-symbol-contracts.md`, `consumer-models.md`, `assurance-methods.md`,
`msvc-pe-abi-model.md`, and the five "Beyond ABI" pages contain no abicheck
invocation. `abi-surface.md` has a section titled "Checking the boundary
with abicheck" that is prose only. `msvc-pe-abi-model.md` has zero case links and zero
commands on the platform where evidence is weakest — there is no worked
Windows example anywhere in the series. `environment-drift.md` links no
case although `case170` is its fixture.

---

## 3. Coverage matrix — scanner features and scenarios vs. the series

"Taught" means a learning-tab page explains the *idea* and shows at least
one invocation or case; "linked" means the series points at the tool page
without teaching; "absent" means neither.

| Feature / scenario | Tool-track owner | Series status |
|---|---|---|
| Evidence ladder L0–L5 and the `--depth` dial | `use/scan-levels.md` | **taught** (five times — see F4) |
| Which change family needs which input | `reference/tool-comparison.md` §evidence-tier benchmark | **taught** (`08-detection.md` §2, `what-each-level-sees.md` §Reference) |
| Header AST (L2): castxml vs clang backends, compile context, why headers need flags | `use/scan-levels.md` §Compile context, `reference/header-backend-capabilities.md` | partial — `08-detection.md` §1a covers compile context; backend choice and its capability gaps (`case122`) only in reference |
| Source replay (L4) and the L5 graph | `use/producing-source-facts.md` | taught, but inside `build-source-data.md` (expert-level, contributor tone) |
| `scan` one-build audit (no `--against`): accidental export, private-header leak, unversioned export, RTTI leak, cross-source checks | `use/scan-levels.md` §single-build audit, `integration/scenarios/single-build-audit.md` | linked (hub §source scan); cases 143–151 indexed on the hub |
| PR-scoped scan (`--since`/`--changed-path`), `--budget`, `--dry-run` cost estimate | `use/scan-levels.md`, `use/github-action-source-scans.md` | linked only |
| Contract domains (`--contract` with `public`, `exports` or `all`), coverage exit `1` | `use/contract-evaluation.md` | taught on `contract-aware-compatibility.md` — but placed in Concepts, not next to Part 0's "define the public surface" |
| Consumer-scoped check (`--used-by APP`) | `use/appcompat.md` | taught (`consumer-models.md`, `evidence-and-detectability.md` §4) — no command |
| Plugin/`dlopen` contract (`--required-symbol[s]`, `plugin_abi`) | `use/plugin-systems.md` | linked (Part 0 §5, role path) |
| Directory/package compare — release fan-out, `--fail-on-removed-library`, RPM/Deb/tar/conda inputs, `--debug-info`/`--devel-pkg` | `use/multi-binary.md` (mostly flag reference), `use/cli-usage.md` | **absent** |
| Bundle layer — SONAME cohort skew, intra-bundle dependency drift, provider ownership, `--manifest` (pattern / template+instantiations / symbol), `--bundle-facts-out` stored-facts comparison, `--bundle-system-providers` | `use/multi-binary.md`, cases 84/90–93 | **absent** (the five bundle cases appear in no learn page) |
| Independent targets vs bundle (S15 vs S14), fan-in with `aggregate`, `project plan`/`.abicheck.yml` topology | `integration/scenarios/*`, `use/aggregate-reports.md`, `reference/project-targets-schema.md` | **absent** |
| Multi-TU `--dump-manifest`, extraction comparability (`NOT_COMPARABLE`, exit 6), `--diagnostic-comparison` | `use/dump-compare-flags.md`, ADR-050 | partial — `build-profile-comparability.md` covers the *why*; the manifest mechanics are absent |
| Dependency floors: `--env-matrix` runtime floors, `deps tree`/`deps compare`, sysroot/container checks | `reference/cli-reference.md` (`--env-matrix`), `integration/scenarios/dependency-and-container-checks.md` (`deps`) | **taught** (`dependency-floors.md`, `environment-drift.md`) |
| glibc/libstdc++-style symbol-versioning discipline, `glibc_symbol_versioned` policy, version-node kinds (cases 13/65/139/141/183/145) | `use/policies.md`, `reference/change-kinds.md` | partial — Part 5 §3 explains version scripts and glibc's append-only nodes in ~25 lines; the discipline as a *strategy a library adopts* is not taught (see §4.2) |
| Surface-growth reporting (`--surface-metrics`, `.abicheck.yml` `severity.addition: error`, `annotate-additions`; the release recommendation) | `use/api-surface-intelligence.md`, `use/github-action-recipes.md`, `use/annotations.md`; `use/output-formats.md` §Release recommendation and `--profile release-cut` | **absent** as a topic |
| Idioms and pattern-aware verdicts (`--pattern-verdicts`, opaque/PIMPL demotion, `opaque_invariant_broken`) | `use/api-surface-intelligence.md` | absent — Part 7 teaches the *patterns* but never that the scanner recognises them |
| Baselines: two kinds, storage recipes A–D, self-approval hazard, `publish-baseline`/`resolve-baseline`/`protect-committed-baseline` | `use/baseline-management.md`, `use/create-baseline.md`, `use/baseline-storage.md`, `reference/publish-baseline.md`, `reference/resolve-baseline.md`, `reference/protect-committed-baseline.md` | **absent** from the ladder |
| Severity / exit-code schemes, policies, suppressions (incl. reachability-aware refusal), `--pack` | `use/ci-gating.md`, `use/severity.md`, `use/policies.md`, `use/suppressions.md` | verdicts taught (`verdicts.md`); governance absent |
| Output formats, SARIF/Code Scanning, PR annotations, sticky comments, JUnit | `use/output-formats.md`, `use/annotations.md` | linked from role path only |
| Build-flag / toolchain drift (L3), probe matrix (`--probe-matrix`) | `use/dump-compare-flags.md`, `use/probe-harness.md` | drift taught (`environment-drift.md`, `build-profile-comparability.md`); probe matrix absent |
| Security-hardening drift (RELRO, canary, exec-stack, TLS model) | `use/security-hardening.md`, cases 128/133–138 | indexed on the hub; Part 5 §5 teaches the mechanism |
| Kernel BTF/CTF, kABI (cases 121/175/176) | `use/kernel-btf.md` | absent |
| SYCL/DPC++ host vs device context (cases 82/126) | `reference/sycl-test-abi-coverage.md` | absent |
| Python: wheel/manylinux floors, `abi3`, NumPy C-API, Cython, `post-manifest` (case163) | `use/python-extensions.md`, `use/post-python.md`, plans G23/G25/G26/G27 | absent |
| Debian `symbols` files | `use/debian-symbols.md` | mentioned once (Part 5) |
| ABICC drop-in (`compat`), migration from libabigail | `use/from-abicc.md`, `use/from-libabigail.md`, `use/tool-modes.md` | `08-detection.md` §3 explains *why* single-method checkers miss families; migration is linked from the role path |
| Static / header-only libraries | `learn/static-and-header-only.md`, `limitations.md` | taught (three treatments — F4) |
| Windows PE/PDB, macOS Mach-O | `reference/platforms.md` | model taught (`msvc-pe-abi-model.md`); no worked example (F7) |

Reading the matrix by *depth*: everything the scanner does at L0–L2 on a
single library is taught, most of it more than once. Everything that
distinguishes a **product** from a **library** — several binaries, several
build profiles, a baseline lifecycle, a PR pipeline, growth reporting,
packaging inputs — is either absent or lives only in flag-level tool docs.
That is the shape of the gap.

---

## 4. Assessment against the requested syllabus

### 4.1 Basic ABI/API handling (novice)

**Covered, well.** `abi-in-5-minutes.md` and Part 1 are the right first
two pages, and Part 0 is the right framing page. Fixes needed are
navigational rather than content: make the on-ramp the actual front door
(F2), state the ladder on the hub, drop Part 1 §8 in favour of
`glossary.md`, and add the failure-*symptom* view the series currently
lacks — the series is organised by mechanism (symbol, layout, C++,
linker, transitive), but a newcomer meets a break as a **symptom**:

| Symptom | Mechanism (Part) | First level that shows it |
|---|---|---|
| link error: undefined reference | symbol removed/renamed (2) | L0 |
| load error: `undefined symbol` / `version GLIBC_2.x not found` | symbol or version node removed, floor raised (5) | L0 |
| crash or silent corruption after upgrade, no rebuild | layout / vtable / calling convention (3, 4) | L1 |
| compile error after upgrade | source-only API break: rename, access, `explicit`, a default argument removed (6) | L2 |
| a call silently binds to a different value | default-argument *value* changed (6) | L2 |
| the source you compile against changed, but no binary did | public macro or inline function removed, template signature moved (6) | L4 |
| works on the build box, fails on the customer's distro | dependency floor / toolchain drift | L0 + `--env-matrix` |
| works for the app, breaks for the plugin / sibling library | consumer model, bundle contract | `--used-by`, directory compare |

A one-page "How a break shows up" in the orientation tier, mapping symptom
→ mechanism → level, would give novices the index they need and would be
the natural place to *introduce* the evidence ladder before the trio
teaches it in full.

### 4.2 Flow levels for libraries and system libraries (glibc, binutils)

**Partial.** Part 5 §3 explains version scripts and glibc's append-only
`GLIBC_2.x` nodes in ~25 lines; Part 7 Pattern 4 shows a version script;
`dependency-floors.md` and `environment-drift.md` cover the *deployment*
consequences (floors, binutils default drift). What is not taught is the
discipline itself as a strategy a library can adopt, and the ladder of
strategies that different tiers of the stack use:

| Tier | Example | Strategy | What abicheck offers |
|---|---|---|---|
| kernel ↔ user space | syscalls, kABI | never break; symbol namespaces, CRCs | `kernel-btf.md`, cases 175/176 |
| C runtime | glibc | one SONAME for decades; every ABI change is a *new version node*, old node kept as a compat symbol; append-only | `glibc_symbol_versioned` policy, `symbol_version_*` kinds, cases 13/65/139/141/183 |
| C++ runtime | libstdc++ | same SONAME since 2004; `GLIBCXX_3.4.x` nodes; the dual ABI as a *parallel* namespace rather than a break | `glibcxx_dual_abi_flip_detected`, `modern-cpp-toolchain-hazards.md`, case104 |
| system tooling | binutils / `ld` | linker defaults (`DT_RELR`, RPATH vs RUNPATH, hash style, CET/static-TLS, `--as-needed`, RELRO) drift between releases and move a library's contract without a source change | `environment-drift.md` §binutils (the first four), `use/security-hardening.md` (RELRO), L3 flag drift |
| vendor SDK / product | oneDAL, TBB, OpenSSL | SONAME bump per major, inline-namespace generations, explicit-instantiation matrix, experimental namespaces | Part 7, cases 99–101, `--manifest` |
| application plugin | host ↔ `dlopen` | required-symbol contract, direction reversed | `plugin_abi`, `--required-symbol` |

A "How system libraries stay compatible" page in the Platforms &
Toolchains group would close this: the glibc/libstdc++ model as the
worked example of *governing* the linker-level contract (Part 5's closing
"How to govern" box states five rules but never shows a real system
library applying them), the binutils role, where a
vendor library sits on the ladder, and how each strategy maps to an
abicheck policy profile and set of cases. `compatibility-direction.md`
already covers the direction axis that the plugin row needs.

### 4.3 ABI/API compatibility levels

**Covered, but scattered.** Part 0 §2 names eight dimensions;
`compatibility-direction.md` names six directions; `consumer-models.md`
names eight consumer shapes; `contract-aware-compatibility.md` names three
contract domains; `build-profile-comparability.md` names the precondition.
Each is a good page. What is missing is the single *ladder* a reader can
place their library on, in order of strength of promise:

1. no promise (internal, `detail::`, experimental namespaces)
2. source compatibility (recompile and it builds — `API_BREAK` is the gate)
3. binary backward compatibility (old consumers load the new library —
   `BREAKING` is the gate; the default `compare`)
4. binary forward compatibility (new consumers on the old library — the
   plugin/host case, `compatibility-direction.md`)
5. deployment compatibility (the floor: same binary loads on the same OS
   matrix — `--env-matrix`)
6. wire/data compatibility (outlives both — `data-wire-compatibility.md`)

with the verdict, exit code, SemVer action, and `--contract` domain each
level implies. Part 0 is the right owner; the ladder should replace the
eight-way dimension table's role as the page's spine (the dimensions stay
as the columns). `contract-aware-compatibility.md` belongs beside it in the
Define Your Contract group rather than in Concepts: "which surface is the
contract" is a contract question, not a tool internal.

### 4.4 Failure types and what each scan level discovers, up to source AST

**The strongest area, and the most over-taught.** The evidence trio
(`evidence-and-detectability.md` model, `what-each-level-sees.md` worked
example, `use/scan-levels.md` flag reference) is a deliberate design that
`docs/AGENTS.md` protects ("don't add a fourth page"). The problem is that
`08-detection.md` §2 and `build-source-data.md` §Evidence layers each
carry their own evidence table outside the trio, neither registered as a
summary and neither carrying the trio's "this topic lives in three pages"
banner (F4).

Two content gaps within the area:

- **The AST as an artifact.** The series explains *what* L2 sees but not
  what a header AST dump *is*: that `dump -H` produces a castxml or clang
  AST, that the compile context (include paths, defines, `-std`) decides
  what that AST contains, that castxml emits template *instantiations*
  only while the clang L2 backend also records the uninstantiated
  *pattern* (`reference/header-backend-capabilities.md`), and that the
  same header under two contexts is two different surfaces
  (`build-profile-comparability.md`). The L4 lesson is separate and must
  stay so: a change to an uninstantiated template's *signature*
  (`case122`, `min_evidence: L4`) is found by the source-ABI replay
  extractor over compile-unit evidence, not by switching the L2 frontend
  — the header backend does not model that template at all.
  The backend facts are already owned: `topics.yaml`'s
  `ast-frontend-resolution` topic has `reference/header-backend-capabilities.md`
  as canonical page. So the fix is not a new full section on the (already
  long) model page: `08-detection.md` §1a, which is the seed and sits on
  the educational tab, grows into the *summary* — registered as an
  `allowed_summaries` entry of that topic — with the L4 source replay
  presented as "the same idea, applied to `.cpp` files, per translation
  unit", and links to the reference page for the capability matrix.
- **The authority rule needs one home.** It is stated on the hub, in
  `build-source-data.md` §1, `evidence-and-detectability.md`,
  `what-each-level-sees.md`, and the glossary. Register it as a
  `terminology.yaml` term with one canonical page (decided before Phase 2
  moves any of `build-source-data.md`), so the other four become links.

### 4.5 Large products: multi-binary, build profiles, template libraries

**Absent from the series** (§3). The tool has the most machinery here
(bundle layer, `--manifest` instantiation matrix, stored bundle facts,
`--dump-manifest`, comparability gate, `aggregate` fan-in, `project`
topology, seeded scans, budgets, the L4 cache, RAM-aware worker caps) and
the most hard-won lessons (`docs/contribute/performance.md`: the one cost
cliff at L4 that tracks template depth; OOM-killed full-target replays on
oneTBB/oneDNN; a 4→20 minute regression from a serial preprocessor tier;
and `plans/g38-bundle-facts-model-and-multibuild-comparability.md`: its
origin in a real oneDAL checkout where CPU and data-parallel variants were
silently unioned). None of it is taught. Two pages in an "At scale" group
(joined there by §4.2's system-library page and §4.7's packaging page)
would close this:

- **Products, not libraries** — the bundle contract (a symbol one sibling
  imports from another is public *inside* the product even if hidden from
  users), SONAME cohorts, provider ownership, independent targets (S15) vs
  release bundle (S14) vs monorepo (S25), fan-out per target and fan-in
  with `aggregate`, per-library header roots and compile contexts, why the
  bundle analysis is ELF-only today, and comparing against *stored* bundle
  facts instead of re-opening old binaries. Cases 84, 90–93.
- **Template-heavy and header-heavy libraries** — what an instantiation
  actually exports, why the contract for oneDAL/MKL/libtorch-class
  libraries is the explicit-instantiation matrix, the `template:` +
  `instantiations:` manifest shape, the uninstantiated-template blind spot
  and its L4/clang-only remedy, the L4 cost cliff and the answer to it
  (seeded `--depth source --since`, `--budget`, the content-addressed L4
  cache restored via the Actions cache, `--dry-run` before spending),
  `--dump-manifest` for multi-TU surfaces, and why "not comparable" is a
  better answer than a page of phantom additions. Cases 17, 79, 85, 87,
  122, 191.

### 4.6 Practical integration: baselines, pipelines, PR level, surface growth

**Absent from the series; rich in the tool track.** The tool docs already
contain the *model* content a learning page needs — it just has to be
lifted out of the how-to pages and taught in order:

1. **Baselines as contracts.** A baseline is a frozen statement of what
   was promised; a project needs two (release-contract: "did we break
   what we shipped?", accepted-main: "did *this PR* introduce a break?");
   the recurring failure when a label skips the *check* instead of
   relaxing the *gate* (`use/baseline-management.md` explains it
   precisely); baseline identity is more than a version (profile,
   toolchain, scope, schema generation); what makes two snapshots
   comparable at all; a new library's first release; scanner-upgrade
   generations. Storage is a footnote (Recipes A–D) with one warning worth
   teaching: a committed baseline can approve itself in a PR unless it is
   read from the base commit.
2. **Where in the pipeline.** PR gate (cheap tiers always; seeded L4 for
   the changed TUs), merge-to-main refresh of the accepted-main baseline,
   nightly unseeded deep scan, release-cut publish of the release-contract
   baseline; the accuracy/cost trade-off per stage; why catching a break
   at PR time is the whole point (a break on `main` re-fails every
   unrelated PR until it is re-baselined).
3. **Report the surface, not only the breaks.** Additions are compatible
   but not invisible: `--surface-metrics` roll-ups
   (`public_surface_grew`, undocumented-export ratio), the
   `severity.addition: error` config gate for frozen APIs, `annotate-additions`
   notices on the PR, the SemVer/SONAME recommendation, and single-build
   hygiene (accidental exports, unversioned exports) as *growth you did
   not intend*. This is the one place the series would say plainly that
   "0 breaks" is not the same as "nothing to review".
4. **Rollout and governance.** Advisory → gating, `intentional-breaking-change`
   labels that relax gates rather than skip checks, suppressions as
   *contract statements* with owners and expiry, the reachability-aware
   refusal to suppress a public-reachable break, policy profiles as
   named contract shapes, packs.

Each of these already has a tool-track page, and several of those pages
are *registered* owners in `topics.yaml`. So "add a learning page" is
decided per topic against the registry, one of three ways — never by
writing a second explanation next to a registered one:

1. **The registered canonical page is already an explanation** —
   `baseline-lifecycle` → `use/baseline-management.md` (front matter
   `doc_type: explanation`; its sections are exactly the model above). No
   new page, and no tab move either: the page carries Action inputs and
   internal names (`abicheck.product_baseline`), which the educational tab
   forbids in prose, so it stays in the tool track and the ladder *links*
   to it as the Practice tier's baseline entry — the same treatment §5
   gives the evidence trio. "Baselines as contracts" is therefore a
   ladder link, not a ★ page and not a move.
2. **The registered canonical page is a flag/how-to reference** —
   `bundle-analysis` → `use/multi-binary.md`. The new learning page takes
   `canonical_page` and the how-to is re-registered as a `task_pages`
   entry *in the same change* (`docs/contribute/documentation.md`
   "Retiring or merging a page" is the procedure), so the topic never has
   two owners.
3. **The page spans several registered topics** — rollout & governance
   over `policies`, `suppressions` and the migration scenario; triage over
   `evidence-model` plus `use/troubleshooting.md` and `limitations.md`
   (those two own no registered topic, so the page links them and
   registers nothing about them); "where in the pipeline" over
   `github-actions-surface` and `project-integration`. The page owns one
   new cross-cutting topic (registered) and is an `allowed_summaries`
   entry of each registered topic it touches; it links, never re-explains.

Under `docs/AGENTS.md` "When does a new fact need a new page?", each ★
page in §5 rests on criterion 2 (a mental model the how-to does not state:
"a product is one contract", "additions are not invisible", "the
explicit-instantiation matrix *is* the contract") or criterion 4 (a new
audience: readers who need the model before any flag). A proposed page
that cannot name its criterion is folded into an existing owner instead —
which is why §4.7's "reading machine output" is *not* a new page:
`use/output-formats.md` already reads the report field by field and is the
registered owner.

### 4.7 Other aspects worth covering

- **Triage: is this finding real?** `start/first-report.md` reads one
  report; nothing teaches the checklist for a *suspicious* finding —
  header/binary mismatch as the first suspect, symbols-only false positives
  and the `.dynsym` filter, a comparability mismatch, missing DWARF,
  compile-context drift. `limitations.md` and `use/troubleshooting.md`
  hold the pieces.
- **Consumers beyond C/C++.** FFI bindings (Rust/Go/Python `ctypes`),
  CPython extensions and the `abi3` floor, manylinux/glibc floors for
  wheels, NumPy's C-API envelope, Debian `symbols` as a *consumer-declared*
  contract. Today wheels appear on one page.
- **Windows and macOS worked examples.** `msvc-pe-abi-model.md` teaches a
  different mental model with no case and no command; the MSVC lane exists
  in CI and the catalog has PE-capable cases. macOS has neither an owning
  page nor a break-shaped fixture, so only the Windows example is planned
  (§6).
- **Other ABI domains** (kernel kABI/BTF, SYCL host vs device): one
  "advanced/expert" page listing them as further reading would connect the
  existing tool docs to the ladder without teaching them in full.
- **Reading machine output.** The AI-agent role path exists, and
  `use/output-formats.md` already reads the report field by field; what is
  missing is a link from the ladder to it, and a `reference/` home for
  `impact-analysis.md`'s field detail (registered as that topic's
  `reference_page`, with the narrative page staying where it is).
- **Lean on the failure demos.** Every case page has a "Real Failure
  Demo"; concept pages should link one per section (F7), and Part 7 —
  the pattern capstone — links two cases in 450 lines.

---

## 5. Proposed target shape

One ladder, levelled, with the numbered spine kept intact. The tiers are a
**reading order**, rendered on the hub and carried by each page's `level:`
badge — not a replacement for the nav's by-question groups, which are a
recorded decision (`docs/AGENTS.md` "Layout", `mkdocs.yml`'s nav comments,
ADR-051) this plan keeps. Within each group the order becomes
non-decreasing in level, and a page changes *tab* only when it carries no
internal module names in prose (`environment-drift.md` was checked and is
clean; `contract-aware-compatibility.md` is not — it names
`ExportSurface.exclusion_is_provable` and the reason-code registry — so
it stays on the Concepts tab with the evidence trio, and the ladder links
across tabs). The
level column uses the four values the docs gate accepts — novice =
`beginner`, practitioner = `intermediate`, then `advanced` and `expert` —
so every page can carry its tier without changing the gate.

| Tier | Level | Pages (★ = new, ↻ = merged/moved) |
|---|---|---|
| **0 · Orientation** | beginner | ABI in Five Minutes · ★ How a break shows up (symptom → mechanism → level) · Cheat Sheet · Glossary (↻ absorbs Part 1 §8) |
| **1 · Foundations** | beginner → intermediate | Part 0 Product Contract (↻ gains the compatibility-levels ladder, §4.3) · Part 1 Foundations (the spine's own 0 → 7 order is kept) · Your ABI Surface |
| **2 · Mechanics** | intermediate (branches: advanced) | Parts 2, 3, 4, 5, 6 — with Class Layout, Exception Unwinding, Modern Hazards, MSVC/PE listed *under* their Part as optional "go deeper" branches, `advanced`, outside the spine's monotonicity check (Part 4 shortens its inline summaries to a sentence and a link) |
| **3 · Define the contract** | intermediate | Compatibility Direction · Consumer Models · Contract-Aware Compatibility (linked; stays on the Concepts tab) · Build-Profile Comparability · Static & Header-Only Contracts (↻ from Beyond ABI — library *shape* is a contract question) |
| **4 · Evidence & detection** | intermediate | ↻ Detecting Breaks (§1a grows into the AST summary, §2 becomes links into the trio) · Evidence & Detectability and What Each Level Sees (stay on the Concepts tab; the appendix of removed flags moves to `use/companion-commands.md`) · Assurance Beyond Static Checking |
| **5 · Practice** | intermediate | Baselines as contracts (`use/baseline-management.md`, linked from the ladder, stays in the tool track — §4.6 case 1) · ★ Where in the pipeline (PR / main / nightly / release) · ★ Report the surface, not only the breaks · ★ Rollout & governance · ★ Triage a suspicious finding |
| **6 · Design** | intermediate | Part 7 Designing for Stability (↻ adds "the scanner recognises these patterns": idioms, pattern-aware verdicts) |
| **7 · At scale** | advanced | ★ Products, not libraries (multi-binary) · ★ Template- and header-heavy libraries · ★ How system libraries stay compatible (glibc / libstdc++ / linker) · Dependency & Runtime Floors · Environment & Toolchain Drift (↻ from Concepts) · ★ Packages and consumers (deb/rpm/conda/wheel, `abi3`, FFI) |
| **8 · Beyond static ABI** | advanced | Behavioral · Data & Wire · Ownership · Concurrency — four pages, as `topics.yaml` records (criterion 4); the shared "cannot decide this" argument gets one owner and four links |
| **Concepts tab** (tool internals) | intermediate → expert | Verdicts (`intermediate`, reclassified from `beginner`) · Contract-Aware Compatibility (member here; Tier 3 links to it) · Evidence & Detectability · What Each Level Sees · Architecture · Build & Source Data (↻ split: model and workflow stay and keep `canonical_for`; findings/schema/storage become a registered `reference_page`) · Graph Coverage (↻ pass-state detail to a `reference_page`) · Impact Assessment (↻ plan/ADR framing removed; field detail to a `reference_page`) · ELF Symbol Filtering · Limitations |

Reading paths by role stay on the hub, but each path is then a walk *up*
the ladder rather than a jump list across tabs.

The `level:` front matter should carry the tier's level (mapped as above)
on every page in the learning tree (today 16 pages have none). Level
alone cannot reconstruct the ladder (tiers 2–6 are all `intermediate`),
so tier membership and order get one machine-readable owner:
`docs/_meta/learning-ladder.yaml`, next to `topics.yaml`. It holds
**two ordered sequences**, one per track: `educational` (tiers 0–8, the
ABI/API Compatibility tab) and `concepts` (the tool track, in its own
simple-to-expert order starting at `verdicts.md`). Every learn page
belongs to exactly one sequence; a page a tier merely *links* (the
evidence trio from Tier 4, the baseline page from Tier 5) is listed as
a link in that tier, not as a member, so it is never counted twice.
Monotonicity is checked per sequence — the tool track restarting at
`intermediate` after the educational track ends at `advanced` is two
ladders, not a regression. The hub's ladder table is then *generated*
by joining that file with each page's `level:` — a small script splicing
between sentinels with a `--check` mode, the pattern ADR-051 established
for the platform matrix — so neither the badge column nor the tier rows
are a hand-copied table. The hub itself is the page that *renders* the
ladder and is explicitly exempt from it (the one documented exception,
stated in the ladder file). Branch pages (Tier 2's "go deeper" deep dives) are marked as such in the
ladder file and checked only against their parent Part. `--check` fails on any other learn page missing from the
ladder, on a ladder entry that is not a page, and on any level regression
along either sequence's full reading order (not only within a nav
group). The hub renders the result as a badge per row so the ladder is visible in the
navigator, not only in this plan.

---

## Goal & acceptance criteria

The series is done when a reader can answer, from the learning tab in
order: what an ABI break is and how it shows up; which level of evidence
finds each kind; what their own contract is; how to set up a baseline and
a PR gate on a real project; how to read a report that shows growth as
well as breaks; and what changes when the product is several binaries or
a template library. Checkable form:

- every page under the ABI/API Compatibility tab except the hub carries
  `level:` and a previous/next footer, every learn page except the hub is
  in exactly one of `docs/_meta/learning-ladder.yaml`'s two sequences,
  and level is
  non-decreasing along each sequence's full reading order and within
  each nav group;
- every ★ page in §5 exists, is registered in `topics.yaml` per §4.6's
  three cases, and has at least one linked case and — except "How a break
  shows up", the pre-tool Tier 0 page, which deliberately runs nothing —
  at least one runnable invocation;
- no two learning pages re-explain the same registered topic
  (`scripts/check_docs_contract.py` clean, and its duplicate-block warning
  count no higher than before);
- every row of §3's coverage matrix reads "taught" or "linked", none
  "absent" — the kernel BTF/CTF and SYCL/DPC++ rows become "linked"
  through the last section of the packages page (page specs B9 §6), at
  pointer depth.

## Files & surfaces

`docs/learn/**` (content, front matter, footers); `mkdocs.yml` (nav order
and `redirect_maps`); `docs/_meta/topics.yaml`, `terminology.yaml` and the new
`learning-ladder.yaml` (new topics, re-registrations, the authority-rule
term, tier membership);
`docs/AGENTS.md` "Layout" (the two tab moves); `docs/index.md` and
`start/getting-started.md` (front-door links); `docs/use/policies.md` and
`docs/use/suppressions.md` (each links a hub anchor that moves);
`docs/use/companion-commands.md` (receives the removed-flags appendix);
the hub's generated ladder splice and its `scripts/` generator, with the
ladder rules in `scripts/check_docs_contract.py` and a nav-order check
beside `scripts/check_ai_readiness.py`; `docs/contribute/plans/index.md`
(this row). No `abicheck/` code, no example fixtures, no generated case
pages.

## Tests

The existing gates are the tests: `scripts/check_docs_contract.py`
(ownership, front matter, duplicate blocks, retired surfaces),
`mkdocs build --strict` (links, nav), `scripts/check_ai_readiness.py`
(`mkdocs-nav-coverage`, `doc-count-sync`, `changekind-docs`), and
`scripts/check_docs_review_triggers.py` (`depends_on`). Phase 1 adds
three: ladder rules inside `scripts/check_docs_contract.py` (completeness,
level monotonicity along each sequence's whole reading order, footers,
reading paths), a generator drift check for the hub's splice, and an
AI-readiness check that each learning nav group is non-decreasing in
`level:` — the acceptance criterion above, made executable. Anchor
rewrites need `mkdocs.yml`'s `validation: anchors:` setting (page specs
A2d), since `mkdocs build --strict` does not validate fragments by default.

## Effort & risk

L overall (the index row's estimate): Phase 1 is S, Phase 2 M, Phase 3 L
(nine pages, each needing a topic decision), Phase 4 M. The main risk is
the one F4 documents — a new page becoming a second explanation of a
registered topic — which §4.6's three-case rule and the docs-contract gate
exist to prevent. The second is URL and anchor churn for externally linked
pages; every move keeps a redirect and every moved anchor is rewritten.

## 6. Phased change list

The artifact-level detail for every item below — the ladder file schema,
the generator contract, the per-page level table, the `topics.yaml`
fragments, the rebuilt hub's layout, and a section-by-section spec for
each new and reworked page — lives in the companion page
[Learning series — page specifications](learning-series-page-specs.md),
whose §F sequences the work into independently mergeable pull requests
(Phase 1 = P1–P2, Phase 2 = P3, Phase 3 = P4–P8, Phase 4 = P9). Where
the order below and §F differ, §F wins: it records the dependencies
between artifacts.

Each phase is independently mergeable and leaves the site consistent.
Every moved page keeps its URL via `mkdocs.yml` `redirect_maps`; every new
page registers a topic in `docs/_meta/topics.yaml`, carries front matter,
and enters the nav (`scripts/check_docs_contract.py`,
`mkdocs build --strict`, and the `mkdocs-nav-coverage` AI-readiness check
gate all of this).

**Phase 1 — navigation and hub (no new prose).**

- Rebuild `abi-api-handling.md` around the tier table in §5; move the
  23-row break-family index to the cheat sheet and the two "deeper"
  sections on the source scan and the L5 graph to the trio, whose pages
  exist today. The third "deeper" section (the "now run it" CI table)
  stays on the hub until Phase 3 creates its practice-page owner, so this
  phase leaves no dangling link. Two consequences to carry: inbound anchor links (`08-detection.md` and
  five other pages link the index and the two "deeper" sections by
  fragment, which `mkdocs build --strict` does not verify) are rewritten,
  and the hub keeps its role as `terminology.yaml`'s defining page for
  the terms ABI and API.
- Make the on-ramp the front door: hub, `docs/index.md`, and
  `start/getting-started.md` link it first; remove the "don't start here"
  admonition.
- Add a consistent previous/next footer to every page on the educational
  tab that lacks one — the 15 educational-tab deep dives and the three
  orientation pages (on-ramp, cheat sheet, glossary); the numbered Parts
  already have one. The 11 Concepts-tab pages get the same footer along
  their own sequence, since the ladder file orders them too.
  Give `what-each-level-sees.md` a forward "Next" and resolve Part 7's
  double next.
- Keep the nav's by-question groups; reorder within each so level is
  non-decreasing. Add `level:` front matter to the 16 pages lacking it
  *and* reconcile the 23 existing values with the tier mapping in §5 —
  today's values were assigned page by page, not against a ladder, so
  several disagree with it: `verdicts.md` (`beginner`) becomes the
  Concepts tab's first, `intermediate` entry; the four Tier 8 pages move
  from `intermediate` to `advanced`; and the Tier 2 "go deeper" pages
  keep `advanced` but are marked as *branches* in the ladder file, which
  the monotonicity check skips (a branch is read optionally, off the
  spine, and must only be ≥ the Part it hangs from). The one page that changes tab is
  `environment-drift.md`, verified clean of internal names (§5);
  `docs/AGENTS.md` "Layout" is updated for that move in the same change.

**Phase 2 — de-duplicate (content moves, no new topics).**

- Evidence model: `08-detection.md` §2 and `build-source-data.md`
  §Evidence layers become short summaries linking the trio, and are
  registered as such; the trio banner goes on all three trio pages.
- Part 4 shortens the inline summaries of its three split-out sections to
  one sentence and a link each (they stay registered `allowed_summaries`).
- Class layout gets one owner: `class-layout-abi.md` is registered as
  `canonical_page` of a new `class-layout` topic (it is the page that maps
  each layout change to its `ChangeKind` and evidence tier). Part 3 keeps
  the C-level fundamentals (size, offset, alignment, enums, unions,
  bitfields) and hands C++ class layout to that page with a summary and a
  link; Part 4 §7 becomes the same. Both Parts are registered as
  `allowed_summaries` of the new topic.
- Part 1 §8 → `glossary.md`. Verdicts get one table: `verdicts.md` keeps
  the numeric verdict/exit-code table; `architecture.md` links instead of
  restating; `abi-cheat-sheet.md` and `07-designing-for-stability.md`
  shrink their verdict-meaning tables to a one-line legend plus a link
  and are added to the `verdicts` topic's `allowed_summaries`.
- The four "cannot decide this" pages stay four pages (`topics.yaml`
  records that decision under criterion 4, with four registered topics).
  The repeated argument gets one owner — `evidence-and-detectability.md`
  §5 "What ABI tools cannot prove" is the existing home — and the four
  pages open with a sentence and a link instead of the argument.
  `static-and-header-only.md`, the fifth page in today's Beyond ABI
  group, is listed under Tier 3 on the hub's ladder.
- `graph-coverage.md`'s pass-state detail, `impact-analysis.md`'s field
  detail, and `build-source-data.md`'s schema/storage sections move to
  `reference/` as each topic's registered `reference_page`; the
  narrative pages stay in `docs/learn/` and keep `canonical_for` (a
  narrative owner cannot live under `reference/`). Each move edits
  `topics.yaml` in the same change and follows
  `docs/contribute/documentation.md` "Retiring or merging a page". The
  removed-flags appendix moves to `use/companion-commands.md`, the CLI
  migration page.

**Phase 3 — new pages, in the order a reader needs them.**

1. How a break shows up (§4.1) — new topic, criterion 2; registered as an
   `allowed_summaries` entry of `evidence-model`, since it introduces the
   ladder the trio owns
2. Baselines as contracts — a ladder link to `use/baseline-management.md`
   (§4.6 case 1), listed for reading order; no new file, no tab move
3. Where in the pipeline (§4.6 item 2) — §4.6 case 3
4. Report the surface, not only the breaks (§4.6 item 3) — criterion 2
5. Products, not libraries (§4.5) — takes `bundle-analysis` from
   `use/multi-binary.md` (§4.6 case 2)
6. Template- and header-heavy libraries (§4.5) — criterion 2
7. How system libraries stay compatible (§4.2) — criterion 4
8. Rollout & governance (§4.6 item 4) — §4.6 case 3
9. Triage a suspicious finding (§4.7) — §4.6 case 3
10. Packages and consumers (§4.7) — criterion 4

The "other ABI domains" further reading from §4.7 (kernel kABI/BTF, SYCL)
is not a page of its own: it is the last section of Packages and
consumers (page specs B9 §6), at pointer depth. A macOS worked example is
out of scope for this plan (see the list at the end of this section): no
learn page owns Mach-O today (Part 5 carries platform parallels only),
and the only macOS fixture, `tests/test_cross_platform_integration.py`'s
clang-built `.dylib`, exercises metadata parsing rather than a
compatibility break — it needs an owning page and a break-shaped fixture
before a spec can name either.

The ownership rule for every entry is §4.6's three-case decision; the
how-tos keep the commands and the learning page keeps the model.

**Phase 4 — worked examples on every concept page.**

- At least one runnable invocation and one linked case per concept page
  that teaches something the scanner detects (F7). The four Tier 8 pages
  and Assurance Beyond Static Checking teach what static comparison
  cannot decide, so their item is one concrete non-static check per page
  as a shell line and no case (page specs C15); a Windows worked example
  for `msvc-pe-abi-model.md`; `case170` on
  `environment-drift.md`; the five bundle cases on the products page; the
  template cases on the templates page; and `build-profile-comparability.md`
  links the probe matrix (`use/probe-harness.md`) as the tool's answer to
  "compare one library across build configurations", which turns §3's
  probe-matrix row from absent to linked.
- Part 0 gains the compatibility-levels ladder; Part 7 gains the
  idiom/pattern-verdict section.

**Out of scope for this plan:** any change to what the scanner detects or
reports; the tool-track how-to pages' commands (they stay the fact owners);
the example catalog; a macOS worked example (no owning learn page and no
break-shaped Mach-O fixture exist yet — both come first). If writing a practice page exposes a missing tool
feature — the most likely candidate is a first-class "surface growth"
report rather than three separate flags — record it in
`docs/contribute/usecase-registry.yaml` rather than papering over it in
prose.

---

## 7. Constraints the rewrite must respect

- `docs/AGENTS.md`'s one-owner rule and `topics.yaml`: a new learning page
  that restates a tool page's content is the defect this plan is fixing,
  not a way to fix it. Every new page's ownership is decided by §4.6's
  three cases against the registry before it is written.
- The evidence trio is deliberately three pages and nothing here adds a
  fourth: the AST material is a registered summary on `08-detection.md`
  of the reference page that owns it, and the authority rule becomes a
  `terminology.yaml` term with one defining page.
- Every moved or renamed page keeps a redirect; pre-Stage-4 `concepts/…`
  URLs already redirect and must keep doing so.
- The educational tab keeps abicheck's internal module names out of
  prose (`depends_on` front matter carries traceability instead).
- Headline counts (change kinds, case count) are pulled from their fact
  owners, never typed into a learning page. The line, page and row counts
  *this* plan quotes are a diagnostic snapshot taken at the commit that
  added it, not fact owners; re-measure rather than trust them when a
  phase starts.
