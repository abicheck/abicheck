---
doc_type: contributor
level: advanced
lifecycle: active
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
cheat sheet, and 27 deep dives — ~10,400 lines), their `mkdocs.yml` nav
placement, `docs/_meta/topics.yaml` ownership, and — for the coverage
matrix — the tool track (`docs/use/`, `docs/integration/scenarios/`,
`docs/reference/`), the 197-case example catalog, and
`docs/contribute/usecase-registry.yaml`.

---

## 1. What exists today

The learning material is physically one directory but is navigated as two
tabs (`docs/AGENTS.md` "Layout"):

| Tab | Pages | Role |
|---|---|---|
| **ABI/API Compatibility** (educational) | hub, on-ramp, cheat sheet, Parts 0–7, capstone (`08-detection.md`), glossary, and 17 deep dives grouped as Define Your Contract / ABI Mechanics / Beyond ABI / Platforms & Toolchains / Designing Stable Interfaces / Verification & Assurance | "understand the problem" — tool-independent |
| **Concepts** (tool track) | `verdicts.md`, `contract-aware-compatibility.md`, `evidence-and-detectability.md`, `what-each-level-sees.md`, `architecture.md`, `build-source-data.md`, `graph-coverage.md`, `impact-analysis.md`, `environment-drift.md`, `elf-symbol-filtering.md`, `limitations.md` | "how abicheck models it" |

Declared reader level (front matter `level:`), where a page declares one:

| Level | Pages |
|---|---|
| beginner | `verdicts.md` only |
| intermediate | 22 pages (every other deep dive that has front matter) |
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
cost is that the 27 deep dives have *no* previous/next links at all — only
trailing "See also" lines — so a reader who steps off a Part into
`class-layout-abi.md` has no way forward except back. Three concrete
navigation faults:

- `07-designing-for-stability.md` ends with two different "next" targets
  (the capstone and the hub).
- `evidence-and-detectability.md` and `what-each-level-sees.md` each name
  the other as "Next" — a two-page loop.
- `08-detection.md` spends its first ten lines explaining why it is not
  "Part 8" — a symptom of the two orders disagreeing.

### F2 — The entry page tells newcomers not to start there

`abi-api-handling.md` is the URL the series is published and linked under,
and its first admonition is "New to the topic? Don't start here." It is a
390-line *navigator* (a role table, a role-path table, a 22-row break-family
index, three "deeper" sections on scan/L5/CI) rather than a curriculum. The
two pages a novice most needs — the five-minute on-ramp and the glossary —
have exactly one inbound link each across the whole docs tree.

### F3 — Complexity does not grow monotonically

- 22 of 25 pages that declare a level say `intermediate`; the front matter
  cannot express the ladder the user asked for (novice → practitioner →
  advanced → expert), so neither can the nav.
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

| Topic | Taught in full on | Also (re)explained on |
|---|---|---|
| L0–L5 evidence model | `evidence-and-detectability.md` (723 lines) | `what-each-level-sees.md` (515), `08-detection.md` §2, `architecture.md` §Evidence layers, `build-source-data.md` §Evidence layers — five L0–L5 tables |
| "a static comparer structurally cannot see this" | — | `behavioral-compatibility.md`, `data-wire-compatibility.md`, `ownership-and-lifetime.md`, `concurrency-and-initialization.md` each open with the same argument, in the same shape, and `assurance-methods.md` summarises all four |
| Struct/class layout | `03-type-layout.md` | `04-cpp-abi.md` §7, `class-layout-abi.md` (which calls itself "the single page") |
| Platform capability matrix | `reference/platforms.md` | `architecture.md`, `limitations.md`, `05-linker-elf.md` §parallels, `msvc-pe-abi-model.md` |
| Verdict → exit-code table | `verdicts.md` | `architecture.md`, `abi-cheat-sheet.md`, `07-designing-for-stability.md` |
| Symbols-only false positives | `elf-symbol-filtering.md` | the closing section of `limitations.md`, near-verbatim |
| Glossary | `glossary.md` | `01-foundations.md` §8 |

The `docs/AGENTS.md` rule ("one question is explained in full on exactly
one page") is enforced for the topics registered in `topics.yaml`; most of
the pairs above predate registration.

### F5 — Contributor material sits inside the learning tree

`impact-analysis.md` opens with "slice 1 of G29 Phase 3 (ADR-052)" and links
`contribute/plans/`; `graph-coverage.md` uses internal pass-state field
names and "G31 Phase B"; `build-source-data.md` (810 lines, the longest page
in the tree) cites four ADRs and carries schema/storage/redaction detail;
`architecture.md` is `audience: contributor`; `evidence-and-detectability.md`
ends with an appendix cataloguing *removed* scan flags and carries a
self-correction about a previously over-claimed coverage figure. A user
reading "Concepts" top to bottom crosses from mental model into
release-engineering vocabulary without a marker.

### F6 — The practice track is missing from the learning tab

Measured by where a topic is *taught* (not merely mentioned):

| Practice topic | Tool-track home | Learning-tab treatment |
|---|---|---|
| What a baseline is; why a project needs two (release-contract vs accepted-main); baseline identity and comparability; scanner-upgrade generations | `use/baseline-management.md`, `use/create-baseline.md`, `use/baseline-storage.md`, `reference/publish-baseline.md`, `reference/resolve-baseline.md`, `reference/protect-committed-baseline.md` | absent from every numbered Part; mentioned in six deep dives only in passing |
| PR-level gating (catch it before merge, not at release) | `use/ci-gating.md`, `use/github-action*.md`, `use/scan-levels.md` §PR gate | one sentence each in Parts 5/6/7; `--since`-seeded PR scan only in `build-source-data.md` |
| Reporting surface *growth*, not only breaks (`--surface-metrics` roll-ups, `severity-addition: error`, `annotate-additions`, the SemVer bump recommendation) | `use/api-surface-intelligence.md`, `use/github-action-recipes.md` §"Detect unintentional API expansion", `use/annotations.md`, `use/severity.md` | no page treats additions as a topic; the cheat sheet lists them as "safe" and stops |
| Multi-binary products (bundle contract, SONAME cohort, provider ownership, instantiation manifest) | `use/multi-binary.md` (670 lines, mostly flag reference), `integration/scenarios/release-bundle.md`, `multi-dso-project.md`, `monorepo.md` | Part 0 §5 names the shape in one paragraph; nothing teaches it |
| Rollout: advisory → gating, intentional-break labels, suppression hygiene | `use/policies.md`, `use/suppressions.md`, `integration/scenarios/migration-and-rollout.md` | absent |
| Packaging and consumers: `.deb`/`.rpm`/conda/wheel inputs, manylinux glibc floors, CPython `abi3`, FFI bindings | `use/debian-symbols.md`, `start/scanning-conda-packages.md`, `use/python-extensions.md`, `use/post-python.md` | wheels appear on one page (Part 1); `abi3`/FFI on none |

### F7 — Concept pages with nothing to run

`02-symbol-contracts.md`, `compatibility-direction.md`, `consumer-models.md`,
`assurance-methods.md`, `msvc-pe-abi-model.md`, and the four "Beyond ABI"
pages contain no abicheck invocation. `abi-surface.md` has a section titled
"Checking the boundary with abicheck" that is prose only.
`compatibility-direction.md` says "run `compare` once per direction" and
shows no command. `msvc-pe-abi-model.md` has zero case links and zero
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
| Contract domains (`--contract public|exports|all`), coverage exit `1` | `use/contract-evaluation.md` | taught on `contract-aware-compatibility.md` — but placed in Concepts, not next to Part 0's "define the public surface" |
| Consumer-scoped check (`--used-by APP`) | `use/appcompat.md` | taught (`consumer-models.md`, `evidence-and-detectability.md` §4) — no command |
| Plugin/`dlopen` contract (`--required-symbol[s]`, `plugin_abi`) | `use/plugin-systems.md` | linked (Part 0 §5, role path) |
| Directory/package compare — release fan-out, `--fail-on-removed-library`, RPM/Deb/tar/conda inputs, `--debug-info`/`--devel-pkg` | `use/multi-binary.md`, `use/cli-usage.md` | **absent** |
| Bundle layer — SONAME cohort skew, intra-bundle dependency drift, provider ownership, `--manifest` (pattern / template+instantiations / symbol), `--bundle-facts-out` stored-facts comparison, `--bundle-system-providers` | `use/multi-binary.md`, cases 84/90–93 | **absent** (the five bundle cases appear in no learn page) |
| Independent targets vs bundle (S15 vs S14), fan-in with `aggregate`, `project plan`/`.abicheck.yml` topology | `integration/scenarios/*`, `use/aggregate-reports.md`, `reference/project-targets-schema.md` | **absent** |
| Multi-TU `--dump-manifest`, extraction comparability (`NOT_COMPARABLE`, exit 6), `--diagnostic-comparison` | `use/dump-compare-flags.md`, ADR-050 | partial — `build-profile-comparability.md` covers the *why*; the manifest mechanics are absent |
| Dependency floors: `--env-matrix` runtime floors, `deps tree`/`deps compare`, sysroot/container checks | `use/companion-commands.md`, `integration/scenarios/dependency-and-container-checks.md` | **taught** (`dependency-floors.md`, `environment-drift.md`) |
| glibc/libstdc++-style symbol-versioning discipline, `glibc_symbol_versioned` policy, version-node kinds (cases 13/65/139/141/183/145) | `use/policies.md`, `reference/change-kinds.md` | partial — Part 5 §3 explains version scripts and glibc's append-only nodes in ~25 lines; the discipline as a *strategy a library adopts* is not taught (see §4.2) |
| Surface-growth reporting (`--surface-metrics`, `severity-addition`, `annotate-additions`, SemVer recommendation) | `use/api-surface-intelligence.md`, `use/github-action-recipes.md`, `use/annotations.md` | **absent** as a topic |
| Idioms and pattern-aware verdicts (`--pattern-verdicts`, opaque/PIMPL demotion, `opaque_invariant_broken`) | `use/api-surface-intelligence.md` | absent — Part 7 teaches the *patterns* but never that the scanner recognises them |
| Baselines: two kinds, storage recipes A–D, self-approval hazard, `publish-baseline`/`resolve-baseline`/`protect-committed-baseline` | `use/baseline-*.md`, `reference/*-baseline.md` | **absent** from the ladder |
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
| compile error after upgrade | source-only API break (6) | L2 |
| behaviour changed, nothing else did | inline body, macro, `constexpr`, default arg (6) | L4 |
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
| system tooling | binutils / `ld` | the defaults (`--as-needed`, `-z relro`, `DT_RELR`, default symver) drift between releases and move a library's contract without a source change | `environment-drift.md` §binutils, L3 flag drift |
| vendor SDK / product | oneDAL, TBB, OpenSSL | SONAME bump per major, inline-namespace generations, explicit-instantiation matrix, experimental namespaces | Part 7, cases 99–101, `--manifest` |
| application plugin | host ↔ `dlopen` | required-symbol contract, direction reversed | `plugin_abi`, `--required-symbol` |

A "How system libraries stay compatible" page in the Platforms &
Toolchains group would close this: the glibc/libstdc++ model as the
worked example of *governing* the linker-level contract (Part 5's closing
section promises this and links out), the binutils role, where a
vendor library sits on the ladder, and how each strategy maps to an
abicheck policy profile and set of cases. `compatibility-direction.md`
already covers the direction axis that the plugin row needs.

### 4.3 ABI/API compatibility levels

**Covered, but scattered.** Part 0 §2 names eight dimensions;
`compatibility-direction.md` names five directions; `consumer-models.md`
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
two *more* pages (`08-detection.md` §2, `architecture.md`) restate the L0–L5
table and `build-source-data.md` restates it a third time, none of them
carrying the trio's "this topic lives in three pages" banner (F4).

Two content gaps within the area:

- **The AST as an artifact.** The series explains *what* L2 sees but not
  what a header AST dump *is*: that `dump -H` produces a castxml or clang
  AST, that the compile context (include paths, defines, `-std`) decides
  what that AST contains, that castxml emits instantiations only while the
  clang backend can see uninstantiated templates (`case122`,
  `reference/header-backend-capabilities.md`), and that the same header
  under two contexts is two different surfaces (`build-profile-comparability.md`).
  `08-detection.md` §1a is the seed; it should grow into one section of
  the model page, with the L4 source replay presented as "the same idea,
  applied to `.cpp` files, per translation unit".
- **The authority rule needs one home.** It is stated on the hub, in
  `build-source-data.md` §1, `evidence-and-detectability.md`, and
  `what-each-level-sees.md`. One statement, three links.

### 4.5 Large products: multi-binary, build profiles, template libraries

**Absent from the series** (§3). The tool has the most machinery here
(bundle layer, `--manifest` instantiation matrix, stored bundle facts,
`--dump-manifest`, comparability gate, `aggregate` fan-in, `project`
topology, seeded scans, budgets, the L4 cache, RAM-aware worker caps) and
the most hard-won lessons (`docs/contribute/performance.md`: the one cost
cliff at L4 that tracks template depth; OOM-killed full-target replays on
oneTBB/oneDNN; a 4→20 minute regression from a serial preprocessor tier;
G38's origin in a real oneDAL checkout where CPU and data-parallel variants
were silently unioned). None of it is taught. An "At scale" group with two
pages would close this:

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
   `severity-addition: error` gate for frozen APIs, `annotate-additions`
   notices on the PR, the SemVer/SONAME recommendation, and single-build
   hygiene (accidental exports, unversioned exports) as *growth you did
   not intend*. This is the one place the series would say plainly that
   "0 breaks" is not the same as "nothing to review".
4. **Rollout and governance.** Advisory → gating, `intentional-breaking-change`
   labels that relax gates rather than skip checks, suppressions as
   *contract statements* with owners and expiry, the reachability-aware
   refusal to suppress a public-reachable break, policy profiles as
   named contract shapes, packs.

Each of these has a tool-track owner already; the learning pages are
summaries-with-links under `topics.yaml`'s `allowed_summaries`, not second
explanations.

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
  in CI and the catalog has PE-capable cases.
- **Other ABI domains** (kernel kABI/BTF, SYCL host vs device): one
  "advanced/expert" page listing them as further reading would connect the
  existing tool docs to the ladder without teaching them in full.
- **Reading machine output.** The AI-agent role path exists; a short
  "reading the JSON/SARIF" page (verdict, `compatibility_decision`,
  `impact_assessment`, coverage block, `evaluation_context`) would serve
  agents and CI authors alike and give `impact-analysis.md` a user-facing
  home for its field reference.
- **Lean on the failure demos.** Every case page has a "Real Failure
  Demo"; concept pages should link one per section (F7), and Part 7 —
  the pattern capstone — links two cases in 450 lines.

---

## 5. Proposed target shape

One ladder, levelled, with the numbered spine kept intact. Deep dives are
badged by tier rather than interleaved between Parts. Tool internals leave
the learning tree.

| Tier | Level | Pages (★ = new, ↻ = merged/moved) |
|---|---|---|
| **0 · Orientation** | novice | ABI in Five Minutes · ★ How a break shows up (symptom → mechanism → level) · Cheat Sheet · Glossary (↻ absorbs Part 1 §8) |
| **1 · Foundations** | novice → practitioner | Part 1 Foundations · Part 0 Product Contract (↻ gains the compatibility-levels ladder, §4.3) · Your ABI Surface |
| **2 · Mechanics** | practitioner | Parts 2, 3, 4, 5, 6 — with Class Layout, Exception Unwinding, Modern Hazards, MSVC/PE listed *under* their Part as "go deeper" (Part 4 drops its inline summaries) |
| **3 · Define the contract** | practitioner | Compatibility Direction · Consumer Models · ↻ Contract-Aware Compatibility (from Concepts) · Build-Profile Comparability |
| **4 · Evidence & detection** | practitioner → advanced | Evidence & Detectability (↻ gains the AST-as-artifact section; loses the removed-flags appendix to `contribute/archive/`) · What Each Level Sees · ↻ Detecting Breaks (§2 becomes links into the trio) · Assurance Beyond Static Checking |
| **5 · Practice** | practitioner | ★ Baselines as contracts · ★ Where in the pipeline (PR / main / nightly / release) · ★ Report the surface, not only the breaks · ★ Rollout & governance · ★ Triage a suspicious finding |
| **6 · At scale** | advanced | ★ Products, not libraries (multi-binary) · ★ Template- and header-heavy libraries · ★ How system libraries stay compatible (glibc / libstdc++ / linker) · Dependency & Runtime Floors · Environment & Toolchain Drift (↻ from Concepts) · ★ Packages and consumers (deb/rpm/conda/wheel, `abi3`, FFI) |
| **7 · Beyond static ABI** | advanced | ↻ one "What no static comparison can see" page with Behavioral / Data & Wire / Ownership / Concurrency as sections sharing a single introduction |
| **8 · Design** | practitioner | Part 7 Designing for Stability (↻ adds "the scanner recognises these patterns": idioms, pattern-aware verdicts) |
| **Concepts tab** (tool internals) | advanced → expert | Verdicts · Architecture · Build & Source Data (↻ split: model stays, findings/schema/storage to `reference/`) · Graph Coverage · Impact Assessment (↻ or `reference/`) · ELF Symbol Filtering · Limitations (↻ drops its copy of the filter section) |

Reading paths by role stay on the hub, but each path is then a walk *up*
the ladder rather than a jump list across tabs.

The `level:` front matter should carry the tier's level on every page in
the learning tree (today 14 pages have none), and the hub should render
that as a badge per row so the ladder is visible in the navigator, not
only in this plan.

---

## 6. Phased change list

Each phase is independently mergeable and leaves the site consistent.
Every moved page keeps its URL via `mkdocs.yml` `redirect_maps`; every new
page registers a topic in `docs/_meta/topics.yaml`, carries front matter,
and enters the nav (`scripts/check_docs_contract.py`,
`mkdocs build --strict`, and the `mkdocs-nav-coverage` AI-readiness check
gate all of this).

**Phase 1 — navigation and hub (no new prose).**

- Rebuild `abi-api-handling.md` around the tier table in §5; move the
  22-row break-family index and the three "deeper" sections to their
  owners (the cheat sheet, the trio, the practice pages once they exist).
- Make the on-ramp the front door: hub, `docs/index.md`, and
  `start/getting-started.md` link it first; remove the "don't start here"
  admonition.
- Add a consistent previous/next footer to every deep dive (the numbered
  Parts already have one); resolve the two-page loop and Part 7's double
  next.
- Regroup the nav to the tiers; add `level:` front matter to the 14
  pages lacking it.

**Phase 2 — de-duplicate (content moves, no new topics).**

- Evidence model: `08-detection.md` §2 and `architecture.md` §Evidence
  layers become one-paragraph summaries linking the trio; the trio banner
  goes on all three; `build-source-data.md`'s L0–L5 table goes.
- Part 4 drops the inline summaries of its three split-out sections.
- Part 1 §8 → `glossary.md`; `limitations.md`'s filter section → link to
  `elf-symbol-filtering.md`; one platform matrix (`reference/platforms.md`)
  with the others linking; one verdict/exit-code table (`verdicts.md`).
- The four "Beyond ABI" pages become sections of one page with a shared
  introduction (or keep four pages and delete the repeated introduction —
  either is fine; the repeated argument is the defect).
- `impact-analysis.md`, `graph-coverage.md`'s pass-state detail, and
  `build-source-data.md`'s schema/storage sections move to `reference/`
  or `contribute/`; the removed-flags appendix moves to
  `contribute/archive/`.

**Phase 3 — new pages, in the order a reader needs them.**

1. How a break shows up (§4.1)
2. Baselines as contracts (§4.6 item 1)
3. Where in the pipeline (§4.6 item 2)
4. Report the surface, not only the breaks (§4.6 item 3)
5. Products, not libraries (§4.5)
6. Template- and header-heavy libraries (§4.5)
7. How system libraries stay compatible (§4.2)
8. Rollout & governance; Triage (§4.6 item 4, §4.7)
9. Packages and consumers (§4.7)

Each is a narrative owner in `topics.yaml` whose task pages are the
existing `use/`/`integration/` how-tos, so the how-tos keep the commands
and the learning page keeps the model.

**Phase 4 — worked examples on every concept page.**

- At least one runnable invocation and one linked case per concept page
  (F7); a Windows worked example for `msvc-pe-abi-model.md`; `case170` on
  `environment-drift.md`; the five bundle cases on the products page; the
  template cases on the templates page.
- Part 0 gains the compatibility-levels ladder; Part 7 gains the
  idiom/pattern-verdict section.

**Out of scope for this plan:** any change to what the scanner detects or
reports; the tool-track how-to pages' commands (they stay the fact owners);
the example catalog. If writing a practice page exposes a missing tool
feature — the most likely candidate is a first-class "surface growth"
report rather than three separate flags — record it in
`docs/contribute/usecase-registry.yaml` rather than papering over it in
prose.

---

## 7. Constraints the rewrite must respect

- `docs/AGENTS.md`'s one-owner rule and `topics.yaml`: a new learning page
  that restates a tool page's content is the defect this plan is fixing,
  not a way to fix it. New pages are narrative owners; the how-tos are
  their task pages.
- The evidence trio is deliberately three pages; the AST section and the
  authority rule consolidate *into* it, they do not add a fourth.
- Every moved or renamed page keeps a redirect; pre-Stage-4 `concepts/…`
  URLs already redirect and must keep doing so.
- The educational tab keeps abicheck's internal module names out of
  prose (`depends_on` front matter carries traceability instead).
- Headline counts (change kinds, case count) are pulled from their fact
  owners, never typed into a learning page.
